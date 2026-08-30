from corm.compiler import BooleanNetlist, NetGate, compile_netlist
from corm.core import Contract, Variant
from corm.embodied_runtime import EmbodiedRuntime
from corm.program import Program, make_input_patterns
from corm.sequential import AccumulatorOrganMachine


def _forbidden(*args, **kwargs):
    raise AssertionError("abstract semantic evaluator was called by the physical DUT")


def _and_machine() -> tuple[Program, EmbodiedRuntime, list[int], int, tuple[int, ...]]:
    netlist = BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,))
    program = compile_netlist(netlist)
    program.set_variant_policy("min")
    runtime = EmbodiedRuntime(program, seed=7)
    patterns, mask = make_input_patterns(2)
    return program, runtime, patterns, mask, (0b1000,)


def test_physical_execute_with_all_abstract_evaluators_disabled(monkeypatch):
    _, runtime, patterns, mask, expected = _and_machine()
    monkeypatch.setattr(Program, "evaluate_bits", _forbidden)
    monkeypatch.setattr(Program, "evaluate_scalar", _forbidden)
    monkeypatch.setattr(Variant, "evaluate_bits", _forbidden)
    monkeypatch.setattr(Variant, "evaluate_scalar", _forbidden)
    monkeypatch.setattr(Contract, "evaluate_bits", _forbidden)

    result = runtime.physical_execute(patterns, mask)
    assert result.valid
    assert result.outputs == expected


def test_gate_and_wire_damage_are_physically_causal_and_repairable():
    _, runtime, patterns, mask, expected = _and_machine()
    gate = next(iter(runtime.active_gate_cells))
    runtime.corrupt_gate_operation(gate, "OR")
    corrupted = runtime.physical_execute(patterns, mask)
    assert corrupted.valid
    assert corrupted.outputs != expected

    runtime.corrupt_gate_operation(gate, "AND")
    output_cell = runtime.output_boundary_cells[0]
    route = next(
        route
        for route in runtime.graph.routed_nets.values()
        if route.destination_cell == output_cell
    )
    runtime.fail_cells([route.ordered_wire_cells[-1]])
    damaged = runtime.physical_execute(patterns, mask)
    assert not damaged.valid
    assert damaged.reason in {"INVALID", "TIMEOUT"}
    runtime.repair_all_faults()
    repaired = runtime.physical_execute(patterns, mask)
    assert repaired.valid
    assert repaired.outputs == expected


def test_total_active_support_destruction_cannot_preserve_output():
    _, runtime, patterns, mask, expected = _and_machine()
    runtime.fail_cells(runtime.active_gate_cells | runtime.active_wire_cells)
    destroyed = runtime.physical_execute(patterns, mask)
    assert not destroyed.valid or destroyed.outputs != expected
    runtime.repair_all_faults()
    repaired = runtime.physical_execute(patterns, mask)
    assert repaired.valid
    assert repaired.outputs == expected


def test_state_machine_uses_physical_cells_with_abstract_evaluators_disabled(monkeypatch):
    machine = AccumulatorOrganMachine(width=4, seed=31, capacity_factor=5.0)
    monkeypatch.setattr(Program, "evaluate_bits", _forbidden)
    monkeypatch.setattr(Program, "evaluate_scalar", _forbidden)
    monkeypatch.setattr(Variant, "evaluate_bits", _forbidden)
    monkeypatch.setattr(Variant, "evaluate_scalar", _forbidden)
    monkeypatch.setattr(Contract, "evaluate_bits", _forbidden)
    for cycle in range(32):
        machine.step(
            cycle % 4,
            (cycle * 3) & 15,
            fault_count=1 if cycle % 11 == 0 else 0,
            turnover_count=1 if cycle < len(machine.program.organs) else 0,
            migrate_state_bit=cycle if cycle < 4 else None,
        )
    audit = machine.audit()
    assert audit["state_matches_reference"]
    assert audit["original_compute_remaining"] == 0
    assert audit["original_wire_remaining"] == 0
    assert audit["original_state_remaining"] == 0
