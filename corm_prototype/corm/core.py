from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Callable, Iterable, Sequence
import json

CONST0 = -1
CONST1 = -2


@dataclass(frozen=True)
class Gate:
    op: str
    args: tuple[int, ...]


@dataclass
class Variant:
    name: str
    n_inputs: int
    gates: list[Gate]
    outputs: tuple[int, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            payload = {
                "n_inputs": self.n_inputs,
                "gates": [(g.op, g.args) for g in self.gates],
                "outputs": self.outputs,
            }
            self.fingerprint = sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest()

    @property
    def gate_count(self) -> int:
        return len(self.gates)

    def evaluate_bits(self, inputs: Sequence[int], universe_mask: int) -> tuple[int, ...]:
        if len(inputs) != self.n_inputs:
            raise ValueError(f"expected {self.n_inputs} inputs, got {len(inputs)}")
        values: list[int] = list(inputs)
        for gate in self.gates:
            args = [
                0 if ref == CONST0 else universe_mask if ref == CONST1 else values[ref]
                for ref in gate.args
            ]
            op = gate.op
            if op == "NOT":
                out = (~args[0]) & universe_mask
            elif op == "AND":
                out = args[0] & args[1]
            elif op == "OR":
                out = args[0] | args[1]
            elif op == "XOR":
                out = args[0] ^ args[1]
            elif op == "MUX":
                # args = selector, when_true, when_false
                out = (args[0] & args[1]) | (((~args[0]) & universe_mask) & args[2])
            elif op == "BUF":
                out = args[0]
            else:
                raise ValueError(f"unknown gate op: {op}")
            values.append(out & universe_mask)

        def resolve(ref: int) -> int:
            if ref == CONST0:
                return 0
            if ref == CONST1:
                return universe_mask
            return values[ref]

        return tuple(resolve(ref) for ref in self.outputs)

    def evaluate_scalar(self, inputs: Sequence[int]) -> tuple[int, ...]:
        outputs = self.evaluate_bits([int(bool(x)) for x in inputs], 1)
        return tuple(x & 1 for x in outputs)

    def edge_refs(self) -> list[tuple[int, int]]:
        """Edges between local signals. Gate output ref is n_inputs + gate index."""
        edges: list[tuple[int, int]] = []
        for idx, gate in enumerate(self.gates):
            dst = self.n_inputs + idx
            for src in gate.args:
                if src >= 0:
                    edges.append((src, dst))
        return edges


class VariantBuilder:
    def __init__(self, n_inputs: int):
        self.n_inputs = n_inputs
        self.gates: list[Gate] = []
        self.cache: dict[tuple[str, tuple[int, ...]], int] = {}

    def _gate(self, op: str, *args: int) -> int:
        if op in {"AND", "OR", "XOR"} and len(args) == 2:
            args = tuple(sorted(args))
        else:
            args = tuple(args)

        # Constant folding and basic identities.
        if op == "NOT":
            if args[0] == CONST0:
                return CONST1
            if args[0] == CONST1:
                return CONST0
        elif op == "AND":
            a, b = args
            if CONST0 in args:
                return CONST0
            if a == CONST1:
                return b
            if b == CONST1:
                return a
            if a == b:
                return a
        elif op == "OR":
            a, b = args
            if CONST1 in args:
                return CONST1
            if a == CONST0:
                return b
            if b == CONST0:
                return a
            if a == b:
                return a
        elif op == "XOR":
            a, b = args
            if a == CONST0:
                return b
            if b == CONST0:
                return a
            if a == b:
                return CONST0
        elif op == "MUX":
            s, t, f = args
            if t == f:
                return t
            if s == CONST0:
                return f
            if s == CONST1:
                return t
            if t == CONST1 and f == CONST0:
                return s

        key = (op, tuple(args))
        if key in self.cache:
            return self.cache[key]
        ref = self.n_inputs + len(self.gates)
        self.gates.append(Gate(op, tuple(args)))
        self.cache[key] = ref
        return ref

    def not_(self, a: int) -> int:
        return self._gate("NOT", a)

    def and_(self, a: int, b: int) -> int:
        return self._gate("AND", a, b)

    def or_(self, a: int, b: int) -> int:
        return self._gate("OR", a, b)

    def xor(self, a: int, b: int) -> int:
        return self._gate("XOR", a, b)

    def mux(self, s: int, t: int, f: int) -> int:
        return self._gate("MUX", s, t, f)

    def reduce_and(self, refs: Sequence[int]) -> int:
        if not refs:
            return CONST1
        cur = refs[0]
        for ref in refs[1:]:
            cur = self.and_(cur, ref)
        return cur

    def reduce_or(self, refs: Sequence[int]) -> int:
        if not refs:
            return CONST0
        cur = refs[0]
        for ref in refs[1:]:
            cur = self.or_(cur, ref)
        return cur

    def reduce_xor(self, refs: Sequence[int]) -> int:
        if not refs:
            return CONST0
        cur = refs[0]
        for ref in refs[1:]:
            cur = self.xor(cur, ref)
        return cur

    def finish(self, name: str, outputs: Sequence[int]) -> Variant:
        # Every organ boundary output must be backed by a physical cell.  This
        # makes producer updates strictly local: replacing one organ changes
        # only its own output endpoints, never a transitive pass-through chain.
        materialized: list[int] = []
        for ref in outputs:
            if ref < self.n_inputs:
                ref = self._gate("BUF", ref)
            materialized.append(ref)
        return Variant(name, self.n_inputs, self.gates.copy(), tuple(materialized))


@dataclass
class Contract:
    name: str
    n_inputs: int
    truth_outputs: tuple[int, ...]
    variants: list[Variant] = field(default_factory=list)
    contract_hash: str = ""

    def __post_init__(self) -> None:
        rows = 1 << self.n_inputs
        limit = (1 << rows) - 1
        for out in self.truth_outputs:
            if out < 0 or out > limit:
                raise ValueError("truth table output exceeds input width")
        payload = {
            "name": self.name,
            "n_inputs": self.n_inputs,
            "truth_outputs": self.truth_outputs,
        }
        self.contract_hash = sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @property
    def n_outputs(self) -> int:
        return len(self.truth_outputs)

    @property
    def local_mask(self) -> int:
        return (1 << (1 << self.n_inputs)) - 1

    def input_patterns(self) -> list[int]:
        rows = 1 << self.n_inputs
        patterns: list[int] = []
        for i in range(self.n_inputs):
            value = 0
            for assignment in range(rows):
                if (assignment >> i) & 1:
                    value |= 1 << assignment
            patterns.append(value)
        return patterns

    def verify_variant(self, variant: Variant) -> bool:
        if variant.n_inputs != self.n_inputs:
            return False
        got = variant.evaluate_bits(self.input_patterns(), self.local_mask)
        return got == self.truth_outputs

    def verified_variants(self) -> list[Variant]:
        return [variant for variant in self.variants if self.verify_variant(variant)]

    def evaluate_bits(self, inputs: Sequence[int], universe_mask: int) -> tuple[int, ...]:
        """Evaluate truth table directly over arbitrary bit-parallel inputs."""
        if len(inputs) != self.n_inputs:
            raise ValueError("input arity mismatch")
        outputs: list[int] = []
        rows = 1 << self.n_inputs
        for truth in self.truth_outputs:
            out = 0
            for assignment in range(rows):
                if (truth >> assignment) & 1:
                    term = universe_mask
                    for i, x in enumerate(inputs):
                        term &= x if ((assignment >> i) & 1) else ((~x) & universe_mask)
                    out |= term
            outputs.append(out & universe_mask)
        return tuple(outputs)


def truth_from_callable(n_inputs: int, n_outputs: int, fn: Callable[[tuple[int, ...]], Sequence[int]]) -> tuple[int, ...]:
    rows = 1 << n_inputs
    packed = [0] * n_outputs
    for assignment in range(rows):
        bits = tuple((assignment >> i) & 1 for i in range(n_inputs))
        result = tuple(int(bool(x)) for x in fn(bits))
        if len(result) != n_outputs:
            raise ValueError("function output arity mismatch")
        for j, bit in enumerate(result):
            if bit:
                packed[j] |= 1 << assignment
    return tuple(packed)


def _sop_variant(contract: Contract) -> Variant:
    b = VariantBuilder(contract.n_inputs)
    outputs: list[int] = []
    rows = 1 << contract.n_inputs
    for truth in contract.truth_outputs:
        minterms: list[int] = []
        for assignment in range(rows):
            if not ((truth >> assignment) & 1):
                continue
            literals: list[int] = []
            for i in range(contract.n_inputs):
                literals.append(i if ((assignment >> i) & 1) else b.not_(i))
            minterms.append(b.reduce_and(literals))
        outputs.append(b.reduce_or(minterms))
    return b.finish("sop", outputs)


def _anf_variant(contract: Contract) -> Variant:
    b = VariantBuilder(contract.n_inputs)
    outputs: list[int] = []
    rows = 1 << contract.n_inputs
    for truth in contract.truth_outputs:
        coeff = [(truth >> i) & 1 for i in range(rows)]
        for var in range(contract.n_inputs):
            for mask in range(rows):
                if mask & (1 << var):
                    coeff[mask] ^= coeff[mask ^ (1 << var)]
        monomials: list[int] = []
        for mask, bit in enumerate(coeff):
            if not bit:
                continue
            refs = [i for i in range(contract.n_inputs) if mask & (1 << i)]
            monomials.append(b.reduce_and(refs))
        outputs.append(b.reduce_xor(monomials))
    return b.finish("anf", outputs)


def _shannon_variant(contract: Contract) -> Variant:
    b = VariantBuilder(contract.n_inputs)

    def synth(values: tuple[int, ...], variables: tuple[int, ...], memo: dict[tuple[tuple[int, ...], tuple[int, ...]], int]) -> int:
        if all(v == 0 for v in values):
            return CONST0
        if all(v == 1 for v in values):
            return CONST1
        key = (values, variables)
        if key in memo:
            return memo[key]
        if not variables:
            return CONST1 if values[0] else CONST0
        # Choose the variable that yields most immediate simplification.
        best_pos = 0
        best_score = None
        for pos in range(len(variables)):
            lo = tuple(v for idx, v in enumerate(values) if ((idx >> pos) & 1) == 0)
            hi = tuple(v for idx, v in enumerate(values) if ((idx >> pos) & 1) == 1)
            score = len(set(lo)) + len(set(hi))
            if best_score is None or score < best_score:
                best_score = score
                best_pos = pos
        var = variables[best_pos]
        lo = tuple(v for idx, v in enumerate(values) if ((idx >> best_pos) & 1) == 0)
        hi = tuple(v for idx, v in enumerate(values) if ((idx >> best_pos) & 1) == 1)
        rest = variables[:best_pos] + variables[best_pos + 1 :]
        lo_ref = synth(lo, rest, memo)
        hi_ref = synth(hi, rest, memo)
        ref = b.mux(var, hi_ref, lo_ref)
        memo[key] = ref
        return ref

    outputs: list[int] = []
    rows = 1 << contract.n_inputs
    for truth in contract.truth_outputs:
        values = tuple((truth >> assignment) & 1 for assignment in range(rows))
        outputs.append(synth(values, tuple(range(contract.n_inputs)), {}))
    return b.finish("shannon", outputs)


def _redundant_variant(contract: Contract, base: Variant) -> Variant:
    """Semantics-preserving fallback that forces a distinct topology.

    Each output y becomes (y & p) | (y & !p) for input p. This is used only
    when independent synthesis methods collapse to the same network.
    """
    b = VariantBuilder(contract.n_inputs)
    remap: dict[int, int] = {i: i for i in range(contract.n_inputs)}
    remap[CONST0] = CONST0
    remap[CONST1] = CONST1
    for idx, gate in enumerate(base.gates):
        args = tuple(remap[a] for a in gate.args)
        if gate.op == "NOT":
            ref = b.not_(args[0])
        elif gate.op == "AND":
            ref = b.and_(args[0], args[1])
        elif gate.op == "OR":
            ref = b.or_(args[0], args[1])
        elif gate.op == "XOR":
            ref = b.xor(args[0], args[1])
        elif gate.op == "MUX":
            ref = b.mux(args[0], args[1], args[2])
        elif gate.op == "BUF":
            ref = args[0]
        else:
            raise ValueError(gate.op)
        remap[base.n_inputs + idx] = ref
    p = 0 if contract.n_inputs else CONST1
    np = b.not_(p)
    outputs = []
    for out in base.outputs:
        y = remap[out]
        outputs.append(b.or_(b.and_(y, p), b.and_(y, np)))
    return b.finish("redundant_partition", outputs)


def synthesize_contract(name: str, n_inputs: int, truth_outputs: Sequence[int]) -> Contract:
    contract = Contract(name, n_inputs, tuple(truth_outputs))
    candidates = [_sop_variant(contract), _anf_variant(contract), _shannon_variant(contract)]
    unique: dict[str, Variant] = {}
    for variant in candidates:
        if contract.verify_variant(variant):
            unique.setdefault(variant.fingerprint, variant)
    if len(unique) < 2:
        base = next(iter(unique.values()))
        fallback = _redundant_variant(contract, base)
        if contract.verify_variant(fallback):
            unique.setdefault(fallback.fingerprint, fallback)
    contract.variants = list(unique.values())
    if not contract.variants:
        raise AssertionError(f"no verified implementation for contract {name}")
    return contract


def make_contract(name: str, n_inputs: int, n_outputs: int, fn: Callable[[tuple[int, ...]], Sequence[int]]) -> Contract:
    return synthesize_contract(name, n_inputs, truth_from_callable(n_inputs, n_outputs, fn))
