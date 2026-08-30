from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import random
from typing import Sequence


def _edge(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _gate(operation: str, args: list[int], mask: int) -> int:
    if operation == "NOT":
        return (~args[0]) & mask
    if operation == "BUF":
        return args[0]
    if operation == "AND":
        return args[0] & args[1]
    if operation == "OR":
        return args[0] | args[1]
    if operation == "XOR":
        return args[0] ^ args[1]
    if operation == "MUX":
        return (args[0] & args[1]) | (((~args[0]) & mask) & args[2])
    raise ValueError(f"unknown gate operation: {operation}")


def validate_machine(machine: dict) -> None:
    cells = {int(cell["cell_id"]): cell for cell in machine["cells"]}
    edges = {_edge(int(a), int(b)) for a, b in machine["physical_edges"]}
    max_degree = int(machine["max_degree"])
    for cell_id, cell in cells.items():
        neighbors = {int(value) for value in cell["neighbors"]}
        if len(neighbors) > max_degree:
            raise ValueError(f"Cell {cell_id} exceeds the physical degree bound")
        if any(_edge(cell_id, neighbor) not in edges for neighbor in neighbors):
            raise ValueError(f"Cell {cell_id} contains a non-physical neighbor")

    occupied: set[int] = set()
    for route in machine["routed_nets"]:
        source = int(route["source_cell"])
        destination = int(route["destination_cell"])
        wires = tuple(int(value) for value in route["ordered_wire_cells"])
        if not wires:
            raise ValueError("teleport route without Wire Cells")
        if occupied.intersection(wires):
            raise ValueError("Wire Cell is shared by multiple routed nets")
        occupied.update(wires)
        path = (source, *wires, destination)
        if any(_edge(a, b) not in edges for a, b in zip(path, path[1:])):
            raise ValueError("routed net contains non-adjacent path elements")
        for wire in wires:
            cell = cells[wire]
            original_role = cell.get("original_role")
            if cell["role"] != "WIRE" and original_role != "WIRE":
                raise ValueError("routed net path contains a non-Wire Cell")
            if int(cell["net_id"]) != int(route["net_id"]):
                raise ValueError("Wire Cell ownership does not match routed net")


def replay(
    machine: dict,
    input_values: Sequence[int],
    universe_mask: int,
    *,
    epoch: int = 1,
    schedule_seed: int | None = None,
) -> dict:
    """Independently replay exported physical Cells without importing CORM."""

    validate_machine(machine)
    cells = {int(cell["cell_id"]): cell for cell in machine["cells"]}
    edges = {_edge(int(a), int(b)) for a, b in machine["physical_edges"]}
    failed_edges = {_edge(int(a), int(b)) for a, b in machine.get("failed_edges", [])}
    input_cells = tuple(int(value) for value in machine["boundary_cells"]["inputs"])
    output_cells = tuple(int(value) for value in machine["boundary_cells"]["outputs"])
    if len(input_values) != len(input_cells):
        raise ValueError("input arity mismatch")

    inbox: dict[int, dict[int, tuple[int, bool]]] = {}
    values: dict[int, int] = {}
    valid: dict[int, bool] = {}
    emitted: set[int] = set()
    fifo: deque[tuple[int, int, int, bool]] = deque()
    random_queue: list[tuple[int, int, int, bool]] = []
    rng = random.Random(schedule_seed)
    messages = 0
    activations = 0

    def enqueue(sender: int, target: int, value: int, is_valid: bool) -> None:
        nonlocal messages
        link = _edge(sender, target)
        if link not in edges or link in failed_edges:
            return
        item = (target, sender, value & universe_mask, is_valid)
        messages += 1
        if schedule_seed is None:
            fifo.append(item)
        else:
            random_queue.append(item)

    def emit_source(cell_id: int, value: int) -> None:
        cell = cells[cell_id]
        is_valid = not bool(cell["failed"]) and cell["role"] != "FAILED"
        values[cell_id] = value & universe_mask
        valid[cell_id] = is_valid
        emitted.add(cell_id)
        for target in cell["output_ports"]:
            enqueue(cell_id, int(target), value, is_valid)

    for cell_id, value in zip(input_cells, input_values):
        if cells[cell_id]["role"] != "STATE":
            emit_source(cell_id, int(value))
    for cell_id, cell in cells.items():
        if cell["role"] == "CONST":
            emit_source(cell_id, universe_mask if cell["operation"] == "1" else 0)
        elif cell["role"] == "STATE":
            emit_source(cell_id, int(cell["value"]))

    while fifo or random_queue:
        if schedule_seed is None:
            target, sender, value, message_valid = fifo.popleft()
        else:
            index = rng.randrange(len(random_queue))
            target, sender, value, message_valid = random_queue.pop(index)
        activations += 1
        cell = cells[target]
        if cell["role"] == "STATE":
            if message_valid and not cell["failed"]:
                values[target] = value
                valid[target] = True
            else:
                valid[target] = False
            continue
        if target in emitted:
            continue
        expected = {int(value) for value in cell["input_ports"].values()}
        if sender not in expected:
            continue
        inbox.setdefault(target, {})[sender] = (value, message_valid)
        if not expected.issubset(inbox[target]):
            continue
        ordered = [inbox[target][int(cell["input_ports"][name])] for name in sorted(cell["input_ports"])]
        is_valid = all(item[1] for item in ordered) and not cell["failed"] and cell["role"] != "FAILED"
        args = [item[0] for item in ordered]
        out = 0
        if is_valid:
            try:
                if cell["role"] == "GATE":
                    out = _gate(cell["operation"], args, universe_mask)
                elif cell["role"] in {"WIRE", "ROUTER", "OUTPUT_BOUNDARY"}:
                    out = args[0] & universe_mask
                else:
                    is_valid = False
            except (IndexError, ValueError):
                is_valid = False
                out = 0
        values[target] = out
        valid[target] = is_valid
        emitted.add(target)
        for next_cell in cell["output_ports"]:
            enqueue(target, int(next_cell), out, is_valid)

    output_values = tuple(values.get(cell_id, 0) & universe_mask for cell_id in output_cells)
    output_valid = tuple(cell_id in emitted and valid.get(cell_id, False) for cell_id in output_cells)
    reached = tuple(cell_id in emitted for cell_id in output_cells)
    return {
        "outputs": output_values,
        "output_valid": output_valid,
        "valid": all(output_valid),
        "reason": "OK" if all(output_valid) else "INVALID" if all(reached) else "TIMEOUT",
        "epoch": epoch,
        "messages": messages,
        "activations": activations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("machine", type=Path)
    parser.add_argument("--inputs", required=True, help="comma-separated decimal bitsets")
    parser.add_argument("--mask", required=True, type=int)
    parser.add_argument("--epoch", type=int, default=1)
    parser.add_argument("--schedule-seed", type=int)
    args = parser.parse_args()
    machine = json.loads(args.machine.read_text())
    values = [int(value) for value in args.inputs.split(",") if value]
    result = replay(
        machine,
        values,
        args.mask,
        epoch=args.epoch,
        schedule_seed=args.schedule_seed,
    )
    printable = dict(result)
    printable["outputs"] = [str(value) for value in result["outputs"]]
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
