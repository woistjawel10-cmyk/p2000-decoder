import random
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsk_demod import demodulate_2fsk


def synthesize_2fsk_audio(bits: np.ndarray, sample_rate: float, baud_rate: float, noise_std: float = 0.0, seed: int = 0) -> np.ndarray:
    """Builds a plausible already-FM-demodulated audio signal for a known
    bit sequence: each bit becomes a constant-level rectangular pulse
    (+1 or -1) for one symbol period, then lightly smoothed (a real FM
    discriminator's output isn't a perfect rectangle - it has finite
    bandwidth) and optionally noised, to give the demodulator something
    non-trivial to actually recover timing from.
    """
    samples_per_symbol = sample_rate / baud_rate
    n_samples = int(len(bits) * samples_per_symbol)
    signal = np.zeros(n_samples)
    for i, bit in enumerate(bits):
        start = int(i * samples_per_symbol)
        end = int((i + 1) * samples_per_symbol)
        signal[start:end] = 1.0 if bit else -1.0

    # Smooth with a small moving average to emulate finite channel bandwidth
    # (a real signal isn't an instantaneous rectangular step).
    smooth_window = max(1, int(samples_per_symbol * 0.15))
    kernel = np.ones(smooth_window) / smooth_window
    signal = np.convolve(signal, kernel, mode="same")

    if noise_std > 0:
        rng = np.random.default_rng(seed)
        signal = signal + rng.normal(0, noise_std, size=signal.shape)

    return (signal * 10000).astype(np.float64)  # scale to a plausible PCM-ish amplitude


class TestDemodulate2Fsk(unittest.TestCase):
    def test_recovers_known_bit_pattern_noise_free(self):
        rng = random.Random(1)
        bits = np.array([rng.randint(0, 1) for _ in range(200)], dtype=np.int8)
        sample_rate = 44100.0
        baud = 1600.0
        audio = synthesize_2fsk_audio(bits, sample_rate, baud)

        result = demodulate_2fsk(audio, sample_rate, baud_rate=baud)

        # Allow the recovered stream to start at a slightly different
        # symbol than bits[0] was intended to represent (phase search may
        # lock a few symbols in); check for a long matching run instead of
        # a byte-for-byte match at position 0.
        self.assertTrue(_contains_matching_run(result.bits, bits, min_run=150))

    def test_recovers_known_bit_pattern_with_moderate_noise(self):
        rng = random.Random(2)
        bits = np.array([rng.randint(0, 1) for _ in range(300)], dtype=np.int8)
        sample_rate = 44100.0
        baud = 1600.0
        audio = synthesize_2fsk_audio(bits, sample_rate, baud, noise_std=0.15, seed=3)

        result = demodulate_2fsk(audio, sample_rate, baud_rate=baud)
        self.assertTrue(_contains_matching_run(result.bits, bits, min_run=200))

    def test_invert_flips_all_bits(self):
        rng = random.Random(4)
        bits = np.array([rng.randint(0, 1) for _ in range(100)], dtype=np.int8)
        sample_rate = 44100.0
        baud = 1600.0
        audio = synthesize_2fsk_audio(bits, sample_rate, baud)

        normal = demodulate_2fsk(audio, sample_rate, baud_rate=baud, invert=False)
        inverted = demodulate_2fsk(audio, sample_rate, baud_rate=baud, invert=True)

        self.assertTrue(np.array_equal(normal.bits, 1 - inverted.bits))

    def test_symbol_count_roughly_matches_input_length(self):
        bits = np.ones(500, dtype=np.int8)
        sample_rate = 44100.0
        baud = 1600.0
        audio = synthesize_2fsk_audio(bits, sample_rate, baud)

        result = demodulate_2fsk(audio, sample_rate, baud_rate=baud)
        self.assertAlmostEqual(len(result.bits), len(bits), delta=3)

    def test_transition_tracking_handles_transmitter_clock_offset(self):
        rng = random.Random(9)
        bits = np.array([rng.randint(0, 1) for _ in range(5000)], dtype=np.int8)
        sample_rate = 44100.0
        actual_baud = 1592.0
        audio = synthesize_2fsk_audio(bits, sample_rate, actual_baud, noise_std=0.05, seed=10)

        tracked = demodulate_2fsk(
            audio,
            sample_rate,
            baud_rate=1600.0,
            track_timing=True,
            timing_loop_gain=0.2,
        )
        fixed = demodulate_2fsk(audio, sample_rate, baud_rate=1600.0, track_timing=False)

        tracked_score = _best_aligned_accuracy(tracked.bits, bits)
        fixed_score = _best_aligned_accuracy(fixed.bits, bits)
        self.assertGreater(tracked_score, fixed_score + 0.15)
        self.assertGreater(tracked_score, 0.85)


def _contains_matching_run(recovered: np.ndarray, expected: np.ndarray, min_run: int) -> bool:
    """True if `expected` (or its bit-inverse, since absolute polarity is
    not guaranteed by a threshold-at-zero slicer) appears as a contiguous
    subsequence of `recovered` for at least `min_run` symbols."""
    for candidate in (expected, 1 - expected):
        target = "".join(str(b) for b in candidate[:min_run])
        haystack = "".join(str(b) for b in recovered)
        if target in haystack:
            return True
    return False


def _best_aligned_accuracy(recovered: np.ndarray, expected: np.ndarray, max_shift: int = 20) -> float:
    best = 0.0
    for invert in (False, True):
        candidate = 1 - recovered if invert else recovered
        for shift in range(-max_shift, max_shift + 1):
            if shift >= 0:
                a, b = candidate[shift:], expected
            else:
                a, b = candidate, expected[-shift:]
            n = min(len(a), len(b))
            if n:
                best = max(best, float(np.mean(a[:n] == b[:n])))
    return best


if __name__ == "__main__":
    unittest.main()
