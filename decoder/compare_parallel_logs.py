"""Compare logging-only decoder output with PDW by content and timestamp."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from benchmark_captures import MESSAGE_RE

PARALLEL_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*PARALLEL FLEX "
    r"(\S+) capcode=(\d+) text=(.*)$"
)
PDW_TIME_RE = re.compile(r"^\d+\s+(\d{2}:\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{2})\s+")
LOG_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")
PARALLEL_START_MARKER = "Parallel FLEX-decoder actief"


@dataclass(frozen=True)
class TimedMessage:
    timestamp: datetime
    capcode: int
    output_type: str
    text: str

    @property
    def key(self) -> tuple[int, str, str]:
        return self.capcode, self.output_type, self.text


def parse_parallel_line(line: str) -> TimedMessage | None:
    match = PARALLEL_RE.match(line.strip())
    if not match:
        return None
    return TimedMessage(
        timestamp=datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"),
        output_type=match.group(2),
        capcode=int(match.group(3)),
        text=match.group(4).strip(),
    )


def parse_pdw_line(line: str) -> TimedMessage | None:
    message = MESSAGE_RE.match(line.strip())
    timestamp = PDW_TIME_RE.match(line.strip())
    if not message or not timestamp:
        return None
    return TimedMessage(
        timestamp=datetime.strptime(f"{timestamp.group(2)} {timestamp.group(1)}", "%d-%m-%y %H:%M:%S"),
        capcode=int(message.group(1)),
        output_type=message.group(2),
        text=message.group(3).strip(),
    )


def compare(decoded: list[TimedMessage], expected: list[TimedMessage], tolerance_seconds: float) -> dict:
    remaining = set(range(len(expected)))
    delays = []
    for actual in decoded:
        candidates = [
            (abs((actual.timestamp - expected[index].timestamp).total_seconds()), index)
            for index in remaining
            if actual.key == expected[index].key
        ]
        if not candidates:
            continue
        delay, index = min(candidates)
        if delay <= tolerance_seconds:
            remaining.remove(index)
            delays.append(delay)
    exact = len(delays)
    return {
        "decoded": len(decoded),
        "expected": len(expected),
        "exact": exact,
        "precision": exact / len(decoded) if decoded else 0.0,
        "recall": exact / len(expected) if expected else 0.0,
        "missed": len(expected) - exact,
        "unmatched_decoder_outputs": len(decoded) - exact,
        "max_absolute_delay_seconds": max(delays) if delays else None,
    }


def _read(paths: list[Path], encoding: str, parser) -> list[TimedMessage]:
    messages = []
    for path in paths:
        for line in path.read_text(encoding=encoding, errors="replace").splitlines():
            if message := parser(line):
                messages.append(message)
    return messages


def latest_parallel_session(paths: list[Path]) -> tuple[datetime, datetime]:
    start = None
    end = None
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            timestamp_match = LOG_TIME_RE.match(line)
            if not timestamp_match:
                continue
            timestamp = datetime.strptime(timestamp_match.group(1), "%Y-%m-%d %H:%M:%S")
            if PARALLEL_START_MARKER in line:
                start = timestamp
                end = timestamp
            elif start is not None and timestamp >= start:
                end = max(end, timestamp)
    if start is None or end is None:
        raise ValueError("receiver log contains no parallel-decoder session")
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receiver-log", type=Path, action="append", required=True)
    parser.add_argument("--pdw-log", type=Path, action="append", required=True)
    parser.add_argument("--tolerance-seconds", type=float, default=15.0)
    args = parser.parse_args()
    decoded = _read(args.receiver_log, "utf-8", parse_parallel_line)
    expected = _read(args.pdw_log, "latin-1", parse_pdw_line)
    start, end = latest_parallel_session(args.receiver_log)
    decoded = [message for message in decoded if start <= message.timestamp <= end]
    expected = [message for message in expected if start <= message.timestamp <= end]
    print(json.dumps({"summary": compare(decoded, expected, args.tolerance_seconds)}))


if __name__ == "__main__":
    main()
