from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Iterable

from .physical_cells import CellRole, PhysicalCell


def edge_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


@dataclass(frozen=True)
class RoutedNet:
    net_id: int
    source_cell: int
    destination_cell: int
    ordered_wire_cells: tuple[int, ...]
    generation: int
    logical_net_id: int | None = None
    organ_id: int | None = None
    kind: str = "internal"
    destination_port: str = "in"

    @property
    def path(self) -> tuple[int, ...]:
        return (self.source_cell, *self.ordered_wire_cells, self.destination_cell)


class PhysicalGraph:
    """A bounded-degree physical Cell adjacency graph.

    Coordinates are stable substrate addresses.  Physical distance one is
    represented by membership in ``physical_edges``; no execution code may
    communicate between Cells without such an edge.
    """

    def __init__(self, capacity: int, max_degree: int = 6):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.max_degree = max_degree
        self.cells: dict[int, PhysicalCell] = {}
        self.physical_edges: set[tuple[int, int]] = set()
        self.failed_edges: set[tuple[int, int]] = set()
        self.routed_nets: dict[int, RoutedNet] = {}
        self.edge_routes: dict[tuple[int, int], int] = {}
        self.failed_cells: set[int] = set()
        self.retired_cells: set[int] = set()
        self._free_ids: list[int] = []
        self._next_cell_id = 0
        self._next_net_id = 0

    def _coordinate(self, cell_id: int) -> tuple[int, int, int]:
        side = max(2, int(self.capacity ** (1 / 3)) + 1)
        return cell_id % side, (cell_id // side) % side, cell_id // (side * side)

    def allocate_cell(
        self,
        role: CellRole,
        *,
        organ_id: int | None = None,
        net_id: int | None = None,
        operation: str = "",
        generation: int = 0,
        value: int = 0,
    ) -> int:
        cell_id = None
        while self._free_ids and cell_id is None:
            candidate = heapq.heappop(self._free_ids)
            if self.cells[candidate].role == CellRole.FREE:
                cell_id = candidate
        if cell_id is None:
            if self._next_cell_id >= self.capacity:
                raise MemoryError("physical Cell capacity exhausted")
            cell_id = self._next_cell_id
            self._next_cell_id += 1
        self.cells[cell_id] = PhysicalCell(
            cell_id=cell_id,
            coordinate=self._coordinate(cell_id),
            role=role,
            organ_id=organ_id,
            net_id=net_id,
            operation=operation,
            generation=generation,
            value=value,
            valid=role == CellRole.STATE,
        )
        return cell_id

    def allocate_net_id(self) -> int:
        net_id = self._next_net_id
        self._next_net_id += 1
        return net_id

    def connect(self, a: int, b: int) -> None:
        if a == b:
            raise ValueError("self edge is not a physical link")
        if a not in self.cells or b not in self.cells:
            raise KeyError("physical edge endpoint does not exist")
        edge = edge_key(a, b)
        if edge in self.physical_edges:
            return
        if edge in self.failed_edges:
            raise MemoryError("permanently failed physical edge cannot be rebound")
        if len(self.cells[a].neighbors) >= self.max_degree:
            raise MemoryError(f"Cell {a} degree bound exceeded")
        if len(self.cells[b].neighbors) >= self.max_degree:
            raise MemoryError(f"Cell {b} degree bound exceeded")
        self.physical_edges.add(edge)
        self.cells[a].neighbors.add(b)
        self.cells[b].neighbors.add(a)

    def disconnect(self, a: int, b: int) -> None:
        self.physical_edges.discard(edge_key(a, b))
        if a in self.cells:
            self.cells[a].neighbors.discard(b)
        if b in self.cells:
            self.cells[b].neighbors.discard(a)

    def adjacent(self, a: int, b: int) -> bool:
        return edge_key(a, b) in self.physical_edges

    def edge_failed(self, a: int, b: int) -> bool:
        return edge_key(a, b) in self.failed_edges

    def fail_cells_permanently(self, cell_ids: Iterable[int]) -> set[int]:
        failed: set[int] = set()
        for cell_id in cell_ids:
            cell = self.cells.get(cell_id)
            if cell is None or cell.role in {CellRole.FREE, CellRole.RETIRED}:
                continue
            cell.failed = True
            cell.original_role = cell.original_role or cell.role
            cell.role = CellRole.FAILED
            self.failed_cells.add(cell_id)
            failed.add(cell_id)
        return failed

    def fail_edges_permanently(self, edges: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
        failed: set[tuple[int, int]] = set()
        for a, b in edges:
            edge = edge_key(a, b)
            if edge in self.physical_edges:
                self.failed_edges.add(edge)
                failed.add(edge)
        return failed

    def release_cell(self, cell_id: int, *, retire: bool = False) -> None:
        cell = self.cells[cell_id]
        for neighbor in tuple(cell.neighbors):
            self.disconnect(cell_id, neighbor)
        cell.input_ports.clear()
        cell.output_ports.clear()
        if cell.failed:
            return
        if retire:
            cell.role = CellRole.RETIRED
            self.retired_cells.add(cell_id)
            return
        cell.role = CellRole.FREE
        cell.organ_id = None
        cell.net_id = None
        cell.operation = ""
        cell.value = 0
        cell.valid = False
        heapq.heappush(self._free_ids, cell_id)

    def retire_free_cells(self, cell_ids: Iterable[int]) -> None:
        for cell_id in cell_ids:
            cell = self.cells.get(cell_id)
            if cell is not None and cell.role == CellRole.FREE:
                cell.role = CellRole.RETIRED
                self.retired_cells.add(cell_id)

    def active_cell_ids(self) -> set[int]:
        return {
            cell_id
            for cell_id, cell in self.cells.items()
            if cell.role not in {CellRole.FREE, CellRole.RETIRED}
        }

    def active_wire_cells(self) -> set[int]:
        return {
            wire
            for route in self.routed_nets.values()
            for wire in route.ordered_wire_cells
        }

    def validate_route(self, route: RoutedNet, *, require_healthy: bool = True) -> bool:
        if not route.ordered_wire_cells:
            return False
        path = route.path
        if len(set(route.ordered_wire_cells)) != len(route.ordered_wire_cells):
            return False
        for wire in route.ordered_wire_cells:
            cell = self.cells.get(wire)
            if cell is None:
                return False
            role = cell.original_role if cell.role == CellRole.FAILED else cell.role
            if role != CellRole.WIRE or cell.net_id != route.net_id:
                return False
            if require_healthy and cell.failed:
                return False
        for a, b in zip(path, path[1:]):
            if not self.adjacent(a, b):
                return False
            if require_healthy and self.edge_failed(a, b):
                return False
        return True

    def audit(self) -> dict[str, int | bool]:
        degrees_ok = all(len(cell.neighbors) <= self.max_degree for cell in self.cells.values())
        routes_complete = all(self.validate_route(route, require_healthy=False) for route in self.routed_nets.values())
        wire_uses: list[int] = [
            wire for route in self.routed_nets.values() for wire in route.ordered_wire_cells
        ]
        exclusive_wires = len(wire_uses) == len(set(wire_uses))
        no_direct_gate_edges = all(
            not (
                (self.cells[a].original_role or self.cells[a].role) == CellRole.GATE
                and (self.cells[b].original_role or self.cells[b].role) == CellRole.GATE
            )
            for a, b in self.physical_edges
        )
        return {
            "degree_bound_ok": degrees_ok,
            "routes_complete": routes_complete,
            "wire_ownership_exclusive": exclusive_wires,
            "no_direct_gate_edges": no_direct_gate_edges,
            "active_cells": len(self.active_cell_ids()),
            "wire_cells": len(self.active_wire_cells()),
            "physical_edges": len(self.physical_edges),
            "failed_cells": len(self.failed_cells),
            "failed_edges": len(self.failed_edges),
            "maximum_degree": max((len(cell.neighbors) for cell in self.cells.values()), default=0),
        }
