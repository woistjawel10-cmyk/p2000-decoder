import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdw_bch import ECS, correct_pdw_column


def valid_column(data_bits):
    ecc = 0
    for i, bit in enumerate(data_bits):
        if bit:
            ecc ^= ECS[i]
    ecc_bits = [(ecc >> (9 - i)) & 1 for i in range(10)]
    parity = (sum(data_bits) + sum(ecc_bits)) & 1
    return list(data_bits) + ecc_bits + [parity]


class TestPdwBch(unittest.TestCase):
    def test_valid_asymmetric_column_is_unchanged(self):
        original = valid_column([1, 0, 1, 1, 0, 0, 1] + [0] * 14)
        result = correct_pdw_column(original)
        self.assertEqual(result.bits, original)
        self.assertEqual(result.errors, 0)

    def test_corrects_each_single_data_bit_error(self):
        original = valid_column([1, 0, 0, 1, 1] + [0] * 16)
        for position in range(21):
            damaged = original.copy()
            damaged[position] ^= 1
            result = correct_pdw_column(damaged)
            self.assertEqual(result.bits[:21], original[:21])
            self.assertLess(result.errors, 3)

    def test_corrects_two_data_bit_errors(self):
        original = valid_column([1, 0, 1, 0, 1] + [0] * 16)
        damaged = original.copy()
        damaged[2] ^= 1
        damaged[17] ^= 1
        result = correct_pdw_column(damaged)
        self.assertEqual(result.bits[:21], original[:21])
        self.assertEqual(result.errors, 2)


if __name__ == "__main__":
    unittest.main()
