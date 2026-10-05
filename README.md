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

The workflow maps each candidate with `synth -noabc; abc -g NAND -fast` and
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

`data/dot32_t.json` is the public X Layer #3@2.245 3,829-NAND circuit with raw
SHA-256 `b1507f55d3bd80bbc55f9e06dc656d81e049138827c97ec7fe640d7bfa2ce0b7`.
`golden.py` and the strict CEC verdict logic derive from the MIT-licensed
[first-chip project](https://github.com/JogJohgoeg/tt-nand-hardwired).
The NAND mapping interface follows the
[official Yosys ABC command](https://yosyshq.readthedocs.io/projects/yosys/en/0.47/cmd/abc.html).

See `evidence/local_counts.json` for constructed counts and local check status.
Actual Actions receipts supersede the pending entries when the run completes.
