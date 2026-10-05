# NAND + LATCH mini Llama research

Random ternary weights become constant NAND wiring. This repository measures
concrete selection networks and arithmetic/state components for a proposed
254,528-parameter Llama 3.1 operator graph. It contains no trained model and
does not claim language quality, a completed processor, or physical signoff.

All Python is standard library only. **Yosys/ABC run only in GitHub Actions.**
The workflow performs no tapeout, transactions, paid compute, or deployment.

```sh
python -m unittest test_harness.py
python bench.py --group weights_z25 --small
python bench.py --group dot
python bench.py --group state
python ci.py --group weights_z25 --prepare
```

Full construction/counting is allowed locally. Gate simulation over 4,000 NAND
is refused locally; its receipt says `pending Actions`. The seven Actions jobs
run 0/25/50% zero-weight selectors, embedding/norm/scale, nonlinear tables,
fixed DOT32 samples, and state/conversion primitives. Artifacts contain source,
encoded netlists, vectors, before/after counts, logs and file hashes.

Counts include one tied-input NAND per NOT, and two explicit copy NANDs per
observable output to meet TapeOut's last-record output rule. `core_nand` removes
only those diagnostic copies. Depth is NAND levels, **not clock timing**.
LATCH state is exported in actual four-byte records, with next-state logic and
current-state inputs exposed to combinational equivalence checking. This is
the Boolean `step()` contract, not transparent physical latch behavior.

The current hand constructions compare:

- Flat decoded sums vs shared Shannon subtrees vs complemented subtrees.
- Constant-folded deployed DOT32 vs separate positive/negative trees vs one
  final subtractor. Four random blocks at each zero fraction, not cherry-picked.
- Finite 256-entry BF16 tables: rsqrt/reciprocal on `1+i/256`, exp on
  `-16+16*i/255`, sigmoid on `-8+16*i/255`, with all address values checked. Range reduction,
  BF16 exceptions, and a complete RMSNorm/softmax/SiLU/RoPE implementation remain
  outside these table experiments.
- Separate vs shared add/subtract CORDIC microsteps; shared vs per-row BF16
  rounding; enabled modulo-6912 counter; mux vs one-hot 16×16 state ports.

The workflow lowers the already explicit NAND graph with
`proc; flatten; techmap; opt -fast; abc -g NAND -fast` and
rejects every cell except NAND/NOT. It decodes the **mapped canonical bytes**
and compares them to the independent reference vectors again, then ABC CEC
compares the original and mapped canonical networks. A real output gate is
flipped and must produce a CEC counterexample; exit code zero alone is never
accepted as proof. Tables/counters are exhaustive; DOT/arithmetic/memory use
documented boundary/random vectors against independent integer references.
This does not turn randomized arithmetic checks into all-input math proofs.

Each subprocess is bounded (mapping 300 s, CEC 120 s plus a 150 s process cap).
Timeout, unknown verdict, missing output, empty experiment group, undefined
signal and unexpected cell types fail the job. A partial receipt is explicitly
`in_progress`, never `pass`.

The first run, **186a71c / 37353304026**, passed all six groups other than
`weights_z0` (77 mapped candidates, with CEC and mutation rejection). All three
zero-free source networks passed 8,192-address exhaustive checks, but their
first, 601,293-gate flat baseline timed out in the repeated RTL/memory passes
of `synth -noabc`, before ABC. The new direct gate-lowering flow removes those
unneeded passes and retains the 300-second bound. Cases now run in ascending
NAND count; the larger baseline remains mandatory for a complete pass. This
change still requires a new Actions run; no timeout has been relabeled a pass.
[Archived CI summary](evidence/ci_186a71c/summary.json) records 80 source-network
checks and 77 mapped CEC passes. All 77 mapped canonical byte streams are retained
as `.mapped.bin.gz`, alongside positive/negative logs, receipts, stateful variants
and a hash manifest. The import audit checked all 936 artifact hashes recorded
by the successful receipts. Archived measurements remain bound to 186a71c and
the original mapping flow, independently of the new flow's pending run.

`data/dot32_t.json` is the public X Layer #3@2.245 3,829-NAND circuit with raw
SHA-256 `b1507f55d3bd80bbc55f9e06dc656d81e049138827c97ec7fe640d7bfa2ce0b7`.
`golden.py` and the strict CEC verdict logic derive from the MIT-licensed
[first-chip project](https://github.com/JogJohgoeg/tt-nand-hardwired).
The NAND mapping interface follows the
[official Yosys ABC command](https://yosyshq.readthedocs.io/projects/yosys/en/0.47/cmd/abc.html).

See `evidence/local_counts.json` for constructed counts and local check status.
Actual Actions receipts supersede the pending entries when the run completes.
