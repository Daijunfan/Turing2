# E-CORM v0.2 result

- **物理 Cell 是否已经真正承载程序计算？** 是。DUT 仅通过统一的
  `cell_transition` 在 Gate/Router/Wire/Boundary/State Cell 上传播 epoch
  bitset；禁用 `Program`、`Variant`、`Contract` 的全部抽象求值器后，组合
  与状态物理执行仍然成功。
- **摧毁路径是否会导致输出失败？** 是。切断 adder8 输出必经 Wire 后
  得到 `INVALID`，涉及 32,768 个输出位；Gate、内部 Wire、器官间 Wire
  和物理边四类负面对照都在修复前真实失败。
- **修复后是否由新物理路径恢复？** 是。示例输出路径由
  `[29, 558, 17]` 变为 `[29, 591, 17]`，新影子物理子图通过完整局部
  真值表重放后切换，恢复为零错误。
- **是否存在任何抽象语义旁路？** 已注册的 DUT 执行路径中没有。
  `tests/test_no_semantic_bypass.py` 令五个抽象求值入口一旦调用就抛错，
  物理执行和物理状态机仍通过。抽象求值仅在 DUT 返回后作为外部 oracle。
- **哪些中央控制仍未消除？** 中央编译器、确定性物理路由器、全局故障/
  所有权索引、repair coordinator、generation 原子切换协调器和 Cell
  激活 event queue。数据运算本身是局部的；完全局部发育尚未宣称。
- **哪一项硬门槛尚未通过？** 无。正式全规模
  `results_v02/summary.json` 中 14/14 硬门槛均为 `true`，
  `all_hard_gates_pass=true`，未预期失败数为 0。

## What changed from CORM v0.1

The v0.1 `CORMRuntime` and `results/` directory remain intact as a legacy
baseline.  E-CORM v0.2 adds physical Cells, explicit exclusive Wire paths,
bounded fanout Routers, a local epoch-valid transition kernel, physical shadow
repair, replay-based certificates, state feedback/migration, complete Gate/Wire
turnover, and a standalone JSON replay auditor.  v0.2 writes its evidence only
to `results_v02/`.

## Completion evidence

- 8-bit adder and multiplier: all 65,536 input pairs in three physical phases,
  zero errors.
- 8-bit ALU: all 262,144 combinations in three physical phases, zero errors.
- Ten independent 1,024-gate random DAGs: 65,536 assignments per phase, zero
  errors after 10% permanent support damage/repair and full turnover.
- One 4,096-gate, 16-output DAG: the same complete three-phase check, zero
  errors.
- Three programs × 100 random asynchronous schedules: zero differences and no
  stale/future epoch reads.
- 8-bit accumulator: 2,000 exact physical cycles with faults, feedback
  rerouting, compute turnover, and State Cell migration.
- Final initial-support counts: Gate 0, Wire 0, State 0.
- Independent physical replay equals runtime output equals reference output.
- Finite physical witness: old blueprint infeasible; seven-Gate/twenty-two-Wire
  morphology feasible and exhaustive-exact.
- Pytest: 16 passed.

## Next scientific result

The next nontrivial target is not another scale-only simulator run.  It is to
replace the remaining central compiler/router/repair coordinator with a common
local developmental rule on a geometry-aware 2-D/3-D substrate, while retaining
the same causality, exhaustive replay, asynchronous-schedule, and full-turnover
gates.  Timing, congestion, energy, and correlated-fault measurements should be
added at that stage rather than inferred from the bonded-graph model.
