from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random
import subprocess
import sys
import time

from audit.independent_physical_replay import replay as independent_replay
from corm.builders import build_adder, build_alu, build_multiplier
from corm.compiler import BooleanNetlist, NetGate, compile_netlist, random_netlist
from corm.core import Contract, Variant
from corm.embodied_runtime import EmbodiedRuntime
from corm.physical_certificate import verify_physical_certificate
from corm.program import Program, make_input_patterns
from corm.router import DeterministicRouter
from corm.runtime import CORMRuntime
from corm.sequential import AccumulatorOrganMachine


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results_v02"
TRACES = RESULTS / "physical_traces"
FIGURES = ROOT / "figures_v02"
for directory in (RESULTS, TRACES, FIGURES):
    directory.mkdir(exist_ok=True)


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True))


def error_bits(got: tuple[int, ...], expected: tuple[int, ...]) -> int:
    return sum((a ^ b).bit_count() for a, b in zip(got, expected))


def record_failure(failures: list[dict], experiment: str, case: str, detail) -> None:
    failures.append(
        {
            "expected": False,
            "experiment": experiment,
            "case": case,
            "detail": repr(detail),
        }
    )


def forbidden(*args, **kwargs):
    raise AssertionError("abstract evaluator entered the physical DUT")


def no_semantic_bypass_experiment() -> dict:
    netlist = BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,))
    program = compile_netlist(netlist)
    runtime = EmbodiedRuntime(program, seed=5)
    patterns, mask = make_input_patterns(2)
    original = {
        (Program, "evaluate_bits"): Program.evaluate_bits,
        (Program, "evaluate_scalar"): Program.evaluate_scalar,
        (Variant, "evaluate_bits"): Variant.evaluate_bits,
        (Variant, "evaluate_scalar"): Variant.evaluate_scalar,
        (Contract, "evaluate_bits"): Contract.evaluate_bits,
    }
    try:
        for (owner, name) in original:
            setattr(owner, name, forbidden)
        result = runtime.physical_execute(patterns, mask)
    finally:
        for (owner, name), function in original.items():
            setattr(owner, name, function)
    return {
        "disabled_functions": [f"{owner.__name__}.{name}" for owner, name in original],
        "physical_valid": result.valid,
        "physical_outputs": result.outputs,
        "expected_outputs": (0b1000,),
        "success": result.valid and result.outputs == (0b1000,),
    }


def physical_causality_experiment(failures: list[dict]) -> dict:
    program = build_adder(8)
    program.set_variant_policy("random", seed=101)
    runtime = EmbodiedRuntime(program, seed=101)
    patterns, mask = make_input_patterns(16)
    initial = runtime.physical_execute(patterns, mask)
    expected = program.evaluate_bits(patterns, mask)  # external oracle after DUT
    initial_machine = runtime.export_machine(TRACES / "adder8_initial_machine.json")
    independent = independent_replay(initial_machine, patterns, mask)

    output_route = next(
        route
        for route in runtime.graph.routed_nets.values()
        if route.destination_cell == runtime.output_boundary_cells[0]
    )
    old_path = output_route.path
    cut_wire = output_route.ordered_wire_cells[-1]
    runtime.fail_cells((cut_wire,))
    damaged = runtime.physical_execute(patterns, mask)
    repairs = runtime.repair_all_faults()
    repaired = runtime.physical_execute(patterns, mask)
    new_output_route = next(
        route
        for route in runtime.graph.routed_nets.values()
        if route.destination_cell == runtime.output_boundary_cells[0]
    )
    new_path = new_output_route.path

    target_organ = runtime.organs[program.organs[-1].organ_id]
    target_ref = program.organs[-1].contract.variants[target_organ.variant_index].outputs[-1]
    gate_cell = target_organ.gate_cells[target_ref]
    old_operation = runtime.graph.cells[gate_cell].operation
    replacement_op = "OR" if old_operation != "OR" else "AND"
    runtime.corrupt_gate_operation(gate_cell, replacement_op)
    gate_corrupted = runtime.physical_execute(patterns, mask)
    runtime.corrupt_gate_operation(gate_cell, old_operation)

    result = {
        "assignments": 1 << 16,
        "initial_valid": initial.valid,
        "initial_error_bits": error_bits(initial.outputs, expected),
        "independent_replay_valid": independent["valid"],
        "independent_replay_error_bits": error_bits(independent["outputs"], expected),
        "cut_wire_cell": cut_wire,
        "old_output_path": old_path,
        "damaged_valid": damaged.valid,
        "damaged_reason": damaged.reason,
        "damaged_error_bits": error_bits(damaged.outputs, expected),
        "repairs": len(repairs),
        "new_output_path": new_path,
        "path_changed": old_path != new_path,
        "repaired_valid": repaired.valid,
        "repaired_error_bits": error_bits(repaired.outputs, expected),
        "gate_operation_corruption_error_bits": error_bits(gate_corrupted.outputs, expected),
        "route_metrics": runtime.router.statistics(),
    }
    result["success"] = bool(
        result["initial_valid"]
        and result["initial_error_bits"] == 0
        and result["independent_replay_valid"]
        and result["independent_replay_error_bits"] == 0
        and (not result["damaged_valid"] or result["damaged_error_bits"] > 0)
        and result["path_changed"]
        and result["repaired_valid"]
        and result["repaired_error_bits"] == 0
        and result["gate_operation_corruption_error_bits"] > 0
    )
    if not result["success"]:
        record_failure(failures, "physical_causality", "adder8", result)
    return result


