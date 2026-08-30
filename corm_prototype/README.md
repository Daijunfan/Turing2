# E-CORM v0.2: Embodied Contract-Organ Machine

The repository now contains the physical E-CORM v0.2 data plane.  Every Gate
is a Cell, every dependency owns an explicit adjacent Wire Cell path, physical
faults causally affect output, and repair constructs and exhaustively replays a
new physical subgraph before cutover.  The original `CORMRuntime`, experiments,
and `results/` are preserved as the v0.1 legacy baseline; see `AUDIT_V02.md` for
their exact support scope.

Run the full v0.2 hard-gate suite with:

```bash
python3 run_experiments_v02.py
```

Its authoritative output is `results_v02/summary.json`.  Model and algorithm
details are in `PHYSICAL_MODEL.md` and `ALGORITHM_V02.md`; the direct result is
summarized in `NEXT_RESULT.md`.

## Legacy CORM v0.1

CORM is an executable research prototype for **Exact Contract Morphogenesis**: a program is compiled into a graph of bounded-interface semantic contracts; each contract owns multiple independently synthesized, exhaustively verified circuit morphologies. When cells fail, the runtime does not restore the old blueprint. It selects any exact morphology that fits the local resource budget, grows it as a shadow organ, validates its certificate, atomically cuts over, and updates only the corresponding path in a certificate tree.

The implementation is deliberately training-free. The same compiler and runtime are used for arithmetic circuits, arbitrary random Boolean DAGs, a stateful accumulator machine, and a Rule 110 dynamical system.

## Core algorithm

For a damaged organ with contract `C`, current implementation `V_old`, local free-cell budget `B_R`, and verified morphology set `M(C)`:

1. Locate the organ through the `cell_owner` reverse index.
2. Enumerate exact candidates `V in M(C)`.
3. Keep candidates whose gate count fits `B_R`.
4. Place each candidate near the old anchor and estimate wire length.
5. Select the minimum-cost candidate.
6. Verify the contract certificate again.
7. Build the candidate as a shadow implementation.
8. Atomically switch the organ boundary.
9. Retire or release old cells.
10. Update only the leaf-to-root certificate path.

The executable implementation is in `corm/runtime.py`.

## Reproduce

```bash
python3 -m pip install -e .
./run_all.sh
```

The full run writes JSON/CSV results into `results/`, figures into `figures/`, aggregates everything into `results/summary.json`, and runs the test suite.

Run one stage:

```bash
python3 run_experiments.py --stage generic_compiler
python3 run_experiments.py --stage large64
python3 run_experiments.py --stage rule110
```

Run tests only:

```bash
pytest -q
```

## Verified headline results

- All **65,536 four-input Boolean functions** received at least two distinct exact implementations.
- **196,601** four-input variants were exhaustively checked; another **3,000** variants covered 1,000 random six-input functions.
- A generic compiler processed **12 independent 4,096-gate Boolean DAGs**. All 16-input assignments were exhaustively checked before damage, after 15% permanent damage, and after complete component turnover: **37,748,736 output bits, zero mismatches**.
- Exact 8-bit adder, multiplier, and ALU semantics were exhaustively verified before damage, after 20% random permanent damage, and after complete turnover.
- A 64-bit multiplier contained **12,288 organs and 96,254 active cells**. After 4,812 permanent faults, all affected organs regenerated and all local contracts remained exact.
- With eight damaged organs held fixed while program size grew from 32 to 4,096 organs, local regrowth remained exactly **56 cells**; global recompilation grew from 224 to 28,672 cells.
- A deterministic local-capacity witness made blueprint repair impossible while a seven-cell exact alternative morphology succeeded over all 65,536 input pairs.
- A 32-bit multiplier replaced all **3,072 organs**, retained no original active cell, changed every organ morphology, and preserved semantics.
- A stateful accumulator executed 2,000 exact cycles while all original compute and state cells were replaced.
- A 1,024-cell Rule 110 ring executed 2,048 exact rounds—**2,097,152 cell updates**—through permanent faults and full compute/state turnover.

See `RESEARCH_REPORT_CN.md`, `CLAIMS_AND_LIMITATIONS.md`, and `results/summary.json`.
