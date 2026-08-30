# Claims and Limitations

> **Legacy v0.1 scope.** These claims describe the preserved abstract CORM
> baseline.  They do not establish physical Cell execution, routed Wire
> causality, or physical repair.  See `AUDIT_V02.md` for the source audit and
> `CLAIMS_AND_LIMITATIONS_V02.md` for E-CORM v0.2 claims.

## Claims directly supported by the artifact

1. The compiler accepts any finite acyclic Boolean netlist composed of `NOT`, `BUF`, `AND`, `OR`, `XOR`, and `MUX` primitives.
2. Every active primitive/organ morphology is checked against an exact finite Boolean contract before deployment and before repair cutover.
3. In the tested model, replacing an organ by another contract-equivalent morphology preserves the full program's observable Boolean behavior.
4. Fault localization uses a cell-to-organ reverse index and does not scan the entire organ set.
5. For a fixed number of bounded-size damaged organs, measured regrowth work is independent of total program size in the scaling family; certificate updates grow logarithmically.
6. There are resource states in which restoring the old blueprint is impossible but an exact alternative morphology is feasible.
7. Complete physical-support turnover is possible in the tested synchronous model while exact computation continues.
8. All registered experiments in `results/summary.json` completed with `success=true`.

## Claims not established yet

1. The current simulator is not yet an FPGA/ASIC implementation and does not model detailed congestion, timing closure, metastability, power, or transistor-level faults.
2. The developmental and repair scheduler is a centralized reference implementation. Repair decisions are local in data dependence and indexed work, but the code is not yet an asynchronous identical-cell microkernel.
3. Exact certificates are executable exhaustive checks for bounded Boolean interfaces, not a completed Lean/Coq mechanization.
4. The natural-model lower bound separating semantic morphology repair from every fixed-blueprint architecture has not yet been formally proved. The artifact contains a strict finite resource-feasibility witness and an empirical scaling separation.
5. The 64-bit multiplier is certified compositionally at every local contract and sampled at the top level; exhaustive top-level enumeration of all `2^128` input pairs is impossible. Smaller 8-bit programs and all 16-input generic circuits are checked exhaustively.
6. Hosting Rule 110 demonstrates exact execution of a known universal local transition rule on a bounded ring. It is not itself an implementation of Cook's infinite-background universality construction.
7. No responsible experiment can guarantee a future Turing Award. The artifact demonstrates a serious candidate paradigm and identifies the precise remaining theorem and hardware milestones.
