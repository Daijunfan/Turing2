from corm.compiler import compile_netlist, random_netlist
from corm.program import make_input_patterns
from corm.runtime import CORMRuntime


def test_generic_netlist_compiler_exact_through_faults_and_turnover():
    netlist = random_netlist(8, 256, 8, seed=44)
    program = compile_netlist(netlist)
    program.set_variant_policy("random", seed=44)
    patterns, mask = make_input_patterns(netlist.n_inputs)
    expected = netlist.evaluate_bits(patterns, mask)
    assert program.evaluate_bits(patterns, mask) == expected

    runtime = CORMRuntime(program, capacity_factor=4.0, seed=44)
    runtime.inject_random_cell_faults(max(1, len(runtime.active_cells) // 7), seed=45)
    runtime.repair_all_faults("morph")
    assert program.evaluate_bits(patterns, mask) == expected
    assert runtime.audit()["owner_index_valid"]

    turnover = runtime.turnover_all()
    assert turnover["original_cells_remaining"] == 0
    assert program.evaluate_bits(patterns, mask) == expected
