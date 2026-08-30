from __future__ import annotations

from dataclasses import dataclass
import random

from .builders import build_alu
from .embodied_runtime import EmbodiedRuntime


@dataclass
class VMTraceRecord:
    cycle: int
    opcode: int
    operand: int
    before: int
    after: int
    reference: int
    repaired_organs: int
    turnover_organs: int
    original_compute_remaining: int
    original_state_remaining: int


class AccumulatorOrganMachine:
    """Stateful CORM demonstration.

    The combinational next-state function is an organ machine. State lives in
    versioned cells and migrates by shadow-copy/cutover between clock ticks.
    """

    def __init__(self, width: int = 8, seed: int = 0, capacity_factor: float = 4.0):
        self.width = width
        self.mask = (1 << width) - 1
        self.program = build_alu(width)
        self.program.set_variant_policy("random", seed=seed)
        self.runtime = EmbodiedRuntime(self.program, capacity_factor=capacity_factor, seed=seed)
        self.rng = random.Random(seed)
        self.state = 0
        self.reference_state = 0
        self.cycle = 0
        self.turnover_cursor = 0
        self.state_cells: list[int] = []
        self.original_state_cells: set[int] = set()
        self._network_turnover_complete = False
        self._allocate_state_cells()
        self.trace: list[VMTraceRecord] = []

    def _allocate_state_cells(self) -> None:
        self.runtime.configure_state_feedback(
            {index: index for index in range(self.width)},
            [0] * self.width,
        )
        self.state_cells = list(self.runtime.state_cells)
        self.original_state_cells = set(self.state_cells)

    @property
    def original_state_remaining(self) -> int:
        return len(set(self.state_cells) & self.original_state_cells)

    def migrate_state_bit(self, bit: int) -> None:
        self.state_cells[bit] = self.runtime.migrate_state_cell(bit)

    def turnover_next_organs(self, count: int = 1) -> int:
        self.runtime.graph.retire_free_cells(
            self.runtime.original_gate_cells | self.runtime.original_wire_cells
        )
        done = 0
        while done < count and self.turnover_cursor < len(self.program.organs):
            organ = self.program.organs[self.turnover_cursor]
            record = self.runtime.repair_organ(
                organ.organ_id, mode="turnover", retire_old=True
            )
            if record is None:
                raise MemoryError("compute turnover failed")
            self.turnover_cursor += 1
            done += 1
        if self.turnover_cursor == len(self.program.organs) and not self._network_turnover_complete:
            if not self.runtime.reroute_external(retire_old=True):
                raise MemoryError("external route turnover failed")
            if not self.runtime.reroute_state_feedback(retire_old=True):
                raise MemoryError("state feedback turnover failed")
            self._network_turnover_complete = True
        return done

    def inject_and_repair_compute_faults(self, count: int = 1) -> int:
        if count <= 0:
            return 0
        candidates = sorted(self.runtime.active_gate_cells | self.runtime.active_wire_cells)
        chosen = self.rng.sample(candidates, min(count, len(candidates)))
        self.runtime.fail_cells(chosen)
        return len(self.runtime.repair_all_faults("morph"))

    def _reference_step(self, opcode: int, operand: int) -> int:
        if opcode == 0:
            return (self.reference_state + operand) & self.mask
        if opcode == 1:
            return self.reference_state ^ operand
        if opcode == 2:
            return self.reference_state & operand
        if opcode == 3:
            return operand & self.mask
        raise ValueError(opcode)

    def step(
        self,
        opcode: int,
        operand: int,
        fault_count: int = 0,
        turnover_count: int = 0,
        migrate_state_bit: int | None = None,
    ) -> int:
        repaired = self.inject_and_repair_compute_faults(fault_count) if fault_count else 0
        turned = self.turnover_next_organs(turnover_count) if turnover_count else 0
        if migrate_state_bit is not None:
            self.migrate_state_bit(migrate_state_bit % self.width)

        before = self.state
        inputs = (
            [(self.state >> i) & 1 for i in range(self.width)]
            + [(operand >> i) & 1 for i in range(self.width)]
            + [opcode & 1, (opcode >> 1) & 1]
        )
        execution = self.runtime.physical_execute_scalar(inputs)
        if not execution.valid:
            raise AssertionError(
                f"physical execution failed at cycle {self.cycle}: {execution.reason}"
            )
        outputs = execution.outputs
        after = sum(bit << i for i, bit in enumerate(outputs))
        reference = self._reference_step(opcode, operand)
        if after != reference:
            raise AssertionError(
                f"semantic divergence at cycle {self.cycle}: got {after}, expected {reference}"
            )
        self.state = after
        self.reference_state = reference
        self.trace.append(
            VMTraceRecord(
                self.cycle,
                opcode,
                operand,
                before,
                after,
                reference,
                repaired,
                turned,
                self.runtime.original_non_io_gate_cells_remaining,
                self.original_state_remaining,
            )
        )
        self.cycle += 1
        return after

    def audit(self) -> dict[str, int | bool | str]:
        audit = self.runtime.audit()
        audit.update(
            {
                "state_matches_reference": self.state == self.reference_state,
                "original_compute_remaining": self.runtime.original_non_io_gate_cells_remaining,
                "original_wire_remaining": self.runtime.original_wire_cells_remaining,
                "original_state_remaining": self.original_state_remaining,
                "cycles": self.cycle,
            }
        )
        return audit
