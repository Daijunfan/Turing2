from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable, Sequence
import math
import random

from .core import Variant, CONST0, CONST1
from .program import Program, OrganInstance


@dataclass
class ActiveOrgan:
    organ_id: int
    variant_index: int
    cell_for_gate: dict[int, int]
    generation: int
    anchor: tuple[int, int]
    certificate: str

    @property
    def cells(self) -> set[int]:
        return set(self.cell_for_gate.values())


@dataclass
class RepairRecord:
    organ_id: int
    old_variant: str
    new_variant: str
    old_cells: int
    new_cells: int
    failed_cells: int
    touched_certificate_nodes: int
    local_radius: int
    wirelength_before: int
    wirelength_after: int
    structural_change: bool


class CellSubstrate:
    def __init__(self, capacity: int, seed: int = 0):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.width = math.ceil(math.sqrt(capacity))
        self.height = math.ceil(capacity / self.width)
        self.free: set[int] = set(range(capacity))
        self.failed: set[int] = set()
        self.retired: set[int] = set()
        self.rng = random.Random(seed)

    def coord(self, cell: int) -> tuple[int, int]:
        return cell % self.width, cell // self.width

    def distance(self, a: int, b: int) -> int:
        ax, ay = self.coord(a)
        bx, by = self.coord(b)
        return abs(ax - bx) + abs(ay - by)

    def _ring_cells(self, anchor: tuple[int, int], radius: int):
        ax, ay = anchor
        if radius == 0:
            if 0 <= ax < self.width and 0 <= ay < self.height:
                cell = ay * self.width + ax
                if cell < self.capacity:
                    yield cell
            return
        for dx in range(-radius, radius + 1):
            dy = radius - abs(dx)
            for sign in (-1, 1) if dy else (1,):
                x, y = ax + dx, ay + sign * dy
                if 0 <= x < self.width and 0 <= y < self.height:
                    cell = y * self.width + x
                    if cell < self.capacity:
                        yield cell

    def allocate_near(self, count: int, anchor: tuple[int, int], max_radius: int | None = None) -> tuple[list[int], int] | None:
        if count == 0:
            return [], 0
        selected: list[int] = []
        hard_limit = self.width + self.height
        limit = hard_limit if max_radius is None else min(max_radius, hard_limit)
        last_radius = 0
        for radius in range(limit + 1):
            last_radius = radius
            for cell in self._ring_cells(anchor, radius):
                if cell in self.free and cell not in self.failed and cell not in self.retired:
                    selected.append(cell)
                    if len(selected) == count:
                        for chosen in selected:
                            self.free.remove(chosen)
                        return selected, last_radius
        return None

    def release(self, cells: Iterable[int], retire: bool = False) -> None:
        for cell in cells:
            if cell in self.failed:
                continue
            if retire:
                self.retired.add(cell)
                self.free.discard(cell)
            elif cell not in self.retired:
                self.free.add(cell)

    def fail(self, cells: Iterable[int]) -> None:
        for cell in cells:
            self.failed.add(cell)
            self.free.discard(cell)


