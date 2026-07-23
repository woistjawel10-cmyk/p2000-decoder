"""Reproducible offline benchmark against captured audio and PDW logs."""
from __future__ import annotations

import argparse
import json
import re
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Set

import numpy as np

from block_deinterleave import frame_words, short_address_capcode
from flex_messages import GroupAssignmentTracker, parse_alpha_messages, parse_group_assignments
from flex_sync import find_sync_matches
from flex_words import flex_checksum_ok
from fsk_demod import demodulate_2fsk

CAPCODE_RE = re.compile(r"^(\d+)")
MESSAGE_RE = re.compile(r"^(\d+)\s+\S+\s+\S+\s+FLEX-\S+\s+(\S+)\s+\d+\s+(.*)$")


@dataclass
class CaptureResult:
    wav_file: str
    pdw_capcodes: int
    syncs: int
    trusted_fiws: int
    plausible_biws: int
    decoded_address_words: int
    matching_capcodes: List[int]
    decoded_alpha_messages: int
    exact_message_matches: int
    expected_alpha_messages: int
    expected_group_messages: int
    decoded_alpha_outputs: int
    decoded_group_outputs: int
    exact_alpha_matches: int
    exact_group_matches: int


def pdw_capcodes(lines: Iterable[dict]) -> Set[int]:
    result = set()
    for item in lines:
        match = CAPCODE_RE.match(item.get("raw_line", ""))
        if match:
            result.add(int(match.group(1)))
    return result


def pdw_messages(lines: Iterable[dict]) -> Set[tuple[int, str]]:
    return {(capcode, text) for capcode, _output_type, text in pdw_typed_messages(lines)}


def pdw_typed_messages(lines: Iterable[dict]) -> Set[tuple[int, str, str]]:
    result = set()
    for item in lines:
        match = MESSAGE_RE.match(item.get("raw_line", ""))
        if match:
            result.add((int(match.group(1)), match.group(2), match.group(3).strip()))
    return result


def address_capcodes_from_words(words: List[int]) -> tuple[bool, Set[int], int]:
    """Use the BIW bounds; word 0 itself is never an address."""
    if not words:
        return False, set(), 0
    vector_start = (words[0] >> 10) & 0x3F
    address_start = ((words[0] >> 8) & 0x03) + 1
    plausible = flex_checksum_ok(words[0]) and address_start <= vector_start <= min(63, len(words))
    if not plausible:
        return False, set(), 0
    address_words = words[address_start:vector_start]
    capcodes = {
        capcode
        for word in address_words
        if 0 <= (capcode := short_address_capcode(word)) <= 9_999_999
    }
    return True, capcodes, len(address_words)


