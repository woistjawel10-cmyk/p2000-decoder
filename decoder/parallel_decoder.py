"""Buffered, logging-only FLEX decoder for safe parallel validation."""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from block_deinterleave import frame_words_with_errors
from flex_messages import FlexMessage, GroupAssignmentTracker, parse_alpha_messages, parse_group_assignments
from flex_sync import find_sync_matches
from fsk_demod import demodulate_2fsk


class ParallelFlexDecoder:
    """Decode overlapping PCM windows using both timing strategies.

    Only the newly complete portion of each window is emitted. The class has
    no network or dispatch dependency and is therefore safe to run alongside
    PDW while its output is being validated.
    """

    def __init__(
        self,
        sample_rate: int,
        on_message: Callable[[FlexMessage], None],
        window_seconds: float = 8.0,
        hop_seconds: float = 4.0,
    ):
        if not 0 < hop_seconds < window_seconds - 2.0:
            raise ValueError("hop_seconds must leave at least two seconds of frame tail")
        self.sample_rate = sample_rate
        self.on_message = on_message
        self.window_samples = round(window_seconds * sample_rate)
        self.hop_samples = round(hop_seconds * sample_rate)
        self.window_seconds = window_seconds
        self.hop_seconds = hop_seconds
        self._pcm = np.empty(0, dtype=np.int16)
        self._first_window = True
        self._trackers = {True: GroupAssignmentTracker(), False: GroupAssignmentTracker()}
        self._recent: dict[tuple[int, str, str], int] = {}
        self._window_number = 0

    def feed_pcm(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16).copy()
        self._pcm = np.concatenate((self._pcm, samples))
        while len(self._pcm) >= self.window_samples:
            self._decode_window(self._pcm[:self.window_samples])
            self._pcm = self._pcm[self.hop_samples:]

    def _decode_window(self, samples: np.ndarray) -> None:
        # A complete FLEX1600 frame needs just under two seconds after sync.
        accept_end = round((self.window_seconds - 2.0) * 1600)
        accept_start = 0 if self._first_window else round(
            (self.window_seconds - self.hop_seconds - 2.0) * 1600
        )
        candidates: dict[tuple[int, str, str], tuple[FlexMessage, int, int]] = {}
        for track_timing in (True, False):
            bits = demodulate_2fsk(
                samples, self.sample_rate, 1600.0, track_timing=track_timing
            ).bits
            tracker = self._trackers[track_timing]
            for sync in find_sync_matches(bits):
                if not accept_start <= sync.bit_offset < accept_end:
                    continue
                words, word_errors = frame_words_with_errors(
                    bits, sync.bit_offset, polarity_inverted=sync.polarity_inverted
                )
                for assignment in parse_group_assignments(words):
                    tracker.add(assignment)
                for message in tracker.expand(sync.frame_no, parse_alpha_messages(words)):
                    vector = words[message.vector_word_index]
                    packed = vector >> 7
                    message_start = packed & 0x7F
                    message_stop = ((packed >> 7) & 0x7F) + message_start - 1
                    relevant = {message.address_word_index, message.vector_word_index}
                    relevant.update(range(message_start, message_stop + 1))
                    quality = sum(word_errors[index] for index in relevant if index < len(word_errors))
                    key = (message.capcode, message.output_type, message.text)
                    previous = candidates.get(key)
                    if previous is None:
                        candidates[key] = (message, quality, 1)
                    else:
                        candidates[key] = (message, min(quality, previous[1]), previous[2] + 1)

        found: dict[tuple[int, str], tuple[FlexMessage, int, int]] = {}
        for message, quality, support in candidates.values():
            conflict_key = (message.capcode, message.output_type)
            previous = found.get(conflict_key)
            if previous is None or (quality, -support) < (previous[1], -previous[2]):
                found[conflict_key] = (message, quality, support)

        self._window_number += 1
        for message, _quality, _support in found.values():
            key = (message.capcode, message.output_type, message.text)
            if self._recent.get(key, -10) < self._window_number - 2:
                self.on_message(message)
            self._recent[key] = self._window_number
        self._recent = {
            key: window for key, window in self._recent.items()
            if window >= self._window_number - 2
        }
        self._first_window = False