class CORMRuntime:
    """Contract-Organized Regenerative Machine prototype.

    The runtime is program-independent. Program-specific information is only the
    contract graph and the developmental seed (organ instances and wiring).
    """

    def __init__(self, program: Program, capacity_factor: float = 3.0, seed: int = 0):
        self.program = program
        self.seed = seed
        self.rng = random.Random(seed)
        required = max(1, program.max_gate_count)
        capacity = max(required + 64, math.ceil(required * capacity_factor))
        self.substrate = CellSubstrate(capacity, seed)
        self.active: dict[int, ActiveOrgan] = {}
        # Runtime reverse index.  Fault localization is therefore proportional
        # to the number of newly failed active cells, not to the total number
        # of organs in the program.
        self.cell_owner: dict[int, int] = {}
        self.pending_fault_organs: set[int] = set()
        self.net_producer: dict[int, tuple[int, int | None]] = {}
        self.generation = 0
        self.repair_log: list[RepairRecord] = []
        self.original_cells: set[int] = set()
        self.turnover_epoch = False
        self._deploy_initial()

    def _organ_anchor(self, organ: OrganInstance) -> tuple[int, int]:
        # Stable developmental coordinates derived from module path and id.
        digest = int(sha256((repr(organ.module_path) + str(organ.organ_id)).encode()).hexdigest()[:16], 16)
        x = digest % self.substrate.width
        y = (digest // self.substrate.width) % self.substrate.height
        return x, y

    def _variant_certificate(self, organ: OrganInstance, variant: Variant) -> str:
        if not organ.contract.verify_variant(variant):
            raise AssertionError(f"variant {variant.name} violates {organ.contract.name}")
        return sha256((organ.contract.contract_hash + variant.fingerprint).encode()).hexdigest()

    def _deploy_initial(self) -> None:
        # The selected program variants are the developmental seed. Allocation is
        # performed by the same generic local placement rule for every program.
        for organ in self.program.organs:
            variant = organ.variant
            anchor = self._organ_anchor(organ)
            allocated = self.substrate.allocate_near(variant.gate_count, anchor)
            if allocated is None:
                raise MemoryError("insufficient substrate during development")
            cells, _ = allocated
            mapping = {variant.n_inputs + i: cell for i, cell in enumerate(cells)}
            self.active[organ.organ_id] = ActiveOrgan(
                organ.organ_id,
                organ.active_variant,
                mapping,
                self.generation,
                anchor,
                self._variant_certificate(organ, variant),
            )
            for cell in cells:
                if cell in self.cell_owner:
                    raise AssertionError("cell assigned to multiple organs")
                self.cell_owner[cell] = organ.organ_id
        self.original_cells = self.active_cells.copy()
        self._refresh_net_producers()

    def begin_turnover_epoch(self) -> None:
        """Forbid every generation-0 cell from ever becoming active again."""
        if self.turnover_epoch:
            return
        self.turnover_epoch = True
        for cell in self.original_cells:
            if cell in self.substrate.free:
                self.substrate.retired.add(cell)
                self.substrate.free.discard(cell)

    @property
    def active_cells(self) -> set[int]:
        cells: set[int] = set()
        for active in self.active.values():
            cells.update(active.cells)
        return cells

    @property
    def original_cells_remaining(self) -> int:
        return len(self.active_cells & self.original_cells)

    def _resolve_signal_cell(self, organ: OrganInstance, variant: Variant, active: ActiveOrgan, signal: int) -> int | None:
        if signal >= variant.n_inputs:
            return active.cell_for_gate[signal]
        if signal >= 0:
            net = organ.input_nets[signal]
            producer = self.net_producer.get(net)
            return None if producer is None else producer[1]
        return None

    def _refresh_net_producers(self) -> None:
        """One-time construction of the net endpoint table."""
        self.net_producer = {}
        for net in self.program.input_nets:
            self.net_producer[net] = (-1, None)
        for net in self.program.constant_nets:
            self.net_producer[net] = (-2, None)
        for organ in self.program.organs:
            self._update_organ_net_producers(organ)

    def _update_organ_net_producers(self, organ: OrganInstance) -> None:
        """Update only one organ's boundary endpoints after a cutover."""
        active = self.active[organ.organ_id]
        variant = organ.contract.variants[active.variant_index]
        for net, signal in zip(organ.output_nets, variant.outputs):
            if signal < variant.n_inputs:
                raise AssertionError("organ output was not materialized")
            self.net_producer[net] = (organ.organ_id, active.cell_for_gate[signal])

    def _wirelength(self, organ: OrganInstance, variant: Variant, mapping: dict[int, int]) -> int:
        length = 0
        # Internal and input-to-gate edges.
        for gate_index, gate in enumerate(variant.gates):
            dst_ref = variant.n_inputs + gate_index
            dst_cell = mapping[dst_ref]
            for src in gate.args:
                if src >= variant.n_inputs:
                    src_cell = mapping[src]
                elif src >= 0:
                    net = organ.input_nets[src]
                    producer = self.net_producer.get(net)
                    src_cell = None if producer is None else producer[1]
                else:
                    src_cell = None
                if src_cell is not None:
                    length += self.substrate.distance(src_cell, dst_cell)
        return length

    def topology_edges(self) -> set[tuple[int, int, str]]:
        edges: set[tuple[int, int, str]] = set()
        for organ in self.program.organs:
            active = self.active[organ.organ_id]
            variant = organ.contract.variants[active.variant_index]
            for gate_index, gate in enumerate(variant.gates):
                dst = active.cell_for_gate[variant.n_inputs + gate_index]
                for src in gate.args:
                    if src >= variant.n_inputs:
                        src_cell = active.cell_for_gate[src]
                    elif src >= 0:
                        producer = self.net_producer.get(organ.input_nets[src])
                        src_cell = None if producer is None else producer[1]
                    else:
                        src_cell = None
                    if src_cell is not None:
                        edges.add((src_cell, dst, gate.op))
        return edges

    def total_wirelength(self) -> int:
        total = 0
        for organ in self.program.organs:
            active = self.active[organ.organ_id]
            variant = organ.contract.variants[active.variant_index]
            total += self._wirelength(organ, variant, active.cell_for_gate)
        return total

    def _choose_mapping(self, organ: OrganInstance, variant: Variant, cells: list[int]) -> dict[int, int]:
        # Greedy locality-aware mapping: place each gate near already placed inputs.
        remaining = set(cells)
        mapping: dict[int, int] = {}
        for gate_index, gate in enumerate(variant.gates):
            ref = variant.n_inputs + gate_index
            source_cells: list[int] = []
            for src in gate.args:
                if src >= variant.n_inputs and src in mapping:
                    source_cells.append(mapping[src])
                elif 0 <= src < variant.n_inputs:
                    producer = self.net_producer.get(organ.input_nets[src])
                    if producer is not None and producer[1] is not None:
                        source_cells.append(producer[1])
            if source_cells:
                cell = min(
                    remaining,
                    key=lambda c: sum(self.substrate.distance(c, s) for s in source_cells),
                )
            else:
                ax, ay = self._organ_anchor(organ)
                cell = min(
                    remaining,
                    key=lambda c: abs(self.substrate.coord(c)[0] - ax) + abs(self.substrate.coord(c)[1] - ay),
                )
            mapping[ref] = cell
            remaining.remove(cell)
        return mapping

    def _candidate_variants(self, organ: OrganInstance, mode: str) -> list[int]:
        verified = [i for i, v in enumerate(organ.contract.variants) if organ.contract.verify_variant(v)]
        current = self.active[organ.organ_id].variant_index
        if mode == "blueprint":
            return [current]
        if mode == "morph":
            return sorted(
                verified,
                key=lambda i: (
                    organ.contract.variants[i].gate_count,
                    i == current,
                    organ.contract.variants[i].fingerprint,
                ),
            )
        if mode == "turnover":
            alternatives = [i for i in verified if i != current]
            pool = alternatives if alternatives else [current]
            return sorted(
                pool,
                key=lambda i: (
                    organ.contract.variants[i].gate_count,
                    organ.contract.variants[i].fingerprint,
                ),
            )
        raise ValueError(mode)

    def repair_organ(
        self,
        organ_id: int,
        mode: str = "morph",
        retire_old: bool = False,
        max_radius: int | None = None,
    ) -> RepairRecord | None:
        organ = self.program.organs[organ_id]
        old_active = self.active[organ_id]
        old_variant = organ.contract.variants[old_active.variant_index]
        old_wire = self._wirelength(organ, old_variant, old_active.cell_for_gate)
        failed_count = len(old_active.cells & self.substrate.failed)

        best = None
        anchor = old_active.anchor
        for variant_index in self._candidate_variants(organ, mode):
            variant = organ.contract.variants[variant_index]
            # Allocate a shadow implementation before touching the live organ.
            allocated = self.substrate.allocate_near(variant.gate_count, anchor, max_radius=max_radius)
            if allocated is None:
                continue
            cells, radius = allocated
            mapping = self._choose_mapping(organ, variant, cells)
            wire = self._wirelength(organ, variant, mapping)
            structural_change = variant.fingerprint != old_variant.fingerprint
            score = variant.gate_count + 0.02 * wire - (0.25 if structural_change and mode != "blueprint" else 0.0)
            candidate = (score, variant_index, variant, mapping, cells, radius, wire, structural_change)
            if best is None or candidate[0] < best[0]:
                if best is not None:
                    self.substrate.release(best[4])
                best = candidate
            else:
                self.substrate.release(cells)

        if best is None:
            return None
        _, variant_index, variant, mapping, cells, radius, new_wire, structural_change = best
        certificate = self._variant_certificate(organ, variant)

        # Atomic semantic cutover.
        self.generation += 1
        for cell in old_active.cells:
            if self.cell_owner.get(cell) == organ_id:
                del self.cell_owner[cell]
        for cell in cells:
            if cell in self.cell_owner:
                raise AssertionError("shadow cell already owned")
            self.cell_owner[cell] = organ_id
        self.active[organ_id] = ActiveOrgan(
            organ_id,
            variant_index,
            mapping,
            self.generation,
            anchor,
            certificate,
        )
        organ.active_variant = variant_index
        if retire_old:
            self.substrate.release(old_active.cells, retire=True)
        elif self.turnover_epoch:
            old_original = old_active.cells & self.original_cells
            old_nonoriginal = old_active.cells - self.original_cells
            self.substrate.release(old_original, retire=True)
            self.substrate.release(old_nonoriginal, retire=False)
        else:
            self.substrate.release(old_active.cells, retire=False)
        self._update_organ_net_producers(organ)
        self.pending_fault_organs.discard(organ_id)
        touched = self.program.certificate_tree.update_organ(organ) if self.program.certificate_tree else 1

        record = RepairRecord(
            organ_id,
            old_variant.name,
            variant.name,
            old_variant.gate_count,
            variant.gate_count,
            failed_count,
            touched,
            radius,
            old_wire,
            new_wire,
            structural_change,
        )
        self.repair_log.append(record)
        return record

    def inject_random_cell_faults(self, count: int, seed: int | None = None) -> set[int]:
        rng = self.rng if seed is None else random.Random(seed)
        candidates = sorted(self.active_cells)
        count = min(count, len(candidates))
        chosen = set(rng.sample(candidates, count))
        self.fail_cells(chosen)
        return chosen

    def fail_cells(self, cells: Iterable[int]) -> set[int]:
        """Fail cells and enqueue only their owning organs for local repair."""
        chosen = set(cells)
        for cell in chosen:
            owner = self.cell_owner.get(cell)
            if owner is not None:
                self.pending_fault_organs.add(owner)
        self.substrate.fail(chosen)
        return chosen

    def repair_all_faults(self, mode: str = "morph", max_radius: int | None = None) -> list[RepairRecord]:
        affected = sorted(self.pending_fault_organs)
        records: list[RepairRecord] = []
        for organ_id in affected:
            record = self.repair_organ(organ_id, mode=mode, max_radius=max_radius)
            if record is None:
                raise MemoryError(f"unable to repair organ {organ_id}")
            records.append(record)
        return records

    def turnover_all(self, batch: int = 1) -> dict[str, int | float]:
        self.begin_turnover_epoch()
        before_edges = self.topology_edges()
        before_impl = self.program.implementation_fingerprint()
        repairs = 0
        structural = 0
        for organ in self.program.organs:
            record = self.repair_organ(organ.organ_id, mode="turnover", retire_old=True)
            if record is None:
                raise MemoryError(f"turnover failed for organ {organ.organ_id}")
            repairs += 1
            structural += int(record.structural_change)
        after_edges = self.topology_edges()
        intersection = len(before_edges & after_edges)
        union = len(before_edges | after_edges)
        jaccard = intersection / union if union else 1.0
        return {
            "repairs": repairs,
            "structural_replacements": structural,
            "original_cells_remaining": self.original_cells_remaining,
            "edge_jaccard": jaccard,
            "implementation_changed": int(before_impl != self.program.implementation_fingerprint()),
        }

    def audit(self) -> dict[str, int | bool | str]:
        active_cells = self.active_cells
        disjoint = len(active_cells) == sum(len(a.cells) for a in self.active.values())
        owner_index_ok = (
            len(self.cell_owner) == len(active_cells)
            and set(self.cell_owner) == active_cells
            and all(cell in self.active[owner].cells for cell, owner in self.cell_owner.items())
        )
        no_failed_active = not bool(active_cells & self.substrate.failed)
        contracts_ok = self.program.verify_all_contracts()
        certificates_ok = all(
            active.certificate
            == self._variant_certificate(
                self.program.organs[organ_id],
                self.program.organs[organ_id].contract.variants[active.variant_index],
            )
            for organ_id, active in self.active.items()
        )
        return {
            "disjoint_placement": disjoint,
            "owner_index_valid": owner_index_ok,
            "no_failed_active_cells": no_failed_active,
            "contracts_exact": contracts_ok,
            "certificates_valid": certificates_ok,
            "active_cells": len(active_cells),
            "failed_cells": len(self.substrate.failed),
            "retired_cells": len(self.substrate.retired),
            "certificate_root": self.program.certificate_tree.root_digest if self.program.certificate_tree else "",
        }
