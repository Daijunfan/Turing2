from corm.dynamics import Rule110OrganMachine


def test_rule110_exact_during_faults_and_complete_turnover():
    machine = Rule110OrganMachine(width=64, seed=91, capacity_factor=5.0)
    for cycle in range(128):
        machine.step(
            fault_count=1 if cycle % 19 == 0 else 0,
            turnover_count=1 if cycle < 64 else 0,
            migrate_state_count=1 if cycle < 64 else 0,
        )
    audit = machine.audit()
    assert audit["state_matches_reference"]
    assert audit["original_compute_remaining"] == 0
    assert audit["original_state_remaining"] == 0
    assert audit["contracts_exact"]
    assert audit["owner_index_valid"]
