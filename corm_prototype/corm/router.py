from __future__ import annotations

from dataclasses import dataclass

from .physical_cells import CellRole
from .physical_graph import PhysicalGraph, RoutedNet


@dataclass
class RoutingMetrics:
    attempts: int = 0
    failures: int = 0
    total_wire_cells_allocated: int = 0


class DeterministicRouter:
    """Route point-to-point nets through exclusive physical Wire Cells.

    The substrate is an explicit Cell adjacency graph rather than an implicit
    teleport edge.  Every successful route owns at least one Wire Cell and all
    consecutive path elements are joined by registered physical edges.
    """

    def __init__(self, graph: PhysicalGraph, wire_cells_per_route: int = 1):
        if wire_cells_per_route < 1:
            raise ValueError("a physical route needs at least one Wire Cell")
        self.graph = graph
        self.wire_cells_per_route = wire_cells_per_route
        self.metrics = RoutingMetrics()

    def route(
        self,
        source_cell: int,
        destination_cell: int,
        *,
        generation: int,
        logical_net_id: int | None,
        organ_id: int | None,
        kind: str,
        destination_port: str,
    ) -> RoutedNet | None:
        self.metrics.attempts += 1
        source = self.graph.cells.get(source_cell)
        destination = self.graph.cells.get(destination_cell)
        if (
            source is None
            or destination is None
            or source.failed
            or destination.failed
            or source.role in {CellRole.FREE, CellRole.RETIRED}
            or destination.role in {CellRole.FREE, CellRole.RETIRED}
        ):
            self.metrics.failures += 1
            return None

        net_id = self.graph.allocate_net_id()
        wires: list[int] = []
        connected: list[tuple[int, int]] = []
        try:
            for _ in range(self.wire_cells_per_route):
                wires.append(
                    self.graph.allocate_cell(
                        CellRole.WIRE,
                        organ_id=organ_id,
                        net_id=net_id,
                        operation="BUF",
                        generation=generation,
                    )
                )
            path = (source_cell, *wires, destination_cell)
            for a, b in zip(path, path[1:]):
                self.graph.connect(a, b)
                connected.append((a, b))

            source.output_ports.add(wires[0])
            for index, wire_id in enumerate(wires):
                previous = source_cell if index == 0 else wires[index - 1]
                following = destination_cell if index == len(wires) - 1 else wires[index + 1]
                wire = self.graph.cells[wire_id]
                wire.input_ports["in"] = previous
                wire.output_ports.add(following)
            if destination_port in destination.input_ports:
                raise ValueError(f"destination port already routed: {destination_port}")
            destination.input_ports[destination_port] = wires[-1]

            routed = RoutedNet(
                net_id=net_id,
                source_cell=source_cell,
                destination_cell=destination_cell,
                ordered_wire_cells=tuple(wires),
                generation=generation,
                logical_net_id=logical_net_id,
                organ_id=organ_id,
                kind=kind,
                destination_port=destination_port,
            )
            self.graph.routed_nets[net_id] = routed
            for a, b in zip(path, path[1:]):
                self.graph.edge_routes[(a, b) if a < b else (b, a)] = net_id
            if not self.graph.validate_route(routed):
                raise AssertionError("router created an invalid physical path")
            self.metrics.total_wire_cells_allocated += len(wires)
            return routed
        except (KeyError, MemoryError, ValueError, AssertionError):
            self.graph.routed_nets.pop(net_id, None)
            for a, b in connected:
                self.graph.edge_routes.pop((a, b) if a < b else (b, a), None)
            source.output_ports.difference_update(wires)
            if destination.input_ports.get(destination_port) in wires:
                destination.input_ports.pop(destination_port, None)
            for a, b in reversed(connected):
                self.graph.disconnect(a, b)
            path = (source_cell, *wires, destination_cell)
            for index, wire in enumerate(wires, start=1):
                touches_failed_edge = (
                    self.graph.edge_failed(path[index - 1], wire)
                    or self.graph.edge_failed(wire, path[index + 1])
                )
                self.graph.release_cell(wire, retire=touches_failed_edge)
            self.metrics.failures += 1
            return None

    def remove_route(self, net_id: int, *, retire: bool = False) -> None:
        route = self.graph.routed_nets.pop(net_id)
        path = route.path
        source = self.graph.cells[route.source_cell]
        destination = self.graph.cells[route.destination_cell]
        source.output_ports.discard(route.ordered_wire_cells[0])
        if destination.input_ports.get(route.destination_port) == route.ordered_wire_cells[-1]:
            destination.input_ports.pop(route.destination_port, None)
        for a, b in zip(path, path[1:]):
            self.graph.edge_routes.pop((a, b) if a < b else (b, a), None)
            self.graph.disconnect(a, b)
        for index, wire in enumerate(route.ordered_wire_cells, start=1):
            touches_failed_edge = (
                self.graph.edge_failed(path[index - 1], wire)
                or self.graph.edge_failed(wire, path[index + 1])
            )
            self.graph.release_cell(wire, retire=retire or touches_failed_edge)

    def statistics(self) -> dict[str, int | float]:
        lengths = [len(route.ordered_wire_cells) for route in self.graph.routed_nets.values()]
        return {
            "routing_attempts": self.metrics.attempts,
            "routing_failures": self.metrics.failures,
            "total_wire_cells_allocated_lifetime": self.metrics.total_wire_cells_allocated,
            "active_routed_nets": len(lengths),
            "active_wire_cells": sum(lengths),
            "total_route_hops": sum(length + 1 for length in lengths),
            "maximum_path_hops": max((length + 1 for length in lengths), default=0),
            "wire_cell_congestion": 1 if lengths else 0,
        }
