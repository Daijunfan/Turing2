# E-CORM v0.2 claims and limitations

## Claims supported by the v0.2 artifact

1. Program data is computed by physical Gate Cells and delivered through
   explicit, exclusive Wire Cell paths.  The DUT succeeds while all abstract
   Program/Variant/Contract evaluators are disabled.
2. Physical causality is observable: corrupting a live Gate changes output;
   removing a required Wire or edge produces INVALID/TIMEOUT; destroying all
   active Gate/Wire support cannot preserve a valid result.
3. EMHS repair allocates new Gate/Router/Wire support, exhaustively replays the
   shadow physical organ, verifies a PhysicalCertificate, and then cuts over.
4. The 8-bit adder and multiplier exhaust all 65,536 input pairs in each of
   three phases.  The 8-bit ALU exhausts all 262,144 state/operand/opcode
   combinations.  Initial, repaired, and complete-turnover phases have zero
   output-bit errors.
5. Ten independent 16-input, 1,024-source-gate, 8-output random DAGs exhaust
   all 65,536 assignments before damage, after 10% permanent Gate/Wire damage
   and repair, and after complete physical turnover, with zero errors.
6. One 16-input, 4,096-source-gate, 16-output random DAG passes the same three
   exhaustive physical phases with zero errors.
7. Gate, internal Wire, inter-organ Wire, and physical-edge fault controls all
   fail before repair and recover exactly afterward.
8. Three programs each produce identical exact outputs under 100 seeded random
   fair Cell activation orders, with no stale/future-epoch reads.
9. Complete turnover leaves zero active initial non-I/O Gate and Wire Cells.
10. The physical 8-bit accumulator runs 2,000 exact cycles during permanent
    Gate/Wire faults, compute turnover, feedback rerouting, and migration of all
    initial State Cells; final Gate/Wire/State initial support is zero.
11. The independent JSON auditor reproduces runtime and reference outputs
    without calling the runtime executor.
12. A finite-capacity physical witness admits a seven-Gate/twenty-two-Wire
    alternative morphology while the old blueprint cannot allocate and route
    its shadow; the alternative exhaustively implements the contract.
13. All v0.2 hard gates and all 16 registered tests pass in
    `results_v02/summary.json`.

These are simulator claims for the checked finite instances.  The authoritative
machine-readable evidence is `results_v02/summary.json`; the legacy `results/`
directory supports only the narrower scopes recorded in `AUDIT_V02.md`.

## Limitations and deliberately retained central control

1. The substrate is a bounded-degree bonded Cell graph.  Coordinates are stable
   addresses and edges define physical distance one, but v0.2 does not perform
   Euclidean 2-D/3-D floorplanning, timing closure, analog signal modeling,
   power analysis, or fabrication-rule checks.
2. A central compiler selects organ boundaries and logical dependencies.  A
   central deterministic router allocates FREE Cells and bonds physical paths.
3. A central repair coordinator reads the full failure/ownership tables,
   Contract and Variant library, capacity, and generation state.  It chooses
   repair candidates and performs atomic cutover.
4. A central event queue chooses which locally enabled Cell activates next.
   The queue does not compute values, and random-order tests establish bounded
   schedule independence, but this is not a distributed hardware scheduler.
5. Physical certificates are exhaustive executable proofs for bounded Boolean
   interfaces, not Lean/Coq proofs and not proofs about unbounded programs.
6. The router uses one exclusive Wire Cell per point-to-point routed segment.
   It validates capacity, exclusivity, degree, path continuity, and fault
   avoidance, but does not optimize geometric length or model electromagnetic
   interference.
7. State migration copies a bit during a quiescent epoch boundary under central
   coordination.  Concurrent analog metastability is outside the model.
8. The fault experiments cover permanent stuck/unavailable Cells and physical
   edges.  They do not model transient analog faults, Byzantine message
   fabrication, or correlated substrate-wide power loss.
9. Full local morphogenesis and repair by identical Cells is explicitly a next
   stage.  E-CORM v0.2 claims a local data plane, not a fully decentralized
   developmental controller.
10. Passing bounded experiments does not establish a universal asymptotic
    theorem or predict scientific awards.

## Interpretation guardrails

None of the following alone counts as physical success: abstract Program
equivalence, `active_variant` metadata changes, a matching hash, a failed Cell
disappearing from the active set, or an endpoint-distance wirelength estimate.
Success requires live physical replay, causal negative controls, exact repair,
and explicit support turnover together.
