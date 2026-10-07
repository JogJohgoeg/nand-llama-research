# R118 final residual x -> token head, SKY130A layout

Pinned source: R118 run 37572617795 (62,311 NAND / 3,778 LATCH), whose 31
actual-graph transactions reproduce C `int_pick` on `int_run`'s own logits.
The layout keeps the norm-cache hold settings (post-GRT resizer timing, 0.2 ns
hold margin) at 10% core utilisation and 12% placement density, so cells spread over the whole die. Global routing overflowed at 25%/35% (run 37574059433: 104.16% demand, 125,550 overflow) and at 12%/25% (run 37586503819: 80.43% average but met1/met2 hotspots, 150,857 overflow; E8 address nets fan out to ~1,700 pins). At 12%/15% (run 37598224864) only 91 overflow remained (75.98%, max 3/2); Allowing that residue (run 37603522452) crashed incremental global routing during antenna diode insertion (GRT-0183 heap underflow), so congestion is again not allowed and the die is enlarged instead. Route/Magic/KLayout DRC, LVS and XOR must be zero before publishing. Post-route simulation replays the first
152,400 source clocks: reset and two complete transactions on the same x with
two random words (tokens 23 and 25). Actions only; no EDA on the developer machine.

At 10%/12% (run 37616435051) global routing reached zero overflow (60.95%), yet OpenROAD again
stopped with GRT-0183 (heap underflow) in the incremental re-route after antenna diode insertion,
the same internal error as run 37603522452. GRT antenna repair is therefore off; antenna results are
still checked and reported (not a manufacturing signoff), while DRC/LVS/XOR must be zero.

Sixth run 37620309151 (10%/12%, GRT antenna repair off): detailed routing converged to **0 violations in 3 h 03 min**
(peak 4.46 GB), then DRT antenna-repair iterations re-routed the die until the five-hour flow cap. Seventh run sets
`DRT_ANTENNA_REPAIR_ITERS: 0` (antennas still checked and reported, not a manufacturing signoff) and moves post-route
simulation and packaging into a separate `post` job that restores this run's `xhead-layout-logs`, so routing gets the
whole 6 h job budget (flow cap 20,100 s). DRC/LVS/XOR must still be zero before publishing (claude-opus-h2).