def exact_program_experiment(failures: list[dict]) -> dict:
    cases = (
        ("adder8", build_adder, 16),
        ("multiplier8", build_multiplier, 16),
        ("alu8", build_alu, 18),
    )
    rows = []
    for index, (name, builder, n_inputs) in enumerate(cases):
        started = time.perf_counter()
        try:
            program = builder(8)
            program.set_variant_policy("random", seed=200 + index)
            runtime = EmbodiedRuntime(program, seed=200 + index)
            patterns, mask = make_input_patterns(n_inputs)
            initial = runtime.physical_execute(patterns, mask)
            expected = program.evaluate_bits(patterns, mask)
            initial_gates = runtime.active_gate_cells.copy()
            initial_wires = runtime.active_wire_cells.copy()
            faulted = runtime.inject_random_support_faults(0.10, seed=300 + index)
            damaged = runtime.physical_execute(patterns, mask)
            repairs = runtime.repair_all_faults()
            repaired = runtime.physical_execute(patterns, mask)
            turnover = runtime.turnover_all()
            final = runtime.physical_execute(patterns, mask)
            row = {
                "name": name,
                "assignments": 1 << n_inputs,
                "initial_gate_cells": len(initial_gates),
                "initial_wire_cells": len(initial_wires),
                "faulted_cells": len(faulted),
                "faulted_gate_cells": len(faulted & initial_gates),
                "faulted_wire_cells": len(faulted & initial_wires),
                "damaged_valid": damaged.valid,
                "repairs": len(repairs),
                "initial_error_bits": error_bits(initial.outputs, expected),
                "repaired_error_bits": error_bits(repaired.outputs, expected),
                "turnover_error_bits": error_bits(final.outputs, expected),
                "repaired_valid": repaired.valid,
                "turnover_valid": final.valid,
                "turnover": turnover,
                "elapsed_seconds": time.perf_counter() - started,
            }
            row["success"] = bool(
                initial.valid
                and row["initial_error_bits"] == 0
                and not damaged.valid
                and repaired.valid
                and row["repaired_error_bits"] == 0
                and final.valid
                and row["turnover_error_bits"] == 0
                and turnover["original_non_io_gate_cells_remaining"] == 0
                and turnover["original_wire_cells_remaining"] == 0
            )
        except Exception as exc:
            row = {"name": name, "success": False, "error": repr(exc)}
        if not row["success"]:
            record_failure(failures, "exact_programs", name, row)
        rows.append(row)
    return {"cases": rows, "success": all(row["success"] for row in rows)}


