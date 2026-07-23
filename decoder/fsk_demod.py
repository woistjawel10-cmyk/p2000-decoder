"""2-level FSK demodulation from already-frequency-demodulated audio (i.e.
rtl_fm's `-M fm` output - the audio *is* the instantaneous frequency
deviation) to a bit array.

Covers the FLEX 1600bps profile only for now. 4-level FSK (used by FLEX
3200/2, 3200/4, 6400/4) is not implemented yet: real captured traffic so
far is overwhelmingly 1600bps (every PDW-logged line seen during
development shows baud=1600), so this is built and validated against real
signal first; 4-level slicing is a follow-up once this path is proven.

No hardware bit-clock is available, so initial symbol timing is recovered
by a phase search over FLEX's preamble. During slicing, data transitions
then continuously pull the sampling clock toward the observed symbol
boundaries with the same fine loop gain (0.015) used by PDW. This matters
on real captures: a six-file benchmark found 47 syncs with tracking versus
28 with fixed timing, while retaining BCH-trustworthy FIWs.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sp_signal


def lowpass_filter(samples: np.ndarray, sample_rate: float, cutoff_hz: float, order: int = 4) -> np.ndarray:
    nyquist = sample_rate / 2
    normalized_cutoff = min(cutoff_hz / nyquist, 0.99)
    b, a = sp_signal.butter(order, normalized_cutoff, btype="low")
    return sp_signal.filtfilt(b, a, samples)


def find_symbol_phase(
    filtered: np.ndarray, samples_per_symbol: float, search_start: int, search_len: int, steps: int = 32
) -> float:
    """Returns the fractional sample offset (0..samples_per_symbol) within
    a symbol period that best avoids transition edges, by maximizing mean
    |sample| at the candidate slice points over the search window."""
    best_phase = 0.0
    best_score = -np.inf
    n_symbols = max(1, int(search_len / samples_per_symbol))
    for phase in np.linspace(0, samples_per_symbol, steps, endpoint=False):
        indices = (search_start + phase + np.arange(n_symbols) * samples_per_symbol).astype(int)
        indices = indices[(indices >= 0) & (indices < len(filtered))]
        if len(indices) == 0:
            continue
        score = float(np.mean(np.abs(filtered[indices])))
        if score > best_score:
            best_score = score
            best_phase = float(phase)
    return best_phase


@dataclass
class DemodResult:
    bits: np.ndarray
    symbol_phase: float
    samples_per_symbol: float


def slice_with_transition_tracking(
    filtered: np.ndarray,
    samples_per_symbol: float,
    initial_phase: float,
    loop_gain: float = 0.015,
) -> np.ndarray:
    """Slice symbols while gently correcting timing at data transitions.

    This mirrors PDW's receive-clock principle: a transition supplies a
    timing-error observation and moves the future sampling clock by a small
    fraction of that error. Runs without transitions leave the clock alone.
    Linear interpolation avoids adding integer-sample quantisation jitter.
    """
    if not 0 < loop_gain <= 1:
        raise ValueError("loop_gain must be in (0, 1]")
    if len(filtered) < 2:
        return np.empty(0, dtype=np.int8)

    centers = []
    center = float(initial_phase)
    previous_bit = None
    previous_center = None
    while center < len(filtered) - 1:
        left = int(center)
        fraction = center - left
        value = float(filtered[left] * (1.0 - fraction) + filtered[left + 1] * fraction)
        bit = 1 if value > 0 else 0

        if previous_bit is not None and bit != previous_bit:
            # Locate the zero crossing between the two symbol centres. The
            # ideal transition boundary is their midpoint.
            lo = int(previous_center)
            hi = min(len(filtered) - 1, int(center) + 1)
            segment = filtered[lo:hi + 1]
            signs = segment > 0
            crossings = np.flatnonzero(signs[1:] != signs[:-1])
            if len(crossings):
                ideal_boundary = (previous_center + center) / 2.0
                crossing_positions = lo + crossings + 0.5
                crossing = float(crossing_positions[np.argmin(np.abs(crossing_positions - ideal_boundary))])
                timing_error = crossing - ideal_boundary
                center += loop_gain * timing_error

        centers.append(bit)
        previous_bit = bit
        previous_center = center
        center += samples_per_symbol

    return np.asarray(centers, dtype=np.int8)


def demodulate_2fsk(
    samples: np.ndarray,
    sample_rate: float,
    baud_rate: float = 1600.0,
    invert: bool = False,
    phase_search_seconds: float = 0.05,
    track_timing: bool = True,
    timing_loop_gain: float = 0.015,
) -> DemodResult:
    """samples: 1-D array of already-FM-demodulated audio (e.g. int16 PCM
    from rtl_fm). Returns one bit per recovered symbol period. By default,
    transition-driven timing tracking remains active across the input;
    ``track_timing=False`` retains the old fixed-phase path for benchmarks.
    """
    x = samples.astype(np.float64)
    x -= np.mean(x)

    filtered = lowpass_filter(x, sample_rate, cutoff_hz=baud_rate * 0.6)

    samples_per_symbol = sample_rate / baud_rate
    search_len = min(len(filtered), max(1, int(sample_rate * phase_search_seconds)))
    phase = find_symbol_phase(filtered, samples_per_symbol, search_start=0, search_len=search_len)

    if track_timing:
        bits = slice_with_transition_tracking(filtered, samples_per_symbol, phase, timing_loop_gain)
    else:
        n_symbols = int((len(filtered) - phase) / samples_per_symbol)
        indices = (phase + np.arange(n_symbols) * samples_per_symbol).astype(int)
        indices = indices[(indices >= 0) & (indices < len(filtered))]
        symbol_values = filtered[indices]
        bits = (symbol_values > 0).astype(np.int8)
    if invert:
        bits = 1 - bits
    return DemodResult(bits=bits, symbol_phase=phase, samples_per_symbol=samples_per_symbol)
