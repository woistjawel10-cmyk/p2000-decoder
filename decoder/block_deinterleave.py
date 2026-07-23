"""De-interleaves the 256-bit FLEX data blocks that follow a frame's sync
word + Frame Info Word, and extracts short-form address (capcode) words
from them.

Interleaving structure (8 columns x 32 rows per 256-bit block, producing 8
21-bit words per block) taken from PDW's own open-source FLEX decoder
(github.com/Discriminator/PDW, Flex.cpp): ``k = j*8 + i; ob[j] = block[k]``
for row j in 0..31, column i in 0..7. A FLEX frame carries 11 such blocks
(88 words total), matching multimon-ng's ``PHASE_WORDS 88``.

HOLDOFF: the bit gap between the end of the located central 32-bit sync word
and the first 256-bit data block. PDW detects the full 64-bit header 16 bits
after our central sync ends, initializes ``flex_bc=89`` while consuming the
current symbol, and starts data on the symbol after the countdown: 104 gap
bits. A strict 12-capture benchmark confirmed this overwhelmingly: holdoff
104 produced 82 checksum-valid BIWs and 75 same-capture PDW address matches
(66 distinct capcodes); the next-best candidate produced only 7 matches.

The earlier holdoff=98 "capcode
1520032" validation was invalid: it treated word 0 (the BIW slot) as an
address and skipped PDW's BCH correction. The exact PDW-compatible ecd()
port corrects that raw value to 1520033, confirming it was not evidence of
an address decode.
"""
from __future__ import annotations

from typing import List

import numpy as np

from pdw_bch import correct_pdw_column

BLOCK_BITS = 256
WORDS_PER_BLOCK = 8
WORD_BITS = 32
BLOCKS_PER_FRAME = 11

# Analytically derived from PDW and independently confirmed against captures.
HOLDOFF_BITS = 104


def deinterleave_block(bits256) -> List[int]:
    """Reconstructs the 8 21-bit interleaved words of a single 256-bit FLEX
    data block. Returns raw (uncorrected - no BCH decode applied) integer
    values.

    The full column is first corrected using a behavioral port of PDW's
    ``ecd()``. Bit construction then follows PDW's shift loop exactly: over the first 21
    rows of each column, ``ob[0]`` ends up in result bit 0 and ``ob[20]``
    in result bit 20, with polarity inverted (a 0 bit contributes a 1).
    Uncorrectable words carry PDW's 0x400000 error flag.
    """
    if len(bits256) < BLOCK_BITS:
        raise ValueError(f"need {BLOCK_BITS} bits, got {len(bits256)}")
    words, _errors = deinterleave_block_with_errors(bits256)
    return words


def deinterleave_block_with_errors(bits256) -> tuple[List[int], List[int]]:
    """Return corrected words plus their BCH correction counts."""
    if len(bits256) < BLOCK_BITS:
        raise ValueError(f"need {BLOCK_BITS} bits, got {len(bits256)}")
    words = []
    errors = []
    for i in range(WORDS_PER_BLOCK):
        column = bits256[i::WORDS_PER_BLOCK][:32]
        corrected = correct_pdw_column(column)
        value = 0
        for bit in corrected.bits[:21]:
            value >>= 1
            if bit == 0:
                value ^= 0x100000
        if not corrected.correctable:
            value |= 0x400000
        words.append(value)
        errors.append(corrected.errors)
    return words, errors


def short_address_capcode(word: int) -> int:
    """Converts a raw short-address word value to its capcode, per PDW's
    formula: ``capcode = (aw & 0x1fffff) - 32768``.
    """
    return (word & 0x1FFFFF) - 32768


def frame_words(
    bits: np.ndarray,
    sync_bit_offset: int,
    holdoff: int = HOLDOFF_BITS,
    polarity_inverted: bool = False,
) -> List[int]:
    """Returns all 88 de-interleaved words (11 blocks x 8 words) for the
    frame whose sync word starts at ``sync_bit_offset``. Words beyond the
    available bit stream are omitted (shorter list than 88).
    """
    words, _errors = frame_words_with_errors(bits, sync_bit_offset, holdoff, polarity_inverted)
    return words


def frame_words_with_errors(
    bits: np.ndarray,
    sync_bit_offset: int,
    holdoff: int = HOLDOFF_BITS,
    polarity_inverted: bool = False,
) -> tuple[List[int], List[int]]:
    """Return frame words and the BCH error count for every corresponding word."""
    start = sync_bit_offset + 32 + holdoff
    words: List[int] = []
    errors: List[int] = []
    for block_index in range(BLOCKS_PER_FRAME):
        block_start = start + block_index * BLOCK_BITS
        if block_start + BLOCK_BITS > len(bits):
            break
        block = bits[block_start:block_start + BLOCK_BITS]
        if polarity_inverted:
            block = 1 - block
        block_words, block_errors = deinterleave_block_with_errors(block)
        words.extend(block_words)
        errors.extend(block_errors)
    return words, errors
