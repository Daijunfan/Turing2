# CORM v0.1 source audit for E-CORM v0.2

This audit records what the preserved v0.1 artifact actually establishes.  The
files under `results/` and the original `CORMRuntime` remain a legacy baseline;
they are not evidence of embodied execution.

## 1. Abstract evaluators used by the old experiments

`run_experiments.py` routes every reported program-level correctness result
through `Program.evaluate_bits` or `Program.evaluate_scalar`:

- `exact_check_adder`, `exact_check_multiplier`, and `exact_check_alu` call
  `Program.evaluate_bits` (lines 52, 64, and 77).
- `random_arithmetic_check` calls `Program.evaluate_scalar` for multiplier,
  adder, and ALU samples (lines 100, 108, and 121).
- `experiment_generic_compiler` calls `Program.evaluate_bits` before damage,
  after repair, and after turnover (lines 214, 220, and 224).
- `experiment_exact_programs`, `experiment_local_capacity_separation`,
  `experiment_large_multiplier64`, `experiment_turnover_multiplier32`, and
  `experiment_repeated_damage` inherit those abstract helper calls.
- `AccumulatorOrganMachine.step` and `Rule110OrganMachine.step` call
  `Program.evaluate_scalar` directly (`corm/sequential.py:119` and
  `corm/dynamics.py:138`).

The original tests do the same in `tests/test_core.py`, `tests/test_runtime.py`,
and `tests/test_compiler.py`.  `BooleanNetlist.evaluate_*` is an acceptable
external oracle only after DUT execution; in v0.1 it is compared with another
abstract evaluator, not a physical DUT.

## 2. Outputs that bypass physical mapping

All v0.1 program outputs bypass the `ActiveOrgan.cell_for_gate` mapping.
`Program.evaluate_bits` constructs a Python `nets` dictionary, evaluates each
active `Variant` (or the `Contract` when `semantic=True`), and returns values
from that dictionary.  It never receives a `CORMRuntime`, never reads a Cell,
and never consults failed or retired substrate state.  Consequently every old
program experiment—including exact arithmetic, generic DAGs, turnover,
accumulator, and Rule 110—can report correct output even when its assigned
cells are destroyed.

`CORMRuntime` changes `active_variant`, `cell_for_gate`, ownership metadata, and
certificate hashes, but supplies no data-execution method.  The old tests only
check the abstract `Program` after those metadata changes.

## 3. Topology edges that are not routed paths

`CORMRuntime.topology_edges` emits a direct `(source_cell, destination_cell,
gate_op)` tuple for each dependency whose producer has a mapped gate cell.
Those tuples may span arbitrary Manhattan distance.  There are no Wire Cells,
ordered paths, occupied routing resources, per-hop messages, congestion
checks, edge-failure state, or continuity validation.  Primary inputs and
constants have `None` endpoints and therefore have no physical edge at all.
`_wirelength` merely sums endpoint Manhattan distance; it does not construct a
route.  Thus every v0.1 topology edge is a placement-level dependency estimate,
not an executable physical path.

## 4. Why the old certificate is only a digest

`ActiveOrgan.certificate` is
`sha256(contract_hash + variant.fingerprint)`.  `CertificateTree` hashes that
digest with logical boundary net identifiers and combines leaf hashes in a
Merkle-style tree.  Verification recomputes the same metadata hash after using
`Contract.verify_variant`, which itself calls `Variant.evaluate_bits`.

The certificate contains no physical subgraph, route list, boundary Cell
mapping, physical truth outputs, generation-bound replay result, ownership
proof, or path-integrity evidence.  Matching the hash proves only that the same
abstract metadata was hashed.  It cannot prove that deployed Cells implement
the contract or that communication paths exist.

## 5. Global information visible to the central controller

The v0.1 runtime centrally holds and reads:

- the complete `Program`, all organs, contracts, variants, module paths, and
  program-wide net identifiers;
- the complete substrate free/failed/retired sets and every Cell coordinate;
- the global `cell_owner` reverse index and `pending_fault_organs` set;
- the global `net_producer` table;
- every active organ's placement, variant, generation, and certificate;
- global candidate pools, free-cell searches, placement costs, wirelength
  estimates, certificate tree, and turnover order.

The v0.2 scope may retain a central compiler, router, event-queue scheduler, and
repair coordinator.  It must not let a Cell transition or DUT data execution
read this information: a transition may read only its local state, neighbor
messages, fixed operation, and epoch.

## Legacy result support labels

- `results/contract_space.json` and `results/random6_contracts.json`: support
  bounded abstract synthesis equivalence only.
- `results/generic_compiler.json` and `results/exact_programs.json`: support
  abstract netlist/program equivalence only; their damage and turnover phases
  do not establish physical causality.
- `results/scaling.*`, `fault_fraction*`, `large_multiplier64.json`,
  `turnover_multiplier32.json`, and `repeated_damage.json`: support metadata
  allocation/repair simulations, not routed physical repair.
- `results/local_capacity_separation.json`: supports a Gate-count allocation
  witness only; it omits Wire resources and route feasibility.
- `results/stateful_vm.json`, `vm_trace.csv`, `rule110.json`, and
  `rule110_trace.csv`: support abstract sequential evaluation coupled to Cell
  bookkeeping, not physical state/data execution.
- `results/summary.json` and the five old figures aggregate those same legacy
  scopes.  Their `success=true` fields must never be interpreted as v0.2 hard
  gate results.

Accordingly, the v0.1 claim that complete "physical-support turnover" preserves
execution is reclassified as **abstract semantic preservation under placement
metadata turnover**.  E-CORM v0.2 results are written separately under
`results_v02/`.
