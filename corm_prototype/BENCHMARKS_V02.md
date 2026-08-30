# E-CORM v0.2 benchmarks

The table values below are copied from the full run in
`results_v02/summary.json` on 2026-08-30.  Times are wall-clock measurements on
the recorded local environment and should be treated as implementation
measurements, not portable performance guarantees.

## Exact arithmetic programs

| Program | Assignments/phase | Initial Gate Cells | Initial Wire Cells | 10% failed support | Repaired organs | Initial / repaired / turnover error bits | Time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| adder8 | 65,536 | 108 | 377 | 48 | 8 | 0 / 0 / 0 | 0.069 |
| multiplier8 | 65,536 | 1,545 | 5,805 | 735 | 154 | 0 / 0 / 0 | 0.774 |
| alu8 | 262,144 | 268 | 959 | 123 | 36 | 0 / 0 / 0 | 0.545 |

Every damaged phase became physically invalid before repair.  Every final
phase had zero initial Gate and Wire Cells remaining.

## Random programs

All programs have 16 inputs and exhaust 65,536 assignments in each of the
initial, repaired, and turnover phases.

| Seed | Source gates | Outputs | Physical Gate Cells | Physical Wire Cells | Failed support | Repaired organs | Error bits in all phases | Time (s) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 9100 | 1,024 | 8 | 2,884 | 11,424 | 1,431 | 583 | 0 | 1.452 |
| 9101 | 1,024 | 8 | 2,964 | 11,617 | 1,458 | 564 | 0 | 1.455 |
| 9102 | 1,024 | 8 | 2,987 | 11,703 | 1,469 | 568 | 0 | 1.450 |
| 9103 | 1,024 | 8 | 3,068 | 11,870 | 1,494 | 568 | 0 | 1.483 |
| 9104 | 1,024 | 8 | 2,933 | 11,612 | 1,454 | 571 | 0 | 1.419 |
| 9105 | 1,024 | 8 | 2,664 | 10,946 | 1,361 | 557 | 0 | 1.402 |
| 9106 | 1,024 | 8 | 2,827 | 11,371 | 1,420 | 565 | 0 | 1.447 |
| 9107 | 1,024 | 8 | 2,890 | 11,504 | 1,439 | 565 | 0 | 1.409 |
| 9108 | 1,024 | 8 | 3,038 | 11,777 | 1,482 | 601 | 0 | 1.424 |
| 9109 | 1,024 | 8 | 2,963 | 11,685 | 1,465 | 561 | 0 | 1.466 |
| 9900 | 4,096 | 16 | 11,688 | 45,882 | 5,757 | 2,295 | 0 | 6.480 |

## Causality, routing, and replay

- Cutting adder8 Wire Cell 558 changed the physical result to INVALID with
  32,768 affected output bits.
- Repair changed the required output path from `[29, 558, 17]` to
  `[29, 591, 17]` and restored zero errors.
- Corrupting a critical Gate opcode caused 24,640 output-bit errors.
- The independent replay auditor produced zero errors against both runtime and
  reference outputs.
- The active bounded-degree graph used maximum degree four; each routed segment
  had exclusive congestion one and two physical hops.

## Scheduling and state

- adder8, multiplier8, and a 256-source-gate random program each ran under 100
  random Cell activation orders.  All 300 executions matched the fixed-order
  output and oracle, with zero stale/future-epoch reads.
- The accumulator ran 2,000 cycles, performed 112 physical repair/turnover
  events, ended at state 228 exactly matching its reference, and left zero
  initial Gate, Wire, or State Cells active.  Runtime: 9.422 seconds.

## Physical morphology witness

With a 35-Cell shadow budget, the old `sop` full-adder morphology could not
allocate and route.  The alternative `anf` morphology used seven Gate Cells and
22 Wire Cells, routed successfully, and had zero errors over all 65,536 adder8
input pairs.

## Reproduction

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python run_experiments_v02.py
```

The run also executes pytest and writes:

- `results_v02/summary.json`;
- `results_v02/all_failures.json`;
- `results_v02/physical_traces/`;
- `figures_v02/routing_and_turnover.svg`.
