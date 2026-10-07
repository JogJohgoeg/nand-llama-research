# gpusim — bit-parallel NAND/LATCH simulation on a Vulkan GPU (prototype)

m149 only (Radeon 8060S, Strix Halo iGPU, Mesa RADV Vulkan 1.4, no ROCm), wgpu-py 0.32 + WGSL.
Semantics are exactly `golden.Netlist.step` (TapeOut records, all state zero at reset).

**Kernel.** Stimulus lanes are independent, so one invocation owns one u32 word (32 lanes;
`vec=4` gives 128) and walks the canonical record list, which is already topological (a LATCH
record yields its stored bit), then latches capture their D wires; many clocks run inside one
dispatch. Values are `[wire][word]`, so neighbouring invocations read neighbouring words.
Dispatches are sized to stay well under the amdgpu ring timeout (one long single-lane dispatch
of 2,000 x 66k records triggered a ring reset before this limit was added).

**Correctness** (`check.py`, results/check.json), four accepted netlists:

| netlist | NAND / LATCH | random lanes x clocks = CPU ref | golden.step_simd 64 lanes | gate mutation | accepted C vectors |
|---|---|---|---|---|---|
| Q matrix ring (word) | 20,023 / 1,505 | 65,536 x 32, all outputs + final state | equal | caught | — |
| R92 norm/A8/C16 cache (R105 layout core) | 12,873 / 18,367 | 65,536 x 32 | equal | caught | — |
| R112 sampler | 3,860 / 127 | 65,536 x 32 | equal | caught | 20,000 clocks, 0 mismatches |
| R118 x -> token head | 62,311 / 3,778 | 65,536 x 32 | equal | caught | 20,000 clocks, 0 mismatches |

**Speed** (results/bench.json; G = 1e9 NAND evaluations x clocks x lanes per second):

| netlist | GPU kernel best (lanes) | GPU end-to-end | CPU 64-bit OpenMP ref best | Verilator 1 instance best | GPU / Verilator instance | GPU / 32 Verilator procs |
|---|---|---|---|---|---|---|
| sampler | 834 (2.1M) | 518 | 609 | 1.8 | 468x | 14.6x |
| Q ring | 775 (524k) | 563 | 486 | 3.4 | 229x | 7.2x |
| x->token head | 372 (131k) | 336 | 441 | 6.1 | 61x | 1.9x |
| norm cache (18k LATCH) | 190 (524k) | 76 | 14 | 3.1 | 61x | 1.9x |

Verilator (5.053, -O3) is fastest single-threaded or with 4 threads; 16 threads is slower on
these sizes. golden.step_simd is about 0.02 G.

**When to use it.** Only for many independent stimuli: random/exhaustive checks, multi-seed
protocol runs, per-lane fault injection. The GPU passes the 32-thread CPU bit-parallel
reference from roughly 16k words (0.5M lanes) on 4k-20k-gate cores, and wins clearly on
LATCH-heavy cores. A single sequential C-vector stream runs one invocation and is about
two orders of magnitude slower than Verilator: keep Verilator for replaying one stream.

Files: `gpusim.py` (kernel + driver + golden helpers), `ref.c` (CPU reference/baseline),
`check.py`, `scale.py`, `bench_verilator.sh` + `vlt_main.cpp`, `summarize.py`, `stage.sh`,
`results/` (raw JSON from m149).

**Used in verification (R120).** `compare.py` runs two netlists in lockstep on identical
biased random streams. R120 narrowed head vs accepted R118 head: 16,384 lanes x 90,000
clocks (1.47e9 lane-clocks), zero mismatching clocks (results/compare_R120_R118.json).
Negative: the same R120 head with an unsigned score compare differs on 13,909 clocks,
first at clock 76,091, i.e. when the first sampled tokens appear (one transaction is
about 76k clocks), so the random streams do run complete transactions through the sorter
(results/compare_R120badtie_R118.json; the file name predates the switch from the tie
fault to the unsigned-compare fault). About 47 min of GPU time per pair on m149.
