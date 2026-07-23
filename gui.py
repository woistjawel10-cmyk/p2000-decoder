"""p2000-decoder GUI: PDW-achtige tabelweergave om P2000/FLEX-meldingen live
te decoderen vanaf een RTL-SDR-dongle - instellingen, Start/Stop, zoeken/
filteren, geluid bij spoedmeldingen, en CSV-export / oude logs terugladen.

Auteur: Starlight FM - https://grunnalert.nl
"""
from __future__ import annotations

import csv
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

try:
    import winsound
except ImportError:  # pragma: no cover - alleen relevant buiten Windows
    winsound = None

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR / "decoder"))
sys.path.insert(0, str(_SCRIPT_DIR / "sdr"))

if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = _SCRIPT_DIR

from config import SdrConfig  # noqa: E402 - sys.path set up above
from rtl_source import RtlFmProcessError, RtlFmSource  # noqa: E402
from singleton import AlreadyRunningError, SingleInstanceLock  # noqa: E402
from parallel_decoder import ParallelFlexDecoder  # noqa: E402
from pdw_compatible_log import DailyFlexLogWriter  # noqa: E402
from flex_messages import FlexMessage  # noqa: E402

CHUNK_SIZE = 4096
DEFAULT_FREQUENCY_HZ = 169_650_000  # P2000 (Nederland)
APP_TITLE = "p2000-decoder - door Starlight FM"

# Herkent P1/A1-achtige spoedprioriteiten in de berichttekst voor rode
# opmaak in de tabel, bijv. "P 1 BNH-01 ...", "A1 Ambu ...", "SPOED AMBU".
_URGENT_RE = re.compile(r"\b([AB]1\b|P ?1\b)|SPOED", re.IGNORECASE)

# Matcht regels zoals format_pdw_compatible_line() ze schrijft, voor het
# terugladen van bestaande .log-bestanden:
# "0302229 14:12:54 23-07-26 FLEX-A  ALPHA  1600  SPOED AMBU"
_LOG_LINE_RE = re.compile(
    r"^(?P<capcode>\d{7}) (?P<time>\d{2}:\d{2}:\d{2}) (?P<date>\d{2}-\d{2}-\d{2}) "
    r"FLEX-A\s+(?P<type>\S+)\s+1600\s+(?P<text>.*)$"
)

Row = Tuple[str, str, str, str, bool]  # tijd, capcode, type, tekst, is_urgent


@dataclass
class DeviceOption:
    label: str
    index: Optional[int]
    serial: Optional[str]


