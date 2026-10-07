# Integer norm / C16 normalized-input cache layout

R96 measures a third physical macro: the already verified R92 graph,
**12,873 NAND / 18,367 LATCH**, 40 input and 40 output bits. It contains the
real layer-0 norm constants, exact integer normalization, A8 conversion,
the shared input slot and a complete 16-position normalized-input cache.
The caller must supply the same X vector over 384 scalar handshakes.
Q/K/V and a whole-token controller are outside this macro.

`flow.py prepare` downloads only the pinned successful source proof:
run **37550016136**, commit `71f7212d8d00aa275038524b53f46b8323de2dc5`,
artifact **11452117630**, ZIP SHA256
`d2257c2faff347ce8e5043189fad5bae9888f1b3e04f2c0e640e64daeda415f3`.
It checks the run, archive, formal verdicts and real negative controls,
all 39 source hashes, the exact NAND-to-RTL encoding and original testbench.
It changes only the module name and vector path, then replays every
**1,000,883 C-data clock** in Verilator and rejects an actual output-gate
mutation. The source proof's conditional DIV scope is retained; this is
not a new universal theorem about the full arithmetic protocol.

The artifact expires on 2026-11-06. If it expires or a pinned source changes,
preparation fails: regenerate and audit the R92 proof before updating the pin.
There is no fallback to an unverified netlist.

`flow.py harden`, `mapped` and `publish` use the existing pinned LibreLane
3.0.14/SKY130A flow, all source C vectors in zero-delay standard-cell
simulation, and checked GDS-to-OAS conversion. The initial configuration
matches the successful Q-ring retry: 200 ns, core utilization 20%, target
placement density 35%, post-CTS hold margin 0.2 ns, unchanged SDC and all
CTS/DRC/LVS/XOR checks. The five-hour flow cap and free public runner remain.

Source cell proxy is **0.4160127392 mm²**; neither that estimate nor the
20% utilization setting is a measured die area. The experiment measures
clock/hold overhead, routing, memory and runtime for a larger state array.
Do not subtract this component's saving from the frozen whole-model budget.

The workflow emits `norm-layout-source`, `norm-layout-logs` and, only on
success, `norm-gds-site`. Keep the existing two published viewers intact;
audit a successful result before adding this third design to their catalog.
All EDA, large gate simulation and geometry conversion run on Actions.

中文：这是已云验 R92 的实际 norm/A8/C16 缓存物理试验，外部仍须重复供给
同一 X，尚非整机。源封包、源码、网表、向量及证明均锁摘要，新模块重跑全部
RTL/C 数据拍和真门变异，再布局布线与后布线核对。保持原 Q 成功配置和检查，
只使用免费 runner；实际面积、时序、DRC/LVS 与较大状态阵列的布线成本待实测。
