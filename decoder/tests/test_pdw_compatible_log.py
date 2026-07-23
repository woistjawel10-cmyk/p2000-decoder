import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flex_messages import FlexMessage
from pdw_compatible_log import DailyFlexLogWriter, format_pdw_compatible_line


def _message(text="Testmelding"):
    return FlexMessage(123456, 5, text, 0, 1, 2, "GROUP")


class TestPdwCompatibleLog(unittest.TestCase):
    def test_formats_the_existing_dispatcher_input_contract(self):
        line = format_pdw_compatible_line(_message(), datetime(2026, 7, 19, 17, 45, 1))
        self.assertEqual(
            line,
            "0123456 17:45:01 19-07-26 FLEX-A  GROUP  1600  Testmelding",
        )

    def test_appends_to_daily_latin1_log(self):
        with TemporaryDirectory() as tmp:
            writer = DailyFlexLogWriter(Path(tmp))
            path = writer.write(_message("Melding café"), datetime(2026, 7, 19, 17, 45, 1))
            self.assertEqual(path.name, "260719.log")
            self.assertEqual(path.read_text(encoding="latin-1").strip(),
                             "0123456 17:45:01 19-07-26 FLEX-A  GROUP  1600  Melding café")


if __name__ == "__main__":
    unittest.main()
