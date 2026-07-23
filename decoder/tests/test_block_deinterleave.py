import sys
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from block_deinterleave import HOLDOFF_BITS, deinterleave_block, short_address_capcode
from flex_sync import find_sync_matches
from fsk_demod import demodulate_2fsk
from pdw_bch import ECS

CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"


class TestDeinterleaveBlockSynthetic(unittest.TestCase):
    def test_asymmetric_pattern_locks_lsb_first_inverted_mapping(self):
        # Use a deliberately asymmetric pattern: the old alternating test
        # was identical when reversed and therefore could not detect which
        # end of the column became bit 0.
        bits = np.zeros(256, dtype=np.int8)
        expected_words = [0x000001, 0x000002, 0x000004, 0x012345, 0x100000, 0x155555, 0x0ABCDE, 0x1FEDCB]
        for column_index, expected in enumerate(expected_words):
            data_bits = [1 - ((expected >> row) & 1) for row in range(21)]
            ecc = 0
            for row, bit in enumerate(data_bits):
                if bit:
                    ecc ^= ECS[row]
            ecc_bits = [(ecc >> (9 - row)) & 1 for row in range(10)]
            parity = (sum(data_bits) + sum(ecc_bits)) & 1
            column_bits = data_bits + ecc_bits + [parity]
            for row in range(21):
                bits[row * 8 + column_index] = column_bits[row]
            for row in range(21, 32):
                bits[row * 8 + column_index] = column_bits[row]

        self.assertEqual(deinterleave_block(bits), expected_words)


@unittest.skipUnless(
    (CAPTURES_DIR / "manifest.jsonl").exists() and (CAPTURES_DIR / "capture_20260719_134345.wav").exists(),
    "no real capture data on this machine",
)
class TestDeinterleaveBlockRealCapture(unittest.TestCase):
    """Locks in strict real-world address validation at holdoff 104."""

    def test_real_frame_contains_confirmed_address_capcode(self):
        wav_path = CAPTURES_DIR / "capture_20260719_134345.wav"
        with wave.open(str(wav_path), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            raw = wav_file.readframes(wav_file.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16)

        result = demodulate_2fsk(samples, sample_rate, baud_rate=1600.0)
        syncs = find_sync_matches(result.bits)

        found_capcodes = set()
        for sync_match in syncs:
            start = sync_match.bit_offset + 32 + HOLDOFF_BITS
            if start + 256 > len(result.bits):
                continue
            block = result.bits[start:start + 256]
            if sync_match.polarity_inverted:
                block = 1 - block
            words = deinterleave_block(block)
            vector_start = (words[0] >> 10) & 0x3F
            address_start = ((words[0] >> 8) & 0x03) + 1
            if address_start <= vector_start <= len(words):
                found_capcodes.update(short_address_capcode(word) for word in words[address_start:vector_start])

        self.assertIn(2029569, found_capcodes)


if __name__ == "__main__":
    unittest.main()
