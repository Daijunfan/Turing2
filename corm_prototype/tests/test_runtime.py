from corm.builders import build_adder, build_multiplier, build_alu
from corm.runtime import CORMRuntime


def test_fault_repair_exact():
    p = build_multiplier(6)
    p.set_variant_policy("random", seed=1)
    rt = CORMRuntime(p, capacity_factor=3.0, seed=1)
    rt.inject_random_cell_faults(max(1, len(rt.active_cells) // 10), seed=2)
    records = rt.repair_all_faults("morph")
    assert records
    audit = rt.audit()
    assert all(audit[k] for k in ["disjoint_placement", "no_failed_active_cells", "contracts_exact", "certificates_valid"])
    for a in range(64):
        for b in [0, 1, 7, 31, 63]:
            inputs = [(a >> i) & 1 for i in range(6)] + [(b >> i) & 1 for i in range(6)]
            out = p.evaluate_scalar(inputs)
            value = sum(bit << i for i, bit in enumerate(out))
            assert value == a * b


def test_complete_turnover():
    p = build_alu(8)
    p.set_variant_policy("random", seed=3)
    rt = CORMRuntime(p, capacity_factor=3.5, seed=3)
    result = rt.turnover_all()
    assert result["original_cells_remaining"] == 0
    assert result["implementation_changed"] == 1
    audit = rt.audit()
    assert all(audit[k] for k in ["disjoint_placement", "no_failed_active_cells", "contracts_exact", "certificates_valid"])
