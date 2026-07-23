"""p2000-decoder: koppel een of meer RTL-SDR-dongles, decodeer P2000/FLEX-
meldingen live en toon ze op het scherm - optioneel ook weggeschreven naar
een dag-logbestand.

Gebruik (een dongle, het gangbare geval):
    python cli.py --frequency 169650000
    python cli.py --frequency 169650000 --out logs/
    python cli.py --list-devices

Gebruik (meerdere dongles tegelijk, elk met een eigen frequentie):
    python cli.py --sdr 0 --sdr 1@169650000
    python cli.py --sdr serial:00000001@169650000 --sdr serial:00000002@169650000

Stop met Ctrl+C voor een nette shutdown (alle rtl_fm.exe-processen worden
netjes afgesloten).

Auteur: Starlight FM - https://grunnalert.nl
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

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

# Line-buffer stdout even when it's not a real terminal (piped to a file,
# redirected, etc.) - otherwise status/banner output can sit in Python's
# block-buffer and never appear before the process is killed/exits.
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):  # pragma: no cover - very old Python / exotic stream
    pass

BANNER = r"""
  ____ ___   ___   ___          _                     _
 |  _ \__ \ / _ \ / _ \        | |                   | |
 | |_) |) | | | | | | | ______ | |__   ___  ___  ___ | |__   ___ _ __
 |  _ < / /| | | | | | |______|| '_ \ / _ \/ __|/ __|| '_ \ / _ \ '__|
 | |_) / /_| |_| | |_| |       | | | |  __/ (__ \__ \| |_) |  __/ |
 |____/____|\___/ \___/        |_| |_|\___|\___||___/|_.__/ \___|_|

  p2000-decoder  -  door Starlight FM  -  https://grunnalert.nl
