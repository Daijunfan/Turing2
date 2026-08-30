from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from corm.core import synthesize_contract
from corm.builders import build_adder, build_multiplier, build_alu
from corm.program import make_input_patterns
from corm.runtime import CORMRuntime
from corm.sequential import AccumulatorOrganMachine
from corm.compiler import compile_netlist, random_netlist
from corm.dynamics import Rule110OrganMachine

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
RESULTS.mkdir(exist_ok=True)
FIGURES.mkdir(exist_ok=True)


def dump_json(name: str, data) -> None:
    (RESULTS / name).write_text(json.dumps(data, indent=2, sort_keys=True))


def write_csv(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    with (RESULTS / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def packed_bool(arr: np.ndarray) -> int:
    return int.from_bytes(np.packbits(arr.astype(np.uint8), bitorder="little").tobytes(), "little")


def exact_check_adder(program, width: int) -> bool:
    patterns, mask = make_input_patterns(2 * width)
    got = program.evaluate_bits(patterns, mask)
    values = np.arange(1 << (2 * width), dtype=np.uint32)
    word_mask = (1 << width) - 1
    a = values & word_mask
    b = (values >> width) & word_mask
    total = a + b
    expected = tuple(packed_bool(((total >> i) & 1) != 0) for i in range(width + 1))
    return got == expected


def exact_check_multiplier(program, width: int) -> bool:
    patterns, mask = make_input_patterns(2 * width)
    got = program.evaluate_bits(patterns, mask)
    values = np.arange(1 << (2 * width), dtype=np.uint32)
    word_mask = (1 << width) - 1
    a = values & word_mask
    b = (values >> width) & word_mask
    product = a * b
    expected = tuple(packed_bool(((product >> i) & 1) != 0) for i in range(2 * width))
    return got == expected


def exact_check_alu(program, width: int) -> bool:
    n_inputs = 2 * width + 2
    patterns, mask = make_input_patterns(n_inputs)
    got = program.evaluate_bits(patterns, mask)
    values = np.arange(1 << n_inputs, dtype=np.uint32)
    word_mask = (1 << width) - 1
    state = values & word_mask
    operand = (values >> width) & word_mask
    opcode = (values >> (2 * width)) & 3
    expected_value = np.where(
        opcode == 0,
        (state + operand) & word_mask,
        np.where(opcode == 1, state ^ operand, np.where(opcode == 2, state & operand, operand)),
    )
    expected = tuple(packed_bool(((expected_value >> i) & 1) != 0) for i in range(width))
    return got == expected


def random_arithmetic_check(program, kind: str, width: int, samples: int, seed: int) -> bool:
    rng = random.Random(seed)
    mask = (1 << width) - 1
    for _ in range(samples):
        if kind == "multiplier":
            a = rng.randrange(1 << width)
            b = rng.randrange(1 << width)
            inputs = [(a >> i) & 1 for i in range(width)] + [(b >> i) & 1 for i in range(width)]
            out = program.evaluate_scalar(inputs)
            value = sum(bit << i for i, bit in enumerate(out))
            if value != a * b:
                return False
        elif kind == "adder":
            a = rng.randrange(1 << width)
            b = rng.randrange(1 << width)
            inputs = [(a >> i) & 1 for i in range(width)] + [(b >> i) & 1 for i in range(width)]
            out = program.evaluate_scalar(inputs)
            value = sum(bit << i for i, bit in enumerate(out))
            if value != a + b:
                return False
        elif kind == "alu":
            state = rng.randrange(1 << width)
            operand = rng.randrange(1 << width)
            op = rng.randrange(4)
            inputs = (
                [(state >> i) & 1 for i in range(width)]
                + [(operand >> i) & 1 for i in range(width)]
                + [op & 1, (op >> 1) & 1]
            )
            out = program.evaluate_scalar(inputs)
            value = sum(bit << i for i, bit in enumerate(out))
            expected = [(state + operand) & mask, state ^ operand, state & operand, operand][op]
            if value != expected:
                return False
        else:
            raise ValueError(kind)
    return True


def experiment_contract_space(limit: int = 1 << 16) -> dict:
    start = time.perf_counter()
    exact_functions = 0
    functions_with_multiple_shapes = 0
    variants_verified = 0
    gate_counts: list[int] = []
    methods: dict[str, int] = {}
    for truth in range(limit):
        contract = synthesize_contract(f"f4_{truth}", 4, (truth,))
        exact = all(contract.verify_variant(v) for v in contract.variants)
        exact_functions += int(exact)
        functions_with_multiple_shapes += int(len(contract.variants) >= 2)
        variants_verified += len(contract.variants)
        for variant in contract.variants:
            gate_counts.append(variant.gate_count)
            methods[variant.name] = methods.get(variant.name, 0) + 1
    result = {
        "input_arity": 4,
        "function_space": limit,
        "functions_all_variants_exact": exact_functions,
        "functions_with_at_least_two_distinct_shapes": functions_with_multiple_shapes,
        "variants_verified": variants_verified,
        "minimum_variant_gates": min(gate_counts),
        "median_variant_gates": statistics.median(gate_counts),
        "maximum_variant_gates": max(gate_counts),
        "method_occurrences": methods,
        "elapsed_seconds": time.perf_counter() - start,
        "success": exact_functions == limit and functions_with_multiple_shapes == limit,
    }
    dump_json("contract_space.json", result)
    print("contract-space", result, flush=True)
    return result



def experiment_random6_contracts() -> dict:
    rng = random.Random(2026)
    count = 1000
    start = time.perf_counter()
    exact = 0
    multiple = 0
    variants = 0
    maximum_gates = 0
    for index in range(count):
        truth = rng.getrandbits(1 << 6)
        contract = synthesize_contract(f"random6_{index}", 6, (truth,))
        exact += int(all(contract.verify_variant(v) for v in contract.variants))
        multiple += int(len(contract.variants) >= 2)
        variants += len(contract.variants)
        maximum_gates = max(maximum_gates, max(v.gate_count for v in contract.variants))
    result = {
        "random_6_input_functions": count,
        "functions_all_variants_exact": exact,
        "functions_with_at_least_two_distinct_shapes": multiple,
        "variants_verified": variants,
        "maximum_variant_gates": maximum_gates,
        "elapsed_seconds": time.perf_counter() - start,
        "success": exact == count and multiple == count,
    }
    dump_json("random6_contracts.json", result)
    print("random6-contracts", result, flush=True)
    return result


def experiment_generic_compiler() -> dict:
    """Exhaustively validate source-to-organ compilation on large random DAGs."""

    n_cases = 12
    n_inputs = 16
    n_gates = 4096
    n_outputs = 16
    rows: list[dict] = []
    start_all = time.perf_counter()
    for case in range(n_cases):
        seed = 8100 + case
        netlist = random_netlist(n_inputs, n_gates, n_outputs, seed=seed)
        program = compile_netlist(netlist)
        program.set_variant_policy("random", seed=seed)
        semantic_before = program.semantic_fingerprint()
        implementation_before = program.implementation_fingerprint()
        patterns, mask = make_input_patterns(n_inputs)
        expected = netlist.evaluate_bits(patterns, mask)

        exact_before = program.evaluate_bits(patterns, mask) == expected
        runtime = CORMRuntime(program, capacity_factor=4.0, seed=seed)
        initial_cells = len(runtime.active_cells)
        faults = max(1, initial_cells * 15 // 100)
        runtime.inject_random_cell_faults(faults, seed=seed + 100)
        repairs = runtime.repair_all_faults("morph")
        exact_after_faults = program.evaluate_bits(patterns, mask) == expected
        fault_audit = runtime.audit()

        turnover = runtime.turnover_all()
        exact_after_turnover = program.evaluate_bits(patterns, mask) == expected
        final_audit = runtime.audit()
        success = bool(
            exact_before
            and exact_after_faults
            and exact_after_turnover
            and semantic_before == program.semantic_fingerprint()
            and implementation_before != program.implementation_fingerprint()
            and turnover["original_cells_remaining"] == 0
            and turnover["structural_replacements"] == n_gates
            and fault_audit["owner_index_valid"]
            and final_audit["owner_index_valid"]
            and final_audit["contracts_exact"]
        )
        rows.append(
            {
                "case": case,
                "seed": seed,
                "inputs": n_inputs,
                "gates": n_gates,
                "outputs": n_outputs,
                "input_assignments_exhausted_per_phase": 1 << n_inputs,
                "initial_active_cells": initial_cells,
                "faults_injected": faults,
                "organs_repaired": len(repairs),
                "exact_before": exact_before,
                "exact_after_faults": exact_after_faults,
                "exact_after_turnover": exact_after_turnover,
                "structural_turnover_replacements": turnover["structural_replacements"],
                "original_cells_remaining": turnover["original_cells_remaining"],
                "success": success,
            }
        )
        print(f"generic-compiler case={case} success={success}", flush=True)

    result = {
        "cases": n_cases,
        "inputs_per_case": n_inputs,
        "gates_per_case": n_gates,
        "outputs_per_case": n_outputs,
        "total_source_gates": n_cases * n_gates,
        "input_assignments_exhaustively_checked_across_three_phases": n_cases * (1 << n_inputs) * 3,
        "output_bits_exhaustively_compared": n_cases * (1 << n_inputs) * 3 * n_outputs,
        "successful_cases": sum(row["success"] for row in rows),
        "total_faults_injected": sum(row["faults_injected"] for row in rows),
        "total_fault_repair_events": sum(row["organs_repaired"] for row in rows),
        "total_structural_turnover_replacements": sum(
            row["structural_turnover_replacements"] for row in rows
        ),
        "elapsed_seconds": time.perf_counter() - start_all,
        "rows": rows,
        "success": all(row["success"] for row in rows),
    }
    dump_json("generic_compiler.json", result)
    print(
        "generic-compiler",
        {k: result[k] for k in ["cases", "total_source_gates", "successful_cases", "success"]},
        flush=True,
    )
    return result

def experiment_exact_programs() -> dict:
    cases = [
        ("adder8", build_adder, 8, exact_check_adder),
        ("multiplier8", build_multiplier, 8, exact_check_multiplier),
        ("alu8", build_alu, 8, exact_check_alu),
    ]
    result: dict[str, dict] = {}
    for idx, (name, builder, width, checker) in enumerate(cases):
        program = builder(width)
        program.set_variant_policy("random", seed=100 + idx)
        semantic_before = program.semantic_fingerprint()
        implementation_before = program.implementation_fingerprint()
        exact_before = checker(program, width)
        runtime = CORMRuntime(program, capacity_factor=4.5, seed=200 + idx)
        initial_cells = len(runtime.active_cells)
        fault_count = max(1, initial_cells // 5)
        runtime.inject_random_cell_faults(fault_count, seed=300 + idx)
        repairs = runtime.repair_all_faults("morph")
        exact_after_faults = checker(program, width)
        audit_after_faults = runtime.audit()
        turnover = runtime.turnover_all()
        exact_after_turnover = checker(program, width)
        audit_after_turnover = runtime.audit()
        result[name] = {
            "organs": len(program.organs),
            "initial_active_cells": initial_cells,
            "faults_injected": fault_count,
            "organs_repaired": len(repairs),
            "structural_fault_repairs": sum(r.structural_change for r in repairs),
            "exact_before": exact_before,
            "exact_after_20pct_random_cell_faults": exact_after_faults,
            "exact_after_complete_turnover": exact_after_turnover,
            "semantic_fingerprint_unchanged": semantic_before == program.semantic_fingerprint(),
            "implementation_fingerprint_changed": implementation_before != program.implementation_fingerprint(),
            "turnover": turnover,
            "audit_after_faults": audit_after_faults,
            "audit_after_turnover": audit_after_turnover,
            "success": bool(
                exact_before
                and exact_after_faults
                and exact_after_turnover
                and turnover["original_cells_remaining"] == 0
                and audit_after_turnover["contracts_exact"]
                and audit_after_turnover["certificates_valid"]
            ),
        }
        print("exact-program", name, result[name]["success"], flush=True)
    dump_json("exact_programs.json", result)
    return result


def experiment_scaling() -> dict:
    widths = [32, 64, 128, 256, 512, 1024, 2048, 4096]
    k = 8
    rows: list[dict] = []
    for width in widths:
        program = build_adder(width)
        program.set_variant_policy("min")
        runtime = CORMRuntime(program, capacity_factor=2.5, seed=17)
        before = len(runtime.active_cells)
        organ_ids = random.Random(991).sample(range(width), k)
        fault_cells = [next(iter(runtime.active[oid].cells)) for oid in organ_ids]
        runtime.fail_cells(fault_cells)
        start = time.perf_counter()
        records = runtime.repair_all_faults("morph")
        elapsed = time.perf_counter() - start
        local_work = sum(record.new_cells for record in records)
        certificate_updates = sum(record.touched_certificate_nodes for record in records)
        rows.append(
            {
                "width": width,
                "organs": len(program.organs),
                "active_cells_before": before,
                "faulted_organs": k,
                "local_regrowth_cells": local_work,
                "global_recompile_cells": before,
                "local_to_global_ratio": local_work / before,
                "certificate_nodes_updated": certificate_updates,
                "global_certificate_nodes": 2 * len(program.organs) - 1,
                "maximum_local_radius": max(record.local_radius for record in records),
                "repair_seconds": elapsed,
                "arithmetic_samples_exact": random_arithmetic_check(program, "adder", width, 64, width),
            }
        )
    x = np.log(np.array([r["organs"] for r in rows], dtype=float))
    local_slope = float(np.polyfit(x, np.log([r["local_regrowth_cells"] for r in rows]), 1)[0])
    global_slope = float(np.polyfit(x, np.log([r["global_recompile_cells"] for r in rows]), 1)[0])
    result = {
        "fixed_faulted_organs": k,
        "local_work_loglog_slope": local_slope,
        "global_work_loglog_slope": global_slope,
        "all_arithmetic_samples_exact": all(r["arithmetic_samples_exact"] for r in rows),
        "rows": rows,
        "success": abs(local_slope) < 0.05 and 0.95 < global_slope < 1.05,
    }
    write_csv("scaling.csv", rows)
    dump_json("scaling.json", result)
    print("scaling", {k: result[k] for k in ["local_work_loglog_slope", "global_work_loglog_slope", "success"]}, flush=True)
    return result


def experiment_fault_fraction() -> dict:
    rates = [0.01, 0.05, 0.10, 0.20, 0.30]
    rows: list[dict] = []
    for rate in rates:
        for seed in range(5):
            program = build_multiplier(16)
            program.set_variant_policy("random", seed=seed)
            runtime = CORMRuntime(program, capacity_factor=3.0, seed=seed)
            before = len(runtime.active_cells)
            count = max(1, int(before * rate))
            runtime.inject_random_cell_faults(count, seed=1000 + seed)
            start = time.perf_counter()
            try:
                records = runtime.repair_all_faults("morph")
                audit = runtime.audit()
                arithmetic_ok = random_arithmetic_check(program, "multiplier", 16, 32, 10000 + seed)
                success = bool(
                    arithmetic_ok
                    and audit["contracts_exact"]
                    and audit["certificates_valid"]
                    and audit["no_failed_active_cells"]
                )
                error = ""
            except Exception as exc:  # recorded rather than hidden
                records = []
                success = False
                arithmetic_ok = False
                error = repr(exc)
            elapsed = time.perf_counter() - start
            rows.append(
                {
                    "fault_rate": rate,
                    "seed": seed,
                    "cells_before": before,
                    "faults": count,
                    "organs_repaired": len(records),
                    "cells_after": len(runtime.active_cells),
                    "cell_ratio_after_before": len(runtime.active_cells) / before,
                    "repair_seconds": elapsed,
                    "arithmetic_samples_exact": arithmetic_ok,
                    "success": success,
                    "error": error,
                }
            )
    summary = []
    for rate in rates:
        group = [r for r in rows if r["fault_rate"] == rate]
        summary.append(
            {
                "fault_rate": rate,
                "successes": sum(bool(r["success"]) for r in group),
                "trials": len(group),
                "mean_organs_repaired": statistics.mean(r["organs_repaired"] for r in group),
                "mean_active_cell_ratio": statistics.mean(r["cell_ratio_after_before"] for r in group),
                "mean_repair_seconds": statistics.mean(r["repair_seconds"] for r in group),
            }
        )
    result = {"trials": rows, "summary": summary, "success": all(r["successes"] == r["trials"] for r in summary)}
    write_csv("fault_fraction_trials.csv", rows)
    write_csv("fault_fraction_summary.csv", summary)
    dump_json("fault_fraction.json", result)
    print("fault-fraction", [(r["fault_rate"], r["successes"], r["trials"]) for r in summary], flush=True)
    return result


def _local_capacity_case(mode: str) -> tuple:
    program = build_adder(8)
    program.set_variant_policy("max")
    runtime = CORMRuntime(program, capacity_factor=2.0, seed=77)
    target = 3
    active = runtime.active[target]
    radius = 8
    local_free: list[int] = []
    for r in range(radius + 1):
        for cell in runtime.substrate._ring_cells(active.anchor, r):
            if cell in runtime.substrate.free:
                local_free.append(cell)
    compact_size = min(v.gate_count for v in program.organs[target].contract.variants)
    keep = set(local_free[:compact_size])
    for cell in local_free[compact_size:]:
        runtime.substrate.free.discard(cell)
        runtime.substrate.retired.add(cell)
    failed = next(iter(active.cells))
    runtime.fail_cells([failed])
    record = runtime.repair_organ(target, mode=mode, max_radius=radius)
    return program, runtime, record, len(local_free), compact_size


def experiment_local_capacity_separation() -> dict:
    bp_program, bp_runtime, bp_record, local_free, compact_size = _local_capacity_case("blueprint")
    mo_program, mo_runtime, mo_record, _, _ = _local_capacity_case("morph")
    morph_exact = exact_check_adder(mo_program, 8) if mo_record is not None else False
    result = {
        "initial_local_free_cells": local_free,
        "local_cells_left_available": compact_size,
        "blueprint_required_cells": 20,
        "morph_minimum_required_cells": compact_size,
        "blueprint_repair_succeeded": bp_record is not None,
        "morph_repair_succeeded": mo_record is not None,
        "morph_selected_shape": None if mo_record is None else mo_record.new_variant,
        "morph_selected_cells": None if mo_record is None else mo_record.new_cells,
        "morph_exact_over_all_65536_input_pairs": morph_exact,
        "morph_audit": mo_runtime.audit(),
        "success": bp_record is None and mo_record is not None and morph_exact,
    }
    dump_json("local_capacity_separation.json", result)
    print("local-capacity-separation", result["success"], flush=True)
    return result


def experiment_large_multiplier64() -> dict:
    start = time.perf_counter()
    program = build_multiplier(64)
    program.set_variant_policy("random", seed=42)
    runtime = CORMRuntime(program, capacity_factor=2.5, seed=42)
    deployment_seconds = time.perf_counter() - start
    before = len(runtime.active_cells)
    arithmetic_before = random_arithmetic_check(program, "multiplier", 64, 16, 1)
    fault_count = max(1, before // 20)
    runtime.inject_random_cell_faults(fault_count, seed=99)
    repair_start = time.perf_counter()
    records = runtime.repair_all_faults("morph")
    repair_seconds = time.perf_counter() - repair_start
    arithmetic_after = random_arithmetic_check(program, "multiplier", 64, 16, 2)
    audit = runtime.audit()
    result = {
        "word_width": 64,
        "organs": len(program.organs),
        "active_cells_before": before,
        "maximum_possible_cells": program.max_gate_count,
        "substrate_capacity": runtime.substrate.capacity,
        "faults_injected": fault_count,
        "fault_rate_of_active_cells": fault_count / before,
        "organs_repaired": len(records),
        "active_cells_after": len(runtime.active_cells),
        "deployment_seconds": deployment_seconds,
        "repair_seconds": repair_seconds,
        "all_local_contracts_exact": program.verify_all_contracts(),
        "independent_64bit_products_exact_before": arithmetic_before,
        "independent_64bit_products_exact_after": arithmetic_after,
        "audit": audit,
        "success": bool(arithmetic_before and arithmetic_after and audit["contracts_exact"] and audit["no_failed_active_cells"]),
    }
    dump_json("large_multiplier64.json", result)
    print("large-multiplier64", {k: result[k] for k in ["organs", "active_cells_before", "faults_injected", "organs_repaired", "success"]}, flush=True)
    return result


def experiment_turnover_multiplier32() -> dict:
    program = build_multiplier(32)
    program.set_variant_policy("random", seed=5)
    semantic_before = program.semantic_fingerprint()
    runtime = CORMRuntime(program, capacity_factor=3.5, seed=5)
    initial_cells = len(runtime.active_cells)
    start = time.perf_counter()
    turnover = runtime.turnover_all()
    elapsed = time.perf_counter() - start
    arithmetic = random_arithmetic_check(program, "multiplier", 32, 64, 3)
    audit = runtime.audit()
    result = {
        "word_width": 32,
        "organs": len(program.organs),
        "initial_active_cells": initial_cells,
        "active_cells_after": len(runtime.active_cells),
        "elapsed_seconds": elapsed,
        "turnover": turnover,
        "semantic_fingerprint_unchanged": semantic_before == program.semantic_fingerprint(),
        "independent_products_exact": arithmetic,
        "audit": audit,
        "success": bool(
            turnover["original_cells_remaining"] == 0
            and turnover["structural_replacements"] == len(program.organs)
            and arithmetic
            and audit["contracts_exact"]
        ),
    }
    dump_json("turnover_multiplier32.json", result)
    print("turnover-multiplier32", result["success"], flush=True)
    return result


def experiment_repeated_damage() -> dict:
    program = build_multiplier(16)
    program.set_variant_policy("random", seed=12)
    runtime = CORMRuntime(program, capacity_factor=3.0, seed=12)
    rng = random.Random(12)
    rounds = 100
    exact_checks = 0
    for round_index in range(rounds):
        count = max(1, len(runtime.active_cells) // 100)
        runtime.inject_random_cell_faults(count, seed=2000 + round_index)
        runtime.repair_all_faults("morph")
        if round_index % 10 == 0:
            exact_checks += int(random_arithmetic_check(program, "multiplier", 16, 16, rng.randrange(1 << 30)))
    audit = runtime.audit()
    result = {
        "rounds": rounds,
        "faults_per_round_fraction_of_live_cells": 0.01,
        "cumulative_failed_cells": len(runtime.substrate.failed),
        "active_cells_after": len(runtime.active_cells),
        "substrate_capacity": runtime.substrate.capacity,
        "repair_events": len(runtime.repair_log),
        "periodic_arithmetic_checks_passed": exact_checks,
        "periodic_arithmetic_checks_total": rounds // 10,
        "audit": audit,
        "success": exact_checks == rounds // 10 and audit["no_failed_active_cells"] and audit["contracts_exact"],
    }
    dump_json("repeated_damage.json", result)
    print("repeated-damage", result["success"], flush=True)
    return result


def experiment_stateful_vm() -> dict:
    machine = AccumulatorOrganMachine(width=8, seed=123, capacity_factor=5.0)
    rng = random.Random(456)
    cycles = 2000
    for cycle in range(cycles):
        machine.step(
            rng.randrange(4),
            rng.randrange(256),
            fault_count=1 if cycle % 23 == 0 else 0,
            turnover_count=1 if cycle < len(machine.program.organs) else 0,
            migrate_state_bit=cycle if cycle < machine.width else None,
        )
    audit = machine.audit()
    rows = [asdict(record) for record in machine.trace]
    write_csv("vm_trace.csv", rows)
    result = {
        "cycles": cycles,
        "compute_organs": len(machine.program.organs),
        "compute_faults_injected": len(machine.runtime.substrate.failed),
        "repair_events": len(machine.runtime.repair_log),
        "final_state": machine.state,
        "reference_state": machine.reference_state,
        "audit": audit,
        "success": bool(
            audit["state_matches_reference"]
            and audit["original_compute_remaining"] == 0
            and audit["original_state_remaining"] == 0
            and audit["no_failed_active_cells"]
            and audit["contracts_exact"]
        ),
    }
    dump_json("stateful_vm.json", result)
    print("stateful-vm", result["success"], flush=True)
    return result


def experiment_rule110() -> dict:
    width = 1024
    cycles = 2048
    machine = Rule110OrganMachine(width=width, seed=110, capacity_factor=5.0)
    start = time.perf_counter()
    for cycle in range(cycles):
        machine.step(
            fault_count=1 if cycle % 17 == 0 else 0,
            turnover_count=1 if cycle < width else 0,
            migrate_state_count=1 if cycle < width else 0,
        )
    elapsed = time.perf_counter() - start
    audit = machine.audit()
    rows = [asdict(record) for record in machine.trace]
    write_csv("rule110_trace.csv", rows)
    result = {
        "ring_width": width,
        "cycles": cycles,
        "exact_cell_updates": width * cycles,
        "compute_faults_injected": len(machine.runtime.substrate.failed),
        "repair_and_turnover_events": len(machine.runtime.repair_log),
        "elapsed_seconds": elapsed,
        "final_ones": machine.state.bit_count(),
        "audit": audit,
        "success": bool(
            audit["state_matches_reference"]
            and audit["original_compute_remaining"] == 0
            and audit["original_state_remaining"] == 0
            and audit["no_failed_active_cells"]
            and audit["contracts_exact"]
            and audit["owner_index_valid"]
        ),
    }
    dump_json("rule110.json", result)
    print("rule110", result["success"], flush=True)
    return result


def make_figures(scaling: dict, fault_fraction: dict) -> None:
    import matplotlib.pyplot as plt

    rows = scaling["rows"]
    plt.figure(figsize=(7, 5))
    plt.loglog([r["organs"] for r in rows], [r["local_regrowth_cells"] for r in rows], marker="o", label="CORM local regrowth")
    plt.loglog([r["organs"] for r in rows], [r["global_recompile_cells"] for r in rows], marker="o", label="global recompilation")
    plt.xlabel("Number of organs")
    plt.ylabel("Cells rewritten for 8 damaged organs")
    plt.title("Repair-work scaling")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURES / "repair_scaling.png", dpi=180)
    plt.close()

    plt.figure(figsize=(7, 5))
    plt.semilogx([r["organs"] for r in rows], [r["certificate_nodes_updated"] for r in rows], marker="o", label="local certificate updates")
    plt.semilogx([r["organs"] for r in rows], [r["global_certificate_nodes"] for r in rows], marker="o", label="global certificate tree")
    plt.xlabel("Number of organs")
    plt.ylabel("Certificate nodes touched")
    plt.title("Compositional verification locality")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURES / "certificate_scaling.png", dpi=180)
    plt.close()

    summary = fault_fraction["summary"]
    plt.figure(figsize=(7, 5))
    plt.plot([r["fault_rate"] for r in summary], [r["mean_active_cell_ratio"] for r in summary], marker="o")
    plt.xlabel("Permanent cell faults / initial active cells")
    plt.ylabel("Active cells after exact repair / before")
    plt.title("Resource contraction under damage")
    plt.tight_layout()
    plt.savefig(FIGURES / "damage_adaptation.png", dpi=180)
    plt.close()

    trace_path = RESULTS / "vm_trace.csv"
    data = np.genfromtxt(trace_path, delimiter=",", names=True)
    plt.figure(figsize=(7, 5))
    plt.plot(data["cycle"], data["original_compute_remaining"], label="original compute cells")
    plt.plot(data["cycle"], data["original_state_remaining"], label="original state cells")
    plt.xlabel("Execution cycle")
    plt.ylabel("Original components still active")
    plt.title("Stateful execution through complete component turnover")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURES / "vm_turnover.png", dpi=180)
    plt.close()

    rule_path = RESULTS / "rule110_trace.csv"
    if rule_path.exists():
        rule = np.genfromtxt(rule_path, delimiter=",", names=True)
        plt.figure(figsize=(7, 5))
        plt.plot(rule["cycle"], rule["ones"], label="active Rule 110 cells")
        plt.plot(
            rule["cycle"],
            rule["original_compute_remaining"],
            label="original compute cells",
        )
        plt.plot(
            rule["cycle"],
            rule["original_state_remaining"],
            label="original state cells",
        )
        plt.xlabel("Evolution step")
        plt.ylabel("Count")
        plt.title("Exact Rule 110 evolution through complete turnover")
        plt.legend()
        plt.tight_layout()
        plt.savefig(FIGURES / "rule110_turnover.png", dpi=180)
        plt.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Use a reduced contract-space sweep")
    parser.add_argument(
        "--stage",
        choices=[
            "contract_space", "random6_contracts", "generic_compiler", "exact_programs", "scaling",
            "fault_fraction", "local_capacity", "large64", "turnover32",
            "repeated_damage", "stateful_vm", "rule110", "all"
        ],
        default="all",
    )
    args = parser.parse_args()
    metadata = {
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "pid": os.getpid(),
        "started_unix": time.time(),
    }
    dump_json("environment.json", metadata)

    actions = {
        "contract_space": lambda: experiment_contract_space(4096 if args.quick else 1 << 16),
        "random6_contracts": experiment_random6_contracts,
        "generic_compiler": experiment_generic_compiler,
        "exact_programs": experiment_exact_programs,
        "scaling": experiment_scaling,
        "fault_fraction": experiment_fault_fraction,
        "local_capacity": experiment_local_capacity_separation,
        "large64": experiment_large_multiplier64,
        "turnover32": experiment_turnover_multiplier32,
        "repeated_damage": experiment_repeated_damage,
        "stateful_vm": experiment_stateful_vm,
        "rule110": experiment_rule110,
    }
    if args.stage != "all":
        actions[args.stage]()
        return

    # The all-in-one path is convenient on large machines. The included
    # run_all.sh launches each heavy stage in a fresh process to cap memory.
    all_results = {name: action() for name, action in actions.items()}
    all_results["all_experiments_success"] = all(
        (all(v.get("success", False) for v in result.values()) if name == "exact_programs" else result.get("success", False))
        for name, result in all_results.items()
    )
    dump_json("summary.json", all_results)
    make_figures(all_results["scaling"], all_results["fault_fraction"])
    print("ALL_EXPERIMENTS_SUCCESS", all_results["all_experiments_success"], flush=True)


if __name__ == "__main__":
    main()
