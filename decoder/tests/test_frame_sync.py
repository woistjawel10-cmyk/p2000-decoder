import random
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bch import decode_word, encode_word
from frame_sync import WordCandidate, bits_to_word, find_aligned_runs, find_valid_word_offsets


def word_to_bits(word: int, width: int = 32) -> np.ndarray:
    return np.array([(word >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.int8)


def random_bits(n: int, seed: int) -> np.ndarray:
    rng = random.Random(seed)
    return np.array([rng.randint(0, 1) for _ in range(n)], dtype=np.int8)


class TestBitsToWord(unittest.TestCase):
    def test_round_trips_with_word_to_bits(self):
        word = 0b1010_1100_1111_0000_0000_1111_0011_0101
        bits = word_to_bits(word)
        self.assertEqual(bits_to_word(bits, 0), word)

    def test_reads_from_a_nonzero_offset(self):
        word = 0x12345678
        bits = np.concatenate([random_bits(5, seed=1), word_to_bits(word)])
        self.assertEqual(bits_to_word(bits, 5), word)


class TestFindValidWordOffsets(unittest.TestCase):
    def test_finds_a_single_embedded_valid_word_in_noise(self):
        rng = random.Random(1)
        data = rng.randrange(1 << 21)
        word = encode_word(data)
        noise_before = random_bits(37, seed=2)
        noise_after = random_bits(41, seed=3)
        bits = np.concatenate([noise_before, word_to_bits(word), noise_after])

        candidates = find_valid_word_offsets(bits)
        offsets = [c.bit_offset for c in candidates]
        self.assertIn(37, offsets)
        found = next(c for c in candidates if c.bit_offset == 37)
        self.assertEqual(found.result.data21, data)

    def test_random_noise_false_positive_rate_matches_bch_math(self):
        # A single BCH-valid window is NOT rare on its own: the syndrome
        # table covers every 0/1/2-bit-error pattern (497 of 1024 possible
        # syndromes), so pure noise passes at roughly 48.5% / 2 (parity
        # halves it) =~ 24% per offset. This test locks in that measured
        # rate as a regression check (and as documentation - see module
        # docstring) rather than asserting false positives are rare, which
        # they are not for a single window.
        bits = random_bits(5000, seed=42)
        candidates = find_valid_word_offsets(bits)
        rate = len(candidates) / (len(bits) - 32 + 1)
        self.assertTrue(0.15 < rate < 0.35, f"unexpected false-positive rate {rate:.2%}")


class TestFindAlignedRuns(unittest.TestCase):
    def test_three_consecutive_valid_words_form_a_run(self):
        rng = random.Random(5)
        words = [encode_word(rng.randrange(1 << 21)) for _ in range(3)]
        bits = np.concatenate([word_to_bits(w) for w in words])

        candidates = find_valid_word_offsets(bits)
        runs = find_aligned_runs(candidates, min_run_length=3)

        self.assertEqual(len(runs), 1)
        self.assertEqual([c.bit_offset for c in runs[0]], [0, 32, 64])

    def test_isolated_matches_not_exactly_32_bits_apart_do_not_form_a_run(self):
        # Uses hand-built candidates (not a bit-scan) so the test is exact
        # and deterministic: a single-window false positive rate of ~24%
        # (see TestFindValidWordOffsets) makes relying on random bits here
        # flaky - a "gap" region can easily contain its own coincidental
        # matches, which isn't what this test is about.
        rng = random.Random(6)
        result_a = decode_word(encode_word(rng.randrange(1 << 21)))
        result_b = decode_word(encode_word(rng.randrange(1 << 21)))
        candidates = [WordCandidate(0, result_a), WordCandidate(49, result_b)]  # 49, not 32, apart

        runs = find_aligned_runs(candidates, min_run_length=2)
        self.assertEqual(runs, [])

    def test_real_frame_shape_five_words_in_a_row(self):
        # Note: out-of-phase windows straddling two adjacent real words can
        # *also* coincidentally pass BCH (~24% per window, see above), so
        # this only asserts the true aligned run (offsets 0,32,64,96,128)
        # is found - it does not assert it's the *only* run, since short
        # coincidental runs alongside it are expected, not a bug.
        rng = random.Random(8)
        words = [encode_word(rng.randrange(1 << 21)) for _ in range(5)]
        bits = np.concatenate([word_to_bits(w) for w in words])

        candidates = find_valid_word_offsets(bits)
        runs = find_aligned_runs(candidates, min_run_length=3)

        matching_runs = [r for r in runs if [c.bit_offset for c in r] == [0, 32, 64, 96, 128]]
        self.assertEqual(len(matching_runs), 1)
        for i, c in enumerate(matching_runs[0]):
            self.assertEqual(c.result.data21, words[i] >> 11)  # word = (codeword<<1)|parity, codeword = data<<10|bch


if __name__ == "__main__":
    unittest.main()
