"""Parse FLEX address/vector pairs and ALPHA payloads from corrected words."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Set

from block_deinterleave import short_address_capcode
from flex_words import flex_checksum_ok

MODE_SECURE = 0
MODE_SHORT_INSTRUCTION = 1
MODE_ALPHA = 5


@dataclass
class FlexMessage:
    capcode: int
    message_type: int
    text: str
    fragment_number: int
    address_word_index: int
    vector_word_index: int
    output_type: str = "ALPHA"


@dataclass
class GroupAssignment:
    capcode: int
    assigned_frame: int
    group_bit: int


def _is_long_address_low_word(word: int) -> bool:
    value = word & 0x1FFFFF
    return value < 0x008001 or 0x1E0000 < value < 0x1F0001 or value > 0x1F7FFE


def _decode_alpha_words(words: List[int], start: int, stop: int, fragment: int) -> str:
    characters = []
    for index in range(start, stop + 1):
        if not 0 <= index < len(words):
            break
        word = words[index]
        values = [word & 0x7F, (word >> 7) & 0x7F, (word >> 14) & 0x7F]
        if index == start and fragment == 3:
            values = values[1:]
        characters.extend(value for value in values if value != 0x03)
    return "".join(chr(value) for value in characters).rstrip("\x00 \r\n")


def parse_alpha_messages(words: List[int]) -> List[FlexMessage]:
    if not words or words[0] & 0x400000 or not flex_checksum_ok(words[0]):
        return []
    vector_start = (words[0] >> 10) & 0x3F
    address_start = ((words[0] >> 8) & 0x03) + 1
    if not address_start <= vector_start <= min(63, len(words)):
        return []

    messages = []
    for address_index in range(address_start, vector_start):
        address_word = words[address_index]
        if address_word & 0x400000 or _is_long_address_low_word(address_word):
            continue  # long addresses require their paired-word handling
        vector_index = vector_start + address_index - address_start
        if vector_index >= len(words):
            continue
        vector = words[vector_index]
        if vector & 0x400000 or not flex_checksum_ok(vector):
            continue
        message_type = (vector >> 4) & 0x07
        if message_type not in (MODE_ALPHA, MODE_SECURE):
            continue

        packed = vector >> 7
        message_start = packed & 0x7F
        message_stop = ((packed >> 7) & 0x7F) + message_start - 1
        if not 0 <= message_start < len(words) or message_stop < message_start:
            continue
        fragment = (words[message_start] >> 11) & 0x03
        payload_start = message_start + 1
        text = _decode_alpha_words(words, payload_start, message_stop, fragment)
        messages.append(FlexMessage(
            capcode=short_address_capcode(address_word),
            message_type=message_type,
            text=text,
            fragment_number=fragment,
            address_word_index=address_index,
            vector_word_index=vector_index,
        ))
    return messages


def parse_group_assignments(words: List[int]) -> List[GroupAssignment]:
    if not words or words[0] & 0x400000 or not flex_checksum_ok(words[0]):
        return []
    vector_start = (words[0] >> 10) & 0x3F
    address_start = ((words[0] >> 8) & 0x03) + 1
    if not address_start <= vector_start <= min(63, len(words)):
        return []
    assignments = []
    for address_index in range(address_start, vector_start):
        address_word = words[address_index]
        vector_index = vector_start + address_index - address_start
        if vector_index >= len(words) or address_word & 0x400000 or _is_long_address_low_word(address_word):
            continue
        vector = words[vector_index]
        if vector & 0x400000 or not flex_checksum_ok(vector):
            continue
        if ((vector >> 4) & 0x07) != MODE_SHORT_INSTRUCTION:
            continue
        group_bit = (vector >> 17) & 0x7F
        if group_bit >= 16:
            continue
        assignments.append(GroupAssignment(
            capcode=short_address_capcode(address_word),
            assigned_frame=(vector >> 10) & 0x7F,
            group_bit=group_bit,
        ))
    return assignments


class GroupAssignmentTracker:
    def __init__(self):
        self._frames: Dict[int, int] = {}
        self._capcodes: Dict[int, Set[int]] = {}

    def add(self, assignment: GroupAssignment) -> None:
        if self._frames.get(assignment.group_bit) != assignment.assigned_frame:
            self._capcodes[assignment.group_bit] = set()
        self._frames[assignment.group_bit] = assignment.assigned_frame
        self._capcodes.setdefault(assignment.group_bit, set()).add(assignment.capcode)

    def expand(self, frame_no: int | None, messages: List[FlexMessage]) -> List[FlexMessage]:
        expanded = []
        for message in messages:
            group_bit = message.capcode - 2029568
            if 0 <= group_bit < 16 and frame_no is not None and self._frames.get(group_bit) == frame_no:
                expanded.extend(
                    replace(message, capcode=capcode, output_type="GROUP")
                    for capcode in sorted(self._capcodes.get(group_bit, set()))
                )
                self._frames.pop(group_bit, None)
                self._capcodes.pop(group_bit, None)
            expanded.append(message)
        self._expire_passed(frame_no)
        return expanded

    def _expire_passed(self, current_frame: int | None) -> None:
        if current_frame is None:
            return
        for group_bit, assigned in list(self._frames.items()):
            adjusted_current = current_frame + 128 if assigned > 120 and current_frame < 8 else current_frame
            adjusted_assigned = assigned + 128 if assigned < 8 and current_frame > 120 else assigned
            if adjusted_assigned - adjusted_current <= 0:
                self._frames.pop(group_bit, None)
                self._capcodes.pop(group_bit, None)
