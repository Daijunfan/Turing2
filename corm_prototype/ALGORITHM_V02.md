# E-CORM v0.2 algorithm

## Initial deployment

1. The unchanged central compiler produces a `Program` of Contract organs.
2. The physical deployer allocates program I/O and constant boundary Cells.
3. For each organ it allocates boundary Router Cells and real Gate Cells for
   the selected Variant.
4. Every local source-consumer dependency is expanded into a bounded-degree
   fanout chain and one or more RoutedNets with exclusive Wire Cells.
5. External program nets are routed between program and organ boundaries.
6. Every organ is independently replayed over its complete local truth table.
   Deployment aborts if the physical output differs from the Contract.

The compiler and router are central in v0.2.  They configure the machine; they
do not execute program data.

## Data execution

For epoch `e`:

1. Reset per-epoch inbox/valid state; retain only latched STATE values.
2. Inject input/constant/STATE bitsets into their physical source Cells.
3. Put outgoing neighbor messages on the event queue.
4. Select one pending destination (FIFO or seeded random fair order).
5. Call `cell_transition` with only that Cell and its adjacent messages.
6. Enqueue messages only across nonfailed registered physical edges.
7. Repeat until the queue is empty.
8. Return OK only if all output boundary Cells emitted valid values for `e`;
   otherwise return INVALID or TIMEOUT.

Neither `PhysicalExecutor.execute` nor `cell_transition` receives a Program,
Variant, Contract, or abstract netlist.  `tests/test_no_semantic_bypass.py`
monkeypatches all abstract evaluators to raise and verifies that combinational
and sequential physical execution still succeeds.

## Physical certificate

For an organ and generation, the verifier:

1. validates every ordered route and exclusive Wire owner;
2. rebuilds an execution view containing only the organ's physical Cells,
   boundary mapping, and routed paths;
3. injects all boundary input combinations as bitsets;
4. independently propagates them through Wire/Router/Gate Cells;
5. compares actual outputs with `Contract.truth_outputs`;
6. hashes the exact physical subgraph and boundary mapping.

The resulting `PhysicalCertificate` stores the Contract hash, physical
subgraph digest, boundary mapping, replayed truth outputs, verifier version,
and generation.  A hash match alone is insufficient: verification always
rechecks path/ownership and replays the physical subgraph.

## EMHS physical repair

For a damaged internal Gate/Wire/edge:

1. Map the failed resource to its owning organ through Cell and RoutedNet
   ownership.
2. Read the organ's exact boundary Contract.
3. Order equivalent Variant candidates by required physical Gate/Router/Wire
   resources (blueprint mode restricts the list to the current Variant).
4. Allocate a complete shadow set of Gate, Const, Router, and Wire Cells.
5. Route every internal dependency and organ output.
6. Exhaustively replay the shadow physical subgraph.
7. Create and verify its PhysicalCertificate.
8. Increment generation and switch the organ mapping.
9. Disconnect and release or retire every old internal route and body Cell.

External Wire/edge damage uses the same shadow principle for the complete
external network.  The old and new paths coexist only while quiescent; the
generation cutover removes old ports before another DUT epoch.

Changing `active_variant`, `cell_for_gate`, or a certificate digest without the
shadow build does not repair the physical graph and is an explicit negative
control.

## Complete turnover

1. Mark every already-free initial Gate/Wire ID RETIRED so it cannot reappear.
2. Shadow-replace every organ, preferring a different exact morphology.
3. Retire its old Gate and internal Wire support after cutover.
4. Build a fresh complete external network and retire the old one.
5. For stateful machines, migrate each initial State Cell and rebuild its
   feedback paths.
6. Require all initial Gate/Wire/State intersections with active support to be
   empty before declaring success.

## Failure behavior

- A failed Cell cannot produce a valid message.
- A failed physical edge cannot deliver a message.
- A missing path cannot reuse an earlier epoch's value.
- Route allocation failure returns failure; it never inserts a direct edge.
- A repair cannot cut over until physical exhaustive replay succeeds.

The central coordinator can still inspect global ownership, contracts,
capacity, and failure sets.  Making development and repair fully local is not
claimed by this version.
