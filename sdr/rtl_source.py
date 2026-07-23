"""Wraps rtl_fm.exe as the SDR source: builds its command line from config,
runs it as a subprocess, and streams its raw PCM stdout to a caller.

rtl_fm is used instead of a hand-rolled RTL-SDR driver because it is the
proven, widely-used tool for exactly this job (see librtlsdr docs); see
README.md "Waarom rtl_fm" for the rationale.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional

from config import SdrConfig
from job_object import ChildLifetimeJob, JobObjectUnavailableError

# Substrings seen in rtl_fm/librtlsdr stderr when the device is unavailable,
# used to tell "SDR busy / unplugged" apart from other startup failures.
DEVICE_BUSY_MARKERS = (
    "usb_claim_interface error",
    "usb_open error",
    "Failed to open rtlsdr device",
    "No supported devices found",
)


def build_command(config: SdrConfig) -> List[str]:
    args: List[str] = [
        str(config.rtl_fm_path),
        "-f", str(config.frequency_hz),
        "-M", "fm",
        "-s", str(config.sample_rate_hz),
        "-r", str(config.resample_rate_hz),
        "-p", str(config.ppm_correction),
        "-l", str(config.squelch_level),
    ]

    if config.device_serial:
        args += ["-d", config.device_serial]
    else:
        args += ["-d", str(config.device_index)]

    if config.gain_db != "auto":
        args += ["-g", config.gain_db]

    args.append("-")  # write PCM to stdout
    return args


class RtlFmProcessError(RuntimeError):
    def __init__(self, message: str, device_busy: bool):
        super().__init__(message)
        self.device_busy = device_busy


class RtlFmSource:
    """Runs rtl_fm as a subprocess and exposes its stdout as a byte stream.

    Not safe to call start() from multiple threads; intended to be driven by
    a single reconnect loop (see receiver.py).
    """

    def __init__(
        self,
        config: SdrConfig,
        on_stderr_line: Optional[Callable[[str], None]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._config = config
        self._on_stderr_line = on_stderr_line
        self._logger = logger
        self._process: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_tail: List[str] = []
        self._stderr_lock = threading.Lock()
        self._read_carry = b""  # odd trailing byte from the previous short read, see read_pcm_chunk
        try:
            self._job = ChildLifetimeJob()
        except JobObjectUnavailableError:
            # Best-effort hardening: without it, a hard-killed receiver can
            # orphan rtl_fm.exe holding the SDR device (see README "Bekende
            # risico's"). Not fatal - the receiver still works, just without
            # that guarantee.
            self._job = None

    def start(self) -> None:
        if not Path(self._config.rtl_fm_path).exists():
            raise FileNotFoundError(f"rtl_fm not found at {self._config.rtl_fm_path}")

        command = build_command(self._config)
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        self._read_carry = b""
        if self._job is not None:
            assigned = self._job.assign(self._process)
            if not assigned and self._logger is not None:
                self._logger.warning(
                    "Could not assign rtl_fm.exe (pid %s) to the orphan-protection Job Object - "
                    "if this process is killed hard, rtl_fm.exe may be left running and hold the SDR device.",
                    self._process.pid,
                )
        self._stderr_tail = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for raw_line in self._process.stderr:
            line = raw_line.decode(errors="replace").rstrip()
            if not line:
                continue
            with self._stderr_lock:
                self._stderr_tail.append(line)
                self._stderr_tail = self._stderr_tail[-20:]
            if self._on_stderr_line:
                self._on_stderr_line(line)

    def read_pcm_chunk(self, chunk_size: int = 4096) -> bytes:
        """Returns up to chunk_size bytes of 16-bit-sample-aligned PCM, or
        b"" when the process has exited (EOF).

        stdout is opened with bufsize=0 (raw, unbuffered) so a single read()
        can return fewer bytes than requested - including an odd byte count,
        which would otherwise crash apply_gain()'s int16 array conversion.
        Loops to top up short reads, and carries over a lone trailing byte
        to prepend to the next chunk instead of ever handing out a
        non-sample-aligned chunk.
        """
        assert self._process is not None and self._process.stdout is not None
        buf = bytearray(self._read_carry)
        self._read_carry = b""

        while len(buf) < chunk_size:
            piece = self._process.stdout.read(chunk_size - len(buf))
            if not piece:
                break
            buf += piece

        if len(buf) % 2 != 0:
            self._read_carry = bytes(buf[-1:])
            del buf[-1:]

        return bytes(buf)

    def poll_exit_code(self) -> Optional[int]:
        assert self._process is not None
        return self._process.poll()

    def raise_if_device_busy(self) -> None:
        """Call after stdout hit EOF, to classify the failure. stdout closing
        can be observed a moment before the OS finishes tearing the process
        down and Popen.poll() reports an exit code, so give it a brief grace
        wait rather than risk misreporting a real "device busy" failure as a
        generic, less actionable EOF.
        """
        assert self._process is not None
        try:
            self._process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass

        with self._stderr_lock:
            tail = "\n".join(self._stderr_tail)
        device_busy = any(marker in tail for marker in DEVICE_BUSY_MARKERS)
        exit_code = self.poll_exit_code()
        if exit_code not in (0, None):
            raise RtlFmProcessError(
                f"rtl_fm exited with code {exit_code}. Last stderr:\n{tail}",
                device_busy=device_busy,
            )

    def stop(self, timeout_seconds: float = 5.0) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                self._process.kill()
                try:
                    self._process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    # rtl_fm.exe is fully wedged even against SIGKILL - give up
                    # waiting rather than let this escape the caller's cleanup
                    # (finally block); the Job Object still guarantees it dies
                    # once this process exits, if nothing else does.
                    if self._logger is not None:
                        self._logger.error(
                            "rtl_fm.exe (pid %s) did not exit even after kill() - "
                            "abandoning wait, relying on the Job Object for eventual cleanup.",
                            self._process.pid,
                        )
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=timeout_seconds)
        self._process = None
        self._stderr_thread = None

    def close(self) -> None:
        """Releases the job object handle. Safe to call even if stop() was
        never called (e.g. after a failed start()); the OS also does this
        automatically when the process exits, this is just tidy cleanup for
        long-running processes that create many RtlFmSource instances over
        their lifetime (one per reconnect attempt).
        """
        if self._job is not None:
            self._job.close()
            self._job = None
