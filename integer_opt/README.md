# Integer model gate reductions

`ports.py` compares binary-addressed and cursor-controlled ring storage. Run
37413634878 on commit 237f97f passed independent behavioral-RTL ABC CEC, actual
output-gate mutation rejection, canonical LATCH decoding and access sequences.
The actual one-head 32×276-bit bank changes from 52,897 NAND / 8,832 state bits
to 27,912 NAND / 8,837 bits, including its cursor. Random access now needs up
to 31 preliminary rotations. Whole-model scheduling and physical integration
remain separate work; the two interfaces are not single-clock equivalents.

`weights.py` uses the frozen 283,804-byte model in `physical/model.bin`, with
SHA 5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1.
The C99 parser independently supplies every expected packed weight word,
including all invalid/padded addresses. The original global Shannon selector
reproduces the earlier 323,304-NAND graph byte for byte.

The zero-weight sign completion experiment passed all 81 four-address ternary
functions and four multilane/padding cases with actual negative controls.
It increased real-weight source gates to 366,209 and was rejected. The cloud
jobs instead compare the existing Shannon and complemented source networks
after Yosys/ABC mapping. All address values must match C before and after
mapping; source/mapped CEC and an actual output mutation must also pass.
Run 37415286971 (5282002) passed all checks for layer0 and all weights. Mapped
Shannon uses 80,154 and 323,266 NAND respectively; complemented networks use
89,861 and 362,937. The global 38-gate saving is small. The layer0 Shannon
network is the next pilot's candidate; physical area is not yet measured.

The same run proved the actual 64×640-bit prefix bank and its real latch
translation. Including a 41-NAND / 6-bit cursor, it changes from 245,438 NAND /
40,960 bits to 126,123 NAND / 40,966 bits. Complete control RTL is pending.
`ring_model.py` checks the entire integer C model with physical KV rotations,
including an actual address mutation. It adds 318,076 rotations for C16.

`workspace_model.py` checks two lifetime refinements without changing the
golden: A8 codes reuse dead H/FF inputs after the maximum scan; exponentials
reuse dead attention scores after score-max. C16 removes 2,960 state bits in
the budget. All 70 C16/C32 full-model cases match logits and intermediate
traces; premature source overwrite is rejected. This is a software lifetime
check, with whole-controller gate and physical integration still required.

`prefix_model.py` checks prefix accesses alone and with both prior refinements.
H stages norm inputs; A/H hold residual write-back chunks; the second norm uses
the updated A directly. It adds no row buffer. All 140 complete C cases match
logits and traces and reject actual read-address mutations. C16 adds 11,007
prefix rotations. The independent address count also covers every length.
`Integer memory schedules` runs these cheap C checks separately from the
large gate proofs. Neither is a proof of a complete autonomous controller.
Run 37417996702 on 00085e0 passed all three C schedule checks with GCC
`-Werror`; results match the local receipts except for elapsed time.

`widths.py` measures dense 16/8/1-lane true-weight selectors and folded DOTs.
Selector construction gives 321,852 / 321,278 / 320,618 NAND; the small DOTs
use 2,190 / 1,256 / 248 NAND and pass actual gate tests and mutations. Every
packed address matches the independent C parser, including invalid addresses.
The large selectors have construction counts only, without a gate proof.
Compared with the adopted 32-lane mapped selector plus DOT, even one lane
saves only 6,229 NAND (0.804% of the current conditional whole-chip budget).
Holding eight clocks per DOT group would add 122.35% cycles. This candidate
is rejected; the model and adopted budget remain unchanged.

`circulate.py` explores a different bank interface: every clock rotates all
rows, including reads and unrelated arithmetic clocks. Only the departing
head needs a write mux. The explicit cursor and reset are included. Reset
discards old logical address validity; it does not clear data or rewind live
contents. Construction gives 1,425 NAND / 8,837 LATCH for a KV head and
3,254 NAND / 40,966 LATCH for the prefix bank. The small fixture passes
canonical transitions, a real write-path mutation and arbitrary idle gaps.
`Integer circulating state` run 37419431682 (b374bf4) also proved both actual
sizes against independent RTL, rejected actual write/output gate mutations,
and checked every physical row/cursor over 1,369 / 3,291 clocks. Full-model
scheduling and activity/power evaluation are pending, so this candidate is
not adopted. Continuous movement could be a substantial power tradeoff.

