"""Benchmark the actual overlapping-window decoder against captured PDW output."""
from __future__ import annotations

import argparse
import json
import time
import wave
from collections import Counter
from pathlib import Path

from benchmark_captures import pdw_typed_messages
from parallel_decoder import ParallelFlexDecoder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captures", type=Path, default=Path(__file__).resolve().parent / "captures")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--window-seconds", type=float, default=8.0)
    parser.add_argument("--hop-seconds", type=float, default=4.0)
    parser.add_argument("--chunk-frames", type=int, default=2048)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.captures.joinpath("manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if args.limit is not None:
        rows = rows[:args.limit]

    emitted = []
    decoder = None
    started = time.perf_counter()
    for row in rows:
        with wave.open(str(args.captures / row["wav_file"]), "rb") as wav_file:
            if decoder is None:
                decoder = ParallelFlexDecoder(
                    wav_file.getframerate(),
                    emitted.append,
                    window_seconds=args.window_seconds,
                    hop_seconds=args.hop_seconds,
                )
            if wav_file.getframerate() != decoder.sample_rate:
                raise ValueError("all captures must use the same sample rate")
            while chunk := wav_file.readframes(args.chunk_frames):
                decoder.feed_pcm(chunk)

    decoded = Counter((message.capcode, message.output_type, message.text) for message in emitted)
    expected = Counter()
    for row in rows:
        expected.update(pdw_typed_messages(row.get("pdw_lines", [])))
    exact = decoded & expected
    exact_count = sum(exact.values())
    decoded_count = sum(decoded.values())
    expected_count = sum(expected.values())
    summary = {
        "captures": len(rows),
        "stream_outputs": decoded_count,
        "exact": exact_count,
        "expected": expected_count,
        "precision": exact_count / decoded_count if decoded_count else 0.0,
        "recall": exact_count / expected_count if expected_count else 0.0,
        "alpha_exact": sum(count for (_, kind, _), count in exact.items() if kind == "ALPHA"),
        "group_exact": sum(count for (_, kind, _), count in exact.items() if kind == "GROUP"),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
