from __future__ import annotations

from dataclasses import dataclass
import random

from .core import make_contract
from .program import Program
from .runtime import CORMRuntime


def rule110_contract():
    return make_contract(
        "RULE110",
        3,
        1,
        lambda x: ((110 >> ((x[0] << 2) | (x[1] << 1) | x[2])) & 1,),
    )


def build_rule110_ring(width: int) -> Program:
    if width < 3:
        raise ValueError("Rule 110 ring requires at least three cells")
    program = Program(f"rule110_ring_{width}")
    state = [program.new_input(f"state{i}") for i in range(width)]
    contract = rule110_contract()
    outputs: list[int] = []
    for i in range(width):
        (next_bit,) = program.add_organ(
            f"rule110_{i}",
            contract,
            (state[(i - 1) % width], state[i], state[(i + 1) % width]),
            ("rule110", f"cell{i}"),
        )
        outputs.append(next_bit)
    program.set_outputs(outputs, [f"next{i}" for i in range(width)])
    return program.finalize()


@dataclass
class Rule110Trace:
    cycle: int
    ones: int
    repaired_organs: int
    turnover_organs: int
    original_compute_remaining: int
    original_state_remaining: int
    state_digest: int


class Rule110OrganMachine:
    """Exact sequential execution of a universal local rule on CORM organs.

    This is a bounded-ring experimental witness, not a claim that a finite ring
    itself supplies an unbounded Turing tape.  The transition rule is the known
    universal Rule 110 local function; CORM preserves it through damage and
    complete physical turnover.
    """

    def __init__(self, width: int = 1024, seed: int = 0, capacity_factor: float = 4.0):
        self.width = width
        self.mask = (1 << width) - 1
        self.rng = random.Random(seed)
        self.program = build_rule110_ring(width)
        self.program.set_variant_policy("random", seed=seed)
        self.runtime = CORMRuntime(self.program, capacity_factor=capacity_factor, seed=seed)
        # Random non-degenerate initial condition.
        self.state = self.rng.getrandbits(width) | 1
        self.reference_state = self.state
        self.turnover_cursor = 0
        self.state_migration_cursor = 0
        self.state_cells: list[int] = []
        self.original_state_cells: set[int] = set()
        self.trace: list[Rule110Trace] = []
        self._allocate_state_cells()

    def _allocate_state_cells(self) -> None:
        center = (self.runtime.substrate.width // 2, self.runtime.substrate.height // 2)
        allocated = self.runtime.substrate.allocate_near(self.width, center)
        if allocated is None:
            raise MemoryError("insufficient substrate for Rule 110 state")
        self.state_cells = allocated[0]
        self.original_state_cells = set(self.state_cells)

    @property
    def original_state_remaining(self) -> int:
        return len(set(self.state_cells) & self.original_state_cells)

    @staticmethod
    def reference_step(state: int, width: int) -> int:
        mask = (1 << width) - 1
        out = 0
        for i in range(width):
            left = (state >> ((i - 1) % width)) & 1
            center = (state >> i) & 1
            right = (state >> ((i + 1) % width)) & 1
            index = (left << 2) | (center << 1) | right
            out |= ((110 >> index) & 1) << i
        return out & mask

    def _migrate_next_state_cells(self, count: int) -> int:
        migrated = 0
        while migrated < count and self.state_migration_cursor < self.width:
            bit = self.state_migration_cursor
            old = self.state_cells[bit]
            allocated = self.runtime.substrate.allocate_near(1, self.runtime.substrate.coord(old))
            if allocated is None:
                raise MemoryError("Rule 110 state migration failed")
            self.state_cells[bit] = allocated[0][0]
            self.runtime.substrate.release([old], retire=True)
            self.state_migration_cursor += 1
            migrated += 1
        return migrated

    def _turnover_next_organs(self, count: int) -> int:
        self.runtime.begin_turnover_epoch()
        done = 0
        while done < count and self.turnover_cursor < len(self.program.organs):
            organ = self.program.organs[self.turnover_cursor]
            record = self.runtime.repair_organ(organ.organ_id, mode="turnover", retire_old=True)
            if record is None:
                raise MemoryError("Rule 110 compute turnover failed")
            self.turnover_cursor += 1
            done += 1
        return done

    def step(self, fault_count: int = 0, turnover_count: int = 0, migrate_state_count: int = 0) -> int:
        repaired = 0
        if fault_count:
            self.runtime.inject_random_cell_faults(
                fault_count, seed=self.rng.randrange(1 << 63)
            )
            repaired = len(self.runtime.repair_all_faults("morph"))
        turned = self._turnover_next_organs(turnover_count) if turnover_count else 0
        if migrate_state_count:
            self._migrate_next_state_cells(migrate_state_count)

        inputs = [(self.state >> i) & 1 for i in range(self.width)]
        outputs = self.program.evaluate_scalar(inputs)
        next_state = sum(bit << i for i, bit in enumerate(outputs))
        reference = self.reference_step(self.reference_state, self.width)
        if next_state != reference:
            delta = next_state ^ reference
            raise AssertionError(f"Rule 110 semantic divergence; differing bits={delta.bit_count()}")
        self.state = next_state
        self.reference_state = reference
        digest = hash((self.state & ((1 << 64) - 1), self.state.bit_count(), len(self.trace)))
        self.trace.append(
            Rule110Trace(
                len(self.trace),
                self.state.bit_count(),
                repaired,
                turned,
                self.runtime.original_cells_remaining,
                self.original_state_remaining,
                digest,
            )
        )
        return next_state

    def audit(self) -> dict[str, int | bool | str]:
        audit = self.runtime.audit()
        audit.update(
            {
                "state_matches_reference": self.state == self.reference_state,
                "original_compute_remaining": self.runtime.original_cells_remaining,
                "original_state_remaining": self.original_state_remaining,
                "cycles": len(self.trace),
                "ring_width": self.width,
                "final_ones": self.state.bit_count(),
            }
        )
        return audit