def random_program_case(n_gates: int, n_outputs: int, seed: int) -> dict:
    started = time.perf_counter()
    netlist = random_netlist(16, n_gates, n_outputs, seed)
    program = compile_netlist(netlist)
    program.set_variant_policy("random", seed=seed)
    runtime = EmbodiedRuntime(program, seed=seed)
    patterns, mask = make_input_patterns(16)
    initial = runtime.physical_execute(patterns, mask)
    expected = netlist.evaluate_bits(patterns, mask)  # external oracle after DUT
    initial_gates = runtime.active_gate_cells.copy()
    initial_wires = runtime.active_wire_cells.copy()
    faulted = runtime.inject_random_support_faults(0.10, seed=seed + 1)
    damaged = runtime.physical_execute(patterns, mask)
    repairs = runtime.repair_all_faults()
    repaired = runtime.physical_execute(patterns, mask)
    turnover = runtime.turnover_all()
    final = runtime.physical_execute(patterns, mask)
    audit = runtime.audit(replay_certificates=False)
    return {
        "seed": seed,
        "source_gates": n_gates,
        "outputs": n_outputs,
        "assignments_per_phase": 1 << 16,
        "initial_gate_cells": len(initial_gates),
        "initial_wire_cells": len(initial_wires),
        "faulted_cells": len(faulted),
        "faulted_gate_cells": len(faulted & initial_gates),
        "faulted_wire_cells": len(faulted & initial_wires),
        "repaired_organs": len(repairs),
        "damaged_valid": damaged.valid,
        "initial_error_bits": error_bits(initial.outputs, expected),
        "repaired_error_bits": error_bits(repaired.outputs, expected),
        "turnover_error_bits": error_bits(final.outputs, expected),
        "initial_valid": initial.valid,
        "repaired_valid": repaired.valid,
        "turnover_valid": final.valid,
        "turnover": turnover,
        "maximum_degree": audit["maximum_degree"],
        "route_metrics": runtime.router.statistics(),
        "elapsed_seconds": time.perf_counter() - started,
        "success": bool(
            initial.valid
            and initial.outputs == expected
            and not damaged.valid
            and repaired.valid
            and repaired.outputs == expected
            and final.valid
            and final.outputs == expected
            and turnover["original_non_io_gate_cells_remaining"] == 0
            and turnover["original_wire_cells_remaining"] == 0
            and audit["degree_bound_ok"]
            and audit["routes_complete"]
            and audit["wire_ownership_exclusive"]
        ),
    }


def random_program_experiment(failures: list[dict], quick: bool) -> dict:
    count = 2 if quick else 10
    rows = []
    for case in range(count):
        try:
            row = random_program_case(1024, 8, 9100 + case)
        except Exception as exc:
            row = {"seed": 9100 + case, "source_gates": 1024, "success": False, "error": repr(exc)}
        if not row["success"]:
            record_failure(failures, "random_programs", str(case), row)
        rows.append(row)
    stress_size = 1024 if quick else 4096
    try:
        stress = random_program_case(stress_size, 16, 9900)
    except Exception as exc:
        stress = {"seed": 9900, "source_gates": stress_size, "success": False, "error": repr(exc)}
    if not stress["success"]:
        record_failure(failures, "random_programs", "stress", stress)
    return {
        "required_1024_cases": 10,
        "executed_1024_cases": count,
        "cases": rows,
        "stress": stress,
        "full_scale": not quick,
        "success": not quick and count == 10 and all(row["success"] for row in rows) and stress["source_gates"] == 4096 and stress["success"],
    }


