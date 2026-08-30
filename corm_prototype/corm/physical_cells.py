from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable


class CellRole(str, Enum):
    FREE = "FREE"
    INPUT_BOUNDARY = "INPUT_BOUNDARY"
    OUTPUT_BOUNDARY = "OUTPUT_BOUNDARY"
    CONST = "CONST"
    GATE = "GATE"
    WIRE = "WIRE"
    ROUTER = "ROUTER"
    STATE = "STATE"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class NeighborMessage:
    sender: int
    epoch: int
    value: int
    valid: bool


@dataclass(frozen=True)
class OutgoingMessage:
    target: int
    message: NeighborMessage


@dataclass
class PhysicalCell:
    cell_id: int
    coordinate: tuple[int, int, int]
    role: CellRole
    failed: bool = False
    organ_id: int | None = None
    net_id: int | None = None
    operation: str = ""
    input_ports: dict[str, int] = field(default_factory=dict)
    output_ports: set[int] = field(default_factory=set)
    epoch: int = -1
    value: int = 0
    valid: bool = False
    generation: int = 0
    neighbors: set[int] = field(default_factory=set)
    inbox: dict[int, NeighborMessage] = field(default_factory=dict, repr=False)
    universe_mask: int = 1
    emitted_epoch: int = -1
    latched_epoch: int = -1
    original_role: CellRole | None = None

    def execution_copy(self, epoch: int, universe_mask: int) -> "PhysicalCell":
        return replace(
            self,
            input_ports=dict(self.input_ports),
            output_ports=set(self.output_ports),
            neighbors=set(self.neighbors),
            epoch=epoch,
            value=self.value if self.role == CellRole.STATE else 0,
            valid=False,
            inbox={},
            universe_mask=universe_mask,
            emitted_epoch=-1,
            latched_epoch=-1,
        )


def _gate_value(operation: str, args: list[int], mask: int) -> int:
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
    raise ValueError(f"unknown physical gate op: {operation}")


def _emit(cell: PhysicalCell) -> tuple[OutgoingMessage, ...]:
    message = NeighborMessage(cell.cell_id, cell.epoch, cell.value, cell.valid)
    return tuple(OutgoingMessage(target, message) for target in sorted(cell.output_ports))


def cell_transition(
    cell_state: PhysicalCell,
    neighbor_messages: Iterable[NeighborMessage],
) -> tuple[PhysicalCell, tuple[OutgoingMessage, ...]]:
    """The single data-plane transition rule.

    The function only reads the supplied Cell state and messages from Cells in
    its fixed input ports.  Selection of an activation order is deliberately
    outside this rule.
    """

    cell = cell_state
    messages = tuple(neighbor_messages)

    if cell.role == CellRole.STATE and messages:
        for message in messages:
            if message.sender in cell.input_ports.values() and message.epoch == cell.epoch:
                cell.inbox[message.sender] = message
        if cell.inbox and cell.latched_epoch != cell.epoch:
            message = next(iter(cell.inbox.values()))
            if message.valid and not cell.failed:
                cell.value = message.value & cell.universe_mask
                cell.valid = True
            else:
                cell.valid = False
            cell.latched_epoch = cell.epoch
        return cell, ()

    if cell.emitted_epoch == cell.epoch:
        return cell, ()

    expected = set(cell.input_ports.values())
    for message in messages:
        if message.sender in expected and message.epoch == cell.epoch:
            cell.inbox[message.sender] = message

    if expected and not expected.issubset(cell.inbox):
        return cell, ()

    incoming = [cell.inbox[cell.input_ports[name]] for name in sorted(cell.input_ports)]
    incoming_valid = all(message.valid for message in incoming)
    cell.valid = incoming_valid and not cell.failed and cell.role != CellRole.FAILED

    if cell.valid:
        args = [message.value for message in incoming]
        try:
            if cell.role == CellRole.GATE:
                cell.value = _gate_value(cell.operation, args, cell.universe_mask)
            elif cell.role in {CellRole.WIRE, CellRole.ROUTER, CellRole.OUTPUT_BOUNDARY}:
                cell.value = args[0] & cell.universe_mask
            else:
                cell.valid = False
                cell.value = 0
        except (IndexError, ValueError):
            cell.valid = False
            cell.value = 0
    else:
        cell.value = 0

    cell.emitted_epoch = cell.epoch
    return cell, _emit(cell)