`sample_stream.c` implements the frozen specification's cache-free option:
stream 192 logits into a stable top-40 list, calculate the weight total, then
recompute weights for cumulative choice. It uses 1,600 score/ID state bits
and no weight cache. All 28,800 picks on 144 real/boundary/random vectors
match the unchanged C golden. Reversed ties and wrong second-pass weights
are rejected. At most 40 extra weight evaluations add 8,360 estimated clocks,
removing the unaccounted sampler accesses to the dead KV bank. This is a C
storage refinement; complete controller RTL remains separate work.
Run 37419229751 (bbbaf23) passed these sampling and prior storage checks on
Linux; all four receipts match the local data except elapsed time.

`pilot_v2.py` composes the proved mapped layer0 selector and optimized DIV
through the unchanged physical compositor into an isolated build directory.
The same interface and 9,237 state bits now use 145,629 source NAND, down
10,026 (6.441%). `Integer pilot optimization` reruns the complete 13,840-clock
C99/NAND/Verilator suite and actual mutation before physical adoption. Run
37420508375 on d8d2d29 passed; the actual NAND mutation differs on 10,088
clocks and the RTL mutation also fails. This
does not alter the first layout job or include the circulating-bank candidate.

`weight_cursor.py` turns part of the controller allowance into an actual
452-NAND / 32-LATCH state machine. It emits global weight address, row,
32-lane group and boundary flags, with start/reset/stall/invalid-command rules.
512 arbitrary transitions and 31,792 actual small-netlist clocks pass; the
sequence covers all 30,720 model words and 991 stalls against independent
address rules and the frozen C weight parser. `Integer controller components`
run 37420508449 on d8d2d29 passed independent RTL CEC (0.032 seconds) and
rejected the actual gate mutation. It is not yet the complete inference controller.

`scale_pipeline.py` schedules one serial MUL twice, then DIV and sat20, for
the exact BitLinear expression `S(R(dot*m*alpha, 33292288))`. Its constructed
4,183 NAND / 419 LATCH include operand capture, a busy/valid handshake,
reset/abort, and held results. Local work only prepares 26,729 independent
C/Python expected clocks, including half ties, full input bounds, all 35
model alphas, busy starts and reset boundaries. Run 37421682021 on 59c20bc
passed all actual NAND/Verilator clocks and rejected output-gate mutations.
The verified standalone latency is 198 clocks;
the complete dot-product, operand ports and inference scheduler are separate.
This composition reuses the already budgeted arithmetic units, so its count
is not added on top of them or credited as a whole-chip area reduction.

`pilot_bank.py` integrates the held KV ring and a real seek/accept controller
into the optimized pilot. The constructed slice is 120,715 NAND / 9,242 LATCH,
24,914 NAND below R12 (17.108%), including the cursor and an extra ready output.
View 2 seeks the low-five-bit address; a write happens only on a ready edge.
Other views hold the bank. View 2 + load resets the cursor and discards old
logical validity, so contents must be initialized before observing them.
This changes read latency; it is not single-cycle equivalent to the old port.
The small 176-NAND bank passes 512 arbitrary transitions and a real mutation.
`Integer pilot bank integration` run 37423162017 on bc11f0e passed actual-bank
arbitrary-state CEC against independent RTL (3.823 seconds), all 17,165
NAND/Verilator clocks against C/logical-bank expectations, and actual gate
mutations. The ready-gate mutation changes every clock's expected output.
It does not alter the baseline physical retry or adopt continuous circulation.

