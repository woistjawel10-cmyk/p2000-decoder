import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flex_messages import (
    MODE_ALPHA,
    FlexMessage,
    GroupAssignment,
    GroupAssignmentTracker,
    parse_alpha_messages,
)


def with_checksum(value):
    base = value & ~0xF
    partial = sum((base >> shift) & 0xF for shift in range(4, 20, 4)) + ((base >> 20) & 1)
    return base | ((0xF - partial) & 0xF)


def alpha_word(chars):
    values = [ord(char) for char in chars.ljust(3, "\x03")]
    return values[0] | (values[1] << 7) | (values[2] << 14)


class TestParseAlphaMessages(unittest.TestCase):
    def test_decodes_short_address_vector_and_text(self):
        words = [0] * 12
        address_start, vector_start = 1, 2
        words[0] = with_checksum((vector_start << 10) | ((address_start - 1) << 8))
        words[1] = 32768 + 1234567
        message_start = 4
        message_word_count = 2
        words[2] = with_checksum(
            (MODE_ALPHA << 4) | (message_start << 7) | ((message_word_count + 1) << 14)
        )
        words[message_start] = 0 << 11  # fragment header
        words[message_start + 1] = alpha_word("HEL")
        words[message_start + 2] = alpha_word("LO")

        messages = parse_alpha_messages(words)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].capcode, 1234567)
        self.assertEqual(messages[0].text, "HELLO")

    def test_rejects_bad_vector_checksum(self):
        words = [0] * 8
        words[0] = with_checksum(2 << 10)
        words[1] = 32768 + 123456
        words[2] = (MODE_ALPHA << 4) | (4 << 7)  # deliberately no checksum
        self.assertEqual(parse_alpha_messages(words), [])


class TestGroupAssignmentTracker(unittest.TestCase):
    def test_expands_group_message_in_assigned_frame(self):
        tracker = GroupAssignmentTracker()
        tracker.add(GroupAssignment(capcode=123456, assigned_frame=42, group_bit=1))
        tracker.add(GroupAssignment(capcode=123457, assigned_frame=42, group_bit=1))
        group_message = FlexMessage(2029569, MODE_ALPHA, "TEST", 0, 1, 2)

        messages = tracker.expand(42, [group_message])

        self.assertEqual([(m.capcode, m.output_type) for m in messages], [
            (123456, "GROUP"),
            (123457, "GROUP"),
            (2029569, "ALPHA"),
        ])

    def test_does_not_expand_in_wrong_frame(self):
        tracker = GroupAssignmentTracker()
        tracker.add(GroupAssignment(capcode=123456, assigned_frame=42, group_bit=1))
        group_message = FlexMessage(2029569, MODE_ALPHA, "TEST", 0, 1, 2)
        self.assertEqual(tracker.expand(41, [group_message]), [group_message])


if __name__ == "__main__":
    unittest.main()