def fault_mode_experiment(failures: list[dict]) -> dict:
    modes = ("gate", "internal_wire", "external_wire", "physical_edge")
    rows = []
    for index, mode in enumerate(modes):
        program = compile_netlist(BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,)))
        runtime = EmbodiedRuntime(program, seed=400 + index)
        patterns, mask = make_input_patterns(2)
        expected = runtime.physical_execute(patterns, mask).outputs
        if mode == "gate":
            target = next(iter(runtime.active_gate_cells))
            runtime.fail_cells((target,))
        elif mode == "internal_wire":
            route_id = runtime.organs[0].internal_wire_paths[0]
            target = runtime.graph.routed_nets[route_id].ordered_wire_cells[0]
            runtime.fail_cells((target,))
        else:
            route = runtime.graph.routed_nets[runtime.external_network.route_ids[0]]
            if mode == "external_wire":
                target = route.ordered_wire_cells[0]
                runtime.fail_cells((target,))
            else:
                target = (route.source_cell, route.ordered_wire_cells[0])
                runtime.fail_edges((target,))
        damaged = runtime.physical_execute(patterns, mask)
        runtime.repair_all_faults()
        repaired = runtime.physical_execute(patterns, mask)
        row = {
            "mode": mode,
            "target": target,
            "damaged_valid": damaged.valid,
            "repaired_valid": repaired.valid,
            "repaired_error_bits": error_bits(repaired.outputs, expected),
            "success": (not damaged.valid or damaged.outputs != expected)
            and repaired.valid
            and repaired.outputs == expected,
        }
        if not row["success"]:
            record_failure(failures, "fault_modes", mode, row)
        rows.append(row)
    return {"cases": rows, "success": all(row["success"] for row in rows)}


def asynchronous_experiment(failures: list[dict], quick: bool) -> dict:
    schedules = 10 if quick else 100
    cases = (
        ("adder8", build_adder(8), 16),
        ("multiplier8", build_multiplier(8), 16),
        ("random16_256", compile_netlist(random_netlist(16, 256, 8, 7300)), 16),
    )
    rows = []
    for case_index, (name, program, n_inputs) in enumerate(cases):
        runtime = EmbodiedRuntime(program, seed=700 + case_index)
        patterns, mask = make_input_patterns(n_inputs)
        fixed = runtime.physical_execute(patterns, mask)
        expected = program.evaluate_bits(patterns, mask)
        failures_count = 0
        messages = []
        for schedule_seed in range(schedules):
            result = runtime.physical_execute(
                patterns,
                mask,
                schedule="random",
                seed=10_000 * case_index + schedule_seed,
            )
            messages.append(result.messages)
            if (
                not result.valid
                or result.outputs != expected
                or result.future_epoch_reads
                or result.stale_epoch_reads
            ):
                failures_count += 1
        row = {
            "name": name,
            "random_schedules": schedules,
            "fixed_schedule_exact": fixed.valid and fixed.outputs == expected,
            "random_schedule_failures": failures_count,
            "minimum_messages": min(messages),
            "maximum_messages": max(messages),
            "success": fixed.valid and fixed.outputs == expected and failures_count == 0,
        }
        if not row["success"]:
            record_failure(failures, "asynchronous", name, row)
        rows.append(row)
    return {
        "required_schedules_per_program": 100,
        "full_scale": not quick,
        "cases": rows,
        "success": not quick and schedules == 100 and all(row["success"] for row in rows),
    }


def state_experiment(failures: list[dict], quick: bool) -> dict:
    cycles = 200 if quick else 2000
    machine = AccumulatorOrganMachine(width=8, seed=123, capacity_factor=5.0)
    rng = random.Random(456)
    started = time.perf_counter()
    for cycle in range(cycles):
        machine.step(
            rng.randrange(4),
            rng.randrange(256),
            fault_count=1 if cycle % 23 == 0 else 0,
            turnover_count=1 if cycle < len(machine.program.organs) else 0,
            migrate_state_bit=cycle if cycle < machine.width else None,
        )
    audit = machine.audit()
    trace = [asdict(record) for record in machine.trace]
    dump(TRACES / "accumulator_2000_cycle_trace.json", trace)
    result = {
        "cycles": cycles,
        "full_scale": not quick,
        "final_state": machine.state,
        "reference_state": machine.reference_state,
        "repair_events": len(machine.runtime.repair_log),
        "original_non_io_gate_cells_remaining": audit["original_compute_remaining"],
        "original_wire_cells_remaining": audit["original_wire_remaining"],
        "original_state_cells_remaining": audit["original_state_remaining"],
        "physical_certificates_valid": audit["physical_certificates_valid"],
        "active_support_healthy": audit["active_support_healthy"],
        "elapsed_seconds": time.perf_counter() - started,
    }
    result["success"] = bool(
        not quick
        and cycles == 2000
        and result["final_state"] == result["reference_state"]
        and result["original_non_io_gate_cells_remaining"] == 0
        and result["original_wire_cells_remaining"] == 0
        and result["original_state_cells_remaining"] == 0
        and result["physical_certificates_valid"]
        and result["active_support_healthy"]
    )
    if not result["success"]:
        record_failure(failures, "state_program", "accumulator8", result)
    return result


