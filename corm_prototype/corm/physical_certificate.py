from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Sequence

from .core import Contract
from .physical_cells import CellRole
from .physical_executor import PhysicalExecutor
from .physical_graph import PhysicalGraph


@dataclass
class PhysicalOrgan:
    organ_id: int
    gate_cells: dict[int, int]
    internal_wire_paths: tuple[int, ...]
    input_boundary_cells: tuple[int, ...]
    output_boundary_cells: tuple[int, ...]
    contract: Contract
    generation: int
    variant_index: int
    router_cells: tuple[int, ...] = ()
    const_cells: tuple[int, ...] = ()
    certificate: "PhysicalCertificate | None" = None

    @property
    def body_cells(self) -> set[int]:
        return set(self.gate_cells.values()) | set(self.router_cells) | set(self.const_cells)


@dataclass(frozen=True)
class PhysicalCertificate:
    contract_hash: str
    physical_subgraph_digest: str
    boundary_mapping: dict[str, tuple[int, ...]]
    physical_truth_outputs: tuple[int, ...]
    verifier_version: str
    generation: int


VERIFIER_VERSION = "ecorm-physical-replay-v0.2"


def physical_subgraph_digest(graph: PhysicalGraph, organ: PhysicalOrgan) -> str:
    route_ids = sorted(organ.internal_wire_paths)
    routes = [graph.routed_nets[route_id] for route_id in route_ids]
    cell_ids = set(organ.body_cells)
    cell_ids.update(organ.input_boundary_cells)
    cell_ids.update(organ.output_boundary_cells)
    for route in routes:
        cell_ids.update(route.path)
    payload = {
        "organ_id": organ.organ_id,
        "generation": organ.generation,
        "variant_index": organ.variant_index,
        "boundaries": {
            "inputs": organ.input_boundary_cells,
            "outputs": organ.output_boundary_cells,
        },
        "cells": [
            {
                "cell_id": cell_id,
                "coordinate": graph.cells[cell_id].coordinate,
                "role": graph.cells[cell_id].role.value,
                "failed": graph.cells[cell_id].failed,
                "organ_id": graph.cells[cell_id].organ_id,
                "net_id": graph.cells[cell_id].net_id,
                "operation": graph.cells[cell_id].operation,
                "generation": graph.cells[cell_id].generation,
            }
            for cell_id in sorted(cell_ids)
        ],
        "routes": [
            {
                "net_id": route.net_id,
                "source": route.source_cell,
                "destination": route.destination_cell,
                "wires": route.ordered_wire_cells,
                "logical_net_id": route.logical_net_id,
                "organ_id": route.organ_id,
                "kind": route.kind,
                "destination_port": route.destination_port,
                "generation": route.generation,
            }
            for route in routes
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _ownership_valid(graph: PhysicalGraph, organ: PhysicalOrgan) -> bool:
    owned = organ.body_cells | set(organ.input_boundary_cells) | set(organ.output_boundary_cells)
    if any(graph.cells[cell_id].organ_id != organ.organ_id for cell_id in owned):
        return False
    wire_cells: list[int] = []
    for route_id in organ.internal_wire_paths:
        route = graph.routed_nets.get(route_id)
        if route is None or route.organ_id != organ.organ_id or route.kind != "internal":
            return False
        if not graph.validate_route(route):
            return False
        wire_cells.extend(route.ordered_wire_cells)
        if any(graph.cells[cell_id].organ_id != organ.organ_id for cell_id in route.ordered_wire_cells):
            return False
    return len(wire_cells) == len(set(wire_cells))


def replay_physical_organ(
    graph: PhysicalGraph,
    organ: PhysicalOrgan,
    *,
    schedule: str = "fifo",
    seed: int = 0,
) -> tuple[int, ...] | None:
    result = PhysicalExecutor(graph).execute_subgraph(
        organ.body_cells,
        organ.internal_wire_paths,
        organ.input_boundary_cells,
        organ.output_boundary_cells,
        organ.contract.input_patterns(),
        organ.contract.local_mask,
        organ.generation,
        schedule=schedule,
        seed=seed,
    )
    return result.outputs if result.valid else None


def create_physical_certificate(graph: PhysicalGraph, organ: PhysicalOrgan) -> PhysicalCertificate:
    if not _ownership_valid(graph, organ):
        raise AssertionError("physical organ ownership or routing is invalid")
    outputs = replay_physical_organ(graph, organ)
    if outputs is None or outputs != organ.contract.truth_outputs:
        raise AssertionError("physical organ does not implement its boundary contract")
    return PhysicalCertificate(
        contract_hash=organ.contract.contract_hash,
        physical_subgraph_digest=physical_subgraph_digest(graph, organ),
        boundary_mapping={
            "inputs": organ.input_boundary_cells,
            "outputs": organ.output_boundary_cells,
        },
        physical_truth_outputs=outputs,
        verifier_version=VERIFIER_VERSION,
        generation=organ.generation,
    )


def verify_physical_certificate(
    graph: PhysicalGraph,
    organ: PhysicalOrgan,
    certificate: PhysicalCertificate | None = None,
    *,
    schedules: Sequence[tuple[str, int]] = (("fifo", 0),),
) -> bool:
    cert = certificate or organ.certificate
    if cert is None:
        return False
    if (
        cert.contract_hash != organ.contract.contract_hash
        or cert.generation != organ.generation
        or cert.verifier_version != VERIFIER_VERSION
        or cert.boundary_mapping.get("inputs") != organ.input_boundary_cells
        or cert.boundary_mapping.get("outputs") != organ.output_boundary_cells
        or cert.physical_subgraph_digest != physical_subgraph_digest(graph, organ)
        or not _ownership_valid(graph, organ)
    ):
        return False
    for schedule, seed in schedules:
        outputs = replay_physical_organ(graph, organ, schedule=schedule, seed=seed)
        if outputs != organ.contract.truth_outputs or outputs != cert.physical_truth_outputs:
            return False
    return True
