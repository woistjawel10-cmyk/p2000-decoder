import json
import sys
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdw_bch import ECS
from flex_sync import (
    FRAME_PERIOD_BITS_1600,
    SYNC_WORD,
    SYNC_WORD_BITS,
    find_sync_matches,
    trustworthy_frame_starts,
)
from fsk_demod import demodulate_2fsk

CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"


def _word_to_bits(word: int, width: int) -> np.ndarray:
    return np.array([(word >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.int8)


def _valid_pdw_word(data_bits=None) -> np.ndarray:
    data = list(data_bits or ([0] * 21))
    ecc = 0
    for index, bit in enumerate(data):
        if bit:
            ecc ^= ECS[index]
    ecc_bits = [(ecc >> (9 - index)) & 1 for index in range(10)]
    parity = (sum(data) + sum(ecc_bits)) & 1
    return np.array(data + ecc_bits + [parity], dtype=np.int8)


class TestFindSyncMatchesSynthetic(unittest.TestCase):
    def test_finds_sync_word_followed_by_valid_fiw(self):
        bits = np.concatenate([
            _word_to_bits(SYNC_WORD, SYNC_WORD_BITS),
            np.zeros(32, dtype=np.int8),
            _valid_pdw_word([1, 0, 1] + [0] * 18),
        ])

        matches = find_sync_matches(bits)
        self.assertTrue(any(m.bit_offset == 0 and m.fiw is not None and m.fiw.trustworthy for m in matches))
        self.assertFalse(next(m for m in matches if m.bit_offset == 0).polarity_inverted)

    def test_finds_complement_sync_word_too(self):
        sync_bits = _word_to_bits(SYNC_WORD, SYNC_WORD_BITS)
        inverted_sync = 1 - sync_bits
        normalized_tail = np.concatenate([np.zeros(32, dtype=np.int8), _valid_pdw_word()])
        bits = np.concatenate([inverted_sync, 1 - normalized_tail])

        matches = find_sync_matches(bits)
        self.assertTrue(any(m.bit_offset == 0 and m.fiw is not None and m.fiw.trustworthy for m in matches))
        self.assertTrue(next(m for m in matches if m.bit_offset == 0).polarity_inverted)

    def test_accepts_one_central_error_with_trustworthy_outer_sync(self):
        outer_left = _word_to_bits(0x870C, 16)
        central = _word_to_bits(SYNC_WORD, SYNC_WORD_BITS)
        central[7] ^= 1
        outer_right = _word_to_bits(0x78F3, 16)
        bits = np.concatenate([
            outer_left,
            central,
            outer_right,
            np.zeros(16, dtype=np.int8),
            _valid_pdw_word(),
        ])

        matches = find_sync_matches(bits)

        self.assertTrue(any(m.bit_offset == 16 and m.fiw.trustworthy for m in matches))

    def test_rejects_one_central_error_without_trustworthy_outer_sync(self):
        central = _word_to_bits(SYNC_WORD, SYNC_WORD_BITS)
        central[7] ^= 1
        bits = np.concatenate([
            np.zeros(16, dtype=np.int8),
            central,
            np.zeros(16, dtype=np.int8),
            np.zeros(16, dtype=np.int8),
            _valid_pdw_word(),
        ])

        self.assertFalse(any(m.bit_offset == 16 for m in find_sync_matches(bits)))

    def test_no_sync_word_in_random_bits_usually_finds_nothing(self):
        rng = np.random.default_rng(0)
        bits = rng.integers(0, 2, size=2000, dtype=np.int8)
        matches = trustworthy_frame_starts(bits)
        # A 32-bit sync word appearing by pure chance in 2000 random bits is
        # extremely unlikely (~2000/2^32); this is a sanity check, not a
        # hard mathematical guarantee.
        self.assertEqual(matches, [])

    def test_trustworthy_frame_starts_filters_out_bad_fiw(self):
        # Sync word present, but followed by garbage that won't BCH-decode
        # cleanly - must not be reported as a trustworthy frame start.
        bits = np.concatenate([
            _word_to_bits(SYNC_WORD, SYNC_WORD_BITS),
            np.zeros(32, dtype=np.int8),
            np.zeros(32, dtype=np.int8),
        ])
        bits[SYNC_WORD_BITS + 32:SYNC_WORD_BITS + 64] = np.array(
            [1, 0] * 16, dtype=np.int8
        )  # essentially-random 32 bits, unlikely to be BCH-trustworthy
        starts = trustworthy_frame_starts(bits)
        # Not asserting empty (that 32-bit pattern could coincidentally
        # pass BCH - see frame_sync.py's documented ~24% single-window
        # rate), just that the function only returns entries whose fiw is
        # actually marked trustworthy.
        for s in starts:
            self.assertTrue(s.fiw.trustworthy)


@unittest.skipUnless(CAPTURES_DIR.joinpath("manifest.jsonl").exists(), "no real capture data on this machine")
class TestFindSyncMatchesRealCapture(unittest.TestCase):
    """Regression test locking in the real-world validation performed during
    development: the sync word was found in a real captured P2000 audio
    segment at bit offsets exactly FRAME_PERIOD_BITS_1600 apart (1.875s,
    the documented FLEX 1600bps frame period), each followed by a
    consistent, BCH-trustworthy FIW - strong evidence the whole pipeline
    (demod -> sync -> BCH) is correctly calibrated together.
    """

    def test_real_capture_shows_sync_hits_at_the_flex_frame_period(self):
        with CAPTURES_DIR.joinpath("manifest.jsonl").open(encoding="utf-8") as manifest_file:
            manifest = [json.loads(line) for line in manifest_file]
        chunk = next((m for m in manifest if m["pdw_lines"]), None)
        if chunk is None:
            self.skipTest("no capture chunk with PDW lines available yet")

        with wave.open(str(CAPTURES_DIR / chunk["wav_file"]), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            raw = wav_file.readframes(wav_file.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16)

        start_time = chunk["start_time"]
        t0 = chunk["pdw_lines"][0]["logged_at"] - start_time
        i0, i1 = int(max(0, t0 - 5) * sample_rate), int((t0 + 1) * sample_rate)
        segment = samples[i0:i1]

        result = demodulate_2fsk(segment, sample_rate, baud_rate=1600.0)
        starts = trustworthy_frame_starts(result.bits)

        self.assertGreaterEqual(
            len(starts), 2, "expected at least 2 trustworthy frame starts in this known-good segment"
        )
        gaps = [b.bit_offset - a.bit_offset for a, b in zip(starts, starts[1:])]
        self.assertTrue(
            any(abs(gap - FRAME_PERIOD_BITS_1600) <= 2 for gap in gaps),
            f"expected a ~{FRAME_PERIOD_BITS_1600}-bit gap between consecutive frame starts, got {gaps}",
        )


if __name__ == "__main__":
    unittest.main()
