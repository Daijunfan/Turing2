from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable, Sequence
import json
import math

from .core import Contract, Variant, CONST0, CONST1


@dataclass
class OrganInstance:
    organ_id: int
    name: str
    contract: Contract
    input_nets: tuple[int, ...]
    output_nets: tuple[int, ...]
    module_path: tuple[str, ...] = ()
    active_variant: int = 0

    @property
    def variant(self) -> Variant:
        return self.contract.variants[self.active_variant]


@dataclass
class CertificateNode:
    node_id: int
    left: int | None
    right: int | None
    parent: int | None = None
    leaf_organ: int | None = None
    digest: str = ""


class CertificateTree:
    def __init__(self, organs: Sequence[OrganInstance]):
        self.nodes: dict[int, CertificateNode] = {}
        self.leaf_for_organ: dict[int, int] = {}
        next_id = 0
        level: list[int] = []
        for organ in organs:
            node_id = next_id
            next_id += 1
            node = CertificateNode(node_id, None, None, leaf_organ=organ.organ_id)
            self.nodes[node_id] = node
            self.leaf_for_organ[organ.organ_id] = node_id
            level.append(node_id)
        if not level:
            self.root = None
            return
        while len(level) > 1:
            new_level: list[int] = []
            it = iter(level)
            for left in it:
                right = next(it, None)
                if right is None:
                    new_level.append(left)
                    continue
                node_id = next_id
                next_id += 1
                node = CertificateNode(node_id, left, right)
                self.nodes[node_id] = node
                self.nodes[left].parent = node_id
                self.nodes[right].parent = node_id
                new_level.append(node_id)
            level = new_level
        self.root = level[0]
        self.recompute_all(organs)

    @staticmethod
    def _leaf_digest(organ: OrganInstance) -> str:
        payload = (
            organ.contract.contract_hash
            + organ.variant.fingerprint
            + repr(organ.input_nets)
            + repr(organ.output_nets)
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def recompute_all(self, organs: Sequence[OrganInstance]) -> None:
        by_id = {o.organ_id: o for o in organs}
        for node in self.nodes.values():
            if node.leaf_organ is not None:
                node.digest = self._leaf_digest(by_id[node.leaf_organ])
        for node_id in sorted(self.nodes, reverse=True):
            node = self.nodes[node_id]
            if node.left is not None and node.right is not None:
                node.digest = sha256(
                    (self.nodes[node.left].digest + self.nodes[node.right].digest).encode("utf-8")
                ).hexdigest()

    def update_organ(self, organ: OrganInstance) -> int:
        node_id = self.leaf_for_organ[organ.organ_id]
        self.nodes[node_id].digest = self._leaf_digest(organ)
        touched = 1
        parent = self.nodes[node_id].parent
        while parent is not None:
            node = self.nodes[parent]
            node.digest = sha256(
                (self.nodes[node.left].digest + self.nodes[node.right].digest).encode("utf-8")
            ).hexdigest()
            touched += 1
            parent = node.parent
        return touched

    @property
    def root_digest(self) -> str:
        if self.root is None:
            return sha256(b"").hexdigest()
        return self.nodes[self.root].digest


@dataclass
class Program:
    name: str
    input_nets: list[int] = field(default_factory=list)
    input_names: list[str] = field(default_factory=list)
    constant_nets: dict[int, int] = field(default_factory=dict)
    organs: list[OrganInstance] = field(default_factory=list)
    output_nets: list[int] = field(default_factory=list)
    output_names: list[str] = field(default_factory=list)
    _next_net: int = 0
    _next_organ: int = 0
    certificate_tree: CertificateTree | None = None

    def new_input(self, name: str) -> int:
        net = self._next_net
        self._next_net += 1
        self.input_nets.append(net)
        self.input_names.append(name)
        return net

    def const(self, value: int) -> int:
        for net, val in self.constant_nets.items():
            if val == int(bool(value)):
                return net
        net = self._next_net
        self._next_net += 1
        self.constant_nets[net] = int(bool(value))
        return net

    def add_organ(
        self,
        name: str,
        contract: Contract,
        input_nets: Sequence[int],
        module_path: Sequence[str] = (),
        active_variant: int = 0,
    ) -> tuple[int, ...]:
        if len(input_nets) != contract.n_inputs:
            raise ValueError(f"{name}: contract expects {contract.n_inputs} inputs")
        outputs = tuple(range(self._next_net, self._next_net + contract.n_outputs))
        self._next_net += contract.n_outputs
        organ = OrganInstance(
            self._next_organ,
            name,
            contract,
            tuple(input_nets),
            outputs,
            tuple(module_path),
            active_variant,
        )
        self._next_organ += 1
        self.organs.append(organ)
        return outputs

    def set_outputs(self, nets: Sequence[int], names: Sequence[str] | None = None) -> None:
        self.output_nets = list(nets)
        self.output_names = list(names) if names is not None else [f"out{i}" for i in range(len(nets))]

    def finalize(self) -> "Program":
        self.certificate_tree = CertificateTree(self.organs)
        return self

    def verify_all_contracts(self) -> bool:
        return all(organ.contract.verify_variant(organ.variant) for organ in self.organs)

    @property
    def active_gate_count(self) -> int:
        return sum(organ.variant.gate_count for organ in self.organs)

    @property
    def max_gate_count(self) -> int:
        return sum(max(v.gate_count for v in organ.contract.variants) for organ in self.organs)

    @property
    def min_gate_count(self) -> int:
        return sum(min(v.gate_count for v in organ.contract.variants) for organ in self.organs)

    def set_variant_policy(self, policy: str, seed: int = 0) -> None:
        import random

        rng = random.Random(seed)
        for organ in self.organs:
            verified = [i for i, v in enumerate(organ.contract.variants) if organ.contract.verify_variant(v)]
            if policy == "min":
                organ.active_variant = min(verified, key=lambda i: organ.contract.variants[i].gate_count)
            elif policy == "max":
                organ.active_variant = max(verified, key=lambda i: organ.contract.variants[i].gate_count)
            elif policy == "random":
                organ.active_variant = rng.choice(verified)
            else:
                raise ValueError(policy)
        if self.certificate_tree is not None:
            self.certificate_tree.recompute_all(self.organs)

    def evaluate_bits(self, input_values: Sequence[int], universe_mask: int, semantic: bool = False) -> tuple[int, ...]:
        if len(input_values) != len(self.input_nets):
            raise ValueError("program input arity mismatch")
        nets: dict[int, int] = {}
        for net, value in zip(self.input_nets, input_values):
            nets[net] = value & universe_mask
        for net, value in self.constant_nets.items():
            nets[net] = universe_mask if value else 0
        for organ in self.organs:
            ins = [nets[n] for n in organ.input_nets]
            outs = (
                organ.contract.evaluate_bits(ins, universe_mask)
                if semantic
                else organ.variant.evaluate_bits(ins, universe_mask)
            )
            for net, value in zip(organ.output_nets, outs):
                nets[net] = value & universe_mask
        return tuple(nets[n] for n in self.output_nets)

    def evaluate_scalar(self, input_values: Sequence[int], semantic: bool = False) -> tuple[int, ...]:
        return tuple(v & 1 for v in self.evaluate_bits(input_values, 1, semantic=semantic))

    def semantic_fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "inputs": self.input_names,
            "constants": self.constant_nets,
            "organs": [
                {
                    "contract": o.contract.contract_hash,
                    "inputs": o.input_nets,
                    "outputs": o.output_nets,
                    "module": o.module_path,
                }
                for o in self.organs
            ],
            "outputs": self.output_nets,
        }
        return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def implementation_fingerprint(self) -> str:
        payload = [(o.organ_id, o.variant.fingerprint) for o in self.organs]
        return sha256(json.dumps(payload).encode("utf-8")).hexdigest()


def make_input_patterns(n_inputs: int) -> tuple[list[int], int]:
    rows = 1 << n_inputs
    universe_mask = (1 << rows) - 1
    patterns: list[int] = []
    for i in range(n_inputs):
        period = 1 << i
        block = ((1 << period) - 1) << period
        value = 0
        shift = 0
        while shift < rows:
            value |= block << shift
            shift += 2 * period
        patterns.append(value & universe_mask)
    return patterns, universe_mask
