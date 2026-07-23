"""Locates real FLEX frame starts using the actual FLEX sync word.

Sync word constant (0xA6C6AAAA) and the "MSB-first" word bit order are
taken from the public, GPL-licensed multimon-ng FLEX decoder
(EliasOenal/multimon-ng, demod_flex_next.c) as a reference for the
publicly documented FLEX protocol - not from PDW or any closed-source
decoder. Verified against real captured P2000 audio during development:
matches were found at exactly 3000-bit (1.875 second) intervals, which is
the well-documented FLEX 1600bps frame period (32 frames/minute), each
immediately followed by a BCH-trustworthy Frame Info Word - strong,
self-consistent evidence the sync word, bit order, and BCH polynomial are
all correct together (see flex_decoder/README.md "Validatie-log").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from pdw_bch import PdwBchResult, correct_pdw_column

SYNC_WORD = 0xA6C6AAAA
SYNC_WORD_BITS = 32
SYNC_OUTER_LEFT = 0x870C
SYNC_OUTER_RIGHT = 0x78F3
SYNC_OUTER_BITS = 16
FRAME_PERIOD_BITS_1600 = 3000  # 1.875s at 1600 baud - useful as a sanity cross-check


def _bits_string(bits: np.ndarray) -> str:
    return "".join("1" if b else "0" for b in bits)


@dataclass
class FrameSyncMatch:
    bit_offset: int  # start of the sync word itself
    fiw_offset: int  # FIW starts 32 holdoff bits after the central sync
    fiw: Optional[PdwBchResult]
    polarity_inverted: bool = False
    cycle_no: Optional[int] = None
    frame_no: Optional[int] = None


def _cycle_frame(corrected_bits: List[int]) -> tuple[int, int]:
    header = 0
    for index in range(4, 8):
        header >>= 1
        if corrected_bits[index] == 1:
            header ^= 0x08
    cycle = (header & 0x0F) ^ 0x0F
    for index in range(8, 15):
        header >>= 1
        if corrected_bits[index] == 1:
            header ^= 0x40
    frame = (header & 0x7F) ^ 0x7F
    return cycle, frame


def _candidate_positions(bits_str: str, pattern: str) -> set[int]:
    """Find the exact pattern and every variant with one flipped bit.

    ``str.find`` performs these relatively few scans in optimized C and is
    considerably faster than walking every possible 32-bit window in Python.
    """
    positions: set[int] = set()
    variants = [pattern]
    for index, bit in enumerate(pattern):
        variants.append(pattern[:index] + ("0" if bit == "1" else "1") + pattern[index + 1:])
    for variant in variants:
        start = 0
        while True:
            position = bits_str.find(variant, start)
            if position == -1:
                break
            positions.add(position)
            start = position + 1
    return positions


def _outer_sync_trustworthy(bits_str: str, position: int, inverted: bool) -> bool:
    if position < SYNC_OUTER_BITS or position + SYNC_WORD_BITS + SYNC_OUTER_BITS > len(bits_str):
        return False
    observed = bits_str[position - SYNC_OUTER_BITS:position]
    observed += bits_str[position + SYNC_WORD_BITS:position + SYNC_WORD_BITS + SYNC_OUTER_BITS]
    expected = format(SYNC_OUTER_LEFT, "016b") + format(SYNC_OUTER_RIGHT, "016b")
    if inverted:
        expected = "".join("1" if bit == "0" else "0" for bit in expected)
    return sum(a != b for a, b in zip(observed, expected)) <= 1


def find_sync_matches(bits: np.ndarray) -> List[FrameSyncMatch]:
    """Searches for the sync word AND its bit-complement (since a
    2-level FSK slicer's polarity/"which level is a 1" is not guaranteed
    without extra information - both the true sync word and its complement
    are valid things to search for depending on that polarity). Exact central
    syncs remain accepted for compatibility; one-bit central errors require
    the two outer sync fields to contain at most one error in total, matching
    PDW's sync thresholds. The BCH-protected FIW is decoded for every hit.
    """
    bits_str = _bits_string(bits)
    sync_str = format(SYNC_WORD, f"0{SYNC_WORD_BITS}b")
    sync_str_inv = "".join("1" if c == "0" else "0" for c in sync_str)

    matches: List[FrameSyncMatch] = []
    for pattern, polarity_inverted in ((sync_str, False), (sync_str_inv, True)):
        exact_positions = _candidate_positions(bits_str, pattern)
        for pos in sorted(exact_positions):
            central = bits_str[pos:pos + SYNC_WORD_BITS]
            central_errors = sum(a != b for a, b in zip(central, pattern))
            if central_errors and not _outer_sync_trustworthy(bits_str, pos, polarity_inverted):
                continue
            fiw_offset = pos + SYNC_WORD_BITS + 32
            fiw_result = None
            cycle_no = None
            frame_no = None
            if fiw_offset + 32 <= len(bits_str):
                fiw_bits = bits_str[fiw_offset:fiw_offset + 32]
                if polarity_inverted:
                    fiw_bits = "".join("1" if bit == "0" else "0" for bit in fiw_bits)
                fiw_result = correct_pdw_column(int(bit) for bit in fiw_bits)
                if fiw_result.correctable:
                    cycle_no, frame_no = _cycle_frame(fiw_result.bits)
            matches.append(FrameSyncMatch(
                bit_offset=pos,
                fiw_offset=fiw_offset,
                fiw=fiw_result,
                polarity_inverted=polarity_inverted,
                cycle_no=cycle_no,
                frame_no=frame_no,
            ))
    matches.sort(key=lambda m: m.bit_offset)
    return matches


def trustworthy_frame_starts(bits: np.ndarray) -> List[FrameSyncMatch]:
    """Sync matches whose following FIW actually decoded as BCH-trustworthy
    - the set worth treating as real frame starts rather than a
    coincidental sync-word-shaped bit sequence."""
    return [m for m in find_sync_matches(bits) if m.fiw is not None and m.fiw.trustworthy]
