import sys
import unittest
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flex_messages import FlexMessage
from parallel_decoder import ParallelFlexDecoder
from benchmark_captures import pdw_messages

CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"


def _message(text="Testmelding"):
    return FlexMessage(
        capcode=1234567,
        message_type=5,
        text=text,
        fragment_number=0,
        address_word_index=1,
        vector_word_index=2,
    )


class TestParallelFlexDecoder(unittest.TestCase):
    def test_ensemble_emits_same_message_only_once(self):
        emitted = []
        decoder = ParallelFlexDecoder(100, emitted.append)
        sync = SimpleNamespace(bit_offset=100, polarity_inverted=False, frame_no=1)

        with patch("parallel_decoder.demodulate_2fsk", return_value=SimpleNamespace(bits=np.zeros(12800))), \
                patch("parallel_decoder.find_sync_matches", return_value=[sync]), \
                patch("parallel_decoder.frame_words_with_errors", return_value=([0] * 10, [0] * 10)), \
                patch("parallel_decoder.parse_group_assignments", return_value=[]), \
                patch("parallel_decoder.parse_alpha_messages", return_value=[_message()]):
            decoder._decode_window(np.zeros(800, dtype=np.int16))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].capcode, 1234567)

    def test_feed_uses_overlapping_windows_at_configured_hop(self):
        decoder = ParallelFlexDecoder(100, lambda _message: None)
        with patch.object(decoder, "_decode_window") as decode:
            decoder.feed_pcm(np.zeros(1200, dtype=np.int16).tobytes())

        # 8-second window, then advance 4 seconds: 1200 samples yields two
        # complete windows (0..800 and 400..1200).
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(len(decoder._pcm), 400)

    def test_repeated_overlap_output_is_temporarily_suppressed(self):
        emitted = []
        decoder = ParallelFlexDecoder(100, emitted.append)
        sync = SimpleNamespace(bit_offset=4000, polarity_inverted=False, frame_no=1)
        with patch("parallel_decoder.demodulate_2fsk", return_value=SimpleNamespace(bits=np.zeros(12800))), \
                patch("parallel_decoder.find_sync_matches", return_value=[sync]), \
                patch("parallel_decoder.frame_words_with_errors", return_value=([0] * 10, [0] * 10)), \
                patch("parallel_decoder.parse_group_assignments", return_value=[]), \
                patch("parallel_decoder.parse_alpha_messages", return_value=[_message()]):
            for _ in range(3):
                decoder._decode_window(np.zeros(800, dtype=np.int16))

        self.assertEqual(len(emitted), 1)

    def test_conflicting_text_prefers_fewer_bch_corrections(self):
        emitted = []
        decoder = ParallelFlexDecoder(100, emitted.append)
        sync = SimpleNamespace(bit_offset=100, polarity_inverted=False, frame_no=1)
        clean_errors = [0] * 10
        corrected_errors = [0, 1] + [0] * 8
        with patch("parallel_decoder.demodulate_2fsk", return_value=SimpleNamespace(bits=np.zeros(12800))), \
                patch("parallel_decoder.find_sync_matches", return_value=[sync]), \
                patch("parallel_decoder.frame_words_with_errors", side_effect=[
                    ([0] * 10, clean_errors), ([0] * 10, corrected_errors)
                ]), \
                patch("parallel_decoder.parse_group_assignments", return_value=[]), \
                patch("parallel_decoder.parse_alpha_messages", side_effect=[
                    [_message("Correct")], [_message("Corrupt")]
                ]):
            decoder._decode_window(np.zeros(800, dtype=np.int16))

        self.assertEqual([message.text for message in emitted], ["Correct"])


@unittest.skipUnless(CAPTURES_DIR.joinpath("manifest.jsonl").exists(), "no real capture data")
class TestParallelFlexDecoderRealStream(unittest.TestCase):
    def test_chunked_first_capture_matches_every_pdw_message(self):
        row = json.loads(CAPTURES_DIR.joinpath("manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
        emitted = []
        with wave.open(str(CAPTURES_DIR / row["wav_file"]), "rb") as wav_file:
            decoder = ParallelFlexDecoder(wav_file.getframerate(), emitted.append)
            while chunk := wav_file.readframes(2048):
                decoder.feed_pcm(chunk)

        decoded = {(message.capcode, message.text) for message in emitted}
        expected = pdw_messages(row.get("pdw_lines", []))
        self.assertTrue(expected)
        self.assertEqual(decoded, expected)


if __name__ == "__main__":
    unittest.main()