`weight_radix.py` measures one fixed radix-4 decoder-sharing construction.
All 264 small truth-table cases pass exhaustive NAND checks and mutations.
Actual layer0/all construction grows from 80,187/323,304 to 82,145/335,417 NAND
and adds two logic levels; it is rejected for the gate/depth objective, with
no large gate proof or cloud job. Lower raw pin-load fanout does not establish
mapped area or routing improvement. The adopted whole-model budget is unchanged.

`weight_pack.py` encodes five ternary digits per byte, reducing constant-selector
width from 64 to 52 bits, then restores the original interface with small NAND
decoders. The complete source candidates use 78,286/319,830 NAND for layer0/all;
the global saving versus the adopted selector is only 1.063% (0.444% of the
whole-model budget), with 15 added logic levels. It is not adopted. Small
decoders are exhaustively checked with mutations; actual large tables have
construction/C packing checks only. No ROM or numerical-model change is used.

`linear_engine.py` composes the proved layer0 weights, weight cursor, DOT32,
a signed17 accumulator and the scheduled exact scaler. It traverses an entire
matrix, captures the maximum and matrix ID on an idle start, ignores busy
starts, requests activation groups and holds output under backpressure.
Construction is 88,868 NAND / 504 LATCH; the small 93-NAND phase controller
passes all 4,096 arbitrary transitions and an actual mutation. Local work
prepares 271,459 independent C/Python expected clocks, including all seven
matrices and 1,312 main output rows, stalls, padding, resets and restart.
`Integer BitLinear engine` performs the full source NAND/Verilator check and
actual result-gate mutations on Actions. Run 37424749023 on a0e7cde passed
all 271,459 clocks; the 200-clock last-group-to-valid latency is checked.
The actual result-bit mutation fails at the first valid output in both
interpreters. Activation quantization/storage,
other layers and the complete transformer schedule remain external. This
composition is not a further whole-model budget reduction or a physical result.

`--bounded` on `scale_pipeline.py` and `linear_engine.py` uses the existing
s17/u20/u18 limits to stop the two MULs after 20/18 iterations. The signed
product shifted left by nine fits64, so 55 DIV iterations recover exactly
the original quotient, remainder and RNE result. `scale_bounded_check.py`
checks actual small primitive gates on 216 cases, including one-too-few-step
and real gate mutations. The complete 4,096-NAND / 418-LATCH scaler and
88,781-NAND / 503-LATCH matrix engine passed full-cloud checks on d4373b0:
runs 37426290068 and 37426290070 checked all four baseline/bounded jobs.
The bounded scaler's 13,457 clocks and engine's 141,408 clocks match C99,
actual NAND and Verilator; actual output-gate mutations fail in both engines.
Scaler latency is 99 versus198 clocks, without a numerical change.
Default graph/vector hashes remain identical to the proved baseline. The
conditional whole-model budget falls by 10,391,040 to 88,174,661 clocks while
retaining outer access allowances. It is not integrated full-chip timing.

`quant_stream.py` builds the exact A8 frontend: one maximum scan, followed by
replaying the same signed20 vector, with 1..336 elements and ready/valid ports.
It uses `(x<<7)-x` and 27 restoring-divider iterations, retaining exact RNE.
The module is 2,283 NAND / 169 LATCH, including its controller. Its actual
small netlist passes 122,068 local C99/Python-checked clocks: 29 main vectors,
one restart, eight aborts, ties, extremes and backpressure. An actual result
gate mutation is rejected. `Integer A8 stream` run 37427393412 on fcdd606
also passes all 122,068 Verilator clocks and the actual RTL mutation.
Input storage/replay is external; outputs may overwrite dead inputs
after the maximum pass. The no-stall schedule is `1+31*n` clocks through the
last output acceptance, with 29 clocks from a replay input to output valid.
Sharing its divider with the matrix engine needs explicit arbitration; these
standalone component counts are not additive whole-model savings.

