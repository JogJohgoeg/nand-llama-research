# R118 final residual x -> token head, SKY130A layout

Pinned source: R118 run 37572617795 (62,311 NAND / 3,778 LATCH), whose 31
actual-graph transactions reproduce C `int_pick` on `int_run`'s own logits.
The layout keeps the norm-cache hold settings (post-GRT resizer timing, 0.2 ns
hold margin) at 12% core utilisation and 15% placement density, so cells spread over the whole die. Global routing overflowed at 25%/35% (run 37574059433: 104.16% demand, 125,550 overflow) and at 12%/25% (run 37586503819: 80.43% average but met1/met2 hotspots, 150,857 overflow; E8 address nets fan out to ~1,700 pins). At 12%/15% (run 37598224864) only 91 overflow remained (75.98%, max 3/2); `GRT_ALLOW_CONGESTION` hands that residue to detailed routing, while route/Magic/KLayout DRC, LVS and XOR must still be zero before publishing. Post-route simulation replays the first
152,400 source clocks: reset and two complete transactions on the same x with
two random words (tokens 23 and 25). Actions only; no EDA on the developer machine.