"""


@dataclass
class DeviceSpec:
    label: str
    frequency_hz: int
    device_index: Optional[int] = None
    device_serial: Optional[str] = None


def parse_sdr_spec(spec: str) -> DeviceSpec:
    """Parseert '--sdr'-specificaties: "0", "1@169650000",
    "serial:00000002" of "serial:00000002@169700000"."""
    device_part, _, freq_part = spec.partition("@")
    try:
        frequency_hz = int(freq_part) if freq_part else DEFAULT_FREQUENCY_HZ
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"ongeldige frequentie in --sdr {spec!r}") from exc

    if device_part.startswith("serial:"):
        serial = device_part[len("serial:"):]
        if not serial:
            raise argparse.ArgumentTypeError(f"lege serial in --sdr {spec!r}")
        return DeviceSpec(label=f"serial:{serial}", frequency_hz=frequency_hz, device_serial=serial)

    try:
        index = int(device_part)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ongeldige --sdr {spec!r}: verwacht device-index of 'serial:<serienummer>'"
        ) from exc
    return DeviceSpec(label=f"dev{index}", frequency_hz=frequency_hz, device_index=index)


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


class _SharedCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0

    def increment(self) -> None:
        with self._lock:
            self._value += 1

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


def run_device(
    spec: DeviceSpec,
    args: argparse.Namespace,
    shutdown_event: threading.Event,
    log_writer: Optional[DailyFlexLogWriter],
    counter: _SharedCounter,
    show_label: bool,
) -> None:
    """Draait de connect/decode/reconnect-lus voor exact een SDR-apparaat.
    Bedoeld om per apparaat op een eigen thread te draaien (zie main())."""
    rtl_fm_path = args.rtl_fm_path or (PROJECT_ROOT / "tools" / "rtl_fm.exe")
    sdr_config = SdrConfig(
        device_index=spec.device_index if spec.device_index is not None else 0,
        device_serial=spec.device_serial or "",
        frequency_hz=spec.frequency_hz,
        sample_rate_hz=args.sample_rate,
        resample_rate_hz=args.resample_rate,
        gain_db=args.gain,
        ppm_correction=args.ppm,
        squelch_level=args.squelch,
        rtl_fm_path=rtl_fm_path,
    )

    prefix = f"[{spec.label} {spec.frequency_hz / 1e6:.4f}MHz] " if show_label else ""

    def on_message(message: FlexMessage) -> None:
        counter.increment()
        line = format_pdw_compatible_line(message, datetime.now())
        print(prefix + line, flush=True)
        if log_writer is not None:
            log_writer.write(message)

    decoder = ParallelFlexDecoder(
        sample_rate=sdr_config.resample_rate_hz,
        window_seconds=args.window_seconds,
        hop_seconds=args.hop_seconds,
        on_message=on_message,
    )

    print(
        f"{prefix}gestart: {sdr_config.frequency_hz / 1e6:.4f} MHz, "
        f"sample_rate={sdr_config.sample_rate_hz} Hz -> resample {sdr_config.resample_rate_hz} Hz, "
        f"gain={sdr_config.gain_db}, ppm={sdr_config.ppm_correction}",
        flush=True,
    )

    backoff = 2.0
    max_backoff = 60.0
    backoff_multiplier = 2.0

    while not shutdown_event.is_set():
        source = RtlFmSource(sdr_config)
        try:
            source.start()
            while not shutdown_event.is_set():
                chunk = source.read_pcm_chunk(CHUNK_SIZE)
                if not chunk:
                    source.raise_if_device_busy()
                    raise RtlFmProcessError(f"{prefix}rtl_fm stdout onverwacht gesloten (EOF)", device_busy=False)
                decoder.feed_pcm(chunk)
                backoff = 2.0  # reset backoff once we've read data successfully
        except FileNotFoundError as exc:
            print(f"{prefix}FOUT: {exc}", file=sys.stderr)
            return
        except RtlFmProcessError as exc:
            label = "SDR bezet (nog vastgehouden door een ander programma)" if exc.device_busy else "rtl_fm-fout"
            print(f"{prefix}{label}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - reconnect loop mag nooit stilvallen
            print(f"{prefix}Onverwachte fout: {exc}", file=sys.stderr)
        finally:
            source.stop()
            source.close()

        if shutdown_event.is_set():
            break
        print(f"{prefix}Opnieuw verbinden over {backoff:.0f}s...", file=sys.stderr)
        shutdown_event.wait(backoff)
        backoff = min(backoff * backoff_multiplier, max_backoff)


def build_device_specs(args: argparse.Namespace) -> List[DeviceSpec]:
    if args.sdr:
        return args.sdr
    # Geen --sdr opgegeven: terugvallen op de simpele, klassieke enkele-dongle-vlaggen.
    return [
        DeviceSpec(
            label=f"serial:{args.device_serial}" if args.device_serial else f"dev{args.device_index}",
            frequency_hz=args.frequency,
            device_index=args.device_index,
            device_serial=args.device_serial or None,
        )
    ]


def run(args: argparse.Namespace) -> int:
    specs = build_device_specs(args)

    log_writer: Optional[DailyFlexLogWriter] = None
    if args.out is not None:
        log_writer = DailyFlexLogWriter(args.out)
        print(f"Meldingen worden ook weggeschreven naar: {args.out.resolve()}")

    if not args.no_banner:
        print(BANNER)

    counter = _SharedCounter()
    shutdown_event = threading.Event()

    def _handle_signal(_signum, _frame) -> None:
        print("\nStoppen... (rtl_fm.exe-processen worden netjes afgesloten)")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    show_label = len(specs) > 1
    if show_label:
        print(f"{len(specs)} SDR's tegelijk actief.")
    print("Wachten op meldingen... (Ctrl+C om te stoppen)\n")

    threads = [
        threading.Thread(
            target=run_device,
            args=(spec, args, shutdown_event, log_writer, counter, show_label),
            name=f"sdr-{spec.label}",
            daemon=True,
        )
        for spec in specs
    ]
    for t in threads:
        t.start()

    # De signal handler zet shutdown_event; deze join blijft dus gewoon
    # hangen tot Ctrl+C (of tot een dongle-thread besluit te stoppen, wat in
    # de huidige opzet niet gebeurt zolang shutdown_event niet gezet is).
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        shutdown_event.set()
        for t in threads:
            t.join()

    print(f"\nGestopt. Totaal {counter.value} meldingen gedecodeerd tijdens deze sessie.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Koppel een of meer RTL-SDR-dongles en decodeer P2000/FLEX-meldingen live.",
        epilog="Voorbeeld met meerdere dongles: --sdr 0 --sdr 1@169650000",
    )
    parser.add_argument("--frequency", type=int, default=DEFAULT_FREQUENCY_HZ, help="Frequentie in Hz voor de enkele-dongle-modus (standaard: P2000 169650000)")
    parser.add_argument("--device-index", type=int, default=0, help="RTL-SDR device-index voor de enkele-dongle-modus (standaard: 0)")
    parser.add_argument("--device-serial", type=str, default="", help="RTL-SDR device-serial voor de enkele-dongle-modus (heeft voorrang op --device-index)")
    parser.add_argument(
        "--sdr", type=parse_sdr_spec, action="append",
        help=(
            "Herhaalbaar: voeg een SDR-dongle toe voor de meerdere-dongles-modus. "
            "Formaat: INDEX[@FREQUENTIE_HZ] of serial:SERIENUMMER[@FREQUENTIE_HZ]. "
            "Als --sdr wordt gebruikt, worden --frequency/--device-index/--device-serial genegeerd."
        ),
    )
    parser.add_argument("--sample-rate", type=int, default=250_000, help="rtl_fm sample rate in Hz (standaard: 250000)")
    parser.add_argument("--resample-rate", type=int, default=22_050, help="rtl_fm resample rate in Hz (standaard: 22050)")
    parser.add_argument("--gain", type=str, default="auto", help="Gain in dB, of 'auto' (standaard: auto)")
    parser.add_argument("--ppm", type=int, default=0, help="PPM-correctie (standaard: 0)")
    parser.add_argument("--squelch", type=int, default=0, help="Squelch-niveau (standaard: 0)")
    parser.add_argument("--rtl-fm-path", type=Path, default=None, help="Pad naar rtl_fm.exe (standaard: tools/rtl_fm.exe naast dit script)")
    parser.add_argument("--window-seconds", type=float, default=8.0, help="Decodervenster in seconden (standaard: 8.0)")
    parser.add_argument("--hop-seconds", type=float, default=4.0, help="Decoder-hop in seconden (standaard: 4.0)")
    parser.add_argument("--out", type=Path, default=None, help="Map om dag-logbestanden (.log, PDW-compatibel formaat) in weg te schrijven (gedeeld door alle dongles)")
    parser.add_argument("--list-devices", action="store_true", help="Toon aangesloten RTL-SDR-apparaten en stop")
    parser.add_argument("--no-banner", action="store_true", help="Toon de opstartbanner niet")
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
