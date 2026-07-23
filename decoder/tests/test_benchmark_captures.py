import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark_captures import address_capcodes_from_words, pdw_capcodes, pdw_messages, pdw_typed_messages
from flex_words import flex_checksum_ok


def with_checksum(word_without_low_nibble):
    base = word_without_low_nibble & ~0xF
    partial = sum((base >> shift) & 0xF for shift in range(4, 20, 4)) + ((base >> 20) & 1)
    return base | ((0xF - partial) & 0xF)


class TestPdwCapcodes(unittest.TestCase):
    def test_extracts_leading_capcodes(self):
        lines = [{"raw_line": "1234567 12:00 FLEX-A ALPHA test"}, {"raw_line": "not a message"}]
        self.assertEqual(pdw_capcodes(lines), {1234567})

    def test_extracts_capcode_and_message_text(self):
        lines = [{"raw_line": "1234567 12:00:00 19-07-26 FLEX-A ALPHA 1600 Hello world"}]
        self.assertEqual(pdw_messages(lines), {(1234567, "Hello world")})
        self.assertEqual(pdw_typed_messages(lines), {(1234567, "ALPHA", "Hello world")})


class TestAddressBounds(unittest.TestCase):
    def test_never_treats_biw_as_address(self):
        # address_start=1, vector_start=2; only words[1] is an address.
        biw = with_checksum((2 << 10) | (0 << 8))
        plausible, capcodes, count = address_capcodes_from_words([biw, 32768 + 765432, 32768 + 111111])
        self.assertTrue(plausible)
        self.assertEqual(count, 1)
        self.assertEqual(capcodes, {765432})

    def test_rejects_vector_start_before_address_start(self):
        biw = with_checksum((1 << 10) | (3 << 8))  # vector 1, address start 4
        self.assertEqual(address_capcodes_from_words([biw] * 8), (False, set(), 0))


class TestFlexChecksum(unittest.TestCase):
    def test_accepts_complemented_nibble_sum(self):
        self.assertTrue(flex_checksum_ok(with_checksum(0x123450)))

    def test_rejects_changed_data(self):
        word = with_checksum(0x123450)
        self.assertFalse(flex_checksum_ok(word ^ 0x10))


if __name__ == "__main__":
    unittest.main()