def benchmark_capture(
    captures_dir: Path,
    manifest_row: dict,
    track_timing: bool,
    group_tracker: GroupAssignmentTracker | None = None,
    ensemble_tracker: GroupAssignmentTracker | None = None,
    timing_loop_gain: float = 0.015,
) -> CaptureResult:
    wav_path = captures_dir / manifest_row["wav_file"]
    with wave.open(str(wav_path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        samples = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16)

    expected = pdw_capcodes(manifest_row.get("pdw_lines", []))
    expected_messages = pdw_messages(manifest_row.get("pdw_lines", []))
    expected_typed = pdw_typed_messages(manifest_row.get("pdw_lines", []))
    decoded: Set[int] = set()
    decoded_messages: Set[tuple[int, str]] = set()
    decoded_typed: Set[tuple[int, str, str]] = set()
    trackers = [group_tracker or GroupAssignmentTracker()]
    timing_modes = [track_timing]
    if ensemble_tracker is not None:
        trackers.append(ensemble_tracker)
        timing_modes.append(not track_timing)
    sync_keys = set()
    trustworthy_keys = set()
    plausible_counts = []
    address_counts = []
    for timing_mode, tracker in zip(timing_modes, trackers):
        bits = demodulate_2fsk(
            samples,
            sample_rate,
            1600.0,
            track_timing=timing_mode,
            timing_loop_gain=timing_loop_gain,
        ).bits
        syncs = find_sync_matches(bits)
        plausible_count = 0
        address_count = 0
        for sync in syncs:
            sync_key = (sync.bit_offset, sync.polarity_inverted)
            sync_keys.add(sync_key)
            if sync.fiw is not None and sync.fiw.trustworthy:
                trustworthy_keys.add(sync_key)
            words = frame_words(bits, sync.bit_offset, polarity_inverted=sync.polarity_inverted)
            plausible, capcodes, count = address_capcodes_from_words(words)
            plausible_count += int(plausible)
            address_count += count
            decoded.update(capcodes)
            for assignment in parse_group_assignments(words):
                tracker.add(assignment)
            messages = tracker.expand(sync.frame_no, parse_alpha_messages(words))
            decoded_messages.update((message.capcode, message.text) for message in messages)
            decoded_typed.update((message.capcode, message.output_type, message.text) for message in messages)
        plausible_counts.append(plausible_count)
        address_counts.append(address_count)

    exact_typed = decoded_typed & expected_typed

    return CaptureResult(
        wav_file=manifest_row["wav_file"],
        pdw_capcodes=len(expected),
        syncs=len(sync_keys),
        trusted_fiws=len(trustworthy_keys),
        plausible_biws=max(plausible_counts),
        decoded_address_words=max(address_counts),
        matching_capcodes=sorted(decoded & expected),
        decoded_alpha_messages=len(decoded_messages),
        exact_message_matches=len(decoded_messages & expected_messages),
        expected_alpha_messages=sum(output_type == "ALPHA" for _, output_type, _ in expected_typed),
        expected_group_messages=sum(output_type == "GROUP" for _, output_type, _ in expected_typed),
        decoded_alpha_outputs=sum(output_type == "ALPHA" for _, output_type, _ in decoded_typed),
        decoded_group_outputs=sum(output_type == "GROUP" for _, output_type, _ in decoded_typed),
        exact_alpha_matches=sum(output_type == "ALPHA" for _, output_type, _ in exact_typed),
        exact_group_matches=sum(output_type == "GROUP" for _, output_type, _ in exact_typed),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captures", type=Path, default=Path(__file__).resolve().parent / "captures")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fixed-timing", action="store_true")
    parser.add_argument("--ensemble", action="store_true")
    parser.add_argument("--timing-loop-gain", type=float, default=0.015)
    args = parser.parse_args()

    manifest_path = args.captures / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    if args.limit is not None:
        rows = rows[:args.limit]

    results = []
    group_tracker = GroupAssignmentTracker()
    ensemble_tracker = GroupAssignmentTracker() if args.ensemble else None
    for row in rows:
        result = benchmark_capture(
            args.captures,
            row,
            track_timing=not args.fixed_timing,
            group_tracker=group_tracker,
            ensemble_tracker=ensemble_tracker,
            timing_loop_gain=args.timing_loop_gain,
        )
        results.append(result)
        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)

    summary = {
        "captures": len(results),
        "pdw_capcodes": sum(item.pdw_capcodes for item in results),
        "syncs": sum(item.syncs for item in results),
        "trusted_fiws": sum(item.trusted_fiws for item in results),
        "plausible_biws": sum(item.plausible_biws for item in results),
        "decoded_address_words": sum(item.decoded_address_words for item in results),
        "matching_capcodes": sorted({cap for item in results for cap in item.matching_capcodes}),
        "decoded_alpha_messages": sum(item.decoded_alpha_messages for item in results),
        "exact_message_matches": sum(item.exact_message_matches for item in results),
        "expected_alpha_messages": sum(item.expected_alpha_messages for item in results),
        "expected_group_messages": sum(item.expected_group_messages for item in results),
        "decoded_alpha_outputs": sum(item.decoded_alpha_outputs for item in results),
        "decoded_group_outputs": sum(item.decoded_group_outputs for item in results),
        "exact_alpha_matches": sum(item.exact_alpha_matches for item in results),
        "exact_group_matches": sum(item.exact_group_matches for item in results),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
