"""Fail-closed production cutover gate for the parallel FLEX decoder."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from compare_parallel_logs import _read, compare, latest_parallel_session, parse_parallel_line, parse_pdw_line


def assess_readiness(summary: dict, status: dict, min_expected: int = 200) -> dict:
    reasons = []
    if summary.get("expected", 0) < min_expected:
        reasons.append(f"te weinig referenties ({summary.get('expected', 0)}/{min_expected})")
    if summary.get("recall", 0.0) < 0.99:
        reasons.append(f"recall onder 99% ({100 * summary.get('recall', 0.0):.2f}%)")
    if summary.get("precision", 0.0) < 0.999:
        reasons.append(f"precisie onder 99,9% ({100 * summary.get('precision', 0.0):.2f}%)")
    if status.get("decoder_dropped_chunks", 0):
        reasons.append(f"decoder heeft {status['decoder_dropped_chunks']} PCM-chunks verloren")
    if status.get("decoder_last_error"):
        reasons.append(f"decoderfout: {status['decoder_last_error']}")
    if not status.get("sdr_connected"):
        reasons.append("SDR is niet verbonden")
    return {"ready": not reasons, "reasons": reasons, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receiver-log", type=Path, required=True)
    parser.add_argument("--pdw-log", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--min-expected", type=int, default=200)
    args = parser.parse_args()

    receiver_paths = [args.receiver_log]
    start, end = latest_parallel_session(receiver_paths)
    decoded = [
        message for message in _read(receiver_paths, "utf-8", parse_parallel_line)
        if start <= message.timestamp <= end
    ]
    expected = [
        message for message in _read([args.pdw_log], "latin-1", parse_pdw_line)
        if start <= message.timestamp <= end
    ]
    result = assess_readiness(
        compare(decoded, expected, tolerance_seconds=15),
        json.loads(args.status.read_text(encoding="utf-8")),
        min_expected=args.min_expected,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
