from audit.independent_physical_replay import replay
from corm.builders import build_adder
from corm.embodied_runtime import EmbodiedRuntime
from corm.program import make_input_patterns


def test_physical_routes_replay_faults_and_complete_turnover():
    program = build_adder(3)
    program.set_variant_policy("random", seed=19)
    patterns, mask = make_input_patterns(6)
    expected = program.evaluate_bits(patterns, mask)
    runtime = EmbodiedRuntime(program, seed=19)

    initial = runtime.physical_execute(patterns, mask)
    assert initial.valid and initial.outputs == expected
    graph_audit = runtime.graph.audit()
    assert graph_audit["degree_bound_ok"]
    assert graph_audit["routes_complete"]
    assert graph_audit["wire_ownership_exclusive"]
    assert graph_audit["no_direct_gate_edges"]

    exported = runtime.export_machine()
    independent = replay(exported, patterns, mask)
    assert independent["valid"]
    assert independent["outputs"] == initial.outputs

    internal_wire = next(
        wire
        for organ in runtime.organs.values()
        for route_id in organ.internal_wire_paths
        for wire in runtime.graph.routed_nets[route_id].ordered_wire_cells
    )
    external_route = runtime.graph.routed_nets[runtime.external_network.route_ids[0]]
    edge = (external_route.source_cell, external_route.ordered_wire_cells[0])
    runtime.fail_cells([internal_wire])
    runtime.fail_edges([edge])
    assert not runtime.physical_execute(patterns, mask).valid
    runtime.repair_all_faults()
    repaired = runtime.physical_execute(patterns, mask)
    assert repaired.valid and repaired.outputs == expected

    turnover = runtime.turnover_all()
    assert turnover["original_non_io_gate_cells_remaining"] == 0
    assert turnover["original_wire_cells_remaining"] == 0
    final = runtime.physical_execute(patterns, mask)
    assert final.valid and final.outputs == expected


def test_random_asynchronous_activation_is_schedule_independent():
    program = build_adder(3)
    patterns, mask = make_input_patterns(6)
    expected = program.evaluate_bits(patterns, mask)
    runtime = EmbodiedRuntime(program, seed=23)
    for seed in range(20):
        result = runtime.physical_execute(patterns, mask, schedule="random", seed=seed)
        assert result.valid
        assert result.outputs == expected
        assert result.future_epoch_reads == 0
        assert result.stale_epoch_reads == 0
