"""Field-level parsing of FLEX words once their 21-bit BCH-corrected data
value is known.

Bit positions (all LSB-numbered within the 21-bit data value, bit 0 =
least significant) taken from the public, GPL-licensed multimon-ng FLEX
decoder (EliasOenal/multimon-ng, demod_flex_next.c decode_fiw()) as a
reference for the publicly documented FLEX protocol.
"""
from __future__ import annotations

from dataclasses import dataclass


def flex_checksum_ok(word: int) -> bool:
    """PDW's BIW/vector checksum: sum of 4-bit chunks modulo 16 is 0xF."""
    total = sum((word >> shift) & 0xF for shift in range(0, 20, 4))
    total += (word >> 20) & 0x1
    return (total & 0xF) == 0xF


@dataclass
class FrameInfoWord:
    checksum: int
    cycle_no: int
    frame_no: int
    roaming: bool
    repeat: bool
    traffic: int


def parse_fiw(data21: int) -> FrameInfoWord:
    return FrameInfoWord(
        checksum=data21 & 0xF,
        cycle_no=(data21 >> 4) & 0xF,
        frame_no=(data21 >> 8) & 0x7F,
        roaming=bool((data21 >> 15) & 0x1),
        repeat=bool((data21 >> 16) & 0x1),
        traffic=(data21 >> 17) & 0xF,
    )
