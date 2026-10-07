# R118 final residual x -> token head, SKY130A layout

Pinned source: R118 run 37572617795 (62,311 NAND / 3,778 LATCH), whose 31
actual-graph transactions reproduce C `int_pick` on `int_run`'s own logits.
The layout keeps the norm-cache hold settings (post-GRT resizer timing, 0.2 ns
hold margin) at 12% core utilisation and 25% placement density (run 37574059433 at 25%/35% overflowed global routing: demand 104.16% of resources, 125,550 overflow). Post-route simulation replays the first
152,400 source clocks: reset and two complete transactions on the same x with
two random words (tokens 23 and 25). Actions only; no EDA on the developer machine.
