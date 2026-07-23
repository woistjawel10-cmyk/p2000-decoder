"""BCH(31,21) forward error correction, as used by every 32-bit "word" in a
FLEX frame (Frame Info Word, Block Info Words, Address words, Vector words,
Message words).

This is a standardized paging error-correction code (also used by POCSAG,
predating FLEX) - public, well-documented protocol math, not anything
extracted from PDW or any closed-source decoder. Generator polynomial:

    g(x) = x^10 + x^9 + x^8 + x^6 + x^5 + x^3 + 1   (0x769)

giving a (31,21) BCH code: 21 data bits -> 10 parity bits -> 31-bit
codeword, minimum distance 5, correcting up to 2 bit errors. FLEX/POCSAG
transmit this as a 32-bit "word" by prepending one more overall even-parity
bit over all 31 BCH bits, letting an all-1-bit-error-that-BCH-alone-would
silently miscorrect usually still be caught (parity mismatch after
"correction" flags the word as unrecoverable instead of accepting a wrong
answer).

Bit convention used throughout this module: bit 0 is the LSB. A 21-bit data
value's bit 20 is its MSB. The 31-bit BCH codeword is data in bits 30..10
and parity in bits 9..0 (systematic: shift data left by 10, XOR in the
remainder). The 32-bit transmitted word is the 31-bit codeword in bits 31..1
with the overall parity bit as bit 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GENERATOR = 0x769  # degree-10 generator polynomial, see module docstring
GENERATOR_DEGREE = 10
DATA_BITS = 21
CODEWORD_BITS = 31  # DATA_BITS + GENERATOR_DEGREE


def _popcount(x: int) -> int:
    return bin(x).count("1")


def _poly_mod(value: int, generator: int, generator_degree: int) -> int:
    """GF(2) polynomial remainder of `value` divided by `generator`, via
    XOR-shift long division. `value` may be wider than `generator`."""
    value_degree = value.bit_length() - 1
    while value_degree >= generator_degree:
        if value & (1 << value_degree):
            value ^= generator << (value_degree - generator_degree)
        value_degree = value.bit_length() - 1
    return value


def bch_parity(data21: int) -> int:
    """Returns the 10-bit systematic BCH parity for a 21-bit data value."""
    if not 0 <= data21 < (1 << DATA_BITS):
        raise ValueError("data21 must fit in 21 bits")
    shifted = data21 << GENERATOR_DEGREE
    return _poly_mod(shifted, GENERATOR, GENERATOR_DEGREE)


def bch_encode(data21: int) -> int:
    """Returns the 31-bit systematic BCH codeword for a 21-bit data value."""
    return (data21 << GENERATOR_DEGREE) | bch_parity(data21)


def even_parity_bit(value: int, width: int) -> int:
    """1 if the number of set bits in the low `width` bits of value is odd
    (i.e. the bit that makes the total even), else 0."""
    return _popcount(value & ((1 << width) - 1)) & 1


def encode_word(data21: int) -> int:
    """Full 32-bit FLEX/POCSAG transmitted word: 31-bit BCH codeword plus
    one overall even-parity bit in bit 0."""
    codeword = bch_encode(data21)
    parity = even_parity_bit(codeword, CODEWORD_BITS)
    return (codeword << 1) | parity


# Precomputed correctable single/double-bit error syndromes.
# Syndrome of a codeword c is poly_mod(c, GENERATOR); for a codeword with
# error pattern e (c_received = c_correct XOR e), the syndrome of
# c_received depends only on e (linearity of the code), so we can
# precompute syndrome -> error_pattern for every correctable error (0, 1,
# or 2 bit flips within the 31-bit codeword) once and use it as a lookup
# table at decode time - much simpler and just as correct as an algebraic
# error-locator approach for a code this small.
def _build_syndrome_table() -> dict:
    table = {0: 0}
    for i in range(CODEWORD_BITS):
        e = 1 << i
        table[_poly_mod(e, GENERATOR, GENERATOR_DEGREE)] = e
    for i in range(CODEWORD_BITS):
        for j in range(i + 1, CODEWORD_BITS):
            e = (1 << i) | (1 << j)
            syndrome = _poly_mod(e, GENERATOR, GENERATOR_DEGREE)
            table.setdefault(syndrome, e)  # keep the lowest-weight match found first
    return table


_SYNDROME_TABLE = _build_syndrome_table()


@dataclass
class BchDecodeResult:
    data21: int
    corrected_bit_errors: int
    parity_ok: bool

    @property
    def trustworthy(self) -> bool:
        """0 or 1 corrected bit errors with matching overall parity is the
        code's designed operating range; 2 corrected errors with bad parity,
        or a syndrome outside the table (3+ errors), means the result should
        be treated as unrecoverable rather than silently used."""
        return self.parity_ok and self.corrected_bit_errors <= 2


def decode_word(word32: int) -> Optional[BchDecodeResult]:
    """Corrects up to 2 bit errors in the 31-bit BCH codeword portion of a
    32-bit transmitted word and extracts the 21-bit data value. Returns
    None if the syndrome doesn't match any 0/1/2-bit error pattern (3+
    errors - uncorrectable, not just "unlikely to be trusted").
    """
    if not 0 <= word32 < (1 << (CODEWORD_BITS + 1)):
        raise ValueError("word32 must fit in 32 bits")

    codeword = word32 >> 1
    received_parity_bit = word32 & 1

    syndrome = _poly_mod(codeword, GENERATOR, GENERATOR_DEGREE)
    error_pattern = _SYNDROME_TABLE.get(syndrome)
    if error_pattern is None:
        return None

    corrected_codeword = codeword ^ error_pattern
    data21 = corrected_codeword >> GENERATOR_DEGREE

    expected_parity_bit = even_parity_bit(corrected_codeword, CODEWORD_BITS)
    parity_ok = expected_parity_bit == received_parity_bit

    return BchDecodeResult(
        data21=data21,
        corrected_bit_errors=_popcount(error_pattern),
        parity_ok=parity_ok,
    )