def discover_devices(rtl_test_path: Path) -> List[DeviceOption]:
    """Vraagt rtl_test -t om de aangesloten RTL-SDR's en parseert de
    "  0:  Realtek, RTL2838UHIDIR, SN: 00000001"-regels."""
    options = [DeviceOption(label="Apparaat 0 (standaard)", index=0, serial=None)]
    if not rtl_test_path.exists():
        return options
    try:
        result = subprocess.run(
            [str(rtl_test_path), "-t"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return options

    text = result.stderr or result.stdout or ""
    found: List[DeviceOption] = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+):\s*(.*?),\s*SN:\s*(\S+)", line)
        if match:
            index, name, serial = match.groups()
            found.append(DeviceOption(label=f"{index}: {name} (SN {serial})", index=int(index), serial=serial))
    return found or options


def parse_log_line(line: str) -> Optional[Row]:
    match = _LOG_LINE_RE.match(line.rstrip("\n"))
    if not match:
        return None
    text = match.group("text")
    is_urgent = bool(_URGENT_RE.search(text))
    return (match.group("time"), match.group("capcode"), match.group("type"), text, is_urgent)


class DecoderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x660")
        self.minsize(800, 480)

        self._message_queue: "queue.Queue[FlexMessage]" = queue.Queue()
        self._status_queue: "queue.Queue[str]" = queue.Queue()
        self._shutdown_event: Optional[threading.Event] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._log_writer: Optional[DailyFlexLogWriter] = None
        self._all_rows: List[Row] = []

        self._build_widgets()
        self._refresh_devices()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._poll_queues)

    # ------------------------------------------------------------------ UI
    def _build_widgets(self) -> None:
        settings = ttk.LabelFrame(self, text="Instellingen")
        settings.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        ttk.Label(settings, text="Apparaat:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.device_combo = ttk.Combobox(settings, state="readonly", width=38)
        self.device_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Button(settings, text="Vernieuwen", command=self._refresh_devices).grid(row=0, column=2, padx=4, pady=4)

        ttk.Label(settings, text="Frequentie (MHz):").grid(row=0, column=3, sticky="w", padx=(16, 4), pady=4)
        self.frequency_var = tk.StringVar(value=f"{DEFAULT_FREQUENCY_HZ / 1e6:.4f}")
        ttk.Entry(settings, textvariable=self.frequency_var, width=10).grid(row=0, column=4, sticky="w", padx=4, pady=4)

        ttk.Label(settings, text="Gain:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.gain_var = tk.StringVar(value="auto")
        ttk.Entry(settings, textvariable=self.gain_var, width=10).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        self.write_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings, text="Schrijf naar .log-bestand in map:", variable=self.write_var,
            command=self._on_write_toggle,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(16, 4), pady=4)
        self.out_dir_var = tk.StringVar(value=str(PROJECT_ROOT / "logs"))
        self.out_entry = ttk.Entry(settings, textvariable=self.out_dir_var, width=30, state="disabled")
        self.out_entry.grid(row=1, column=4, sticky="w", padx=4, pady=4)
        self.browse_button = ttk.Button(settings, text="Bladeren...", command=self._browse_out_dir, state="disabled")
        self.browse_button.grid(row=1, column=5, padx=4, pady=4)

        self.sound_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings, text="Geluid bij spoedmeldingen (A1/B1/P1/SPOED)", variable=self.sound_var).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=4, pady=4
        )

        control = ttk.Frame(self)
        control.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 6))
        self.start_button = ttk.Button(control, text="Start", command=self._on_start_stop)
        self.start_button.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Gestopt.")
        ttk.Label(control, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        toolbar = ttk.Frame(self)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(toolbar, text="Zoeken/filteren:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._apply_filter())
        ttk.Entry(toolbar, textvariable=self.search_var, width=30).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Button(toolbar, text="Log openen...", command=self._open_log).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Exporteer naar CSV...", command=self._export_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Wis lijst", command=self._clear_rows).pack(side=tk.LEFT, padx=4)
        self.count_var = tk.StringVar(value="0 meldingen")
        ttk.Label(toolbar, textvariable=self.count_var).pack(side=tk.RIGHT, padx=4)

        table_frame = ttk.Frame(self)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        columns = ("tijd", "capcode", "type", "tekst")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self.tree.heading("tijd", text="Tijd")
        self.tree.heading("capcode", text="Capcode")
        self.tree.heading("type", text="Type")
        self.tree.heading("tekst", text="Tekst")
        self.tree.column("tijd", width=90, anchor="w")
        self.tree.column("capcode", width=90, anchor="w")
        self.tree.column("type", width=70, anchor="w")
        self.tree.column("tekst", width=650, anchor="w")
        self.tree.tag_configure("urgent", foreground="#c62828")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)

        footer = ttk.Frame(self)
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(footer, text=f"{APP_TITLE} - https://grunnalert.nl").pack(side=tk.LEFT)

    def _refresh_devices(self) -> None:
        devices = discover_devices(PROJECT_ROOT / "tools" / "rtl_test.exe")
        self._device_options = devices
        self.device_combo["values"] = [d.label for d in devices]
        self.device_combo.current(0)

    def _on_write_toggle(self) -> None:
        state = "normal" if self.write_var.get() else "disabled"
        self.out_entry.configure(state=state)
        self.browse_button.configure(state=state)

    def _browse_out_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.out_dir_var.get() or str(PROJECT_ROOT))
        if chosen:
            self.out_dir_var.set(chosen)

    # ------------------------------------------------------------- Start/Stop
    def _on_start_stop(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        try:
            frequency_hz = int(round(float(self.frequency_var.get().replace(",", ".")) * 1e6))
        except ValueError:
            messagebox.showerror(APP_TITLE, "Ongeldige frequentie. Gebruik bijv. 169.6500")
            return

        selection = self.device_combo.current()
        device = self._device_options[selection] if 0 <= selection < len(self._device_options) else self._device_options[0]

        self._log_writer = None
        if self.write_var.get():
            out_dir = Path(self.out_dir_var.get())
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Kan map niet aanmaken:\n{exc}")
                return
            self._log_writer = DailyFlexLogWriter(out_dir)

        sdr_config = SdrConfig(
            device_index=device.index if device.index is not None else 0,
            device_serial=device.serial or "",
            frequency_hz=frequency_hz,
            sample_rate_hz=250_000,
            resample_rate_hz=22_050,
            gain_db=self.gain_var.get().strip() or "auto",
            ppm_correction=0,
            squelch_level=0,
            rtl_fm_path=PROJECT_ROOT / "tools" / "rtl_fm.exe",
        )

        self._shutdown_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, args=(sdr_config, self._shutdown_event), daemon=True,
        )
        self._worker_thread.start()
        self.start_button.configure(text="Stop")
        self.status_var.set(f"Gestart op {sdr_config.frequency_hz / 1e6:.4f} MHz...")

    def _stop(self) -> None:
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        self.start_button.configure(text="Start")
        self.status_var.set("Gestopt.")

    def _on_close(self) -> None:
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        self.destroy()

    # --------------------------------------------------------------- worker
    def _worker_loop(self, sdr_config: SdrConfig, shutdown_event: threading.Event) -> None:
        def on_message(message: FlexMessage) -> None:
            self._message_queue.put(message)
            if self._log_writer is not None:
                self._log_writer.write(message)

        decoder = ParallelFlexDecoder(
            sample_rate=sdr_config.resample_rate_hz,
            window_seconds=8.0,
            hop_seconds=4.0,
            on_message=on_message,
        )

        backoff = 2.0
        max_backoff = 60.0
        while not shutdown_event.is_set():
            source = RtlFmSource(sdr_config)
            try:
                source.start()
                self._status_queue.put(f"Verbonden - {sdr_config.frequency_hz / 1e6:.4f} MHz")
                while not shutdown_event.is_set():
                    chunk = source.read_pcm_chunk(CHUNK_SIZE)
                    if not chunk:
                        source.raise_if_device_busy()
                        raise RtlFmProcessError("rtl_fm stdout onverwacht gesloten (EOF)", device_busy=False)
                    decoder.feed_pcm(chunk)
                    backoff = 2.0
            except FileNotFoundError as exc:
                self._status_queue.put(f"FOUT: {exc}")
                return
            except RtlFmProcessError as exc:
                label = "SDR bezet" if exc.device_busy else "rtl_fm-fout"
                self._status_queue.put(f"{label}: {exc}")
            except Exception as exc:  # noqa: BLE001 - reconnect loop mag nooit stilvallen
                self._status_queue.put(f"Onverwachte fout: {exc}")
            finally:
                source.stop()
                source.close()

            if shutdown_event.is_set():
                break
            self._status_queue.put(f"Opnieuw verbinden over {backoff:.0f}s...")
            shutdown_event.wait(backoff)
            backoff = min(backoff * 2, max_backoff)

        self._status_queue.put("Gestopt.")

    # ---------------------------------------------------------------- polling
    def _poll_queues(self) -> None:
        try:
            while True:
                message = self._message_queue.get_nowait()
                self._add_row(
                    datetime.now().strftime("%H:%M:%S"),
                    str(message.capcode),
                    message.output_type,
                    message.text,
                )
        except queue.Empty:
            pass

        try:
            while True:
                status = self._status_queue.get_nowait()
                self.status_var.set(status)
        except queue.Empty:
            pass

        self.after(150, self._poll_queues)

    # ------------------------------------------------------------- rijbeheer
    def _matches_filter(self, row: Row, needle: str) -> bool:
        if not needle:
            return True
        haystack = f"{row[1]} {row[2]} {row[3]}".lower()
        return needle in haystack

    def _add_row(self, tijd: str, capcode: str, msg_type: str, tekst: str) -> None:
        is_urgent = bool(_URGENT_RE.search(tekst))
        row: Row = (tijd, capcode, msg_type, tekst, is_urgent)
        self._all_rows.append(row)
        self.count_var.set(f"{len(self._all_rows)} meldingen")

        if is_urgent and self.sound_var.get() and winsound is not None:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except OSError:
                pass  # geluid is een extraatje, mag nooit de app laten crashen

        needle = self.search_var.get().strip().lower()
        if self._matches_filter(row, needle):
            self._insert_tree_row(row)

    def _insert_tree_row(self, row: Row) -> None:
        tijd, capcode, msg_type, tekst, is_urgent = row
        tags = ("urgent",) if is_urgent else ()
        self.tree.insert("", tk.END, values=(tijd, capcode, msg_type, tekst), tags=tags)
        self.tree.yview_moveto(1.0)

    def _apply_filter(self) -> None:
        needle = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for row in self._all_rows:
            if self._matches_filter(row, needle):
                self._insert_tree_row(row)

    def _clear_rows(self) -> None:
        self._all_rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.count_var.set("0 meldingen")

    # --------------------------------------------------------- import/export
    def _open_log(self) -> None:
        path = filedialog.askopenfilename(
            title="Log openen",
            filetypes=[("P2000/PDW-logbestanden", "*.log *.txt"), ("Alle bestanden", "*.*")],
        )
        if not path:
            return
        added = 0
        skipped = 0
        try:
            with open(path, "r", encoding="latin-1", errors="replace") as handle:
                for line in handle:
                    parsed = parse_log_line(line)
                    if parsed is None:
                        if line.strip():
                            skipped += 1
                        continue
                    self._all_rows.append(parsed)
                    added += 1
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Kan bestand niet lezen:\n{exc}")
            return

        self.count_var.set(f"{len(self._all_rows)} meldingen")
        self._apply_filter()
        messagebox.showinfo(
            APP_TITLE,
            f"{added} melding(en) geladen uit {Path(path).name}."
            + (f"\n{skipped} regel(s) niet herkend en overgeslagen." if skipped else ""),
        )

    def _export_csv(self) -> None:
        visible_ids = self.tree.get_children()
        if not visible_ids:
            messagebox.showinfo(APP_TITLE, "Niets te exporteren (de lijst is leeg of het filter levert niets op).")
            return
        path = filedialog.asksaveasfilename(
            title="Exporteren naar CSV",
            defaultextension=".csv",
            filetypes=[("CSV-bestand", "*.csv"), ("Alle bestanden", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Tijd", "Capcode", "Type", "Tekst"])
                for item_id in visible_ids:
                    writer.writerow(self.tree.item(item_id, "values"))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Kan niet opslaan:\n{exc}")
            return
        messagebox.showinfo(APP_TITLE, f"{len(visible_ids)} melding(en) geëxporteerd naar {Path(path).name}.")


def main() -> int:
    lock_path = PROJECT_ROOT / "p2000-decoder.lock"
    try:
        with SingleInstanceLock(lock_path):
            app = DecoderApp()
            app.mainloop()
    except AlreadyRunningError as exc:
        messagebox.showerror(APP_TITLE, str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
