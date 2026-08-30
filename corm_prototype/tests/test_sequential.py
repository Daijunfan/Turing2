import random
from corm.sequential import AccumulatorOrganMachine


def test_stateful_execution_during_turnover_and_faults():
    m = AccumulatorOrganMachine(width=8, seed=7, capacity_factor=5.0)
    rng = random.Random(9)
    for cycle in range(200):
        op = rng.randrange(4)
        operand = rng.randrange(256)
        m.step(
            op,
            operand,
            fault_count=1 if cycle % 17 == 0 else 0,
            turnover_count=1 if cycle < len(m.program.organs) else 0,
            migrate_state_bit=cycle if cycle < 8 else None,
        )
    audit = m.audit()
    assert audit["state_matches_reference"]
    assert audit["original_compute_remaining"] == 0
    assert audit["original_state_remaining"] == 0
    assert audit["contracts_exact"]
    assert audit["no_failed_active_cells"]
