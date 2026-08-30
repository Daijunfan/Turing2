from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence
import random

from .core import CONST0, CONST1, Contract, make_contract
from .library import and_contract, mux_contract, or_contract, xor_contract
from .program import Program


@dataclass(frozen=True)
class NetGate:
    """A gate in a topologically ordered Boolean netlist.

    References 0..n_inputs-1 address primary inputs.  Gate i produces reference
    n_inputs+i.  CONST0/CONST1 use the shared negative sentinel values.
    """

    op: str
    args: tuple[int, ...]


@dataclass(frozen=True)
class BooleanNetlist:
    name: str
    n_inputs: int
    gates: tuple[NetGate, ...]
    outputs: tuple[int, ...]

    def _validate_ref(self, ref: int, gate_index: int) -> None:
        if ref in (CONST0, CONST1):
            return
        if not (0 <= ref < self.n_inputs + gate_index):
            raise ValueError(f"non-causal net reference {ref} at gate {gate_index}")

    def validate(self) -> None:
        arity = {"NOT": 1, "BUF": 1, "AND": 2, "OR": 2, "XOR": 2, "MUX": 3}
        for index, gate in enumerate(self.gates):
            if gate.op not in arity or len(gate.args) != arity[gate.op]:
                raise ValueError(f"invalid gate {index}: {gate}")
            for ref in gate.args:
                self._validate_ref(ref, index)
        limit = self.n_inputs + len(self.gates)
        for ref in self.outputs:
            if ref not in (CONST0, CONST1) and not (0 <= ref < limit):
                raise ValueError(f"invalid output reference {ref}")

    def evaluate_bits(self, inputs: Sequence[int], universe_mask: int) -> tuple[int, ...]:
        self.validate()
        if len(inputs) != self.n_inputs:
            raise ValueError("input arity mismatch")
        values = [x & universe_mask for x in inputs]

        def resolve(ref: int) -> int:
            if ref == CONST0:
                return 0
            if ref == CONST1:
                return universe_mask
            return values[ref]

        for gate in self.gates:
            args = [resolve(ref) for ref in gate.args]
            if gate.op == "NOT":
                value = (~args[0]) & universe_mask
            elif gate.op == "BUF":
                value = args[0]
            elif gate.op == "AND":
                value = args[0] & args[1]
            elif gate.op == "OR":
                value = args[0] | args[1]
            elif gate.op == "XOR":
                value = args[0] ^ args[1]
            elif gate.op == "MUX":
                value = (args[0] & args[1]) | (((~args[0]) & universe_mask) & args[2])
            else:  # guarded by validate(), retained as a defensive assertion
                raise AssertionError(gate.op)
            values.append(value & universe_mask)
        return tuple(resolve(ref) for ref in self.outputs)

    def evaluate_scalar(self, inputs: Sequence[int]) -> tuple[int, ...]:
        return tuple(x & 1 for x in self.evaluate_bits(inputs, 1))


@lru_cache(maxsize=None)
def primitive_contract(op: str) -> Contract:
    if op == "AND":
        return and_contract()
    if op == "OR":
        return or_contract()
    if op == "XOR":
        return xor_contract()
    if op == "MUX":
        return mux_contract()
    if op == "NOT":
        return make_contract("NOT1", 1, 1, lambda x: (1 - x[0],))
    if op == "BUF":
        return make_contract("BUF1", 1, 1, lambda x: (x[0],))
    raise ValueError(f"unsupported primitive: {op}")


def compile_netlist(netlist: BooleanNetlist) -> Program:
    """Compile any validated acyclic Boolean netlist into contract organs.

    The compiler preserves only semantic dependencies.  Runtime placement,
    concrete gate shapes, physical cells, and repair shapes are not fixed by
    the source netlist.
    """

    netlist.validate()
    program = Program(f"compiled::{netlist.name}")
    ref_to_net: dict[int, int] = {
        index: program.new_input(f"x{index}") for index in range(netlist.n_inputs)
    }
    ref_to_net[CONST0] = program.const(0)
    ref_to_net[CONST1] = program.const(1)

    for index, gate in enumerate(netlist.gates):
        contract = primitive_contract(gate.op)
        inputs = tuple(ref_to_net[ref] for ref in gate.args)
        (output,) = program.add_organ(
            f"g{index}:{gate.op}",
            contract,
            inputs,
            ("compiled", f"block{index // 64}", f"gate{index}"),
        )
        ref_to_net[netlist.n_inputs + index] = output

    program.set_outputs([ref_to_net[ref] for ref in netlist.outputs])
    return program.finalize()


def random_netlist(
    n_inputs: int,
    n_gates: int,
    n_outputs: int,
    seed: int,
    locality_window: int = 256,
) -> BooleanNetlist:
    """Generate a deep random DAG while retaining broad dependency mixing."""

    if n_inputs <= 0 or n_gates <= 0 or n_outputs <= 0:
        raise ValueError("all dimensions must be positive")
    rng = random.Random(seed)
    gates: list[NetGate] = []
    ops = ("AND", "OR", "XOR", "NOT", "MUX")
    weights = (3, 2, 3, 1, 2)

    for index in range(n_gates):
        op = rng.choices(ops, weights=weights, k=1)[0]
        arity = 1 if op == "NOT" else 3 if op == "MUX" else 2
        produced = n_inputs + index
        lower = max(0, produced - locality_window)
        recent = list(range(lower, produced))
        # Inject occasional long-range dependencies so the graph is not merely
        # a collection of short chains.
        pool = recent if rng.random() < 0.82 else list(range(produced))
        if not pool:
            pool = list(range(n_inputs))
        args = tuple(rng.choice(pool) for _ in range(arity))
        gates.append(NetGate(op, args))

    output_start = n_inputs + max(0, n_gates - max(n_outputs * 8, 64))
    output_pool = list(range(output_start, n_inputs + n_gates))
    outputs = tuple(rng.choice(output_pool) for _ in range(n_outputs))
    netlist = BooleanNetlist(
        f"random_{n_inputs}_{n_gates}_{seed}", n_inputs, tuple(gates), outputs
    )
    netlist.validate()
    return netlist
