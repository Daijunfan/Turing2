from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import random
from typing import Iterable, Sequence

from .core import CONST0, CONST1, Variant
from .physical_cells import CellRole
from .physical_certificate import (
    PhysicalCertificate,
    PhysicalOrgan,
    create_physical_certificate,
    verify_physical_certificate,
)
from .physical_executor import PhysicalExecutionResult, PhysicalExecutor
from .physical_graph import PhysicalGraph, RoutedNet, edge_key
from .program import OrganInstance, Program
from .router import DeterministicRouter


@dataclass
class ExternalNetwork:
    route_ids: tuple[int, ...]
    router_cells: tuple[int, ...]
    generation: int


@dataclass
class PhysicalRepairRecord:
    organ_id: int
    old_variant: str
    new_variant: str
    old_gate_cells: int
    new_gate_cells: int
    old_wire_cells: int
    new_wire_cells: int
    generation: int
    certificate_verified: bool
    structural_change: bool


class EmbodiedRuntime:
    """E-CORM v0.2 physical data plane and central repair coordinator."""

    def __init__(
        self,
        program: Program,
        *,
        capacity_factor: float = 3.0,
        capacity: int | None = None,
        seed: int = 0,
        wire_cells_per_route: int = 1,
    ):
        self.program = program
        self.seed = seed
        self.rng = random.Random(seed)
        estimate = self._estimate_maximum_active_cells(program, wire_cells_per_route)
        graph_capacity = capacity or max(512, math.ceil(estimate * capacity_factor))
        self.graph = PhysicalGraph(graph_capacity)
        self.router = DeterministicRouter(self.graph, wire_cells_per_route)
        self.executor = PhysicalExecutor(self.graph)
        self.generation = 0
        self.epoch = 0
        self.organs: dict[int, PhysicalOrgan] = {}
        self.external_network: ExternalNetwork | None = None
        self.state_feedback_network: ExternalNetwork | None = None
        self.state_bindings: dict[int, int] = {}
        self.input_boundary_cells: tuple[int, ...] = ()
        self.output_boundary_cells: tuple[int, ...] = ()
        self.constant_cells: dict[int, int] = {}
        self.pending_fault_organs: set[int] = set()
        self.pending_external_repair = False
        self.repair_log: list[PhysicalRepairRecord] = []
        self.original_gate_cells: set[int] = set()
        self.original_wire_cells: set[int] = set()
        self.original_state_cells: set[int] = set()
        self._deploy_initial()

    @staticmethod
    def _fanout_cost(count: int, wire_cells_per_route: int) -> int:
        if count <= 0:
            return 0
        routes = 1 if count == 1 else 2 * count - 1
        routers = max(0, count - 1)
        return routers + routes * wire_cells_per_route

    @classmethod
    def _variant_cell_estimate(
        cls,
        variant: Variant,
        n_outputs: int,
        wire_cells_per_route: int,
    ) -> int:
        fanouts: dict[int, int] = defaultdict(int)
        constants: set[int] = set()
        for gate in variant.gates:
            for source in gate.args:
                fanouts[source] += 1
                if source in {CONST0, CONST1}:
                    constants.add(source)
        for source in variant.outputs:
            fanouts[source] += 1
            if source in {CONST0, CONST1}:
                constants.add(source)
        return (
            variant.gate_count
            + variant.n_inputs
            + n_outputs
            + len(constants)
            + sum(cls._fanout_cost(count, wire_cells_per_route) for count in fanouts.values())
        )

    @classmethod
    def _estimate_maximum_active_cells(cls, program: Program, wire_cells_per_route: int) -> int:
        organ_cells = sum(
            max(
                cls._variant_cell_estimate(variant, organ.contract.n_outputs, wire_cells_per_route)
                for variant in organ.contract.variants
            )
            for organ in program.organs
        )
        consumers: dict[int, int] = defaultdict(int)
        for organ in program.organs:
            for net in organ.input_nets:
                consumers[net] += 1
        for net in program.output_nets:
            consumers[net] += 1
        external = (
            len(program.input_nets)
            + len(program.output_nets)
            + len(program.constant_nets)
            + sum(cls._fanout_cost(count, wire_cells_per_route) for count in consumers.values())
        )
        return organ_cells + external + 64

    def _deploy_initial(self) -> None:
        self.input_boundary_cells = tuple(
            self.graph.allocate_cell(
                CellRole.INPUT_BOUNDARY,
                net_id=net,
                operation="INPUT",
                generation=0,
            )
            for net in self.program.input_nets
        )
        self.constant_cells = {
            net: self.graph.allocate_cell(
                CellRole.CONST,
                net_id=net,
                operation=str(value),
                generation=0,
            )
            for net, value in self.program.constant_nets.items()
        }
        self.output_boundary_cells = tuple(
            self.graph.allocate_cell(
                CellRole.OUTPUT_BOUNDARY,
                net_id=net,
                operation="OUTPUT",
                generation=0,
            )
            for net in self.program.output_nets
        )
        for organ in self.program.organs:
            physical = self._build_physical_organ(organ, organ.active_variant, generation=0)
            self.organs[organ.organ_id] = physical
        self.external_network = self._build_external_network(generation=0)
        self.original_gate_cells = self.active_gate_cells.copy()
        self.original_wire_cells = self.active_wire_cells.copy()

    def _route_fanout(
        self,
        source: int,
        consumers: Sequence[tuple[int, str]],
        *,
        generation: int,
        logical_net_id: int | None,
        organ_id: int | None,
        kind: str,
        route_ids: list[int],
        router_cells: list[int],
    ) -> None:
        if not consumers:
            return

        def connect(a: int, destination: tuple[int, str]) -> None:
            route = self.router.route(
                a,
                destination[0],
                generation=generation,
                logical_net_id=logical_net_id,
                organ_id=organ_id,
                kind=kind,
                destination_port=destination[1],
            )
            if route is None:
                raise MemoryError("no legal physical route")
            route_ids.append(route.net_id)

        if len(consumers) == 1:
            connect(source, consumers[0])
            return

        routers = [
            self.graph.allocate_cell(
                CellRole.ROUTER,
                organ_id=organ_id,
                net_id=logical_net_id,
                operation="BUF",
                generation=generation,
            )
            for _ in range(len(consumers) - 1)
        ]
        router_cells.extend(routers)
        connect(source, (routers[0], "in"))
        for index, router_cell in enumerate(routers):
            if index < len(routers) - 1:
                connect(router_cell, consumers[index])
                connect(router_cell, (routers[index + 1], "in"))
            else:
                connect(router_cell, consumers[-2])
                connect(router_cell, consumers[-1])

    def _cleanup(self, route_ids: Iterable[int], cells: Iterable[int], *, retire: bool = False) -> None:
        for route_id in reversed(tuple(route_ids)):
            if route_id in self.graph.routed_nets:
                self.router.remove_route(route_id, retire=retire)
        for cell_id in tuple(cells):
            cell = self.graph.cells.get(cell_id)
            if cell is not None and cell.role not in {CellRole.FREE, CellRole.RETIRED}:
                self.graph.release_cell(cell_id, retire=retire)

    def _build_physical_organ(
        self,
        organ: OrganInstance,
        variant_index: int,
        *,
        generation: int,
        boundary_cells: tuple[tuple[int, ...], tuple[int, ...]] | None = None,
    ) -> PhysicalOrgan:
        variant = organ.contract.variants[variant_index]
        created_cells: list[int] = []
        routes: list[int] = []
        routers: list[int] = []
        if boundary_cells is None:
            inputs = tuple(
                self.graph.allocate_cell(
                    CellRole.ROUTER,
                    organ_id=organ.organ_id,
                    net_id=net,
                    operation="BUF",
                    generation=generation,
                )
                for net in organ.input_nets
            )
            outputs = tuple(
                self.graph.allocate_cell(
                    CellRole.ROUTER,
                    organ_id=organ.organ_id,
                    net_id=net,
                    operation="BUF",
                    generation=generation,
                )
                for net in organ.output_nets
            )
            created_cells.extend(inputs)
            created_cells.extend(outputs)
            owns_boundaries = True
        else:
            inputs, outputs = boundary_cells
            owns_boundaries = False

        gate_cells: dict[int, int] = {}
        const_cells: dict[int, int] = {}
        try:
            for gate_index, gate in enumerate(variant.gates):
                ref = variant.n_inputs + gate_index
                cell_id = self.graph.allocate_cell(
                    CellRole.GATE,
                    organ_id=organ.organ_id,
                    operation=gate.op,
                    generation=generation,
                )
                gate_cells[ref] = cell_id
                created_cells.append(cell_id)

            source_for_ref: dict[int, int] = {index: cell for index, cell in enumerate(inputs)}
            source_for_ref.update(gate_cells)
            used_constants = {
                ref
                for gate in variant.gates
                for ref in gate.args
                if ref in {CONST0, CONST1}
            } | {ref for ref in variant.outputs if ref in {CONST0, CONST1}}
            for ref in sorted(used_constants):
                cell_id = self.graph.allocate_cell(
                    CellRole.CONST,
                    organ_id=organ.organ_id,
                    operation="1" if ref == CONST1 else "0",
                    generation=generation,
                )
                const_cells[ref] = cell_id
                source_for_ref[ref] = cell_id
                created_cells.append(cell_id)

            fanouts: dict[int, list[tuple[int, str]]] = defaultdict(list)
            for gate_index, gate in enumerate(variant.gates):
                destination = gate_cells[variant.n_inputs + gate_index]
                for port, source_ref in enumerate(gate.args):
                    fanouts[source_for_ref[source_ref]].append(
                        (destination, f"p{port:02d}:g{generation}")
                    )
            for output_index, source_ref in enumerate(variant.outputs):
                fanouts[source_for_ref[source_ref]].append(
                    (outputs[output_index], f"internal:g{generation}:o{output_index}")
                )

            for source, consumers in fanouts.items():
                self._route_fanout(
                    source,
                    consumers,
                    generation=generation,
                    logical_net_id=None,
                    organ_id=organ.organ_id,
                    kind="internal",
                    route_ids=routes,
                    router_cells=routers,
                )
            created_cells.extend(routers)
            physical = PhysicalOrgan(
                organ_id=organ.organ_id,
                gate_cells=gate_cells,
                internal_wire_paths=tuple(routes),
                input_boundary_cells=inputs,
                output_boundary_cells=outputs,
                contract=organ.contract,
                generation=generation,
                variant_index=variant_index,
                router_cells=tuple(routers),
                const_cells=tuple(const_cells.values()),
            )
            physical.certificate = create_physical_certificate(self.graph, physical)
            return physical
        except Exception:
            self._cleanup(routes, (*created_cells, *routers))
            if not owns_boundaries:
                for cell_id in (*inputs, *outputs):
                    cell = self.graph.cells[cell_id]
                    for port in tuple(cell.input_ports):
                        if f"g{generation}" in port:
                            cell.input_ports.pop(port, None)
            raise

    def _program_net_sources(self) -> dict[int, int]:
        sources = dict(zip(self.program.input_nets, self.input_boundary_cells))
        sources.update(self.constant_cells)
        for organ in self.program.organs:
            physical = self.organs[organ.organ_id]
            sources.update(zip(organ.output_nets, physical.output_boundary_cells))
        return sources

    def _build_external_network(self, *, generation: int) -> ExternalNetwork:
        route_ids: list[int] = []
        routers: list[int] = []
        fanouts: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for organ in self.program.organs:
            physical = self.organs[organ.organ_id]
            for index, net in enumerate(organ.input_nets):
                fanouts[net].append(
                    (physical.input_boundary_cells[index], f"external:g{generation}:i{index}")
                )
        for index, net in enumerate(self.program.output_nets):
            fanouts[net].append(
                (self.output_boundary_cells[index], f"external:g{generation}:o{index}")
            )
        sources = self._program_net_sources()
        try:
            for net, consumers in fanouts.items():
                self._route_fanout(
                    sources[net],
                    consumers,
                    generation=generation,
                    logical_net_id=net,
                    organ_id=None,
                    kind="external",
                    route_ids=route_ids,
                    router_cells=routers,
                )
            return ExternalNetwork(tuple(route_ids), tuple(routers), generation)
        except Exception:
            self._cleanup(route_ids, routers)
            raise

    @property
    def active_gate_cells(self) -> set[int]:
        return {cell for organ in self.organs.values() for cell in organ.gate_cells.values()}

    @property
    def active_wire_cells(self) -> set[int]:
        route_ids = {
            route_id for organ in self.organs.values() for route_id in organ.internal_wire_paths
        }
        if self.external_network is not None:
            route_ids.update(self.external_network.route_ids)
        if self.state_feedback_network is not None:
            route_ids.update(self.state_feedback_network.route_ids)
        return {
            wire
            for route_id in route_ids
            if route_id in self.graph.routed_nets
            for wire in self.graph.routed_nets[route_id].ordered_wire_cells
        }

    @property
    def original_non_io_gate_cells_remaining(self) -> int:
        return len(self.active_gate_cells & self.original_gate_cells)

    @property
    def original_wire_cells_remaining(self) -> int:
        return len(self.active_wire_cells & self.original_wire_cells)

    @property
    def original_state_cells_remaining(self) -> int:
        state_cells = {
            cell_id for cell_id, cell in self.graph.cells.items() if cell.role == CellRole.STATE
        }
        return len(state_cells & self.original_state_cells)

    def physical_execute(
        self,
        input_values: Sequence[int],
        universe_mask: int,
        *,
        schedule: str = "fifo",
        seed: int | None = None,
    ) -> PhysicalExecutionResult:
        if len(input_values) != len(self.input_boundary_cells):
            raise ValueError("physical program input arity mismatch")
        self.epoch += 1
        injections = {
            cell_id: value & universe_mask
            for index, (cell_id, value) in enumerate(zip(self.input_boundary_cells, input_values))
            if index not in self.state_bindings
        }
        return self.executor.execute(
            injections,
            self.output_boundary_cells,
            universe_mask,
            self.epoch,
            schedule=schedule,
            seed=self.seed + self.epoch if seed is None else seed,
        )

    def physical_execute_scalar(
        self,
        input_values: Sequence[int],
        *,
        schedule: str = "fifo",
        seed: int | None = None,
    ) -> PhysicalExecutionResult:
        return self.physical_execute(input_values, 1, schedule=schedule, seed=seed)

    def fail_cells(self, cell_ids: Iterable[int]) -> set[int]:
        candidates = set(cell_ids)
        for cell_id in candidates:
            cell = self.graph.cells.get(cell_id)
            if cell is None:
                continue
            if cell.organ_id is not None:
                self.pending_fault_organs.add(cell.organ_id)
            elif cell.role in {CellRole.WIRE, CellRole.ROUTER}:
                self.pending_external_repair = True
            route = self.graph.routed_nets.get(cell.net_id) if cell.net_id is not None else None
            if route is not None:
                if route.kind == "state":
                    self.pending_external_repair = True
                elif route.organ_id is None:
                    self.pending_external_repair = True
                else:
                    self.pending_fault_organs.add(route.organ_id)
        return self.graph.fail_cells_permanently(candidates)

    def fail_edges(self, edges: Iterable[tuple[int, int]]) -> set[tuple[int, int]]:
        candidates = {edge_key(a, b) for a, b in edges}
        for edge in candidates:
            route_id = self.graph.edge_routes.get(edge)
            route = self.graph.routed_nets.get(route_id) if route_id is not None else None
            if route is None or route.organ_id is None:
                self.pending_external_repair = True
            else:
                self.pending_fault_organs.add(route.organ_id)
        return self.graph.fail_edges_permanently(candidates)

    def inject_random_support_faults(self, fraction: float, *, seed: int | None = None) -> set[int]:
        if not 0 <= fraction <= 1:
            raise ValueError("fault fraction must be between zero and one")
        if fraction == 0:
            return set()
        rng = self.rng if seed is None else random.Random(seed)
        gates = sorted(self.active_gate_cells)
        wires = sorted(self.active_wire_cells)
        total = max(1, round((len(gates) + len(wires)) * fraction))
        chosen: set[int] = set()
        if gates and total:
            chosen.add(rng.choice(gates))
        if wires and len(chosen) < total:
            chosen.add(rng.choice(wires))
        pool = [cell for cell in (*gates, *wires) if cell not in chosen]
        chosen.update(rng.sample(pool, min(total - len(chosen), len(pool))))
        return self.fail_cells(chosen)

    def _candidate_variants(self, organ: OrganInstance, mode: str) -> list[int]:
        current = self.organs[organ.organ_id].variant_index
        choices = list(range(len(organ.contract.variants)))
        if mode == "blueprint":
            return [current]
        if mode == "turnover":
            alternatives = [index for index in choices if index != current]
            choices = alternatives or [current]
        if mode not in {"morph", "turnover"}:
            raise ValueError(mode)
        return sorted(
            choices,
            key=lambda index: (
                self._variant_cell_estimate(
                    organ.contract.variants[index],
                    organ.contract.n_outputs,
                    self.router.wire_cells_per_route,
                ),
                index == current,
                organ.contract.variants[index].fingerprint,
            ),
        )

    def repair_organ(
        self,
        organ_id: int,
        *,
        mode: str = "morph",
        retire_old: bool = False,
    ) -> PhysicalRepairRecord | None:
        organ = self.program.organs[organ_id]
        old = self.organs[organ_id]
        old_variant = organ.contract.variants[old.variant_index]
        generation = self.generation + 1
        replacement: PhysicalOrgan | None = None
        for variant_index in self._candidate_variants(organ, mode):
            try:
                replacement = self._build_physical_organ(
                    organ,
                    variant_index,
                    generation=generation,
                    boundary_cells=(old.input_boundary_cells, old.output_boundary_cells),
                )
                break
            except (MemoryError, AssertionError):
                continue
        if replacement is None:
            return None
        verified = verify_physical_certificate(self.graph, replacement)
        if not verified:
            self._cleanup(
                replacement.internal_wire_paths,
                replacement.body_cells,
            )
            return None

        old_wire_count = sum(
            len(self.graph.routed_nets[route_id].ordered_wire_cells)
            for route_id in old.internal_wire_paths
            if route_id in self.graph.routed_nets
        )
        new_wire_count = sum(
            len(self.graph.routed_nets[route_id].ordered_wire_cells)
            for route_id in replacement.internal_wire_paths
        )
        self.organs[organ_id] = replacement
        organ.active_variant = replacement.variant_index
        self.generation = generation
        self._cleanup(old.internal_wire_paths, old.body_cells, retire=retire_old)
        self.pending_fault_organs.discard(organ_id)
        record = PhysicalRepairRecord(
            organ_id=organ_id,
            old_variant=old_variant.name,
            new_variant=organ.contract.variants[replacement.variant_index].name,
            old_gate_cells=len(old.gate_cells),
            new_gate_cells=len(replacement.gate_cells),
            old_wire_cells=old_wire_count,
            new_wire_cells=new_wire_count,
            generation=generation,
            certificate_verified=verified,
            structural_change=old_variant.fingerprint
            != organ.contract.variants[replacement.variant_index].fingerprint,
        )
        self.repair_log.append(record)
        return record

    def reroute_external(self, *, retire_old: bool = False) -> bool:
        generation = self.generation + 1
        try:
            replacement = self._build_external_network(generation=generation)
        except MemoryError:
            return False
        old = self.external_network
        self.external_network = replacement
        self.generation = generation
        if old is not None:
            self._cleanup(old.route_ids, old.router_cells, retire=retire_old)
        self.pending_external_repair = False
        return True

    def _build_state_feedback_network(self, *, generation: int) -> ExternalNetwork:
        route_ids: list[int] = []
        routers: list[int] = []
        try:
            for input_index, output_index in sorted(self.state_bindings.items()):
                self._route_fanout(
                    self.output_boundary_cells[output_index],
                    ((self.input_boundary_cells[input_index], f"state:g{generation}"),),
                    generation=generation,
                    logical_net_id=self.program.output_nets[output_index],
                    organ_id=None,
                    kind="state",
                    route_ids=route_ids,
                    router_cells=routers,
                )
            return ExternalNetwork(tuple(route_ids), tuple(routers), generation)
        except Exception:
            self._cleanup(route_ids, routers)
            raise

    def configure_state_feedback(
        self,
        bindings: dict[int, int],
        initial_values: Sequence[int] | None = None,
    ) -> None:
        if self.state_bindings:
            raise RuntimeError("state feedback is already configured")
        values = tuple(initial_values or (0,) * len(bindings))
        if len(values) != len(bindings):
            raise ValueError("state initial-value arity mismatch")
        for (input_index, output_index), value in zip(sorted(bindings.items()), values):
            if not (0 <= input_index < len(self.input_boundary_cells)):
                raise ValueError("state input index out of range")
            if not (0 <= output_index < len(self.output_boundary_cells)):
                raise ValueError("state output index out of range")
            cell = self.graph.cells[self.input_boundary_cells[input_index]]
            cell.role = CellRole.STATE
            cell.operation = "STATE"
            cell.value = int(bool(value))
            cell.valid = True
        self.state_bindings = dict(bindings)
        self.generation += 1
        self.state_feedback_network = self._build_state_feedback_network(generation=self.generation)
        self.original_state_cells = {
            self.input_boundary_cells[index] for index in self.state_bindings
        }
        self.original_wire_cells.update(
            wire
            for route_id in self.state_feedback_network.route_ids
            for wire in self.graph.routed_nets[route_id].ordered_wire_cells
        )

    @property
    def state_cells(self) -> tuple[int, ...]:
        return tuple(self.input_boundary_cells[index] for index in sorted(self.state_bindings))

    def migrate_state_cell(self, input_index: int) -> int:
        if input_index not in self.state_bindings:
            raise ValueError("input is not backed by a State Cell")
        old_cell_id = self.input_boundary_cells[input_index]
        old_cell = self.graph.cells[old_cell_id]
        new_cell_id = self.graph.allocate_cell(
            CellRole.STATE,
            net_id=old_cell.net_id,
            operation="STATE",
            generation=self.generation + 1,
            value=old_cell.value,
        )
        previous_inputs = self.input_boundary_cells
        updated = list(previous_inputs)
        updated[input_index] = new_cell_id
        self.input_boundary_cells = tuple(updated)
        generation = self.generation + 1
        try:
            new_external = self._build_external_network(generation=generation)
            new_feedback = self._build_state_feedback_network(generation=generation)
        except Exception:
            self.input_boundary_cells = previous_inputs
            self.graph.release_cell(new_cell_id)
            raise
        old_external = self.external_network
        old_feedback = self.state_feedback_network
        self.external_network = new_external
        self.state_feedback_network = new_feedback
        self.generation = generation
        if old_external is not None:
            self._cleanup(old_external.route_ids, old_external.router_cells)
        if old_feedback is not None:
            self._cleanup(old_feedback.route_ids, old_feedback.router_cells)
        self.graph.release_cell(old_cell_id, retire=True)
        self.graph.retire_free_cells(self.original_gate_cells | self.original_wire_cells)
        return new_cell_id

    def reroute_state_feedback(self, *, retire_old: bool = False) -> bool:
        if not self.state_bindings:
            return True
        generation = self.generation + 1
        try:
            replacement = self._build_state_feedback_network(generation=generation)
        except MemoryError:
            return False
        old = self.state_feedback_network
        self.state_feedback_network = replacement
        self.generation = generation
        if old is not None:
            self._cleanup(old.route_ids, old.router_cells, retire=retire_old)
        return True

    def repair_all_faults(self, mode: str = "morph") -> list[PhysicalRepairRecord]:
        records: list[PhysicalRepairRecord] = []
        for organ_id in sorted(self.pending_fault_organs):
            record = self.repair_organ(organ_id, mode=mode)
            if record is None:
                raise MemoryError(f"unable to physically repair organ {organ_id}")
            records.append(record)
        if self.pending_external_repair and not self.reroute_external():
            raise MemoryError("unable to reroute damaged external net")
        if self.state_bindings and self.state_feedback_network is not None:
            damaged_state_route = any(
                not self.graph.validate_route(self.graph.routed_nets[route_id])
                for route_id in self.state_feedback_network.route_ids
            )
            if damaged_state_route and not self.reroute_state_feedback():
                raise MemoryError("unable to reroute damaged state feedback")
        return records

    def turnover_all(self) -> dict[str, int]:
        self.graph.retire_free_cells(
            self.original_gate_cells | self.original_wire_cells | self.original_state_cells
        )
        for input_index in sorted(self.state_bindings):
            if self.input_boundary_cells[input_index] in self.original_state_cells:
                self.migrate_state_cell(input_index)
        repairs = 0
        structural = 0
        for organ in self.program.organs:
            record = self.repair_organ(organ.organ_id, mode="turnover", retire_old=True)
            if record is None:
                raise MemoryError(f"physical turnover failed for organ {organ.organ_id}")
            repairs += 1
            structural += int(record.structural_change)
        if not self.reroute_external(retire_old=True):
            raise MemoryError("external route turnover failed")
        if not self.reroute_state_feedback(retire_old=True):
            raise MemoryError("state feedback turnover failed")
        return {
            "repairs": repairs,
            "structural_replacements": structural,
            "original_non_io_gate_cells_remaining": self.original_non_io_gate_cells_remaining,
            "original_wire_cells_remaining": self.original_wire_cells_remaining,
            "original_state_cells_remaining": self.original_state_cells_remaining,
        }

    def corrupt_gate_operation(self, cell_id: int, operation: str) -> None:
        if cell_id not in self.active_gate_cells:
            raise ValueError("Cell is not an active Gate")
        self.graph.cells[cell_id].operation = operation

    def _active_route_ids(self) -> set[int]:
        route_ids = {
            route_id for organ in self.organs.values() for route_id in organ.internal_wire_paths
        }
        if self.external_network is not None:
            route_ids.update(self.external_network.route_ids)
        if self.state_feedback_network is not None:
            route_ids.update(self.state_feedback_network.route_ids)
        return route_ids

    def audit(self, *, replay_certificates: bool = True) -> dict[str, int | bool | str | float]:
        graph_audit = self.graph.audit()
        active_routes = [self.graph.routed_nets[route_id] for route_id in self._active_route_ids()]
        active_edges = {
            edge_key(a, b)
            for route in active_routes
            for a, b in zip(route.path, route.path[1:])
        }
        active_support = self.active_gate_cells | self.active_wire_cells
        healthy = not bool(active_support & self.graph.failed_cells) and not bool(
            active_edges & self.graph.failed_edges
        )
        certificates_ok = (
            all(verify_physical_certificate(self.graph, organ) for organ in self.organs.values())
            if replay_certificates
            else all(organ.certificate is not None for organ in self.organs.values())
        )
        certificate_root = sha256(
            "".join(
                self.organs[organ_id].certificate.physical_subgraph_digest
                for organ_id in sorted(self.organs)
                if self.organs[organ_id].certificate is not None
            ).encode("utf-8")
        ).hexdigest()
        result: dict[str, int | bool | str | float] = dict(graph_audit)
        result.update(self.router.statistics())
        result.update(
            {
                "active_support_healthy": healthy,
                "physical_certificates_valid": certificates_ok,
                "no_failed_active_cells": healthy,
                "contracts_exact": certificates_ok,
                "owner_index_valid": bool(graph_audit["wire_ownership_exclusive"]),
                "certificates_valid": certificates_ok,
                "original_non_io_gate_cells_remaining": self.original_non_io_gate_cells_remaining,
                "original_wire_cells_remaining": self.original_wire_cells_remaining,
                "original_state_cells_remaining": self.original_state_cells_remaining,
                "certificate_root": certificate_root,
                "generation": self.generation,
            }
        )
        return result

    def export_machine(self, path: str | Path | None = None) -> dict:
        active = self.graph.active_cell_ids()
        route_ids = self._active_route_ids()
        data = {
            "format": "E-CORM-v0.2",
            "max_degree": self.graph.max_degree,
            "generation": self.generation,
            "cells": [
                {
                    "cell_id": cell_id,
                    "coordinate": self.graph.cells[cell_id].coordinate,
                    "role": self.graph.cells[cell_id].role.value,
                    "original_role": None
                    if self.graph.cells[cell_id].original_role is None
                    else self.graph.cells[cell_id].original_role.value,
                    "failed": self.graph.cells[cell_id].failed,
                    "organ_id": self.graph.cells[cell_id].organ_id,
                    "net_id": self.graph.cells[cell_id].net_id,
                    "operation": self.graph.cells[cell_id].operation,
                    "input_ports": self.graph.cells[cell_id].input_ports,
                    "output_ports": sorted(self.graph.cells[cell_id].output_ports),
                    "epoch": self.graph.cells[cell_id].epoch,
                    "value": str(
                        self.graph.cells[cell_id].value
                        if self.graph.cells[cell_id].role == CellRole.STATE
                        else 0
                    ),
                    "valid": self.graph.cells[cell_id].valid,
                    "generation": self.graph.cells[cell_id].generation,
                    "neighbors": sorted(self.graph.cells[cell_id].neighbors),
                }
                for cell_id in sorted(active)
            ],
            "physical_edges": [list(edge) for edge in sorted(self.graph.physical_edges)],
            "failed_edges": [list(edge) for edge in sorted(self.graph.failed_edges)],
            "routed_nets": [
                asdict(self.graph.routed_nets[route_id]) for route_id in sorted(route_ids)
            ],
            "boundary_cells": {
                "inputs": self.input_boundary_cells,
                "outputs": self.output_boundary_cells,
                "constants": self.constant_cells,
            },
            "gate_operations": {
                str(cell_id): self.graph.cells[cell_id].operation
                for cell_id in sorted(self.active_gate_cells)
            },
            "state_cells": sorted(
                cell_id for cell_id in active if self.graph.cells[cell_id].role == CellRole.STATE
            ),
            "failed_cells": sorted(self.graph.failed_cells),
        }
        if path is not None:
            Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))
        return data