`prefix_codec.py` and `prefix_packed.py` are a separate numerical candidate:
store `sat16(RNE(x/16))` in X and decode by wiring `<<4`, at every embedding
and residual write. The frozen model and current physical slice keep their
existing contract. The 156-NAND codec passes all 1,048,576 signed20 inputs
against C99 and an actual gate mutation. The held 64x512 bank plus cursor is
100,907 NAND / 32,774 LATCH. `Integer packed prefix` run37428807285 on71b0964
passes independent transition CEC (1.418s),256 arbitrary states,64 requests/
2,172 physical clocks and actual gate negatives. C/Python candidate checks match all37,440
logits and 149,760 trace words, including truncation negatives. On the same
512 stories, C16 PPL changes 4.401270 to4.401749 (+0.0109%), C32 3.964536 to
3.965757 (+0.0308%). Its conditional projection saves25,060 NAND /8,192 LATCH
including one shared codec. Full staging/controller integration is pending;
the candidate is not yet adopted or physically implemented.

`prefix_packed_model.py` then checks actual int16-coded physical prefix rows
with the held KV rings and all dead-workspace reuse. The 70 C16/C32 full-model
cases match the P16 candidate; premature use of unrounded residual staging
and a wrong physical read address are each rejected in all70 cases.
`prefix_writer.py` implements a 332-NAND /13-LATCH controller including the
codec: rewrite the existing H/A staging lanes in place, commit each complete
32-lane chunk, and wait for bank acceptance. It adds no vector buffer.
All10,973 actual small-netlist clocks and a real address-gate mutation pass
locally. `Integer prefix write control` run37429979379 on4a94167 also passes
all70 GCC model cases and10,973 NAND/Verilator clocks, including both model
negatives and the actual RTL address-gate mutation. Whole-bank integration
and full model control are still separate work.

`prefix_store.py` composes that writer, an actual4x640-bit H/A staging slot,
and the held64x512 prefix bank with seek/accept control. It is116,038 NAND /
35,347 LATCH. Four chunks refill the existing staging slot; each encoded
chunk is committed directly from its bits[19:4], then the staging ring
advances. There is no second vector buffer. A1,842-NAND small staging bank
passes512 arbitrary-state checks and a gate mutation. The complete macro is
only constructed locally;19,973 C-derived clocks cover42 completed vectors,
six aborts, stalls, ignored busy requests and physical readback.
`Integer prefix storage` runs actual large NAND and Verilator checks in CI.
This macro still needs integration with the other H/A users and full-model
control; its component count is not an extra whole-chip saving.

Local construction and small checks:

```sh
python3 integer_opt/ports.py
python3 integer_opt/weights.py --scope small
python3 integer_opt/weights.py --scope layer0
python3 integer_opt/weights.py --scope all
python3 integer_opt/ring_model.py
python3 integer_opt/workspace_model.py
python3 integer_opt/prefix_model.py
python3 integer_opt/widths.py --lanes 16
python3 integer_opt/widths.py --lanes 8
python3 integer_opt/widths.py --lanes 1
python3 integer_opt/circulate.py
python3 integer_opt/sample_stream.py
python3 integer_opt/pilot_v2.py
python3 integer_opt/weight_cursor.py
python3 integer_opt/scale_pipeline.py
python3 integer_opt/pilot_bank.py
python3 integer_opt/weight_radix.py --scope small
python3 integer_opt/weight_radix.py --scope layer0
python3 integer_opt/weight_radix.py --scope all
python3 integer_opt/weight_pack.py --scope small
python3 integer_opt/weight_pack.py --scope layer0
python3 integer_opt/weight_pack.py --scope all
python3 integer_opt/linear_engine.py
python3 integer_opt/scale_bounded_check.py
python3 integer_opt/scale_pipeline.py --bounded
python3 integer_opt/linear_engine.py --bounded
python3 integer_opt/quant_stream.py
python3 integer_opt/prefix_codec.py
python3 integer_opt/prefix_packed.py
python3 integer_opt/prefix_packed_model.py
python3 integer_opt/prefix_writer.py
python3 integer_opt/prefix_store.py
```

`--cloud` is guarded by `GITHUB_ACTIONS=true`. R21/R22 explicitly test the
separate P16 numerical profile; other rounds retain the frozen contract.
No training, local EDA or local gate simulation above4,000 NAND is used.
