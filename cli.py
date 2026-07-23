"""p2000-decoder: koppel een RTL-SDR-dongle, decodeer P2000/FLEX-meldingen live
en toon ze op het scherm - optioneel ook weggeschreven naar een dag-logbestand.

Gebruik:
    python cli.py --frequency 169650000
    python cli.py --frequency 169650000 --out logs/
    python cli.py --list-devices

Stop met Ctrl+C voor een nette shutdown (rtl_fm.exe wordt netjes afgesloten).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR / "decoder"))
sys.path.insert(0, str(_SCRIPT_DIR / "sdr"))

# When frozen by PyInstaller (--onefile), __file__ resolves inside the
# temporary bootloader extraction dir, not next to the real .exe on disk - so
# tools/rtl_fm.exe (deliberately NOT bundled, see build_exe.bat) would never
# be found there. sys.executable is the real, on-disk .exe path in that case.
# This only affects where we look for tools/; the sys.path entries above are
# harmless no-ops in a frozen build (PyInstaller's own import hooks already
# provide these modules from the bundled archive by name).
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = _SCRIPT_DIR

from config import SdrConfig  # noqa: E402 - sys.path set up above
from rtl_source import RtlFmProcessError, RtlFmSource  # noqa: E402
from singleton import AlreadyRunningError, SingleInstanceLock  # noqa: E402
from parallel_decoder import ParallelFlexDecoder  # noqa: E402
from pdw_compatible_log import DailyFlexLogWriter, format_pdw_compatible_line  # noqa: E402
from flex_messages import FlexMessage  # noqa: E402

CHUNK_SIZE = 4096
DEFAULT_FREQUENCY_HZ = 169_650_000  # P2000 (Nederland)


def list_devices(rtl_test_path: Path) -> int:
    if not rtl_test_path.exists():
        print(f"FOUT: {rtl_test_path} niet gevonden", file=sys.stderr)
        return 1
    result = subprocess.run(
        [str(rtl_test_path), "-t"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    # rtl_test writes its device list to stderr.
    print(result.stderr or result.stdout or "(geen apparaten gevonden)")
    return 0


def _print_message(message: FlexMessage) -> None:
    line = format_pdw_compatible_line(message, datetime.now())
    print(line, flush=True)


def run(args: argparse.Namespace) -> int:
    rtl_fm_path = args.rtl_fm_path or (PROJECT_ROOT / "tools" / "rtl_fm.exe")
    sdr_config = SdrConfig(
        device_index=args.device_index,
        device_serial=args.device_serial or "",
        frequency_hz=args.frequency,
        sample_rate_hz=args.sample_rate,
        resample_rate_hz=args.resample_rate,
        gain_db=args.gain,
        ppm_correction=args.ppm,
        squelch_level=args.squelch,
        rtl_fm_path=rtl_fm_path,
    )

    log_writer: Optional[DailyFlexLogWriter] = None
    if args.out is not None:
        log_writer = DailyFlexLogWriter(args.out)
        print(f"Meldingen worden ook weggeschreven naar: {args.out.resolve()}")

    message_count = 0

    def on_message(message: FlexMessage) -> None:
        nonlocal message_count
        message_count += 1
        _print_message(message)
        if log_writer is not None:
            log_writer.write(message)

    decoder = ParallelFlexDecoder(
        sample_rate=sdr_config.resample_rate_hz,
        window_seconds=args.window_seconds,
        hop_seconds=args.hop_seconds,
        on_message=on_message,
    )

    shutdown_event = threading.Event()

    def _handle_signal(_signum, _frame) -> None:
        print("\nStoppen... (rtl_fm.exe wordt netjes afgesloten)")
        shutdown_event.set()

    import signal

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    backoff = 2.0
    max_backoff = 60.0
    backoff_multiplier = 2.0

    print(
        f"p2000-decoder: {sdr_config.frequency_hz / 1e6:.4f} MHz, "
        f"sample_rate={sdr_config.sample_rate_hz} Hz -> resample {sdr_config.resample_rate_hz} Hz, "
        f"gain={sdr_config.gain_db}, ppm={sdr_config.ppm_correction}"
    )
    print("Wachten op meldingen... (Ctrl+C om te stoppen)\n")

    while not shutdown_event.is_set():
        source = RtlFmSource(sdr_config)
        try:
            source.start()
            while not shutdown_event.is_set():
                chunk = source.read_pcm_chunk(CHUNK_SIZE)
                if not chunk:
                    source.raise_if_device_busy()
                    raise RtlFmProcessError("rtl_fm stdout onverwacht gesloten (EOF)", device_busy=False)
                decoder.feed_pcm(chunk)
                backoff = 2.0  # reset backoff once we've read data successfully
        except FileNotFoundError as exc:
            print(f"FOUT: {exc}", file=sys.stderr)
            return 2
        except RtlFmProcessError as exc:
            label = "SDR bezet (nog vastgehouden door een ander programma)" if exc.device_busy else "rtl_fm-fout"
            print(f"{label}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - reconnect loop mag nooit stilvallen
            print(f"Onverwachte fout: {exc}", file=sys.stderr)
        finally:
            source.stop()
            source.close()

        if shutdown_event.is_set():
            break
        print(f"Opnieuw verbinden over {backoff:.0f}s...", file=sys.stderr)
        shutdown_event.wait(backoff)
        backoff = min(backoff * backoff_multiplier, max_backoff)

    print(f"\nGestopt. Totaal {message_count} meldingen gedecodeerd tijdens deze sessie.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Koppel een RTL-SDR-dongle en decodeer P2000/FLEX-meldingen live.",
    )
    parser.add_argument("--frequency", type=int, default=DEFAULT_FREQUENCY_HZ, help="Frequentie in Hz (standaard: P2000 169650000)")
    parser.add_argument("--device-index", type=int, default=0, help="RTL-SDR device-index (standaard: 0)")
    parser.add_argument("--device-serial", type=str, default="", help="RTL-SDR device-serial (heeft voorrang op --device-index)")
    parser.add_argument("--sample-rate", type=int, default=250_000, help="rtl_fm sample rate in Hz (standaard: 250000)")
    parser.add_argument("--resample-rate", type=int, default=22_050, help="rtl_fm resample rate in Hz (standaard: 22050)")
    parser.add_argument("--gain", type=str, default="auto", help="Gain in dB, of 'auto' (standaard: auto)")
    parser.add_argument("--ppm", type=int, default=0, help="PPM-correctie (standaard: 0)")
    parser.add_argument("--squelch", type=int, default=0, help="Squelch-niveau (standaard: 0)")
    parser.add_argument("--rtl-fm-path", type=Path, default=None, help="Pad naar rtl_fm.exe (standaard: tools/rtl_fm.exe naast dit script)")
    parser.add_argument("--window-seconds", type=float, default=8.0, help="Decodervenster in seconden (standaard: 8.0)")
    parser.add_argument("--hop-seconds", type=float, default=4.0, help="Decoder-hop in seconden (standaard: 4.0)")
    parser.add_argument("--out", type=Path, default=None, help="Map om dag-logbestanden (.log, PDW-compatibel formaat) in weg te schrijven")
    parser.add_argument("--list-devices", action="store_true", help="Toon aangesloten RTL-SDR-apparaten en stop")
    parser.add_argument("--lock-file", type=Path, default=PROJECT_ROOT / "p2000-decoder.lock", help="Pad voor de single-instance lock")
    args = parser.parse_args(argv)

    if args.list_devices:
        return list_devices(PROJECT_ROOT / "tools" / "rtl_test.exe")

    try:
        with SingleInstanceLock(args.lock_file):
            return run(args)
    except AlreadyRunningError as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