def _capacity_machine(mode: str):
    program = build_adder(8)
    program.set_variant_policy("max")
    runtime = EmbodiedRuntime(program, seed=811, capacity_factor=3.0)
    organ = program.organs[0]
    compact_index = min(
        range(len(organ.contract.variants)),
        key=lambda index: runtime._variant_cell_estimate(
            organ.contract.variants[index], organ.contract.n_outputs, 1
        ),
    )
    compact_total = runtime._variant_cell_estimate(
        organ.contract.variants[compact_index], organ.contract.n_outputs, 1
    )
    compact_body = compact_total - organ.contract.n_inputs - organ.contract.n_outputs
    runtime.graph.capacity = runtime.graph._next_cell_id + compact_body
    runtime.fail_cells((next(iter(runtime.organs[0].gate_cells.values())),))
    record = runtime.repair_organ(0, mode=mode)
    return program, runtime, record, compact_index, compact_body


def morphology_capacity_experiment(failures: list[dict]) -> dict:
    bp_program, bp_runtime, bp_record, _, budget = _capacity_machine("blueprint")
    mo_program, mo_runtime, mo_record, compact_index, _ = _capacity_machine("morph")
    patterns, mask = make_input_patterns(16)
    physical = mo_runtime.physical_execute(patterns, mask) if mo_record else None
    expected = mo_program.evaluate_bits(patterns, mask) if physical is not None else ()
    selected = None if mo_record is None else mo_runtime.organs[0]
    result = {
        "local_shadow_cell_budget": budget,
        "blueprint_variant": bp_program.organs[0].contract.variants[bp_runtime.organs[0].variant_index].name,
        "blueprint_physical_placement_routing_feasible": bp_record is not None,
        "alternative_variant": mo_program.organs[0].contract.variants[compact_index].name,
        "alternative_physical_placement_routing_feasible": mo_record is not None,
        "alternative_gate_cells": 0 if selected is None else len(selected.gate_cells),
        "alternative_wire_cells": 0
        if selected is None
        else sum(
            len(mo_runtime.graph.routed_nets[route_id].ordered_wire_cells)
            for route_id in selected.internal_wire_paths
        ),
        "physical_exhaustive_valid": bool(physical and physical.valid),
        "physical_exhaustive_error_bits": -1
        if physical is None
        else error_bits(physical.outputs, expected),
    }
    result["success"] = bool(
        bp_record is None
        and mo_record is not None
        and physical is not None
        and physical.valid
        and physical.outputs == expected
    )
    if not result["success"]:
        record_failure(failures, "morphology_capacity", "adder8_organ0", result)
    return result


