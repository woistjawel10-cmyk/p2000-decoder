"""PDW-compatible BCH correction for de-interleaved FLEX columns."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


def _build_tables() -> tuple[List[int], List[int]]:
    ecs = []
    shift_register = 0x3B4
    for _ in range(21):
        ecs.append(shift_register)
        shift_register = (shift_register >> 1) ^ (0x3B4 if shift_register & 1 else 0)

    table = [0] * 1024
    for n in range(21):
        for i in range(21):
            table[ecs[n] ^ ecs[i]] = (i << 5) + n + 0x2000
    for n in range(21):
        table[ecs[n]] = n + (0x1F << 5) + 0x1000
    for n in range(21):
        for i in range(10):
            table[ecs[n] ^ (1 << i)] = n + (0x1F << 5) + 0x2000
    for n in range(10):
        table[1 << n] = 0x3FF + 0x1000
    for n in range(10):
        for i in range(10):
            if i != n:
                table[(1 << n) ^ (1 << i)] = 0x3FF + 0x2000
    return ecs, table


ECS, BCH_TABLE = _build_tables()


@dataclass
class PdwBchResult:
    bits: List[int]
    errors: int

    @property
    def correctable(self) -> bool:
        return self.errors < 3

    @property
    def trustworthy(self) -> bool:
        return self.correctable


def correct_pdw_column(bits: Iterable[int]) -> PdwBchResult:
    """Behavioral port of PDW Misc.cpp ecd(); input is ob[0..31]."""
    ob = [int(bit) for bit in bits]
    if len(ob) != 32 or any(bit not in (0, 1) for bit in ob):
        raise ValueError("bits must contain exactly 32 binary values")

    ecc = 0
    parity = 0
    for i in range(21):
        if ob[i] == 1:
            ecc ^= ECS[i]
            parity ^= 1

    accumulator = 0
    for i in range(21, 31):
        accumulator = (accumulator << 1) | ob[i]

    syndrome = ecc ^ accumulator
    errors = 0
    if syndrome:
        correction = BCH_TABLE[syndrome]
        if correction:
            first = correction & 0x1F
            second = (correction >> 5) & 0x1F
            if second != 0x1F:
                ob[second] ^= 1
                ecc ^= ECS[second]
            if first != 0x1F:
                ob[first] ^= 1
                ecc ^= ECS[first]
            errors = correction >> 12
        else:
            errors = 3
        if errors == 1:
            parity ^= 1

    parity = (parity + (ecc.bit_count() & 1)) & 1
    if parity != ob[31]:
        errors += 1
    return PdwBchResult(bits=ob, errors=min(errors, 3))
