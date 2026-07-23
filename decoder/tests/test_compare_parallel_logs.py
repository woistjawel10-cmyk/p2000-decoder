import sys
import unittest
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compare_parallel_logs import TimedMessage, compare, latest_parallel_session, parse_parallel_line, parse_pdw_line


class TestLogParsing(unittest.TestCase):
    def test_parses_receiver_parallel_line(self):
        line = "2026-07-19 18:01:02,123 [INFO] grunnalert_receiver: PARALLEL FLEX GROUP capcode=1234567 text=Test"
        message = parse_parallel_line(line)
        self.assertEqual(message.key, (1234567, "GROUP", "Test"))

    def test_parses_pdw_line(self):
        line = "1234567 18:01:00 19-07-26 FLEX-A  GROUP  1600  Test"
        message = parse_pdw_line(line)
        self.assertEqual(message.key, (1234567, "GROUP", "Test"))


class TestComparison(unittest.TestCase):
    def test_matches_same_content_inside_tolerance_once(self):
        timestamp = datetime(2026, 7, 19, 18, 1)
        expected = [TimedMessage(timestamp, 123, "ALPHA", "Test")]
        decoded = [
            TimedMessage(timestamp + timedelta(seconds=5), 123, "ALPHA", "Test"),
            TimedMessage(timestamp + timedelta(seconds=6), 123, "ALPHA", "Test"),
        ]
        result = compare(decoded, expected, 15)
        self.assertEqual(result["exact"], 1)
        self.assertEqual(result["unmatched_decoder_outputs"], 1)

    def test_rejects_right_content_outside_tolerance(self):
        timestamp = datetime(2026, 7, 19, 18, 1)
        result = compare(
            [TimedMessage(timestamp + timedelta(seconds=16), 123, "ALPHA", "Test")],
            [TimedMessage(timestamp, 123, "ALPHA", "Test")],
            15,
        )
        self.assertEqual(result["exact"], 0)

    def test_latest_parallel_session_ignores_older_run(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "receiver.log"
            path.write_text(
                "2026-07-19 17:00:00,000 [INFO] x: Parallel FLEX-decoder actief (logging-only, geen dispatch)\n"
                "2026-07-19 17:05:00,000 [INFO] x: Receiver stopped cleanly.\n"
                "2026-07-19 18:00:00,000 [INFO] x: Parallel FLEX-decoder actief (logging-only, geen dispatch)\n"
                "2026-07-19 18:30:00,000 [INFO] x: Receiver stopped cleanly.\n",
                encoding="utf-8",
            )
            start, end = latest_parallel_session([path])
            self.assertEqual(start, datetime(2026, 7, 19, 18, 0))
            self.assertEqual(end, datetime(2026, 7, 19, 18, 30))


if __name__ == "__main__":
    unittest.main()
