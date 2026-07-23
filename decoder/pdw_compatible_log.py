"""Write decoded FLEX messages in the line format consumed by the dispatcher."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from flex_messages import FlexMessage


def format_pdw_compatible_line(message: FlexMessage, timestamp: datetime | None = None) -> str:
    timestamp = timestamp or datetime.now()
    return (
        f"{message.capcode:07d} {timestamp:%H:%M:%S} {timestamp:%d-%m-%y} "
        f"FLEX-A  {message.output_type}  1600  {message.text}"
    )


class DailyFlexLogWriter:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def write(self, message: FlexMessage, timestamp: datetime | None = None) -> Path:
        timestamp = timestamp or datetime.now()
        path = self.directory / f"{timestamp:%y%m%d}.log"
        line = format_pdw_compatible_line(message, timestamp)
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="latin-1", errors="replace", newline="") as handle:
                handle.write(line + "\n")
                handle.flush()
        return path
