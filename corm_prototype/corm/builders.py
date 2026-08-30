from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import random

from .program import Program
from .library import and_contract, or_contract, xor_contract, mux_contract, full_adder_contract


def build_adder(width: int) -> Program:
    p = Program(f"adder_{width}")
    a = [p.new_input(f"a{i}") for i in range(width)]
    b = [p.new_input(f"b{i}") for i in range(width)]
    carry = p.const(0)
    sums: list[int] = []
    for i in range(width):
        s, carry = p.add_organ(
            f"fa{i}", full_adder_contract(), (a[i], b[i], carry), ("adder", f"bit{i}")
        )
        sums.append(s)
    p.set_outputs(sums + [carry], [f"s{i}" for i in range(width)] + ["cout"])
    return p.finalize()


def _add_vectors(p: Program, x: Sequence[int], y: Sequence[int], module: Sequence[str]) -> list[int]:
    if len(x) != len(y):
        raise ValueError("vector length mismatch")
    carry = p.const(0)
    out: list[int] = []
    for i, (a, b) in enumerate(zip(x, y)):
        s, carry = p.add_organ(
            f"{'.'.join(module)}.fa{i}", full_adder_contract(), (a, b, carry), tuple(module) + (f"bit{i}",)
        )
        out.append(s)
    return out


def build_multiplier(width: int) -> Program:
    p = Program(f"multiplier_{width}")
    a = [p.new_input(f"a{i}") for i in range(width)]
    b = [p.new_input(f"b{i}") for i in range(width)]
    zero = p.const(0)
    total_width = 2 * width
    acc = [zero] * total_width
    for j in range(width):
        row = [zero] * total_width
        for i in range(width):
            (pp,) = p.add_organ(
                f"pp_{i}_{j}", and_contract(), (a[i], b[j]), ("partial_products", f"row{j}")
            )
            row[i + j] = pp
        acc = _add_vectors(p, acc, row, ("accumulate", f"row{j}"))
    p.set_outputs(acc, [f"p{i}" for i in range(total_width)])
    return p.finalize()


def build_alu(width: int) -> Program:
    """Combinational accumulator ALU.

    Inputs: state[width], operand[width], op0, op1.
    opcode 00=ADD, 01=XOR, 10=AND, 11=LOAD operand.
    Outputs: next_state[width].
    """
    p = Program(f"alu_{width}")
    state = [p.new_input(f"state{i}") for i in range(width)]
    operand = [p.new_input(f"operand{i}") for i in range(width)]
    op0 = p.new_input("op0")
    op1 = p.new_input("op1")

    carry = p.const(0)
    add_bits: list[int] = []
    xor_bits: list[int] = []
    and_bits: list[int] = []
    for i in range(width):
        s, carry = p.add_organ(
            f"alu.fa{i}", full_adder_contract(), (state[i], operand[i], carry), ("alu", "add", f"bit{i}")
        )
        add_bits.append(s)
        (x,) = p.add_organ(
            f"alu.xor{i}", xor_contract(), (state[i], operand[i]), ("alu", "xor", f"bit{i}")
        )
        xor_bits.append(x)
        (a,) = p.add_organ(
            f"alu.and{i}", and_contract(), (state[i], operand[i]), ("alu", "and", f"bit{i}")
        )
        and_bits.append(a)

    outputs: list[int] = []
    for i in range(width):
        (low,) = p.add_organ(
            f"alu.lowmux{i}", mux_contract(), (op0, xor_bits[i], add_bits[i]), ("alu", "select", f"bit{i}")
        )
        (high,) = p.add_organ(
            f"alu.highmux{i}", mux_contract(), (op0, operand[i], and_bits[i]), ("alu", "select", f"bit{i}")
        )
        (out,) = p.add_organ(
            f"alu.outmux{i}", mux_contract(), (op1, high, low), ("alu", "select", f"bit{i}")
        )
        outputs.append(out)
    p.set_outputs(outputs, [f"next{i}" for i in range(width)])
    return p.finalize()


def build_random_program(n_inputs: int, n_organs: int, n_outputs: int = 8, seed: int = 0) -> Program:
    rng = random.Random(seed)
    p = Program(f"random_{n_inputs}_{n_organs}_{seed}")
    available = [p.new_input(f"x{i}") for i in range(n_inputs)]
    contracts = [and_contract(), or_contract(), xor_contract()]
    for i in range(n_organs):
        contract = rng.choice(contracts)
        ins = rng.sample(available, 2) if len(available) >= 2 else [available[0], available[0]]
        (out,) = p.add_organ(f"g{i}", contract, ins, ("random", f"layer{i // 32}"))
        available.append(out)
    outs = available[-n_outputs:]
    p.set_outputs(outs)
    return p.finalize()
