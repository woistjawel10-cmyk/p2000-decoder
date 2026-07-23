"""Finds FLEX word boundaries in a recovered bit stream.

IMPORTANT, measured (not assumed) false-positive rate: a 2-error-correcting
BCH(31,21) code's syndrome table covers every 0/1/2-bit error pattern -
that's 1 + 31 + C(31,2) = 497 of the 1024 possible 10-bit syndromes, i.e.
~48.5% of *all* possible 31-bit inputs "decode" to *some* correction
whether or not they were ever a real encoded word. With the extra overall
parity bit roughly halving that, a single random 32-bit window still has
a measured ~24% chance of passing `decode_word(...).trustworthy` by pure
coincidence (see test_frame_sync.py). That means a single passing window,
or even two in a row, is close to meaningless as proof of alignment - only
a much longer consecutive run (this module defaults to requiring >=3, but
real usage needs the actual FLEX sync word as the primary signal, with a
BCH-valid run only as *secondary* confirmation once a sync-word candidate
position is already known - see flex_frame.py once sync-word correlation
is added there).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List

import numpy as np

from bch import BchDecodeResult, decode_word

WORD_BITS = 32


@dataclass
class WordCandidate:
    bit_offset: int
    result: BchDecodeResult


def bits_to_word(bits: np.ndarray, offset: int) -> int:
    """Packs bits[offset:offset+32] (MSB-first, i.e. bits[offset] is the
    top bit of the word) into a Python int."""
    window = bits[offset:offset + WORD_BITS]
    value = 0
    for bit in window:
        value = (value << 1) | int(bit)
    return value


def find_valid_word_offsets(bits: np.ndarray, only_trustworthy: bool = True) -> List[WordCandidate]:
    """Scans every bit offset (not just 32-bit-aligned ones - we don't yet
    know where words start) for a 32-bit window that decodes as a valid
    BCH word. Returns every match found; the caller is expected to look for
    a consistent stride (candidates 32 bits apart) to confirm true frame
    alignment rather than a one-off coincidental match.
    """
    candidates: List[WordCandidate] = []
    last_offset = len(bits) - WORD_BITS
    for offset in range(0, max(0, last_offset + 1)):
        word = bits_to_word(bits, offset)
        result = decode_word(word)
        if result is None:
            continue
        if only_trustworthy and not result.trustworthy:
            continue
        candidates.append(WordCandidate(offset, result))
    return candidates


def find_aligned_runs(candidates: List[WordCandidate], min_run_length: int = 3) -> List[List[WordCandidate]]:
    """Groups candidates into runs where consecutive words are exactly
    WORD_BITS apart (i.e. back-to-back valid words - the signature of
    having found true frame alignment, since a coincidental single match
    is common but two or three in a row at the exact right stride is not).
    """
    by_offset = {c.bit_offset: c for c in candidates}
    seen = set()
    runs: List[List[WordCandidate]] = []

    for c in candidates:
        if c.bit_offset in seen:
            continue
        run = [c]
        seen.add(c.bit_offset)
        next_offset = c.bit_offset + WORD_BITS
        while next_offset in by_offset:
            run.append(by_offset[next_offset])
            seen.add(next_offset)
            next_offset += WORD_BITS
        if len(run) >= min_run_length:
            runs.append(run)
    return runs
