from __future__ import annotations

from functools import lru_cache
from .core import Contract, make_contract


@lru_cache(maxsize=None)
def and_contract() -> Contract:
    return make_contract("AND2", 2, 1, lambda x: (x[0] & x[1],))


@lru_cache(maxsize=None)
def or_contract() -> Contract:
    return make_contract("OR2", 2, 1, lambda x: (x[0] | x[1],))


@lru_cache(maxsize=None)
def xor_contract() -> Contract:
    return make_contract("XOR2", 2, 1, lambda x: (x[0] ^ x[1],))


@lru_cache(maxsize=None)
def mux_contract() -> Contract:
    # inputs selector, true, false
    return make_contract("MUX", 3, 1, lambda x: (x[1] if x[0] else x[2],))


@lru_cache(maxsize=None)
def full_adder_contract() -> Contract:
    def fa(x: tuple[int, ...]):
        a, b, c = x
        total = a + b + c
        return total & 1, (total >> 1) & 1

    return make_contract("FULL_ADDER", 3, 2, fa)
