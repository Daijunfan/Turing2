from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import random
from typing import Mapping, Sequence

from .physical_cells import CellRole, NeighborMessage, PhysicalCell, cell_transition
from .physical_graph import PhysicalGraph, edge_key


@dataclass(frozen=True)
class PhysicalExecutionResult:
    outputs: tuple[int, ...]
    output_valid: tuple[bool, ...]
    valid: bool
    reason: str
    epoch: int
    messages: int
    activations: int
    stable_rounds: int
    future_epoch_reads: int = 0
    stale_epoch_reads: int = 0


class PhysicalExecutor:
    def __init__(self, graph: PhysicalGraph):
        self.graph = graph

    @staticmethod
    def _execute_view(
        base_cells: Mapping[int, PhysicalCell],
        physical_edges: set[tuple[int, int]],
        failed_edges: set[tuple[int, int]],
        injections: Mapping[int, int],
        output_cells: Sequence[int],
        universe_mask: int,
        epoch: int,
        schedule: str,
        seed: int,
    ) -> tuple[PhysicalExecutionResult, dict[int, PhysicalCell]]:
        frames: dict[int, PhysicalCell] = {}
        rng = random.Random(seed)
        fifo: deque[tuple[int, NeighborMessage]] = deque()
        random_queue: list[tuple[int, NeighborMessage]] = []
        message_count = 0
        activations = 0

        def frame(cell_id: int) -> PhysicalCell:
            if cell_id not in frames:
                frames[cell_id] = base_cells[cell_id].execution_copy(epoch, universe_mask)
            return frames[cell_id]

        def enqueue(target: int, message: NeighborMessage) -> None:
            nonlocal message_count
            if target not in base_cells:
                return
            edge = edge_key(message.sender, target)
            if edge not in physical_edges or edge in failed_edges:
                return
            message_count += 1
            if schedule == "fifo":
                fifo.append((target, message))
            else:
                random_queue.append((target, message))

        def emit_source(cell_id: int, value: int) -> None:
            state = frame(cell_id)
            state.value = value & universe_mask
            state.valid = not state.failed and state.role != CellRole.FAILED
            state.emitted_epoch = epoch
            message = NeighborMessage(cell_id, epoch, state.value, state.valid)
            for target in sorted(state.output_ports):
                enqueue(target, message)
            if state.role != CellRole.STATE:
                state.value = 0

        for cell_id, value in injections.items():
            if cell_id not in base_cells:
                raise KeyError(f"input Cell {cell_id} is absent from execution view")
            emit_source(cell_id, value)
        for cell_id, cell in base_cells.items():
            if cell.role == CellRole.CONST:
                emit_source(cell_id, universe_mask if cell.operation == "1" else 0)
            elif cell.role == CellRole.STATE and cell_id not in injections:
                emit_source(cell_id, cell.value)

        maximum_activations = max(1, len(base_cells) * 12)
        while fifo or random_queue:
            if activations >= maximum_activations:
                break
            if schedule == "fifo":
                target, message = fifo.popleft()
            elif schedule == "random":
                index = rng.randrange(len(random_queue))
                target, message = random_queue.pop(index)
            else:
                raise ValueError(f"unknown schedule: {schedule}")
            activations += 1
            state, outgoing = cell_transition(frame(target), (message,))
            frames[target] = state
            for item in outgoing:
                enqueue(item.target, item.message)
            if state.emitted_epoch == epoch and state.role not in {
                CellRole.OUTPUT_BOUNDARY,
                CellRole.STATE,
            }:
                state.value = 0
                state.inbox.clear()

        outputs: list[int] = []
        valid: list[bool] = []
        reached: list[bool] = []
        for cell_id in output_cells:
            state = frames.get(cell_id)
            is_reached = state is not None and state.emitted_epoch == epoch
            is_valid = bool(is_reached and state.valid)
            reached.append(is_reached)
            valid.append(is_valid)
            outputs.append(0 if state is None else state.value & universe_mask)
        all_valid = all(valid)
        reason = "OK" if all_valid else "INVALID" if all(reached) else "TIMEOUT"
        result = PhysicalExecutionResult(
            outputs=tuple(outputs),
            output_valid=tuple(valid),
            valid=all_valid,
            reason=reason,
            epoch=epoch,
            messages=message_count,
            activations=activations,
            stable_rounds=activations,
        )
        return result, frames

    def execute(
        self,
        injections: Mapping[int, int],
        output_cells: Sequence[int],
        universe_mask: int,
        epoch: int,
        *,
        schedule: str = "fifo",
        seed: int = 0,
    ) -> PhysicalExecutionResult:
        active = self.graph.active_cell_ids()
        cells = {cell_id: self.graph.cells[cell_id] for cell_id in active}
        edges = {
            edge for edge in self.graph.physical_edges if edge[0] in active and edge[1] in active
        }
        result, frames = self._execute_view(
            cells,
            edges,
            self.graph.failed_edges,
            injections,
            output_cells,
            universe_mask,
            epoch,
            schedule,
            seed,
        )
        for cell_id, state in frames.items():
            base = self.graph.cells[cell_id]
            base.epoch = epoch
            base.valid = state.valid
            if base.role in {CellRole.OUTPUT_BOUNDARY, CellRole.STATE}:
                base.value = state.value
        return result

    def execute_subgraph(
        self,
        cell_ids: set[int],
        route_ids: Sequence[int],
        input_cells: Sequence[int],
        output_cells: Sequence[int],
        input_values: Sequence[int],
        universe_mask: int,
        epoch: int,
        *,
        schedule: str = "fifo",
        seed: int = 0,
    ) -> PhysicalExecutionResult:
        if len(input_cells) != len(input_values):
            raise ValueError("subgraph input arity mismatch")
        selected = set(cell_ids) | set(input_cells) | set(output_cells)
        routes = [self.graph.routed_nets[route_id] for route_id in route_ids]
        for route in routes:
            selected.update(route.path)

        cells: dict[int, PhysicalCell] = {}
        for cell_id in selected:
            original = self.graph.cells[cell_id]
            copied = original.execution_copy(epoch, universe_mask)
            copied.input_ports = {}
            copied.output_ports = set()
            copied.neighbors = set()
            cells[cell_id] = copied

        edges: set[tuple[int, int]] = set()
        for route in routes:
            path = route.path
            for a, b in zip(path, path[1:]):
                edge = edge_key(a, b)
                if edge in self.graph.physical_edges:
                    edges.add(edge)
                    cells[a].neighbors.add(b)
                    cells[b].neighbors.add(a)
            cells[route.source_cell].output_ports.add(route.ordered_wire_cells[0])
            for index, wire_id in enumerate(route.ordered_wire_cells):
                previous = route.source_cell if index == 0 else route.ordered_wire_cells[index - 1]
                following = route.destination_cell if index == len(route.ordered_wire_cells) - 1 else route.ordered_wire_cells[index + 1]
                cells[wire_id].input_ports["in"] = previous
                cells[wire_id].output_ports.add(following)
            cells[route.destination_cell].input_ports[route.destination_port] = route.ordered_wire_cells[-1]

        for cell_id in input_cells:
            cells[cell_id].role = CellRole.INPUT_BOUNDARY
            cells[cell_id].failed = self.graph.cells[cell_id].failed
            cells[cell_id].input_ports.clear()
        for cell_id in output_cells:
            cells[cell_id].role = CellRole.OUTPUT_BOUNDARY
            cells[cell_id].output_ports.clear()

        result, _ = self._execute_view(
            cells,
            edges,
            self.graph.failed_edges,
            dict(zip(input_cells, input_values)),
            output_cells,
            universe_mask,
            epoch,
            schedule,
            seed,
        )
        return result
