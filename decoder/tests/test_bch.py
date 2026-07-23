import random
import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bch import (
    CODEWORD_BITS,
    DATA_BITS,
    bch_encode,
    decode_word,
    encode_word,
    even_parity_bit,
)


class TestBchEncodeDecodeRoundTrip(unittest.TestCase):
    def test_zero_data_round_trips(self):
        word = encode_word(0)
        result = decode_word(word)
        self.assertIsNotNone(result)
        self.assertEqual(result.data21, 0)
        self.assertEqual(result.corrected_bit_errors, 0)
        self.assertTrue(result.trustworthy)

    def test_all_ones_data_round_trips(self):
        data = (1 << DATA_BITS) - 1
        word = encode_word(data)
        result = decode_word(word)
        self.assertEqual(result.data21, data)
        self.assertEqual(result.corrected_bit_errors, 0)

    def test_random_values_round_trip_with_no_errors(self):
        rng = random.Random(42)
        for _ in range(200):
            data = rng.randrange(1 << DATA_BITS)
            word = encode_word(data)
            result = decode_word(word)
            self.assertIsNotNone(result)
            self.assertEqual(result.data21, data)
            self.assertEqual(result.corrected_bit_errors, 0)
            self.assertTrue(result.trustworthy)


class TestSingleBitErrorCorrection(unittest.TestCase):
    def test_every_single_bit_flip_in_codeword_is_corrected(self):
        rng = random.Random(7)
        for _ in range(50):
            data = rng.randrange(1 << DATA_BITS)
            word = encode_word(data)
            for bit in range(CODEWORD_BITS + 1):  # +1 to include the overall parity bit
                corrupted = word ^ (1 << bit)
                result = decode_word(corrupted)
                self.assertIsNotNone(result, f"data={data} bit={bit}")
                self.assertEqual(result.data21, data, f"data={data} bit={bit}")


class TestDoubleBitErrorCorrection(unittest.TestCase):
    def test_random_double_bit_flips_within_codeword_are_corrected(self):
        rng = random.Random(99)
        for _ in range(300):
            data = rng.randrange(1 << DATA_BITS)
            word = encode_word(data)
            # Flip 2 distinct bits within the 31-bit BCH codeword (not the
            # trailing parity bit - that's a 3rd, separate error source).
            i, j = rng.sample(range(CODEWORD_BITS), 2)
            corrupted = word ^ (1 << (i + 1)) ^ (1 << (j + 1))
            result = decode_word(corrupted)
            self.assertIsNotNone(result, f"data={data} bits={i},{j}")
            self.assertEqual(result.data21, data, f"data={data} bits={i},{j}")
            self.assertEqual(result.corrected_bit_errors, 2)


class TestUncorrectableErrorsAreNotSilentlyAccepted(unittest.TestCase):
    def test_three_bit_errors_are_either_flagged_or_rejected(self):
        # BCH(31,21) has minimum distance 5, guaranteeing correction of up
        # to 2 errors; 3 errors are outside the guarantee. We only require
        # that we never silently return a "trustworthy" wrong answer - the
        # syndrome table may have no entry (returns None) or the answer may
        # be marked not-trustworthy via a parity mismatch.
        rng = random.Random(123)
        false_trustworthy_count = 0
        wrong_but_trustworthy = 0
        trials = 500
        for _ in range(trials):
            data = rng.randrange(1 << DATA_BITS)
            word = encode_word(data)
            bits = rng.sample(range(CODEWORD_BITS), 3)
            corrupted = word
            for b in bits:
                corrupted ^= 1 << (b + 1)
            result = decode_word(corrupted)
            if result is not None and result.trustworthy and result.data21 != data:
                wrong_but_trustworthy += 1
        self.assertEqual(
            wrong_but_trustworthy, 0,
            "decode_word must never mark a wrong answer as trustworthy for 3-bit-error input",
        )


class TestEvenParityBit(unittest.TestCase):
    def test_even_number_of_set_bits_gives_zero(self):
        self.assertEqual(even_parity_bit(0b0011, 4), 0)

    def test_odd_number_of_set_bits_gives_one(self):
        self.assertEqual(even_parity_bit(0b0111, 4), 1)

    def test_only_considers_bits_within_width(self):
        # bit 4 (0b10000) is outside width=4 and must be ignored.
        self.assertEqual(even_parity_bit(0b10011, 4), 0)


class TestEncodeWordStructure(unittest.TestCase):
    def test_encode_word_is_33_bits_wide_at_most(self):
        word = encode_word((1 << DATA_BITS) - 1)
        self.assertLess(word, 1 << (CODEWORD_BITS + 1))

    def test_bch_encode_preserves_data_in_high_bits(self):
        data = 0b101010101010101010101  # 21 bits
        codeword = bch_encode(data)
        self.assertEqual(codeword >> 10, data)


if __name__ == "__main__":
    unittest.main()