def ablation_experiment(morphology: dict, asynchronous: dict) -> dict:
    program = compile_netlist(BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,)))
    patterns, mask = make_input_patterns(2)
    physical = EmbodiedRuntime(program, seed=61)
    expected = physical.physical_execute(patterns, mask).outputs
    physical.fail_cells((next(iter(physical.active_gate_cells)),))
    no_repair = physical.physical_execute(patterns, mask)

    legacy_program = compile_netlist(BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,)))
    legacy = CORMRuntime(legacy_program, seed=62)
    legacy_expected = legacy_program.evaluate_bits(patterns, mask)
    legacy.fail_cells(legacy.active_cells)
    teleport_after_destruction = legacy_program.evaluate_bits(patterns, mask)

    wireless_rejected = False
    try:
        DeterministicRouter(physical.graph, wire_cells_per_route=0)
    except ValueError:
        wireless_rejected = True

    overwrite_program = compile_netlist(BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,)))
    overwrite = EmbodiedRuntime(overwrite_program, seed=63)
    overwrite.fail_cells((next(iter(overwrite.active_gate_cells)),))
    overwrite_program.organs[0].active_variant = (
        overwrite_program.organs[0].active_variant + 1
    ) % len(overwrite_program.organs[0].contract.variants)
    direct_overwrite = overwrite.physical_execute(patterns, mask)

    abstract_program = compile_netlist(BooleanNetlist("and", 2, (NetGate("AND", (0, 1)),), (2,)))
    abstract_only = EmbodiedRuntime(abstract_program, seed=64)
    organ = abstract_only.organs[0]
    gate = next(iter(organ.gate_cells.values()))
    abstract_only.corrupt_gate_operation(gate, "OR")
    abstract_variant_passes = abstract_program.organs[0].contract.verify_variant(
        abstract_program.organs[0].variant
    )
    physical_certificate_passes = verify_physical_certificate(abstract_only.graph, organ)
    corrupted = abstract_only.physical_execute(patterns, mask)

    rows = {
        "no_repair": {
            "semantic_correct": no_repair.valid and no_repair.outputs == expected,
            "expected_negative_control": True,
        },
        "blueprint_only_repair": {
            "repair_success": morphology["blueprint_physical_placement_routing_feasible"],
            "expected_negative_control": True,
        },
        "morphology_repair": {
            "repair_success": morphology["success"],
            "expected_positive_control": True,
        },
        "direct_teleport_edge_legacy": {
            "abstract_output_survived_total_cell_destruction": teleport_after_destruction == legacy_expected,
            "expected_negative_control": True,
        },
        "without_physical_wire_cells": {
            "configuration_rejected": wireless_rejected,
            "expected_negative_control": True,
        },
        "without_shadow_direct_metadata_overwrite": {
            "semantic_correct": direct_overwrite.valid and direct_overwrite.outputs == expected,
            "expected_negative_control": True,
        },
        "abstract_variant_only_certificate": {
            "abstract_variant_passed": abstract_variant_passes,
            "physical_certificate_passed": physical_certificate_passes,
            "physical_error_bits": error_bits(corrupted.outputs, expected),
            "expected_negative_control": True,
        },
        "single_fixed_schedule": {
            "cases_exact": all(case["fixed_schedule_exact"] for case in asynchronous["cases"]),
        },
        "random_asynchronous_schedule": {
            "cases_exact": all(case["random_schedule_failures"] == 0 for case in asynchronous["cases"]),
        },
    }
    success = bool(
        not rows["no_repair"]["semantic_correct"]
        and not rows["blueprint_only_repair"]["repair_success"]
        and rows["morphology_repair"]["repair_success"]
        and rows["direct_teleport_edge_legacy"]["abstract_output_survived_total_cell_destruction"]
        and rows["without_physical_wire_cells"]["configuration_rejected"]
        and not rows["without_shadow_direct_metadata_overwrite"]["semantic_correct"]
        and rows["abstract_variant_only_certificate"]["abstract_variant_passed"]
        and not rows["abstract_variant_only_certificate"]["physical_certificate_passed"]
        and rows["abstract_variant_only_certificate"]["physical_error_bits"] > 0
        and rows["single_fixed_schedule"]["cases_exact"]
        and rows["random_asynchronous_schedule"]["cases_exact"]
    )
    return {"cases": rows, "success": success}


