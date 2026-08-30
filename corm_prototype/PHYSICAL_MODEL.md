# E-CORM v0.2 physical model

E-CORM v0.2 (Embodied Contract-Organ Machine, 具身契约器官机) uses an
explicit bounded-degree Cell adjacency graph as its physical substrate.  A
coordinate is a stable substrate address; physical distance one is an edge in
`PhysicalGraph.physical_edges`.  Execution never treats coordinate proximity
or a logical dependency as communication.  Only a registered physical edge
can deliver a neighbor message.

This v0.2 substrate is a bonded-cell graph, not a timing-accurate Euclidean
floorplan.  The central deterministic router allocates FREE Cells and creates
bounded-degree local bonds.  Geometry-aware 2-D/3-D placement, timing closure,
and fabrication constraints remain later work.

## Cells and local degree

`corm/physical_cells.py` defines the single `PhysicalCell` representation.  It
contains:

- `cell_id`, `coordinate`, `role`, `failed`, `organ_id`, and `net_id`;
- fixed `operation`, `input_ports`, and `output_ports`;
- `epoch`, `value`, `valid`, `generation`, and `neighbors`;
- a local message inbox and transition bookkeeping.

Roles are `FREE`, `INPUT_BOUNDARY`, `OUTPUT_BOUNDARY`, `CONST`, `GATE`, `WIRE`,
`ROUTER`, `STATE`, `FAILED`, and `RETIRED`.  The configured physical degree
bound is six.  The v0.2 fanout construction limits active graphs to degree four:
a Gate has at most three input paths and one output path, while fanout is
expanded into degree-three Router chains.

FAILED and RETIRED Cells cannot be allocated or crossed.  Permanent failures
remain recorded even after their old route or organ is disconnected.

## Routed nets

Every point-to-point dependency is represented by:

```text
RoutedNet {
    net_id,
    source_cell,
    destination_cell,
    ordered_wire_cells,
    generation,
    logical_net_id,
    organ_id,
    kind,
    destination_port
}
```

Every route owns at least one Wire Cell.  The path is exactly
`source -> ordered_wire_cells... -> destination`; each consecutive pair must
be present in `physical_edges`.  A Wire Cell belongs to one routed net, and the
graph audit rejects overlap, missing hops, direct Gate-to-Gate edges, or an
empty teleport route.  Deleting any Wire Cell or physical edge therefore
interrupts the only message path.

One logical signal with multiple consumers is expanded into a Router chain.
Each chain segment and each consumer branch is itself a physical RoutedNet.
This makes resource ownership and failure localization explicit without
allowing unbounded source degree.

The router reports allocation attempts/failures, active Wire Cells, path hops,
maximum path length, and congestion.  In v0.2 each routed segment contains one
exclusive Wire Cell (two physical hops), so congestion is exactly one.

## Physical organs

Each active logical organ has a `PhysicalOrgan` containing:

- physical Gate Cells for the selected Variant;
- stable input and output boundary Router Cells;
- internal Const and fanout Router Cells;
- every internal RoutedNet and Wire Cell;
- its exact Contract, generation, Variant index, and PhysicalCertificate.

Program inputs, constants, and outputs are physical boundary Cells.  Separate
external RoutedNets join those boundaries to organ boundaries.  The data plane
therefore contains no Python-net-dictionary shortcut.

## Epoch-valid execution

`cell_transition(cell_state, neighbor_messages)` is the only data transition.
It reads only the Cell state, messages from fixed neighbor ports, its opcode,
and current epoch.  A central queue chooses activation order but does not
calculate a gate result.

Inputs and constants inject an epoch-tagged bitset.  Wire and Router Cells copy
it one hop.  A Gate waits until all fixed input ports have messages from the
same epoch and applies `NOT`, `BUF`, `AND`, `OR`, `XOR`, or `MUX`.  An output is
returned only when every output boundary is valid in that epoch.  A failed
Cell propagates INVALID when activated; a failed edge suppresses delivery and
causes TIMEOUT.  Each epoch starts with an empty inbox, so old values cannot be
reused.

Python integers carry one bit per input assignment.  Sixteen-input programs
therefore propagate all 65,536 assignments through the physical Cells in one
run without changing the local rule.

## State Cells

The 8-bit accumulator replaces its first eight input boundary Cells with
physical STATE Cells.  Each cycle follows an explicit loop:

```text
STATE -> external Wire paths -> physical ALU organs
      -> output boundary -> feedback Wire -> STATE
```

A STATE Cell emits its stored value at the start of an epoch and latches only a
same-epoch feedback message.  Migration allocates a new STATE Cell, copies the
quiescent bit, constructs new external and feedback routes, atomically switches
the boundary mapping, and retires the old Cell.

## Failures and retirement

Supported permanent faults are Gate Cell, Wire Cell, physical edge, Router,
and State Cell state flags.  The registered hard experiments inject Gate,
internal Wire, inter-organ Wire, and edge failures.  Repair builds and replays
a shadow organ or shadow external network before cutover.  Complete turnover
retires all initial non-I/O Gate and Wire Cells; stateful turnover also migrates
all initial State Cells.  Initial IDs are prohibited from later allocation.

## Export and independent replay

`EmbodiedRuntime.export_machine` writes Cells, physical edges, RoutedNets,
boundary Cells, gate operations, State Cells, failures, and generation as JSON.
`audit/independent_physical_replay.py` is a separate stdlib-only executor.  It
validates route continuity/ownership and reimplements epoch propagation,
INVALID/TIMEOUT, and gate operations without importing or invoking
`EmbodiedRuntime`.
