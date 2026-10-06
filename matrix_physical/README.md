# Real layer-0 Q matrix hardening pilot

A smaller route to the first real INT-C16 GDS: the fixed128×128 Q matrix
(16,384 true ternary constants), one DOT32, exact bounded BitLinear scale,
and a held128×8 activation ring. Numeric rules and the model are unchanged.
This is one matrix. Quantization, norm, RoPE, attention and complete model
inference remain outside. Weights are NAND constants, not ROM/SRAM.

Source construction:22,924 NAND /1,498 LATCH, depth202. The state contains
1,024 activation bits and474 arithmetic/control bits. Inputs are33 bits,
outputs32 bits plus clock:66 signal pins. Local checks cover the108-NAND
controller's8,192 transitions and a2,434-NAND reduced bank's512 arbitrary
transitions, including real output-gate mutations.17 independent C fixtures
produce248,959 protocol clocks with18 full matrices,11 aborts, input/output
backpressure, DOT stalls and busy-start rejection;2,305 valid row results
include one row before an abort. The full graph is verified only on Actions.

| Port | Meaning |
| --- | --- |
| `din[0]` | Synchronous reset; aborts control. Does not initialize activation storage. |
| `din[1]` | Start accepted only idle with1≤maximum≤524288. |
| `din[9:2]` | Signed8 activation code;128 accepted codes overwrite the whole bank. |
| `din[29:10]` | Unsigned20 maximum, captured on start. |
| `din[30]` | Code valid; accepted only while `dout[29]` is high. |
| `din[31]` | Result ready; stalled results hold. |
| `din[32]` | DOT access enable; accepted groups rotate the bank by32 codes. |
| `dout[19:0]` | Signed20 scaled matrix row result, valid only with `dout[30]`. |
| `dout[26:20]` | Row index0–127. |
| `dout[28:27]` | DOT group0–3. |
| `dout[29]` | Activation input ready. |
| `dout[30]` | Row result valid. |
| `dout[31]` | Busy; old-state value may remain high on the reset edge. |

The reference first checks all512 real coefficient words against frozen C,
then checks17 quantized/raw-A8 cases independently in C and Python. The cloud
job additionally proves the complete bank and constant table against separate
Verilog, rejects mutated graphs, and runs all248,959 clocks through canonical
NAND bytes and generated RTL. No initial RAM contents are assumed: reset is
followed by a complete128-code fill before any numeric output is checked.
A no-stall matrix takes13,569 clocks including start, input and output accepts;
200ns is a physical constraint, not a measured maximum frequency.

Local preparation only: `python3 matrix_physical/prepare.py` (55-second cap).
Cloud preparation: `python matrix_physical/prepare.py --cloud`.
`matrix_layout.yaml` has a separate concurrency group, preserving the in-flight
R15 comparison. It runs pinned LibreLane3.0.14/SKY130A at20% core utilization,
35% placement target,200ns and met1–met5 defaults. Timing repair, legal routing,
DRC/LVS/XOR and post-route C99 vector simulation are mandatory. No local EDA.

`flow.py` reuses the existing hardening and mapped-cell checker in a separate
`build/matrix_physical` directory. Successful `matrix-gds-site` contains the
checked GDS, OAS, scope page and source/area/signoff evidence. After auditing
that artifact, copy it into `docs/gds`, add the existing demo's layout entry,
and publish through the existing Pages workflow. No GDS or viewer is claimed
before that run completes. The whole-chip area/control budget is unchanged.


The next layout uses `ring_prepare.py`: R82's verified dual-stride A8 ring with
20,023 NAND /1,505 LATCH instead of22,924/1,498. Its true Q weights and arithmetic
are unchanged; unstalled matrix latency is13,950–14,046 clocks versus13,569.
The wrapper reruns full source verification and checks the exact graph, source,
vector and sample hashes plus real faults before staging the physical input.
It also verifies the full generated testbench while relocating its vector path.
The original `prepare.py` is retained to reproduce the held-bank baseline.
Layout configuration and constraints stay unchanged for the comparison; new
routed results and electrical/antenna limits must be audited after completion.