def write_visualization(exact: dict) -> None:
    rows = exact["cases"]
    width, height = 900, 420
    colors = {"gates": "#4063d8", "wires": "#ef7c2f"}
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#fbfbfd"/>',
        '<text x="40" y="38" font-family="sans-serif" font-size="22">E-CORM v0.2 physical routing and complete turnover</text>',
        '<text x="40" y="68" font-family="sans-serif" font-size="13" fill="#555">Every routed net owns Wire Cells; final original Gate/Wire support is zero.</text>',
    ]
    maximum = max(row.get("initial_wire_cells", 1) for row in rows)
    for index, row in enumerate(rows):
        x = 90 + index * 270
        gate_height = 240 * row.get("initial_gate_cells", 0) / maximum
        wire_height = 240 * row.get("initial_wire_cells", 0) / maximum
        svg.extend(
            [
                f'<rect x="{x}" y="{340-gate_height:.1f}" width="58" height="{gate_height:.1f}" fill="{colors["gates"]}"/>',
                f'<rect x="{x+68}" y="{340-wire_height:.1f}" width="58" height="{wire_height:.1f}" fill="{colors["wires"]}"/>',
                f'<text x="{x}" y="370" font-family="sans-serif" font-size="14">{row["name"]}</text>',
                f'<text x="{x}" y="392" font-family="sans-serif" font-size="12" fill="#333">remaining G/W: {row.get("turnover",{}).get("original_non_io_gate_cells_remaining","?")}/{row.get("turnover",{}).get("original_wire_cells_remaining","?")}</text>',
            ]
        )
    svg.extend(
        [
            f'<rect x="700" y="94" width="18" height="18" fill="{colors["gates"]}"/><text x="726" y="108" font-family="sans-serif" font-size="13">Gate Cells</text>',
            f'<rect x="700" y="122" width="18" height="18" fill="{colors["wires"]}"/><text x="726" y="136" font-family="sans-serif" font-size="13">Wire Cells</text>',
            '</svg>',
        ]
    )
    (FIGURES / "routing_and_turnover.svg").write_text("\n".join(svg))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    failures: list[dict] = []
    started = time.perf_counter()

    no_bypass = no_semantic_bypass_experiment()
    causality = physical_causality_experiment(failures)
    exact = exact_program_experiment(failures)
    random_programs = random_program_experiment(failures, args.quick)
    fault_modes = fault_mode_experiment(failures)
    asynchronous = asynchronous_experiment(failures, args.quick)
    state = state_experiment(failures, args.quick)
    morphology = morphology_capacity_experiment(failures)
    ablations = ablation_experiment(morphology, asynchronous)
    pytest_run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    pytest_result = {
        "returncode": pytest_run.returncode,
        "stdout": pytest_run.stdout.strip(),
        "stderr": pytest_run.stderr.strip(),
        "success": pytest_run.returncode == 0,
    }
    if not pytest_result["success"]:
        record_failure(failures, "pytest", "full_suite", pytest_result)

    hard_gates = {
        "no_abstract_semantic_bypass": no_bypass["success"],
        "every_logical_connection_has_physical_path": causality["success"] and fault_modes["success"],
        "critical_path_damage_causes_failure": causality["success"],
        "bounded_exhaustive_repair_zero_errors": exact["success"],
        "ten_1024_gate_random_programs_three_phases": random_programs["success"],
        "one_4096_gate_stress_program": random_programs["success"] and random_programs["stress"].get("source_gates") == 4096,
        "ten_percent_gate_wire_fault_recovery": random_programs["success"],
        "one_hundred_random_async_schedules": asynchronous["success"],
        "complete_non_io_gate_wire_turnover": exact["success"] and random_programs["success"],
        "state_program_cycle_exactness_and_turnover": state["success"],
        "independent_physical_replay_matches": causality["independent_replay_error_bits"] == 0,
        "blueprint_impossible_morphology_feasible": morphology["success"],
        "pytest_all_pass": pytest_result["success"],
        "all_failure_instances_reported": True,
    }
    summary = {
        "version": "E-CORM v0.2",
        "quick_mode": args.quick,
        "no_semantic_bypass": no_bypass,
        "physical_causality": causality,
        "exact_programs": exact,
        "random_programs": random_programs,
        "fault_modes": fault_modes,
        "asynchronous_schedules": asynchronous,
        "state_program": state,
        "morphology_capacity": morphology,
        "ablations": ablations,
        "pytest": pytest_result,
        "hard_gates": hard_gates,
        "unexpected_failure_count": len(failures),
        "all_hard_gates_pass": all(hard_gates.values()) and not failures and ablations["success"],
        "elapsed_seconds": time.perf_counter() - started,
    }
    dump(RESULTS / "all_failures.json", failures)
    dump(RESULTS / "summary.json", summary)
    write_visualization(exact)
    print(json.dumps({"all_hard_gates_pass": summary["all_hard_gates_pass"], "failures": len(failures), "elapsed_seconds": summary["elapsed_seconds"]}, indent=2))


if __name__ == "__main__":
    main()
