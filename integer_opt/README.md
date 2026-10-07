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
`Integer prefix storage` run37430674649 on53afaf6 passes all19,973 actual
large NAND/Verilator clocks and the real read-gate mutation (296 observed reads).
This macro still needs integration with the other H/A users and full-model
control; its component count is not an extra whole-chip saving.

`prefix_codec12.py` tests one further precision candidate: signed12 storage
with RNE(x/256), decoded by<<8. Its132-NAND codec passes all1,048,576 inputs
against C and an actual mutation. Independent full-layer Python/C checks
match37,440 logits/149,760 trace words, with truncation negatives.
The same512-story C16/C32 PPL changes are +0.8720%/+0.7736%, both below1%
but with less margin than P16. The64x384 bank plus cursor is75,691 NAND /
24,582 LATCH. `prefix_packed.py --storage-bits 12` constructs this bank; the
workflow checks both widths. Default16-bit graph bytes remain unchanged.
Both jobs passed run37431854795 on3500ce9: P12 independent CEC1.067s,
256 arbitrary states,64 requests/2,083 physical clocks and actual negatives;
P16 repeated the original graph and2,172-clock sequence successfully.
Its conditional whole-model projection is724,446 NAND /75,482 LATCH,
8.108..9.730mm2; complete transformer control is still pending.
Neither numerical candidate changes the current demo or physical slice.

The writer, storage macro and complete C lifetime runner also accept
`--storage-bits 12`. P12 is316 NAND /13 LATCH for the writer and90,678 NAND /
27,155 LATCH for the complete storage macro, versus332/13 and116,038/35,347
for P16. Runs37432613723/37432613743 at598e1e8 pass both profiles:70 full C
cases and both lifetime/address negatives each;10,973 writer clocks and
19,973 storage clocks each against actual NAND/RTL, including real gate
mutations. Explicit assertions
preserve the P16 graph/vector and generated C hashes. Whole-chip control
and physical implementation still remain; no additional saving is credited
on top of the P12 bank projection.

R26 `ff_recompute.py` calculates each FFN row twice: first find the maximum,
then recompute and store only its signed8 quantized code. It keeps input H
and its maximum alive, and introduces no new rounding. Both P16/P12 pass70
complete C cases each, including all FF code/maximum digests and real stored
code mutations. `ff_bank.py` constructs the exact336-lane banks, with only16
lanes in the last word and invalid addresses reading zero: raw20 is42,302N /
6,720L; A8 is16,958N /2,688L. Only the526N small bank is simulated locally;
run37434158238 at2f7fe30 proves both actual banks: independent RTL CEC
4.925s/1.718s,512 arbitrary state checks each and real gate mutations pass.
Both profiles' complete C cases also match the local evidence.

This is an area/latency candidate, not adopted: conservatively replacing the
old40,320N port allowance and adding21 state bits plus126N ports projects
701,210N /71,471L,7.787..9.345mm2. Versus P12, NAND decreases3.207% but
cycles increase21.410% to96,182,870/token; the inherited N+3.5L gas proxy
increases16.832%. Full two-pass control and physical integration remain.

R27 `ff_store.py` connects the quantizer to actual scalar writes and word
reads, without an assembly buffer. The combined macro is21,125N /2,858L;
its scalar-write bank is18,861N /2,688L,1,903N more than R26's whole-word
write bank. Including one valid bit and6N ports corrects the area candidate
to703,119N /71,472L,7.801..9.361mm2 (2.944% fewer NAND than P12).
The50k control and1024 arithmetic-state allowances remain; this is still
a conditional budget with96,182,870 cycles/token.

The producer supplies indexed raw FF rows twice, and the bank becomes valid
only after all336 quantized codes have been written. Reset aborts and
invalidates; no bank clearing is assumed. The final word has16 real lanes,
the rest tied0; invalid word addresses read0. A2,814N small macro passes
3,021 actual NAND/C clocks and real read-gate mutation. The actual-size
98,339-clock sequence passes actual NAND/RTL/C in run37435066957 atc94b386;
the actual read-gate mutation fails128 checked clocks. Independent scalar-bank
CEC passes in1.718s, with512 arbitrary states and actual mutations checked.
The raw FF arithmetic producer and shared-DIV arbitration remain external.

R28 `silu_pipeline.py` replaces the three power-of-two divides in the FFN
tail by exact RNE shift logic and uses one MUL for17/20 iterations. The full
actual SIG-table pipeline is4,665N /240L, depth134,40 clocks; its2,412N core
passes40,942 NAND/C/Python clocks locally (971 results,7 aborts), and the
2,287N SIG frontend passes14,331 values including all table tie boundaries.
The full15,952-clock sequence and all-state independent RTL CEC run on Actions.
There is no numerical change or new vector buffer. BitLinear inputs and
shared-MUL arbitration remain external. Keeping20 access+4 control clocks
per row gives cycle candidates71,493,206 for frozen C16,71,560,790 for P12,
80,861,270 for P12+FF recomputation; gate/area budgets are not reduced again.

R28 run 37436078130 (2c3f0a7) passed core/full NAND and RTL sequences
(40,942/15,952 clocks) and real mutations. Core all-state CEC passed in
0.415s. Full reference CEC stopped at an unmapped inferred ROM (`$memrd_v2`),
not a counterexample. The proof flow now explicitly runs `memory_map` before
NAND mapping; source graph/vector hashes are unchanged, retry still required.

R29 `ff_row.py` composes actual layer-0 gate/up weights, shared DOT32 and
bounded scaler, and exact SwiGLU. A start captures row 0..335 and A8 maximum;
the caller supplies the same four 32-lane H-code groups twice. It supports
input/output stalls, rejects illegal rows/zero maximum, and aborts on reset.
Construction is 52,437 NAND/730 LATCH, depth 202. The 39,327-NAND selector covers
86,016 real trits; this narrower scope is not a whole-model weight reduction.
The 180-NAND/4-state controller passes all 4,096 transitions and a real mutation.
All 4,096 packed weight addresses match independent C. The 88,024-clock fixture
covers every row, boundaries, 341 completed results and 10 aborted requests.
Large actual-gate/RTL checks and weight mapping/CEC run only in Actions.
The no-stall reference is 251 clocks including result acceptance. H storage,
two-pass FF scheduling and final A8/store remain external. There are currently
two MUL instances (scale and SwiGLU); complete sharing is not yet implemented.
No numerical rule or whole-chip gate/area budget changes in this round.

Runs 37437228290/37437228386 (7b245e8) now pass R28 and R29 completely.
Core/full SwiGLU all-state CEC is 0.314/0.665s; both sequences and actual
mutations pass. All 88,024 FF-row NAND/RTL clocks match C, including251-clock
latency. All4,096 weight addresses pass before/after mapping, CEC4.124s;
only20 selector gates disappear, with no further whole-chip area credit.
The conditional schedule now uses the stated R28 cycle values, preserving
access margins; numerical-profile adoption and full-chip timing remain separate.

R30 `ff_stream.py` connects the real row producer to the two-pass quantizer
and scalar-write bank. H maximum is captured for both passes, while H codes
stay in the caller's original storage. There is no raw FF or assembly buffer.
Construction is73,716 NAND/3,608 LATCH, depth202. State is2,688 code bits plus
900 arithmetic/control bits and20 captured H-maximum bits. There are still
two MUL and two DIV instances; resource sharing is not yet implemented.
The176-NAND master passes512 arbitrary states and an actual mutation locally.
The886,452-clock C fixture covers four complete vectors, seven resets/aborts,
all336 code readbacks, invalid addresses, saturation, input/write stalls and
busy starts. Full NAND/RTL checks run only on Actions. No-stall timing is
expected to be178,417 clocks including both passes and final code acceptance.
The down matrix, residual and complete transformer are outside this module.
No whole-chip gate, area or numerical rule changes in R30.

R30 run 37438389750 (ba78368) passed all886,452 actual NAND/RTL/C clocks,
including four complete FF banks, seven aborts, saturation and stalls. The
actual code-output mutation is rejected and178,417 no-stall clocks confirmed.

R31 `ff_shared.py` pins that source graph and aliases the duplicate192 MUL
and115 DIV states. It routes complete load operands into one physical MUL and
one DIV, leaving all other controller/data states intact. Construction is
72,007 NAND/3,301 LATCH (minus1,709/307), depth202; all886,452 vector bytes
are unchanged. The54-NAND owner/guard circuit passes all2,048 inputs and a
real mutation locally. Actions checks conditional single-transition CEC for
all307 arithmetic next-state bits, complete NAND/RTL/C sequences and an actual
ownership monitor on every clock. The CEC explicitly assumes load ownership;
it is not an unbounded proof that every reachable control state satisfies it.
The local macro cell estimate falls3.600%, and the N+3.5L work proxy3.224%.
These are not mapped area/gas measurements. Whole-chip budgets already assume
one shared arithmetic core and are not reduced again. H codes, down/residual
and the complete transformer remain external; no numerical rules change.

R31 run37439319381 (6d3283e) passed all886,452 actual NAND/RTL/C clocks
and actual mutations. Load ownership holds on every observed clock. Guarded
307-bit next-state CEC passes in0.215s; the negative in0.064s. This remains a
conditional single-step proof plus complete fixtures, not an unbounded proof.

R32 `ff_input.py` connects the original four640-bit H work words. Four fills
are required after reset or completion. It scans128 signed20 values, writes
each A8 code into the same slot, then supplies gate/up words directly from
that slot. The last scalar write and each word rotation use separate clocks;
otherwise a rotation could discard the newly written last lane. Every eight
FF word accepts return the four-word ring to its original orientation.
Construction is87,508 NAND/6,036 LATCH, depth202:2,560 original H bits,
2,688 FF code bits and788 arithmetic/control bits. There is no extra H code
vector. The H quantizer still has a separate DIV, to be shared next.
The132-NAND outer controller passes all131,072 old-state/input combinations
and a real mutation locally. The932,499-clock C fixture retains all R30
FF transactions, adds five H abort phases/refills, stalls and last-lane
rotation checks. Expected start-to-published latency is182,391 clocks,
excluding four-word refill. Full NAND/RTL/C checks run on Actions only.
Whole-chip budgets and numerical rules stay unchanged; down/residual remain
outside this module.

R32 run37440741634 (5fcf098) passed every932,499 actual NAND/RTL/C clock
and the actual code mutation. The H slot, deferred last-lane rotation,
abort/refill cases and182,391-clock no-stall timing are now confirmed.

R33 `ff_input_shared.py` removes the H quantizer's duplicate115-bit DIV.
H A8, FF scale and FF A8 route their complete load operands into one DIV;
all other states and every one of the932,499 fixture clocks are unchanged.
Construction is86,687 NAND/5,921 LATCH, depth202, one MUL/one DIV. Of these,
5,248 bits are H/FF storage and673 are arithmetic/control/maximum state.
The14-NAND owner check passes all32 inputs and an actual mutation locally.
Actions checks115 next-state bits under explicit H/FF load ownership,
observes that guard on every fixture clock, and executes full NAND/RTL/C
with an actual output mutation. No unbounded ownership proof is claimed.
This removes821 NAND/115 LATCH locally, with no repeated whole-chip credit
against arithmetic resources that were already budgeted as shared.

R33 run37441232679 (40de26f) passes all932,499 NAND/RTL/C clocks,
real mutations and every observed ownership check. Conditional115-bit CEC
passes in0.115s, with the negative rejected in0.064s.

R34 `down_engine.py` implements the true layer0 128x336 down matrix using
20,460 NAND of constant selection and the existing DOT/s17/scale schedule.
The complete standalone endpoint is28,780 NAND/469 LATCH, depth202; no
activation vector is copied. All2,048 weight addresses match the frozen C
parser, including invalid groups and16 zero weight lanes in the final word.
The87,656-clock C/Python fixture covers six matrices,768 output rows, seven
aborts, input/output stalls and arbitrary unused padding. Expected no-stall
matrix time is14,337 clocks. Actions verifies actual NAND/RTL/C, mutations,
and all weight addresses before/after mapping with CEC. Connection to the
existing FF bank/H result slot and merging its arithmetic with the preceding
stages remain separate integration work. No numerical or whole-budget change.

R34 run37442048883 (30769ca) passes all87,656 NAND/RTL/C clocks and
the actual result mutation. The14,337-cycle timing is confirmed; all2,048
weight words match C before/after mapping, with CEC and its negative passing.

R35 `ff_writeback.py` connects those endpoints: completed FF codes feed down,
whose accepted20-bit results overwrite the original H slot. The same physical
scalar-write/rotate port serves both H quantization and result storage. Final
availability waits for the last deferred word rotation; four640-bit reads
recover the vector. No second128x20 result vector is allocated. Construction
is117,299 NAND/6,394 LATCH, depth202, including the still-separate down
MUL/DIV/DOT. The86-NAND controller passes all32,768 old-state/input cases
and a real mutation. The990,599-clock C fixture retains every upstream clock,
checks512 down rows and16 final H words, and prepares an actual H-readout gate
mutation for Actions. Expected no-stall time is196,730 clocks, plus external
fill/read words. Down's independent abort tests are covered in R34; this
combined fixture retains upstream aborts. Norm/residual and whole-transformer
control remain outside, and wide diagnostic ports are not package pin counts.

R35 run37443107542 (7da1ff2) passes all990,599 actual NAND/RTL/C clocks
and the actual H-readout gate mutation in both simulators. The196,730-cycle
no-stall schedule and all final H words are confirmed against C.

R36 `ff_scale_shared.py` shares the complete418-bit scale state, including
temp/alpha/count/phase/result, between gate/up and down. H/FF A8 and SwiGLU
keep using the same physical MUL/DIV. Construction falls117,299→113,699
NAND (3.069%) and6,394→5,976 LATCH (6.537%), depth202. There are5,248
H/FF storage bits and728 arithmetic/control bits; two DOT instances remain.
All990,599 fixture bytes are unchanged. The14-NAND owner circuit passes all64
inputs and an actual mutation locally. Actions checks conditional418-bit
next-state CEC, complete NAND/RTL/C, real H-readout mutation and every observed
ownership guard. This is not an unbounded invariant proof. The local cell
estimate falls3.850%; no whole-chip credit is repeated against already-shared
resources, and numerical rules are unchanged.

R36 run37444299646 (dbdcd68) passes all990,599 NAND/RTL/C clocks and
real H-readout mutations, with zero observed ownership violations. Conditional
418-bit CEC passes in0.265s; its real negative is rejected in0.114s. The source
gate/state saving is accepted within that scope, not an unbounded proof.

R37 `ff_dot_shared.py` selects the H or FF bank's256 A8 bits and the
corresponding64 true-weight code bits before one shared DOT32. Separate s17
accumulators keep their original acceptance/reset schedules. Construction
falls113,699→110,909 NAND (2.454%), with5,976 LATCH and depth202 unchanged.
The same990,599-clock fixture and196,730-cycle reference are retained. A small
owner guard passes all8 inputs and an actual mutation. Actions checks all34
accumulator next-state bits under explicit take ownership, split across both
owner cofactors, then the full NAND/RTL/C fixture, actual H-readout mutation
and every observed ownership guard. Large-graph verification has passed;
there is no repeated whole-chip credit for the already-budgeted shared DOT.

R38 `ff_acc_shared.py` identifies down's s17 accumulator and u20 maximum
with the gate/up registers, selecting the owner's clear/take/load conditions.
Construction falls110,909→110,645 NAND and5,976→5,939 LATCH, depth202.
There are5,248 vector-storage bits and691 other bits. The same990,599 C fixture
clocks, SHA and196,730-cycle latency remain. Actions compares37 next-state bits
after explicit duplicate-old-state identification, splitting on both owners;
the full simulation also checks usage ownership and real H mutations. This is
not an unbounded state-lifetime proof; the full cloud fixture has passed.

R37/R38 runs37447245288/37447245403 (54dc98d) both pass all990,599
actual NAND/RTL/C clocks, H-readout mutations and observed ownership.
The two owner CEC branches take0.316/0.416s for R37 and0.215/0.164s
for R38, with both real negative controls rejected. These are conditional
next-state refinements, not unbounded ownership proofs. Whole-chip shared
budgets are not reduced a second time.

R39 `norm_stream.py` implements true norm[1]:128 s20 inputs are squared
using one serial MUL, accumulated into u46, and passed through24 SQRT steps
for `isqrt(2*sum+4295)`. The caller then replays the row for exact learned-weight
scaling, using16 MUL and38 DIV iterations per output. Original RNE/saturation
rules are retained. The standalone composition is5,991 NAND/512 LATCH,
depth202, including its641-NAND constant selector. It has no input-vector RAM;
H storage and sharing MUL/DIV with FF are still external.

The364-NAND/17-LATCH controller passes66,560 boundary/arbitrary transitions
and a real mutation. All128 constants match C;17 complete C/Python norm cases
agree. The220,602-clock fixture includes18 completed rows,15 resets during
active work, input/output stalls and busy starts. No-stall time is10,395 clocks
(`1+128*22+26+128*59`). Actions checks the full NAND/RTL/C fixture and an actual
result mutation, plus all-address mapping/CEC for the constants. Other learned
norm rows and the whole-chip cycle budget are not silently substituted by this
norm[1] result. Numerical rules and whole-chip gate/state budgets stay fixed.

R39 run37447245278 (54dc98d) passes all220,602 NAND/RTL/C/Python clocks
and actual result mutations. All128 learned constants match C before/after
mapping; CEC passes in0.065s and the negative is rejected in0.032s. The626-NAND
mapped selector is archived; the5,991-NAND source composition is unchanged.

R40 `norm_store.py` connects accepted norm outputs to the original four-word
H slot's scalar write port. A word rotates one clock after its last element;
the final rotation must finish before publication. A start at that pending
edge is ignored. Four640-bit reads restore the word order. No packer or second
H vector is allocated. Construction is17,073 NAND/3,074 LATCH, depth202:
512 norm states,2,560 H bits and2 port-control bits. X replay remains external.

The43-NAND/2-LATCH controller passes all512 transitions and a real mutation.
The235,830-clock C fixture checks19 completed rows and76 H read words; it also
resets after element32 is written but before rotation, then fully rewrites the
row. No cleared-RAM assumption is used. The10,396-cycle no-stall reference
includes the final rotation, with reads separate. Actions checks actual NAND,
RTL and an H-readout gate mutation. FF connection and arithmetic sharing are
still external, so this macro is not added to the whole-chip budget twice.

R40 run37449003305 (58a3b66) passes all235,830 actual NAND/RTL/C
clocks and the H-readout gate mutation, including reset before word rotation.

R41 `norm_ff.py` connects externally replayed X to true norm[1], writes the
same H slot, and launches the complete R38 FFN only after the final rotation.
It constructs114,035 NAND/6,456 LATCH, depth202:5,248 original vector bits
plus1,208 other bits. Norm still has a separate MUL/DIV; residual X storage,
residual addition and attention remain external. No whole-budget double credit.

The101-NAND/5-LATCH parent passes all4,096 transitions and an actual mutation.
Four C end-to-end cases agree with the three existing component C references.
Actions drives actual NAND handshakes, checks independent input/output counts,
C norm values, C final down results, ordered phases and a completion deadline.
It requires five complete rows and three resets (norm-word boundary, H A8,
down writeback), then replays recorded stimuli and C expectations in Verilator.
An actual H-output gate mutation must fail both simulators. This is a bounded
functional handshake test, not an independent exact-cycle or unbounded proof.
Large-graph verification has passed. The numerical contract is unchanged.

R41 run37449584059 (b7d4067) passes all1,253,407 NAND/RTL/C clocks,
five completions, three resets and real H-output mutations. Publication is
207,384/208,138 clocks for the fixture without/with optional stalls; this
includes driver-inserted input bubbles and is not a minimum-latency claim.
The complete vector SHA is02d9bae976528ba19227777232139485fc230ac64ddd1de05c4307d054f4d80b.

R42 `norm_ff_shared.py` routes norm and FF operands into one MUL and DIV,
removing307 duplicated state bits. It constructs111,041 NAND/6,149 LATCH,
depth202:5,248 vector bits and901 other bits. Relative to R41 this saves2,994
NAND (2.626%) and307 LATCH (4.755%); the source-cell proxy drops3.119%.
The whole-chip shared allowance is not credited twice.

Down's first MUL load keeps its source priority over a simultaneous SwiGLU
load even on arbitrary old control states. A13-NAND owner predicate passes
all32 inputs and an actual mutation. Actions compares all307 next-state bits
against independently routed loads under explicit norm/FF ownership, checks
the full C numerical fixture in NAND/RTL, mutates H output and observes every
owner guard. Run37450263237 (276432e) passes all1,253,407 NAND/RTL/C
clocks, actual H-output mutations and zero ownership violations. The307-bit
conditional CEC takes0.265s and its negative control0.115s. The complete
vector SHA and observations exactly match R41. No unbounded lifetime proof
or measured physical-area saving is claimed.

R43 `norm_ff_serial.py` prepares a narrow physical interface:26 inputs,
32 outputs and a clock,59 signals instead of705. The existing H word is
selected one s20 lane at a time; only the32nd accepted scalar rotates it.
A7-bit read index marks the128th result. No result vector is duplicated.
Construction is111,686 NAND/6,156 LATCH, adding645 NAND/7 LATCH to R42.
This interface cost is explicit; it is not a claimed gate-count improvement.

The complete1,986-NAND/7-LATCH port passes4,096 old-index/control cases
with independent random640-bit words and a real gate mutation. The pinned
R41 C fixture expands to1,254,027 clocks and640 scalar results across five
completions, all matching C. Independent norm control predicts ready/replay
and input index. Actions adds an independent RTL CEC over every port input,
large NAND/RTL replay and a scalar-output mutation. The physical queue is
unchanged; reduced signal count is not yet a measured routing improvement.


R43 run37451407926 (c1612f4) passes all1,254,027 NAND/RTL/C clocks,
640 scalar results and five last markers. The all-input port CEC takes0.115s
and its negative0.064s; actual scalar-output mutations fail both simulators.
The vector SHA is unchanged from the local conversion. Physical hardening
has not yet measured this interface.

R44 `ff_sublayer.py` completes the true layer0 FFN sublayer,
`X -> S(X + FFN(norm[1](X)))`. An external128-item scan fills the original
X slot; norm replays it internally. FF writes H, then saturated residuals
rewrite the same X slot. The graph is123,793 NAND/8,719 LATCH, depth202,
with7,808 original vector bits and911 other bits. There is one MUL/DIV/SQRT/
DOT and59 signal pins. Attention and whole-transformer control remain outside.
Original X storage is already in the whole-chip budget, so it is not added twice.

The172-NAND/10-LATCH parent passes262,144 complete state/input combinations
and an actual mutation. The268-NAND residual passes2,097 C boundary/random
cases and a mutation. Six full C fixtures include139 saturated residuals.
Actions will prove the entire X port against independent RTL and run the
complete NAND/RTL/C fixture:seven completions, five reset cases and one
partial-read restart, with independent counts and a260,000-clock deadline.
Internal states select reset points and check protocol; every numeric value
comes from frozen C. Large-graph verification is pending. No model rule changes.


R45 `ff_sublayer_ring.py` replaces X's four-word port with a held128x20
scalar ring. Accepted scan/residual writes append one value, replay/result
reads circulate the head, and all other clocks hold. Full128-item passes
restore logical order. This removes the X head selection and word-local
write port, plus one pending bit. Construction is119,841 NAND/8,718 LATCH,
depth202:3,952 fewer NAND (3.192%) and one fewer LATCH than R44.

The140-NAND/9-LATCH parent passes65,536 arbitrary transitions and mutation;
a582-NAND/160-LATCH small ring passes512 arbitrary transitions and mutation.
The six C fixtures have the same SHA as R44. Actions will prove the actual
full-size X port against independent RTL and compare the whole sublayer in
NAND/RTL against C, including resets and partial-read restart. R44's shared
cloud checker adds a scalar-X policy; its construction/C routines are unchanged.
Large-graph verification and the gate reduction remain pending cloud results.

X movement rises from16 to512 rotations per complete vector including readout,
32x; this is an explicit activity cost, not measured power. Residual processing
needs128 rather than132 no-stall clocks. The source-cell proxy drops2.324%,
but no physical-area/power saving or second whole-chip budget credit is claimed.

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
python3 integer_opt/prefix_codec12.py
python3 integer_opt/prefix_packed.py --storage-bits 12
python3 integer_opt/ff_recompute.py --storage-bits 16
python3 integer_opt/ff_recompute.py --storage-bits 12
python3 integer_opt/ff_bank.py
python3 integer_opt/ff_store.py --small
python3 integer_opt/ff_store.py
python3 integer_opt/silu_pipeline.py --core
python3 integer_opt/silu_pipeline.py
python3 integer_opt/ff_row.py
python3 integer_opt/ff_stream.py
python3 integer_opt/ff_shared.py
python3 integer_opt/ff_input.py
python3 integer_opt/ff_input_shared.py
python3 integer_opt/down_engine.py
python3 integer_opt/ff_writeback.py
python3 integer_opt/ff_scale_shared.py
python3 integer_opt/ff_dot_shared.py
python3 integer_opt/ff_acc_shared.py
python3 integer_opt/norm_stream.py
python3 integer_opt/norm_store.py
python3 integer_opt/norm_ff.py
python3 integer_opt/norm_ff_shared.py
python3 integer_opt/norm_ff_serial.py
python3 integer_opt/ff_sublayer.py
python3 integer_opt/ff_sublayer_ring.py
python3 integer_opt/ff_halfword.py
python3 integer_opt/h_halfword.py
```

`--cloud` is guarded by `GITHUB_ACTIONS=true`. R21/R22 explicitly test the
separate P16 numerical profile; R24 adds P12. R26 preserves each input profile.
No training, local EDA or local gate simulation above4,000 NAND is used.


R44 cloud acceptance: run37453327635/1601f37 passed2,279,743 NAND/RTL/C
clocks,964 scalar outputs,seven completions,five reset aborts and one partial
read restart. All-input X-port CEC took6.832s; negative0.216s and both actual
output mutations were rejected. R45 run37454242351/0397b1d passed the same
cases in2,279,707 clocks; X-port CEC0.616s/negative0.114s. This confirms the
3.192% source NAND reduction in the FFN sublayer, not a physical/whole-chip
saving. The unchanged six C fixtures include139 saturated residual results.

R46 `ff_halfword.py` replaces the addressed336-code FF bank with a held
21x128-bit ring. Each16 accepted int8 writes are followed by one committed
rotation. Each32-code down group consumes two halfwords; the final16-code
group consumes one and pads the other16 DOT lanes with zero. A pending bit
blocks the next DOT while the second half rotates. No code precision or
capacity is removed; reset clears control and requires a full rewrite.

The constructed complete FFN sublayer is110,348 NAND/8,719 LATCH/depth202,
9,493 fewer NAND (7.921%) and one more LATCH than R45. The local cell proxy
falls5.704%; the whole-chip conditional budget remains unchanged pending
cloud verification and reconciliation with its existing port allowance.
The14-NAND pending controller passed all64 transitions and a real mutation;
a2,390-NAND reduced bank passed512 arbitrary-state cases and a mutation.
An independent permutation check tracks all336 distinct code positions.
The unchanged six C fixtures and separately derived FF codes are archived.

Actions proves the complete bank against independent RTL, then runs the
full sublayer against C and Verilator with actual output mutations. Its
additional monitor checks all336 writes,1,408 down groups and2,709 rotations
per completion, including the zero-padded tail and restored final order.
The extra1,280 down wait clocks and increased bank activity are explicit
tradeoffs; total cadence awaits the full run. This is not a power claim.

R47 `h_halfword.py` changes the existing H slot to eight320-bit halfwords,
keeping all128 s20 values. Norm, H quantization, down writeback and residual
access use a16-lane head; gate/up reads32 codes and rotates twice. The old
H-quantization pending bit also schedules the second DOT rotation, so no
new LATCH is needed. The constructed complete FFN is107,352 NAND/8,719
LATCH/depth202,2,996 fewer NAND than R46. Its numerical contract is unchanged.

Local checks cover1,024 arbitrary states of a1,019-NAND reduced bank and
2,048 arbitrary/bias-to-active inputs of the actual166-NAND controller cone,
both with real gate mutations. A unique-label permutation covers both336-row
raw-FF passes:5,376 gate/up groups and10,792 total H rotations per sublayer.
These are schedule checks, not full-graph simulation. Actions proves the
entire H bank against independent RTL and checks all H scan/replay/code/DOT/
down/residual values against C, alongside the R46 FF monitor and complete
output sequences. Three additional resets interrupt committed H halfword
moves. Total cadence remains to be measured; increased H activity is not
a power improvement. Whole-chip gate and area budgets remain conditional
and unchanged until full verification and port accounting are reconciled.

R46 cloud acceptance: run37458183095/60ad07f passed2,292,959 actual NAND/
RTL/C clocks,964 scalar results,seven completions,five resets and one partial
restart. Every completion has336 correct FF writes,1,408 down groups and
2,709 bank rotations. Full-bank CEC1.619s and its real negative0.165s pass;
the actual scalar-output mutation is rejected by NAND and RTL. First
publication without injected stalls takes208,665 clocks,exactly1,280 more
than R45. This confirms7.921% fewer source NAND in this sublayer. Whole-chip
accounting must distinguish its two raw-FF passes from the older one-pass
budget, which retains a20-bit raw-FF vector and has no separate A8 scratch.


R48 adds `ff_recompute.py --storage-bits 20`: an exact frozen-v1.1 full C
comparison, in addition to the existing P16/P12 candidates. All27 C16 and
43 C32 cases match the original C logits/traces, FF codes/maxima and access
counts; all70 real FF-code negatives fail. FF row evaluations double.
Generated C and inputs are saved in `build/integer_opt/ff_recompute20/`.
The corresponding conditional two-pass/R46-ring whole-chip accounting is
743,926 NAND/87,857 LATCH,8.723–10.468mm² and80,896,086 cycles/token:
less area but13.152% more cycles than the older one-pass budget. This is
not a complete gate-level model or physical fit claim. The numerical
contract, model blob and deployed demo remain unchanged.

R47 cloud acceptance:3c0f014/run37459790585 passed2,362,346 NAND/RTL/C
clocks, seven complete/eight reset/one partial-restart transactions,964
outputs, full H-bank CEC1.920s and real NAND/RTL negatives. Every complete
sequence includes5,376 H dot groups/10,792 H rotations and all FF writes
and reads. No-stall first publication212,701 clocks, exactly4,036 more
than R46;107,352 NAND/8,719 LATCH confirmed. R48 accounting still uses
R46 and does not double-credit this H-port saving.

R49 `h_read_shared.py` shares the H quantizer/residual scalar read selector
by choosing the four-bit address first:106,461 NAND/8,719 state bits,
891 NAND fewer than R47, unchanged depth202 and59 signal pins. Local checks
cover the1,077-NAND read port, all13-NAND ownership-guard inputs, real gate
mutations and unchanged C/H-code/FF-code fixtures. Full graph is construction
only locally. Its Actions job compares every D/output bit with R47 under
explicit ownership, uses an actual D-bit negative control, and monitors
ownership throughout the complete NAND/RTL/C sequence. The expected R47
vector hash and clock count are mandatory; this is not an unbounded proof.
No wider whole-chip H-port savings are claimed yet.

R50 `pilot_circulate.py` connects the R10 continuously rotating KV bank to
the unchanged R15 real layer0 weight/arithmetic composition:94,213 NAND /
9,242 state bits,26,502 NAND below R15. Every clock moves the bank, including
unrelated arithmetic and reset; reset discards logical validity and requires
refill. Local74-NAND/34-state-bit arbitrary-state checks and mutations pass;
18,222 C/logical-bank vectors are prepared. The separate Actions job checks
full bank CEC and actual whole-pilot NAND/RTL/C plus real mutations. This is
an unadopted area/activity tradeoff, not unchanged latency or measured power.

### R51: continuous-bank full-model scheduling candidate

`continuous_model.py --context 16` (then32) checks the same70 R48 cases against
frozen C, with and without adversarial gaps. Logits/traces/FF code digests are
identical; actual KV/prefix read-bit faults are each rejected70/70. Existing
dead H/A slots capture prefix words before serial residual arithmetic. This
retains R48 full-word ports and is not a complete gate/controller proof.
`continuous_banks.py` constructs matching-request banks for C16/C32; its
`--cloud` mode additionally verifies actual state transitions, all-input RTL
CEC and real mutations. No large local simulation. Conditional C16 whole
component accounting is515269N/87857L, not adopted; timing/power and physical
fit remain unresolved. Artificial test-gap clocks are not token latency.

### R52: continuous prefix word client

`prefix_client.py` constructs a10298N/42262L client: serial word input/output,
capture of old prefix data,32 serial frozen-C residual adds, then a matching
write. Both640-bit H/A slices are explicit standalone state; no whole-model
budget reduction is claimed. The1186N/250L local client passes1024 arbitrary
transitions and863 clocks including real data-gate faults. The actual-size
83646-clock C reference includes24 original FFN fixture words, all64 entering
phases, stalls and five reset aborts. Its130–193 no-stall residual range still
needs actual large-gate verification. `--cloud` proves every D/output against
independent RTL and runs actual NAND/Verilator/C with real negative controls.

### R53: whole KV words in H, attention accumulators in dead FF storage

`block_kv_model.py --context 16` (then32) retains frozen arithmetic, each
lane's accumulation order and all70 R48 fixtures. Both gap modes give exact
logits/traces/FF digests; actual cached-code and stored-accumulator faults are
each rejected70/70. A276-bit KV cache fits the dead640-bit H head;32 aligned
s64 accumulators fit2048 of the dead2688 FF-code bits. C16 KV reads become5440
instead of174080; dequantization count is unchanged. FF byte transfers and
21-word ring rotations cost an additional1627040 abstract clocks. The
unadopted conditional budget becomes82865398 cycles,4.351% below R51. No
NAND reduction, physical fit or power result; routing/cursor/control remain
to be integrated into actual NAND. Cloud reproduction uses only C here.

### R54: snapshot and dequantize a circulating KV word

`kv_client.py` constructs5937N/9126L: matching read/write,276-bit snapshot,
32 scalar outputs, stalls and reset aborts. The unchanged2488N h3a dequantizer
is pinned in `kv_units`; dedicated arithmetic is an explicit option, not a
free or physically single-cycle substitute for the shared core. Small raw
q/m capture passes1024 arbitrary transitions and219 clocks; numerical gates
separately pass4223 C vectors and a real fault. Full11154-clock actual
NAND/RTL/C verification awaits Actions. Protocol CEC covers every D and raw
q/m/index/flag, with the fixed arithmetic composed afterward; it does not
claim a new all-input arithmetic proof. Whole-model budgets stay unchanged.

### R55: aligned 64-bit accumulators in the FF code bank

`ff_acc_port.py` constructs 9478N/2770L over the unchanged held 21×128-bit
FF bank, with explicit captured64/cursor5/control13 state. The 2468N/460L
small port passes 1024 arbitrary transitions and 574 clocks with real faults.
The first 4096 actual scratch accesses from a complete frozen-C random16
forward pass are replayed in the reference; untouched positions are checked.
Full 37771-clock NAND/RTL/C and all-D/output CEC await `--cloud`. Read/write/
home use 3/10/2 no-stall protocol clocks plus exact rotations. This partial
trace and standalone port do not change whole-model estimates or claim
physical timing. Shared operand/control ownership remains to be connected.

### R56: weighted V numerator updates through the existing banks

`value_row.py` connects the KV client, FF accumulator port and serial MUL:
17598N/12121L. It clears, updates one complete32-lane V row, reads all32
accumulators or loads KV. The held FF halfword supplies the old sum during
17 multiplier steps, without another64-bit register. Local382N/16L control
checks8192 arbitrary transitions; a1338N/192L product view checks128 signed
products and real faults. Five true C attention heads, one per layer, yield
110210 reference clocks with four aborts. `--cloud` proves the complete
state/output composition and runs actual NAND/RTL/C with real mutations.
The fixed dequantizer is shared on both sides of CEC, not independently
reproved arithmetically. Scores, denominator/division and whole-layer storage
ownership stay outside; whole-model budgets and physical timing are unchanged.

### R57: continuous FF storage in the V numerator component

`ff_continuous.py` provides atomic matched64-bit writes and captured reads;
`value_continuous.py` reuses the R56 controller, multiplier and true C cases.
The component constructs9430N/12118L versus17598N/12121L, a46.414% NAND
reduction. Small1229N/457L checks cover1024 transitions and744 actual clocks
with real faults. Full221415-clock NAND/RTL/C and all-state CEC await Actions.
A matched no-stall head costs23943 versus17427 reference clocks (+37.390%);
gate-weighted cycle cost rises18.670%. Every bank position moves every clock.
HOME acknowledges a row0 pass, not a stopped idle bank. This candidate is
not connected to the FFN byte producer/down consumer, so whole-model budgets
and physical claims remain unchanged; fewer NANDs are not a power result.

### R58: compact exact product using stable cached V

`compact_mul.py` computes signed20 by unsigned17 in17 step pulses with
one37-bit register; a21-bit high-part add retains the extra pre-shift sign
bit. `value_compact_mul.py` uses the stable KV cache as x and keeps
the64-bit FF accumulator and R57 command sequence. It constructs8741N/11963L
versus9430N/12118L, with depth308 versus260. This is not a frequency claim.
The498N/37L unit passes4096 arbitrary transitions,256 exact products/6830
clocks and768 real fault detections. The221415 full C-reference vectors and
five true heads retain exactly the R57 SHA values. Actions runs all-D/output
composition CEC and actual NAND/RTL/C; the whole model budget is unchanged.

### R59: exact normalized V stream

`value_normalize.py` connects R58 to the pinned serial DIV. READ32 captures
a nonzero22-bit denominator and emits S(R(n,d)) for each held64-bit numerator.
Zero-denominator reads are ignored; busy input changes and resets are covered.
The constructed component is11207N/12129L/depth308, including115 DIV,
9 control,20 result and22 denominator state bits. Its2420N/144L isolated
arithmetic client passes4832 actual clocks against C with real faults.
Five true C heads, ties, saturation and four combined aborts supply404923
reference clocks; all-D/output composition CEC and actual large NAND/RTL/C
stay on Actions. DIV/dequantizer are pinned shared proof boundaries.
Score/EXP/denominator generation and full ownership remain outside.

### R60: internal denominator and16-row C16 bound

`value_denominator.py` accumulates each accepted u17 weight in R59's
existing denominator register. CLEAR/reset zero the sum/count; a17th
ACCUMULATE and zero-total READ are ignored. Five extra count bits ensure
the sum is at most2097136, exactly within the retained22 bits. The component
constructs11435N/12134L/depth308, removing22 external input pins. Small421N/
27L arbitrary-state checks pass with faults. Five real C heads, signed
half means, maximum/zero sums and four combined aborts supply453853
reference clocks for cloud CEC/NAND/RTL/C. Score/EXP and full scheduling
are still external; whole-model budgets and numerical rules are unchanged.

### R61: score FIFO, signed maximum and EXP stream

`score_stream.py` stores up to16 signed32 scores, updates their maximum
during fill, aligns the held FIFO, and streams exact u17 EXP weights with
index/valid/take. Busy starts and invalid lengths are rejected; reset
invalidates data. The full component is4852N/561L/depth103, including the
pinned2528N EXP. Its3604N/171L four-score version passes413 gate/C clocks,
512 arbitrary transitions and149 actual weight-fault mismatches.
Five frozen real heads retain the R56 80-weight SHA. Signed extrema, ties,
clipping and three-phase resets supply3488 full-size reference clocks.
Actions performs all-D/output CEC with the same pinned EXP boundary,
full NAND/RTL/C replay and actual faults. R60/shared storage integration
is still outside; numerical rules and whole-model budgets are unchanged.

### R62: autonomous score-to-normalized-V head

`attention_stream.py` connects R61 scores to R60 V. LOAD preloads V words;
HEAD accepts1..16 scores, then sequences CLEAR, ordered EXP-weight/V
accumulation and32 normalized signed20 results. Input/output stalls and
reset/reload are explicit. The whole source is16373N/12698L/depth308;
the163N/3L parent passes every32768 state/input combination and real faults.
Five true frozen-C heads,1/3-row arithmetic prefixes and five abort stages
prepare281330 clocks/640 results for Actions. A16-row head takes25430
clocks without stalls, excluding preload. The independent all-D/output
reference retains pinned EXP/DIV/dequantizer boundaries; actual gate/RTL/C
and faults are required. QK scoring, model-wide ownership and physical
storage sharing remain external; whole-model budgets are unchanged.

### R63: exact serial signed QK dot32

`score_dot.py` accepts32 captured s20 pairs, computes each signed40
product in20 steps (subtracting the last sign bit), and accumulates s45.
The pinned2630N score operator retains both C RNE operations.
The full source is4003N/118L/depth277,705 clocks to valid without stalls.
Separate608N/40L multiplier,1463N/118L MAC and2630N score checks stay below
the local4k limit, including15105 actual MAC clocks and true faults.
All80 real scores match the R61 input hash. Actions proves all118D/40outputs
and replays86246 full clocks with resets, stalls, signed extrema and faults.
Q/K storage/dequantization and R62 integration remain outside. The narrower
multiplier is not a general64-bit replacement; model budgets are unchanged.

### R64: handshaken QK pairs through a complete weighted-mean head

`attention_pairs.py` connects R63 to R62 with no new state. The existing
score fill count requests history 0..15; the dot lane requests Q/K 0..31.
After each accepted pair the dot captures both operands, and the caller
may change its inputs. Scores, EXP weights, denominator and the 32 V means
are internal; V words use the actual KV bank. Q/K storage and dequantization
remain external. The full source has 20,314 NAND /12,816 LATCH, depth 308.
The 19-NAND connector passes all 64 inputs and actual gate faults locally.
Three independent frozen-C observations join Q/K, scores and V partials:
315,308 reference clocks cover five real heads, arithmetic prefixes, stalls
and six reset/reload stages, with 512 outputs. No-wait 16-row heads take
36,014–36,056 clocks excluding preload; rotating-bank phase affects timing.
Actions checks all D bits/outputs against the independent composition,
then actual NAND/RTL/C clocks and real D/result/RTL faults. Existing pinned
arithmetic boundaries remain; numerical rules and whole budgets are unchanged.

### R65: one KV bank and dequantizer for both attention phases

`attention_kv.py` stores actual V8 words at addresses 0..15 and K8 words
at 16..31 in the existing 32-row bank. During score filling it reads K
into the same cache/dequantizer and handshakes scalar Q with dot32; during
V accumulation the original owner resumes. Only bank input controls are
multiplexed: the old KV next-state cone is replaced, with no second bank
and no full-state mux. Total 20,342 NAND /12,816 LATCH, depth 308, adds
28 NAND and zero state to R64. Q storage remains external.
The 49-NAND arbiter passes 8,192 mode/address cases and actual faults.
A fourth frozen-C observation supplies real K8 codes/maxima; all decoded
keys match the prior dot operands. The 339,145-clock C schedule includes
five true heads, all 32 bank addresses, seven abort/reload stages, 544
outputs and 104,533 K-owned clocks with no V port conflict. No-wait heads
take 36,518–36,539 clocks excluding preload. Actions checks every D/output,
actual full gate/RTL/C clocks and real faults. Whole budgets are unchanged.

### R66: Q and V sums share the existing FF storage

`attention_local_q.py` keeps 32 exact s20 Q values in the 640 unused FF
bits (logical rows 16..20), alongside 32 s64 V sums in rows 0..15.
QLOAD writes five 128-bit words with a ready handshake; normal LOAD writes
K/V. HEAD then runs without scalar Q input. The 144-bit read window handles
Q values crossing a physical row. A largest-shift-first shrinking window
reduces the small port from 2,068 to 1,444 NAND; complete head 22,104 to
21,480 NAND /12,816 LATCH, depth 308. No extra Q state is allocated.
Both small ports match 4,096 independent cases; actual optimized gate
faults are rejected. The 463,276-clock C schedule covers five real heads,
one signed-extrema arithmetic head, seven abort/reload stages, 576 outputs,
100 Q word writes and 5,058 Q reads with zero Q/V or K/V write conflict.
True no-wait heads take 45,233–45,254 clocks excluding preload: sharing
storage adds rotation waits. Actions checks all D/output CEC and actual
gate/RTL/C faults. Producers, whole-model ownership and shared arithmetic
remain outside; numerical rules and whole budgets are unchanged.

### R67: packed s42 numerator and s20 Q storage candidate

`head_store.py` constructs a specialized 32×62-bit circulating store:
42-bit V numerator and 20-bit Q per row, plus a captured 42-bit accumulator
port and a direct Q port. It has 834 NAND /2,040 LATCH, depth 24, versus
the existing generic FF client's 1,312 NAND /2,767 LATCH. This is a narrower
contract, not an arbitrary 64-bit FFN replacement or an integrated head.
The frozen s42 bound covers 16 sat20 values times legal u17 weights.
The full 2,874-element component passes 512 arbitrary transitions and a
4,096-clock real-C-data prefix locally, including actual gate faults.
All five heads' 16 numerator prefixes prepare 202,374 storage clocks for
Actions, with 2,631 writes/reads, 256 Q writes, stalls, endpoints and reset.
An independent packed-memory reference proves all state/output bits on
Actions. Clock scheduling and state layout differ from the 21-row FF bank;
R66 integration and complete-head timing must be measured before adoption.

### R68: packed Q/V state in the complete attention head

`attention_packed.py` replaces the entire old FF state cone with R67's
32×62-bit bank, sign-extending the s42 payload at the legacy interface.
There is one bank, with no retained shadow storage. The complete head falls
from 21,480 NAND /12,816 LATCH to 19,543 /12,089, depth 308; the local cell
proxy falls 6.472%. QLOAD now accepts 32 scalar s20 words at addresses 0..31,
held until ready, instead of five packed words. Normal K/V LOAD is unchanged.
A 653-NAND action cone passes 4,096 independent cases and actual faults.
The 541,696-clock frozen-C schedule has 576 results, 640 Q writes, 5,058 Q
reads, seven abort/reload phases and no Q/V or K/V conflict. Five true heads
each take 54,143 clocks excluding preload, about 19.7% slower than R66.
Actions checks all state/output bits against an independent composition,
then every actual NAND/RTL/C clock and real D/result/RTL faults. The existing
s42 bound preserves exact arithmetic; numerical rules and whole budgets
are unchanged. Producers and model-wide resource sharing remain external.

### R69: exact score rounding and four-adder constant multiplication

`attention_score.py` implements signed RNE as arithmetic floor plus the
half/sticky/odd correction, then 46341q through f=5q, g=9f, h=4g+q,
p=256h+f. Exact signed widths 34/36/39/41/49 include the positive rounded
endpoint. The score leaf drops 2,630→1,794 NAND and depth 277→138; the R68
head drops 19,543→18,707 NAND with unchanged 12,089 LATCH and depth 308.
Both actual small graphs match frozen C on 12,937 cases; output-gate and
inner/outer tie-rounding faults are rejected. The complete 541,696-clock
schedule and vectors remain byte-identical. The first monolithic old/new score CEC timed out (run 37518493451),
without a counterexample. Actions now proves six universal cut obligations
against independent integer RTL, binds them to the actual NAND DAG using
only exact structural identities, and checks coefficient/range bounds.
The resulting all-45-input integer-spec proof precedes the unchanged
all-D/output head composition and full gate/RTL/C checks. Numerical rules and whole budgets are unchanged;
no extra cycles, global area deduction or physical frequency claim is made.

### R70: exact KV dequantization by base-128 folding

`attention_dequant.py` replaces the one shared K/V dequantizer in R69.
For signed8 q and unsigned20 m, n=q*m fits signed28. Since 127 is odd,
R(n,127)=floor((n+63)/127), with no ties. Base-128 digits fold this into
three arithmetic shifts and a nine-bit remainder sum. Its exact reachable
range is 0..507, so three threshold tests give a two-bit correction.
Radix-4 Booth multiplication and exact signed20 saturation complete the
leaf: 2,488→2,210 NAND (−11.174%), depth 249→206. An array-product version
uses 2,402 NAND but has depth 158; the primary choice minimizes gates.
The complete head is 18,429 NAND / 12,089 LATCH, depth 265, with exactly
the R68/R69 clock sequence. All 15,109 small-graph cases match frozen C,
including 5,120 actual K/V operands, every signed8 code and all remainders;
output-gate, missing-round-bias and missing-saturation faults fail.
The direct old/new dequantizer CEC timed out in run 37521505663. Actions
now checks all 268,435,456 inputs with `dequant_exhaust.c`, a 64-lane NAND
interpreter decoding the exact old/new bytes and comparing every result
with frozen C. A real mutated output gate must fail. It also proves the
six score cuts, all D/output head composition and every one of 541,696
NAND/RTL/C clocks. Local runs use `--stage leaf` then `--stage head`, each
under 55 seconds; source and case hashes bind the two stages. Locally the
exhaustive interpreter checks only four 4,096-input windows. Numerical
rules, PPL, actual netlists and whole budgets are unchanged.

### R71: compact exact attention divider

`attention_divider.py` specializes the existing saturated RNE divide to the
proved signed42 numerator / nonzero unsigned22 denominator domain. It does
one load and 42 restoring steps; quotient parity supplies exact ties-to-even.
The same held-result/ready wrapper takes 44 clocks from load to observed valid,
versus 66 for the old 64-step divider. Reset aborts, busy starts are ignored,
and a zero denominator is rejected before loading.
The raw s20-output core is 1,526 NAND /87 LATCH versus 2,120 /115 with the
old core's unused outputs pruned. Including identical handshake behavior,
2,394 /144 becomes 1,788 /115 (NAND -25.313%, local cell proxy -24.057%).
Local actual NAND/C checks cover 4,962 operands (2,560 real partial attention
numerators), all 233,430 new and 342,693 old clocks, 2,048 arbitrary transitions
per new core/wrapper and real output/D gate faults. Output hashes agree.
Actions proves every D and output against independent restoring/control RTL
and replays every NAND/RTL/C clock with real faults. This standalone block
is not yet integrated into the full head; whole budgets and frozen v1.1
numerical rules are unchanged. It is not a general s64/u25 replacement.

### R72: compact divider integrated into the complete head

`attention_fastdiv.py` installs R71's exact raw divider in the complete R70
head. An explicit adapter preserves the existing hierarchy's interfaces;
the final graph removes all 28 unused adapter state bits and the old high
counter bit. There is one 87-bit divider state, with no hidden old copy.
The complete head falls 18,429→17,823 NAND (-3.288%) and 12,089→12,060 LATCH;
depth remains 265. Score/dequant/raw-divider SHA guards freeze proved leaves.
The 523,242-clock C reference covers the same 576 results, true heads, stalls,
seven abort/reloads and saturation stress. Every output matches R70; five
unstalled true heads now each take 53,119 instead of 54,143 clocks, excluding
preload. This measures interaction with the rotating bank rather than adding
isolated divider savings. A 131-NAND control passes all 4,096 transitions and
real D faults; the raw divider's arbitrary-state checks also pass locally.
Actions separately proves the raw divider against independent RTL, then all
complete-head state/output bits and every NAND/RTL/C clock with real faults.
Whole-model resource sharing and budgets remain unchanged; full source gates
and physical timing await cloud evidence.

### R73: descending layer positions without retained KV history

`reverse_kv.py` generates a temporary pure-integer C implementation from the
frozen golden and existing workspace/two-pass FF refinements. Each layer
visits positions in descending order, so every required source row 0..p is
still old. For each head it recomputes exact Q, then K for score/max, then V
for the weighted sum. RoPE, per-head KV quantization, RNE and saturation stay
unchanged. H holds partial output heads; A serves norm/A8 scratch. The dead
2,688-bit FF bank fits Q20[32], temporary20[32] and accumulator42[32] in
2,624 logical bits, with no additional full vector. Host C containers are
wider; actual packed gates/ports have not been built. Existing proved R11
cache-free sampling avoids dependence on a removed KV scratch cache.
All 27 C16 and 43 C32 fixtures match every frozen logit and layer trace.
Ascending overwrite faults fail on all 66 multi-token cases; actual V-code
bit faults fail on all 70. C16 removes 35,328 logical history bits, but its
attention-related norm calls rise 160→5,760 and K/V output rows each 10,240
→87,040. This is an unadopted area/latency tradeoff. A capacity-only projection
retains all 743,926 NAND and control reserves while reducing 87,857→52,529
LATCH; new packing/ports are not priced or proved. The inherited arithmetic
budget increases from 80.90M to 303.97M cycles before new access costs.
Actions reruns both contexts with GCC and the existing cache-free sampler.
No whole-chip gate/physical fit, clock rate or free bandwidth is asserted.


### R74: immutable normalized-prefix cache

`python3 integer_opt/norm_cache.py --context 16` (also `32`) derives a pure-C
schedule from R73. Each layer stores A8 norm codes and its exact maximum
before updating any prefix row. K/V are still recomputed, but norms are not.
Both ascending and descending positions compare every logit/trace byte to
frozen C; stale-layer and actual code-bit faults must fail. C16 replaces
35,328 KV bits with 16,704 cache bits. This is a value/lifetime prototype,
not a packed-cache, gate-controller or physical timing result. No numerical
specification changes. The separate `norm_cache.yaml` runs strict GCC and
the existing cache-free R11 sampler without installing EDA tools.


### R75: actual byte cache bank

`python3 integer_opt/cache_bank.py` constructs the R74 cache as2,096 circulating
bytes plus a12-bit modulo counter:237 NAND /16,780 LATCH. It checks only small
bank/control gates locally and prepares2,705,975 C-data reference clocks.
`--cloud` is guarded by `GITHUB_ACTIONS` and proves all D/output RTL equivalence,
then replays every clock through canonical NAND and Verilator, with real faults.
The131-byte row includes64 total padding bits. Requests remain stable until
ready; reset discards validity and requires refill. Norm producers, A-slot burst
ports, FF packing and complete model control are not included in this block.


### R76: same-edge scalar update and work-slot rotation

`python3 integer_opt/stage_fused.py` preserves the existing four-row s20 slot
size (14,885 NAND /2,560 LATCH) while rotating the just-updated head. Depth
increases13->15. Only the newly supported simultaneous update/advance mode
differs from the old stage. It loads128 codes in128 rather than132 clocks;
full readback costs another4. Actual old small gates on the new schedule
reproduce the dropped final lane. The separate workflow proves all D/output
against independent RTL and replays both full-size schedules with real faults.
The byte-cache client and normalization producer are still separate.


### R77: cache and existing A-slot transfer client

`python3 integer_opt/cache_client.py` constructs the actual13,476-NAND /
19,378-LATCH client: R75 byte cache, R76 original A slot and38 control bits.
It transfers128 codes plus3 maximum bytes without an extra vector. Same-edge
row rotation sustains131 consecutive accepted bytes after alignment. Local
checks cover only the494-NAND controller and small address logic; all80 true
C vectors produce427,924 reference clocks including stalls, busy inputs and
reset/refill. The workflow proves every D/output against independent RTL and
replays full NAND/Verilator with real faults. Norm producers, matrix ownership,
FF scratch ports and the complete transformer are outside this block.


### R78: real cached Q/K/V matrix path

`python3 integer_opt/cache_matrix.py` connects the R77 cache/A client to the
existing bounded layer0 BitLinear engine. A4-bit owner fetches a cached input,
launches Q/K/V and rotates A on each accepted32-code group. No vector is added.
The full source is101,764 NAND /19,885 LATCH. Local checks cover all16,384 small
owner transitions and512 connector cases;49 complete real-C matrices generate
866,249 reference clocks. Actions proves every owner D and leaf connection,
then checks actual full NAND/Verilator, including a missing-A-rotation mutant.
This does not claim a new all-state proof of the original engine, complete
transformer control, or full-model area credit. The original seven-matrix
constant network remains in this engine.


### R79: cursor-bounded constant weight selector

`python3 integer_opt/matrix_range.py` constructs cursor-range proof obligations before
using only the first four128x128 matrix tables in R78. Reset/inductive predicates
are actual2/305-NAND graphs. Constant propagation of the existing mapped table
uses29,667 NAND, less than a32,729-NAND rebuild. The complete cached path falls
101,764->51,277 NAND with19,885 LATCH unchanged. Local checks cover only small
predicates and the C-parser table; R78 reference bytes are hash-checked and reused.
Actions regenerates them, proves reset/induction and the weight cofactor, exhausts
all2048 legal selector addresses, and replays all866,249 full matrix clocks with
faults. The old engine remains available for seven-matrix calls; this restricted
block's removed FF weights are not credited as a full-model saving.


### R80: Q/K/V-only selector under owner and cursor invariants

`python3 integer_opt/qkv_range.py` constructs the stronger matrix<3 obligations.
The31-NAND owner predicate is exhaustively checked locally, together with reset
and the small cursor predicate. Unreachable O addresses can repeat V, reducing
the selector29,667->24,919 NAND and the full cached path51,277->46,508 NAND.
LATCH count remains19,885; selector depth rises23->31. Actions proves owner/cursor
reset and induction, masks only unreachable addresses for weight equivalence,
and replays the unchanged866,249 full matrix clocks with real faults. O and FF
weights remain necessary elsewhere in the model, so no whole-model credit is
claimed from this restricted block.


### R81: remove only state unobservable at every future output

`python3 integer_opt/qkv_live.py` takes the actual R80 graph's backward closure
through NAND inputs and LATCH D. It retains all observable feedback and removes
1567 state bits plus6763 NAND:39745 NAND /18318 LATCH, depth202. Every retained D
and public output is structurally identical for arbitrary original state/input;
real output mutation breaks identity. The512-case small feedback fixture checks
the pruning rule. Actions proves the projection and inherited range obligations,
then replays the unchanged866249 full NAND/RTL/C-data clocks with pruned faults.
The original external pins remain. Raw A20 producer sharing and complete-model
integration are still outside this consumer path, so no whole-model storage
credit is claimed.


### R82: Q matrix bank circulation with an explicit cycle tradeoff

`python3 integer_opt/qmatrix_ring.py --stride byte|word` keeps the proven fixed
Q matrix's actual weights and arithmetic but changes its A8 storage schedule.
Byte-only uses16923 NAND /1505 LATCH versus22924/1498, at roughly2.43x matrix
cycles. Dual stride (one byte during fill,32 bytes otherwise) uses20023 NAND
and13950–14046 unstalled clocks versus13569. Every acknowledged input and DOT
waits for the true physical cursor; no hidden vector is added. Two small-bank
checks include an actual missing-rotation fault. Actions proves each full bank
and the weights, then checks929083/301045 complete NAND/RTL/C-data clocks with
faults. The existing physical macro stays frozen until a candidate is verified;
full-model sharing and activity/power remain separate questions.


### R83: actual norm-to-cache producer with one A-slot write port

`python3 integer_opt/norm_cache_fill.py` builds true layer0 norm[0], the existing
A20 slot, in-place A8 quantization and the actual131-byte cache writer. The caller
retains/replays X. No normalized or code vector is added:20066 LATCH includes
the existing19378-bit cache/client,512 norm bits,169 quantizer bits and7 owner bits.
Choosing the port command before its shared decoder reduces the composed producer
38790->21809 NAND, without changing state or clocks. The naive composition is a
new reference, not an adopted model budget. Small arbitrary-state port/controller
checks and26 complete C fixtures produce684106 reference clocks; full graphs are
only constructed locally. Actions proves all2560 slot D bits and the connection
logic, checks the128 true norm coefficients, and replays NAND/RTL/C with actual
cached-data and removed-rotation faults. A paused byte can miss a full cache turn;
the observed14804–14805 unstalled clocks are not a bound under arbitrary stalls.
Matrix-consumer ownership, arithmetic sharing and full-model budgets remain open.

### R84: one divider for sequential norm and in-place A8

`python3 integer_opt/norm_cache_shared.py` identifies the two115-bit divider
states and selects their complete load command before the existing divider.
The producer uses20953 NAND/19951 LATCH instead of21809/20066, with unchanged
684106 reference clocks, cases and public pins. All115 D expressions of each
old instance are structurally bound to reconstructed commands. Actions proves
the selected next state after state identification, then checks the complete
shared NAND/RTL/C sequence, including actual wrong-owner, missing-rotation and
data faults. This conditional transition proof is not an unbounded lifetime
proof. No numerical contract, full-model budget or published layout changes.


### R85: remove unobservable producer feedback state

`python3 integer_opt/norm_cache_live.py` follows both NAND inputs and LATCH D
connections from every public output. It removes only48 unreachable high MUL
bits and their exclusive logic:20953/19951 becomes20593 NAND/19903 LATCH. All
19903 retained D bits and40 outputs have identical canonical expressions for
arbitrary old state/input. No zero-initialization or valid-input assumption is
added. The684106 C vectors and interface stay byte-identical. Actions checks
this projection and every actual pruned NAND/RTL/C clock, including real faults.
The unchanged43-line projection helpers now live in `state_projection.py` and
are reused by R81; the original R81 graph is reproduced byte for byte locally
and its existing cloud workflow is retriggered. Whole-model budgets stay separate.


### R86: norm producer and QKV consumer own one actual cache/A20

`python3 integer_opt/norm_qkv.py` connects true norm0, in-place A8, the actual
16-position cache and true Q/K/V rows. Only raw X is replayed externally; no
precomputed activation fill interface remains. The exact R85/R80 child graphs
share19378 physical state bits and an explicit owner, totaling71579 NAND/20411
LATCH. R80 retains all raw A20 bits needed by norm; the consumer-only R81 pruning
cannot replace that shared bank. The1536-input connector, two small ownership
checks and1503175 frozen-C-data protocol clocks pass locally. Actions proves
independent connector/reset/induction cuts and replays the complete real NAND/
RTL with actual data/owner faults. Full graph checks are pending; this is an
integration baseline, with no full-model area or numerical-contract change.


### R87: select cache/A20 commands before the common update logic

`python3 integer_opt/norm_qkv_ports.py` keeps every R86 state bit and public pin
while sharing the cache and A20 update logic. The actual child commands bind
all19378 D expressions exactly. The reproduced baseline71579N/20411L becomes
54314N/20411L, depth202 unchanged. Both small arbitrary-state controller and
slot checks pass with real faults; all1503175 C-data clocks remain identical.
Actions proves every shared D for arbitrary old state and input, preserves all
other D/output expressions, then checks the complete real optimized NAND/RTL
sequence, including result/owner/RTL faults. Full proof/replay remains pending.
No numerical change, extra vector, full-model saving or new layout is claimed.


### R88: one RNE divider for norm/A8 and QKV phases

`python3 integer_opt/norm_qkv_div.py` binds each original115-bit divider D to
its reconstructed command, identifies the states and selects the active owner
before one actual divider. Separate result registers remain. The integration
changes54314N/20411L to52646N/20296L, with unchanged depth202 and1503175 reference
clocks. The512-input small command mux and inherited port/control checks pass.
Actions proves conditional owner-selected D, reruns inherited proofs and the
actual shared NAND/RTL/C sequence, including an actual wrong-divider-owner
fault. This is not an independent unbounded sharing theorem. No full-model
budget or numerical contract changes; full cloud verification is pending.


### R89: share norm's144 live MUL bits with the full QKV192-bit unit

`python3 integer_opt/norm_qkv_mul.py --references build/integer_opt/norm_qkv_div`
uses the source-checked R88 C data locally. Actions `--cloud` always regenerates
all1503175 clocks and checks fixed expected/case/vector digests. Both actual
command bindings and64 arbitrary-state comparisons of the3864/3080-NAND cuts
pass with real faults; the129-bit command mux has512 tests. The complete graph
uses52096N/20152L instead of52646N/20296L. Actions checks conditional192-D sharing,
inherited proofs and every real shared NAND/RTL/C clock, including wrong-MUL,
wrong-DIV, common-owner and numeric/RTL faults. Norm's unobserved48 high bits
use the full multiplier's extension. Results and control remain distinct; no
independent unbounded sharing theorem or full-model saving is claimed.


### R90: remove31 unobservable shared-path state bits

`python3 integer_opt/norm_qkv_live.py --references build/integer_opt/norm_qkv_div`
projects the complete shared-arithmetic path to future-observable state, using
unchanged `state_projection.py`. All20121 retained D and76 public outputs are
canonically identical for arbitrary original state and input. Four cursor bits,
18 high MUL accumulator/left-shift bits and9 high temporary bits disappear;
raw A20 and right-shifting high bits remain. The graph becomes51843N/20121L,
depth202 unchanged. Small feedback/fault checks pass. Actions regenerates all
1503175 C clocks and proves projection/inherited cuts, then checks actual
pruned NAND/RTL and all numeric/common/DIV/MUL-owner faults. Local references
are source/hash checked; no full-model credit or changed arithmetic contract.


R91 `norm_cache_recompute.py`: exact twice-norm producer. The first complete
norm streams directly into A8 maximum scan; the second streams into conversion,
holding each norm output while the same115-bit divider performs A8. Only final
signed8 codes use the original A slot. Future-output projection deletes1536
raw-A high bits and the existing48 dead MUL bits. Constructed12871 NAND/18367
LATCH versus R85 20593/19903, depth202. X is externally replayed512 times per
vector, twice the previous input traffic; no-stall completion25284–25285 clocks
versus14804–14805. No numerical contract or whole-model budget change. Local
3097-NAND connector1536 vectors, owner/feedback/mux and actual mutations pass;
1069497 C-data protocol clocks cover26 cases,32 completions and6 aborts. Full
actual NAND/RTL/C, projection/shared-divider/connector/slot/weight proofs and
real faults require the matching Actions workflow. Local C data is reused only
after R85 source/SHA checks; Actions regenerates it from frozen C.


R92 `norm_cache_root.py` keeps the first exact RMS root for the second norm
output pass. The existing start clears index/count and already preserves root;
only the accepted restart phase changes1->5. Actual17 controlD bind to the
407-NAND independent cut;14336 cases and real mutation pass locally. This adds
2 NAND (12873 total) with18367 LATCH/depth202 unchanged, reducing no-stall
completion25284–25285->23188–23189 clocks and X traffic512->384 scalars; the
two fixed stalled cases remain79781 clocks due to cache phase. All1000883 new
C-data protocol clocks pass locally. Matching workflow checks independent RTL
control, inherited proofs, actual NAND/RTL/C and wrong-restart/other real faults.
This is pending cloud validation; numerical rules and whole-model budget stay
unchanged.


R93 `norm_qkv_recompute.py` connects the exact root-reusing producer to the
actual Q/K/V consumer, retaining the common cache command port and shared
DIV/MUL. Complete-state projection removes1536 A high bits and31 previous
dead scalar bits. Constructed44124 NAND/18585 LATCH/depth202 versus validated
R90 51843/20121; no external A fill or extra vector. Local exact D-command
bindings, projection identities, owner/connector cuts and1693911 C-data clocks
pass (7041 results/28164 groups/two aborts). The same test transaction mix was
1503175 clocks before; this is not a whole-token speed claim. Local protocol
uses source/hash-checked command-boundary checkpoints below55s per process;
repeat the same command while it reports incomplete. Actions regenerates the
uninterrupted trajectory and proves all transformations and actual faults.
Full graph validation is pending; numerical rules and whole-model budget stay
unchanged.

R94 (`qkv_care.py`) reorders the actual Q/K/V weight decision tree and merges
compatible cofactors only in the proved-unreachable fourth matrix. Four fixed
radix orders are recorded; matrix/row/group gives23,777 NAND versus24,919.
The integrated exact-root/cache/QKV path becomes42,982 NAND/18,585 LATCH,
with the same1,693,911-clock C protocol. Small care functions and C word parsing
pass locally. The separate workflow proves the owner/cursor range, all2,048
masked weight addresses and all18,585D+76 outputs under the exact legal-address
predicate, then runs every actual NAND/RTL/C clock and real faults. Cloud checks
remain required; no whole-model budget or numerical rule changes.

R95 (`qkv_plain.py`) keeps that matrix/row/group order and uses ordinary Shannon
sharing, as R4 favored for the larger weight tables. Explicitly repeat V in the
fourth matrix: all2,048 addresses now match R93, without a new range premise.
The selector is21,695 NAND/depth23, and actual complete integration is40,921 NAND
with18,585 LATCH/depth202. State and1,693,911 reference clocks stay unchanged.
48 small fully defined tables/2,016 addresses and actual faults pass locally;
full selector plus every D/output CEC and actual NAND/RTL/C remain Actions-only.

R94's first monolithic transition CEC exceeded150s after the independent
selector/range and all inherited proofs passed. R94/R95 now use `weight_cone.py`:
bind64 actual selector outputs and both polarities, substitute64 common ports,
then reconnect the exact original selector and require every D/output to be
structurally identical to the original graph. The two abstract bodies are
canonically identical (56,524 NAND including output copies); real full-graph
output faults break that comparison. Actions proves that common body and the
selector separately and still replays the complete actual graph. R94 retains
its selector range premise; R95's selector equality covers every address.
Graphs, protocol vectors, model and counts are unchanged; original timeout
evidence is retained in the main repository.


### R97: reverse variable order for the complete true-weight tables

`weight_order.py` keeps the original13/15-bit address and64-bit trit-word interface,
but reverses the internal Shannon variable order and wires it back to the original
pins. The source layer-0 network is80,187→74,697 NAND (−6.846%); all five layers
323,304→317,113 (−1.915%). Depth stays27/31, LATCH stays0. Comparing instead to
R4's mapped baselines80,154/323,266 gives5,457/6,153 fewer source NAND; the new
mapping results are archived below. No whole-model count is adopted in advance.

Local validation exhausts all256 three-input Boolean functions under all6 input
orders, plus padded/multibit cases:1,592 graphs,14,312 addresses and1,592 actual
gate mutations. Large graphs are constructed only. The exact table and frozen
C/parser hashes equal the accepted R4 artifacts, without rerunning unchanged
local C fixtures. Actions re-reads all8,192/32,768 original and padded addresses
through C, checks original/candidate/mapped NAND bytes at every address, and
requires source-to-mapped CEC and an actual mapped-gate negative control.
The small deterministic order comparison is archived in the main H3 report;
row/group permutations that increased gate count are not sent for cloud proof.

Accepted run37554762243 (8604b44): mapped layer0/all74,697/317,093 NAND;
all8,192/32,768 addresses match C, CEC9.986/107.412s and actual faults pass.


### R98: apply reverse order only to the complete FFN gate/up cone

`ff_weight_order.py` replaces the actual64-bit gate/up constant selector in the
accepted R49 norm/FFN/residual sublayer. Its selector39,327→37,497 NAND saves4.653%;
the complete graph106,461→104,633 saves1,828 NAND (1.717%), with8,719 LATCH,
depth202 and the same26/32-bit interface. Down and QKV remain unchanged: reversing
them increased their source count to21,496 and21,926 respectively.

Every actual selector output and inverse is bound to the full DAG. Reconnecting
its own selector must restore every D/output; the two common bodies are exactly
identical, and a real full-graph output mutation breaks that identity. All4096
selector addresses retain their values, so no new reachability condition is
needed. The older arithmetic-sharing contract is not enlarged.

Local mode uses `--reference-dir` pointing to the accepted R49 artifact and checks
all48 source hashes and the three C fixture hashes; no unchanged C fixture is
rerun locally. Actions reconstructs the baseline, regenerates C/FF/H fixtures,
checks both weight graphs at every C-parsed address, proves selector/common-body
CEC with actual negatives, and replays all2,362,346 NAND/RTL/C clocks, including
all H/FF owner and rotation assertions. Run37555484285 (e089553) passes those
checks: selector CEC3.977s and common-body CEC0.566s, with real faults rejected.
It does not alter the default whole-model budget or add its partial-selector
saving to R97's overlapping global selector projection.

### R99: fused tied E8 scalar constant provider

`embed_scalar.py` preserves row[7:0]/column[6:0] and the complete padded domain.
The original 48,702-NAND row table plus 3,071-NAND column mux becomes 49,725
when connected; reversing each address group reduces that actual graph to
43,915 NAND, zero LATCH, depth31. This is a scalar operand provider, not a
complete vocabulary-head controller. The original C/spec/model are unchanged.

Local construction, 96 small graphs/5,400 addresses/real mutations and the
frozen C parser's complete 32,768-address table pass. The workflow repeats the
C parser, exhausts original/compressed/mapped graphs, maps the candidate and
proves both replacement and mapped CEC with actual output-gate faults.
`python3 integer_opt/embed_scalar.py --cloud` is Actions-only; omit `--cloud`
for bounded local preparation. No full-size gate simulation occurs locally.

R99 proof update: the first replacement CEC timed out at150s after the complete
candidate/mapped truth and mapping CEC passed. The new replacement proof binds
128 disjoint column cofactors to the actual full graphs; each leaves every8-bit
row free, including padding. All128 C comparisons, CECs and actual full-graph
output faults are required. Gate bytes and local noncloud preparation AST stay
unchanged; the timeout is not extended. Four representative local cuts pass.

Accepted run37557355256 (81918c9): all original/candidate/mapped32,768 addresses,
all128 bound column proofs and actual full-graph faults pass. Mapping CEC4.872s;
the128 replacement proofs total4.129s. The mapped graph remains43,915 NAND.

### R101: try the full ABC mapping on the largest true-weight network

`weights_dense.py` takes the accepted R97 source bytes and changes only
`abc -g NAND -fast` to `abc -g NAND`; the existing300s mapping cap stays.
Layer0 and all-layer jobs record the actual Yosys version and mapped count.
Any saving is compared with the accepted74,697/317,093 mapped NAND, not an
unmapped baseline. A completed experiment may show no saving; no result or
whole-model adoption is assumed.

Local preparation only constructs the large graphs and validates32 small
cofactors over8,192 inputs, including high-address wiring and real full-graph
output mutations. The C/model/table digests are bound to R97's accepted evidence;
unchanged local C fixtures are not rerun. Actions parses C, checks every source
and mapped address, then proves32/128 disjoint cofactors with the low8 inputs
free. Every cofactor is mechanically extracted from the actual source, mapped
and full-graph fault netlists, with C truth and positive/negative CEC required.
All proof branches and partial failure receipts are retained in artifacts.

Run37558366454 (cd48d47) passes every C/partition/fault check but increases mapped
NAND to78,359/326,869 and depth to33/39. This mapping candidate is rejected;
the accepted fast results74,697/317,093 remain. Full proof artifacts are retained.

### R102: exact vocabulary scaling through one MUL and one DIV

`vocab_scale.py` implements `R(R(dot*m,127)*g,2^24)` with the original s22/u20/u18
input domain and both round-to-even operations. A20-step multiply,42-step divide
of a signed42-bit numerator shifted22, and18-step multiply use the existing
serial cores. The final power-of-two round uses signed floor plus the exact
guard/sticky/parity increment. No clipping or numerical-spec change is added.

The complete scheduled graph is4,212 NAND/430 LATCH/depth189 with86-cycle
latency. Its reset/start/logit/valid/busy interface captures inputs on idle start,
ignores busy starts, holds the result and aborts on reset. Compared with h3a's
frozen11,843-NAND combinational operator, this trades state and cycles for gates;
it does not reduce a whole-model budget that already assumes shared arithmetic.
The frozen original is in `vocab_units/`, including its provenance and hashes.

Local checks cover all2,048 small controller transitions, signed rounding ties,
the actual small arithmetic cores at their shortened iteration counts, and four
deliberate merged-rounding counterexamples. A new C fixture uses the frozen
model, all192 E8 rows on four deterministic norm10 inputs, input endpoints and
random operands. The98,456-clock full graph replay stays on Actions, which also
checks the old operator against those C cases, proves all430 D and34 output bits
against independently written transition RTL, and rejects actual D/result/RTL
faults, including faults while the result is valid. This is a scaling component,
not a complete vocabulary MAC, final normalization, sampler or whole model.

Run37559189558 (e2d5226) passes all98,456 actual NAND/RTL/C clocks, the complete
430D/34output transition CEC in0.315s, and real D/result/RTL faults, including
3,340 checked valid-result clocks. All original1,113 operator/C cases pass.

### R103: bound vocabulary arithmetic without changing the model

`vocab_narrow.py` uses a54-bit product with20-bit unsigned RHS state and a
42-bit signed constant127 divider. The divider stores only quotient/dividend,
seven remainder bits and sign; no denominator register is needed. From a load,
the remainder is0..126;127 is odd, so its RNE increment is exactly remainder>=64.
The first product magnitude is<2^41, its rounded quotient<2^35, and the second
product<2^53. Both multiplications therefore fit signed54; no model input/output
width, rounding or value changes.

The actual graph falls4,212→3,157 NAND and430→291 LATCH, depth189→114, keeping
the62/34 interface and86-clock schedule. Whole-model budgets remain unchanged.
The complete small graph has3,448 records and runs locally on the frozen1,113 C
cases:97,944 parallel-case clocks, random old state followed by reset, arbitrary
busy input changes,2,226 intermediate product/quotient checks, and1,113 actual
valid-result gate faults. Local mode requires `--reference-dir` for the prepared
or audited R102 C fixtures; it never reruns the unchanged C fixture. Actions
regenerates those fixtures and asserts identical case/vector hashes, proves all
291 D/34 outputs against independent new transition RTL, and replays all98,456
original protocol clocks including aborts. The proof is not an arbitrary-state
projection from the old430-bit machine; exact input-domain bounds and complete
C protocol checks are stated separately.


R103 cloud acceptance:198630f/run37560099049 passes all98,456 actual NAND/RTL/C
clocks,291D/34output independent CEC in0.115s,3,340 valid-result gate faults,
and actual D/RTL negatives. All23 execution sources and prepared data are bound.

### R104: one complete streaming vocabulary row

`vocab_row.py` connects the accepted43,915-NAND scalar E8 table, the actual
823-NAND signed8×signed8→signed22 head MAC, the1,367-NAND escale table,
and R103's exact double-round scaler. The connected graph is49,572 NAND,
351 LATCH, depth145. State order is scaler291,row8,max20,column7,acc22,phase3.
No q buffer, final norm, vocabulary scheduler, sampling unit or whole-model
controller is included; the default whole-model budget stays unchanged.

Input pins are reset,start,row8,max20,input_valid,q8; outputs are logit32,
valid,busy,input_ready,column7,captured_row8, all LSB first. An idle/done start
captures row/max,128 accepted q8 samples accumulate the row, then exact scaling
runs. Busy starts and irrelevant inputs are ignored; input stalls hold the
column; reset aborts all phases. Rows192..255 explicitly return zero. The C
protocol predicts216 clocks without stalls; this is pending actual cloud replay.
The largest real row L1 norm is7,253, so every signed8 partial sum has absolute
value<=928,384<2^20 and the unchanged22-bit accumulator is exact.

Local verification covers the control's entire16,384-input domain,1,099 C MAC
cases and all256 C scale-table rows, with actual output-gate faults. A new C
wrapper exports q/max after norm10, all row partial sums and logits. For four
already frozen norm inputs all192 real row results match the earlier C fixture;
all256 rows including padding are prepared, plus nine q8 boundary/random blocks
on16 rows. No unchanged C fixture is rerun locally; `--reuse-fixtures` binds the
new fixture to its C/model SHA when refreshing preparation.

Actions checks all32,768 actual E8 addresses and all256 scales against C, then
proves the351D/50output body with26 free coefficient/scale bits against separate
transition RTL. Reading back the archived body and tables and reconnecting the
listed ports must reproduce the full NL byte for byte. It replays262,479 actual
NAND/RTL/C clocks,1,169 complete rows,15 phase-boundary aborts,5,616 input stalls,
52,128 ignored busy starts, immediate restarts and3,171 checked valid-result
clocks. Actual body-D, connected weight, final-output and RTL faults must fail.
This proof composition keeps the large constant network out of the body CEC;
it does not weaken the constant domain or claim a full autonomous model.


R104 cloud acceptance:95733a9/run37561187859 passes all262,479 actual NAND/RTL/C
clocks and confirms216-clock no-stall latency. All351D/50output body CEC passed
in149.304s; E8/scale full domains and mapping CEC passed. The real connected
weight mutation caused1,697 checked mismatches, the result fault3,171, and the
actual D/RTL faults were rejected.23 sources/76 archive files are bound.

### R106: bound MAC proof decomposition, identical R104 hardware

The149.304s body CEC is too close to its150s process limit. `vocab_mac_proof.py`
binds all22 existing MAC output wires in the actual416-input/401-output body,
cuts their existing polarities, and reconnects the actual823-NAND MAC. Every
351D/50output must reconstruct identically in one canonical builder; a real
whole-body D mutation must break that identity. No new hardware cut is inserted.
The body with22 additional free MAC outputs has438 inputs/401 outputs and4,273
NAND, and is checked against the original independent transition RTL with only
its sum expression replaced by those22 free bits.

The separate signed8×signed8+signed22 MAC proof covers all38 input bits using
256 disjoint sample cofactors; every22-bit accumulator and every8-bit E value
remain free in each CEC. Both cofactored sources come mechanically from the
actual frozen MAC and its independent mapped RTL. A real complete MAC output
fault is cofactored and rejected in every branch. Local small graphs cover all
256 sample values×64 accumulator/E boundaries (16,384 cases and real faults).

The row NL, transition NL, original body/reference, all C cases and262,479-clock
vectors are byte-identical to accepted R104. Only cloud proof/main bookkeeping
changed; the unchanged local C fixture is reused. Actions still checks every
constant address and full NAND/RTL/C clocks with weight/result/RTL negatives.
The new CEC timings are pending; the150s bound is unchanged.


R106 cloud acceptance:0ccc36b/run37562651548. The common-body CEC is0.265s;
all256 MAC cofactors pass (sum184.423s, slowest1.818s), and every actual MAC
fault is rejected. This improves the maximum individual proof time, not the
sum of all proof work. Original262,479 NAND/RTL/C clocks and actual connected
weight/result/RTL faults still pass.24 source files and2,135 evidence members
are bound to the unchanged row graph and frozen C vectors.

### R107: one complete192-row scanner with an always-rotating q8 cache

`vocab_scan.py` composes the accepted49,572-NAND/351-LATCH row engine with
128×8 q data bits, a7-bit rotation cursor and a38-bit parent controller. The
complete candidate is50,053 NAND/1,420 LATCH/depth145, cell proxy0.216306mm².
This is an expanded interface, so no same-function before/after saving or
whole-model budget deduction is claimed. The isolated ring is103 NAND/1,031
LATCH and rotates on every clock, including scaling, backpressure and idle.
Reset discards logical data validity; all128 q bytes must be loaded again.

Inputs, low to high:reset,start,max20,input_valid,q8,out_ready (32bits).
Outputs:logit32,valid,busy,input_ready,row8,done (44bits). A start accepted in
idle/done captures the maximum. Only ready/valid input transfers count. The
last load launches row0, then rows0..191 run in order; output backpressure
holds the result and row. Busy starts are ignored. The next row waits until
the ring is aligned, so a complete no-stall scan predicts its first valid
result at344 clocks and DONE at49,241 clocks after start. These include input
loading and alignment. Full actual graph confirmation is a cloud obligation.

The shell has1,853 arbitrary inputs:1,420 current-state bits,32 external
inputs, and401 free child D/output bits. Independent RTL proves all1,503
outputs, including the39 actual child-call pins. Reconnecting those pins and
the frozen child must recover every full transition bit exactly. The child
gets the complete R106 MAC partition/common-body proof and full E8/scale C
address checks; none of its38 MAC bits or401 transition outputs is sampled
away. A changed shell D must break composition, and an actual cache LATCH
that stops rotating must fail the complete protocol replay.

Local work checks the3,503-NAND shell on512 arbitrary states/free child
outputs and the actual128×8 ring on256 transitions with a real tail-gate
mutation. All large graphs are construction-only. It reuses the already
frozen C fixture and prepares302,033 clocks:five complete scans (960 C rows),
15 aborts,86 backpressure clocks and75,461 ignored busy starts, plus restart.
Actions must pass all actual NAND/RTL/C clocks,1,241 valid-result faults,
cache-state faults, and independent RTL/CEC negatives. No norm/A8, top40,
full-token controller, new numerical rule or quality change is included.


R107 cloud acceptance:0f322f1/run37563544706 passes all302,033 actual
NAND/RTL/C clocks. The first result at344 and DONE at49,241 are confirmed.
All1,853-input/1,503-output shell CEC passes in0.065s, and the complete child
proof/constant domains pass again. The actual cache LATCH fault causes1,048
checked mismatches, the output fault1,241, and the RTL fault is rejected.
26 sources and2,148 evidence members are bound to the prepared graph/vectors.

### R109: actual stable top40 list,1,142 NAND and1,664 LATCH

`top40.py` accepts192 signed32 logits in vocabulary order, then streams the
best40(score32,id8) pairs in descending score order. Equal scores retain the
earlier token. A40×40-bit list rotates on every clock. A new logit is carried
through one revolution; once insertion starts, the remaining old entries
shift in order. This needs only a strict signed32 comparison, not a token-ID
comparator for every shifted entry. Old data is invalidated by count, not
cleared. The1,600 data bits plus64 control/payload bits are explicit.

Inputs, low to high:reset,start,input_valid,score32,out_ready (36bits).
Outputs:score32,id8,valid,busy,input_ready,done (44bits). Input stalls, ignored
busy starts, output backpressure and reset/reload are covered. A fresh list
with no external stalls takes15,402 clocks; alignment waits are included.
This is sorting only: it does not implement EXP, the random threshold or the
final token selection, and does not alter the default whole-model budget.

Local simulation uses the complete2,806-record NAND/LATCH graph, below the
4,000-record limit.64 arbitrary-state transitions and30,808 clocks for equal
and ascending lists agree, including80 frozen C entries and actual output
gate faults. `top40_cases.json` binds133 existing C cases by the original
R11 receipt SHA and each logit-vector SHA; no unchanged C fixture is rerun
locally. The protocol prepares2,118,956 clocks,134 completed lists/5,360
entries,11 aborts,902 output stalls and302,444 ignored busy starts.

Actions replays all133 lists through the original C refinement and int_pick,
proves every1,664D/44output against separate RTL, and runs all actual NAND/RTL
clocks. Real output gates, cache LATCH rotation, the strict tie comparator,
D logic and RTL are mutated and must fail. The4,470-NAND combinational proof
model is never simulated locally. Full cloud evidence remains pending.


R109 cloud acceptance:df7a19a/run37564851859 passes all2,118,956 actual
NAND/RTL/C clocks. All1,664D/44output CEC passes in0.064s;133 frozen C lists
and all5,320 C order entries are rechecked. The actual cache ID LATCH fault
causes3,137 mismatches, the wrong strict-tie rule1,674, and the output fault
6,262. D, tie-CEC and RTL negatives are rejected.13 sources and41 archive
members are bound to the exact graph, reference and frozen protocol.

### R110: vocabulary scan connected directly to stable top40

`vocab_top40.py` joins the accepted R107 scanner and R109 sorter without new
state:51,110 NAND/3,084 LATCH/depth145. Source cell proxy is0.253586mm². The
scanner sees the sorter's actual input-ready signal; the sorter receives the
scanner's score and valid signals. A shared start is accepted only when both
children are idle/done, so a busy start cannot restart a finished scanner
while its sorter still owns the transaction. All192 handoff IDs are checked.

The32 input bits are reset,start,max20,input_valid,q8,out_ready.44 output bits
are score32,id8,valid,busy,input_ready,done. The sorter works while later rows
are calculated. The protocol predicts first sorted output at49,282 clocks
and DONE at49,322, including loading/alignment; the standalone15,402 sorting
clocks are not added serially to the scanner schedule. This remains a stream
from externally normalized/quantized q8 to top40, not a complete sampler or
model. The default whole-model budget stays fixed.

Local work constructs/binds the actual graphs and checks the256-input common
start/ready controller, with real faults. It prepares548,085 clocks, five
complete lists/200 entries,2,113 checked row handoffs,13 aborts including the
sorting/draining interval,136,424 ignored busy starts and31 output stalls.
No large graph is evaluated locally. Frozen C fixtures are reused unchanged.

Actions freshly proves the6,288-input/3,196-output parent, the scanner shell,
the entire sorter transition, the row MAC partitions/common body and every
E8/scale address. All actual component reconnections must match. Four exact C
logit blocks are sorted again by the original C refinement. Full joint
NAND/RTL/C must pass, while a real ungated-start connection, wrong tie rule,
output gate and RTL mutations must fail. Accepted in run 37565740794 (4e71930):
all548,085 clocks match, first sorted49,282 and DONE49,322 are confirmed.

### R111: exact sampling EXP weight,2,550 NAND (was3,004)

`sample_weight.py` rebuilds `exp_weight(RNE(5*(top-logit),4))` for the top40
sampler without changing the numerical contract. The frozen EXP table is zero
for indices755..1024, so any delta>=65536 is zero and only a16-bit delta feeds
a small index unit. With `n=5*delta`, `q=n>>8`, `r=n&255`, the two RNE steps
equal `q+(q odd ? r>=126 : r>=131)`; this is NOT one RNE(n/256). Signed32
difference, out-of-range masking and the zero extension for illegal top<logit
are kept, identical to the read-only h3a operator
(`sample_weight_units/baseline.nl`,3,004 NAND/depth117).

New graph: 2,550 NAND /0 LATCH /depth84 (-454, -15.11% of this operator);
index unit240 NAND, exhaustively checked over all65,536 deltas. Locally,
200,715 C observations (every low-16 delta at three signed placements, random
full range and large-difference edges) match the actual NAND graph; a
single-round index graph (208 mismatches) and an EXP[127] bit fault (50) are
rejected. Actions regenerates the C fixtures and proves universal 64-input
CEC of the new graph against the h3a baseline, an independent full-width
double-round RTL reference and the Yosys/ABC-mapped graph; output-flip,
single-round and table faults must be different.
Accepted in run 37567890412 (6178f0a): universal CEC equivalent to the h3a
baseline, the wide reference and the mapped graph; all three faults different.

### R112: two-pass exact top40 sampler,3,860 NAND /127 LATCH

`sampler.py` consumes the sorted top40 stream (score32,id8,valid) twice and
returns exactly `int_pick(logits,random,sample)`. Pass 1 keeps the first score
and sums the40 R111 weights into a22-bit total (max40*65536). A32-clock
shift-add multiplies the latched random word by total in place (random shifts
out of its own register, product high bits in a22-bit register), leaving
`at=(random*total)>>32`. It then raises `replay` so the source presents the same
list again; pass 2 subtracts weights until `at<w` and records that id. Greedy
mode records the first id and finishes after one pass. One22-bit adder serves
all three uses. State: phase3/count6/top32/total22/high22/random32/token8/found/mode.

Local: the actual graph (3,987 records) runs1,650 C picks (all133 frozen top40
lists, every cumulative-weight boundary r and r-1, 0/all-ones, random words,
plus greedy) lane-parallel, with random valid gaps and replay delays;
every token equals C `int_pick` and `stream_pick`. A real borrow fault changes
1,268 picks. No-stall latency:43 clocks greedy,117-121 sampling.
Actions proves the127-state/76-input transition against an independent RTL
reference (wide double-RNE weight), replays all200,908 sequential clocks in
NAND and RTL, and rejects output-flip, borrow and RTL faults. Integration needs
the R109 sorter to replay its list once; RNG remains an external input.
The first run 37568516051 (5d6ebab) stopped before any proof: Yosys inferred a
ROM (`$memrd_v2`) from the reference EXP case table. The reference script now
runs `memory_map` (as R111 did); the sampler graph is unchanged.

### R113: logits to token,4,942 NAND /1,791 LATCH

`pick40.py` joins the accepted R109 sorter transition (imported byte-for-byte,
SHA bound in `pick40_units/manifest.json`) and the R112 sampler. A56-NAND shell
(exhaustive over all2^17 inputs) lets the sampler request a replay: only when
the sorter is DONE, not reset and no start, its next phase becomes read and its
index0; the ring kept rotating, so the same40 entries are presented again.
A common start is accepted only when both cores are idle/done. Separately the
two cores count5,002 NAND; joined4,942 NAND, no extra LATCH.

Inputs reset,start,sample,random32,input_valid,score32; outputs token8,
input_ready,busy,done. No-stall latency from start: greedy15,403, sampling
15,483 clocks (the sampler adds80 clocks after the sort). Local behavioural
replay prepares2,133,921 clocks: all133 frozen logit blocks (cumulative-boundary,
random and all-ones words, every fourth greedy), stalls, 237,097 ignored busy
starts, 5 aborts, 103 replays; every token equals C `int_pick`. Actions
proves the 1,858-input/1,802-output joint transition against a composed
independent reference, replays all clocks in NAND and RTL, and must reject
output-flip, missing index clear, ungated common start and borrow faults.
The first R113 run 37569137156 failed its joint CEC because the composed
reference fed the sampler from the sorter's next-state bits instead of its
output field (so[1704]/so[1703:1664]); the NAND graph was correct and unchanged.

### R114: q8 to token output head,54,912 NAND /3,211 LATCH

`vocab_pick.py` reconnects the accepted R107 vocabulary scanner and R113
pick40 through an R110-style parent shell (free child-output inputs, proved
alone). Inputs reset,start,max20,input_valid,q8,sample,random32; outputs
token8,busy,input_ready,done. A start needs the scanner, sorter and sampler all
idle/done. No extra state; the R110 scanner+top40 was51,110 N, the sampler and
replay add3,802 N /127 L. No-stall: greedy49,323, sampling49,403 clocks after
start, i.e. the token is ready81 clocks after the R110 top40 list.

Local work checks the11-input control exhaustively and prepares645,248
behavioural clocks over the four frozen R110 C E8/scale logit blocks (boundary
and random words, sample and greedy, stalls,161,299 ignored busy starts, aborts
inside scan/sort/multiply/pass2); every token equals C `int_pick`, every one of
2,496 row handoffs equals the C logit. Actions proves the parent shell, replays
all clocks in NAND and RTL, and must reject output, ungated-start, borrow and
missing-replay-index faults.

### R115: final norm[10] + A8 front end,8,310 NAND /682 LATCH

`final_a8.py` turns the final residual x (20-bit stream) into the q8 bytes and
maximum m that the R114 head consumes: exactly C `quant(norm(x,10))`. It reuses
the accepted R39 norm_stream constructor with only the coefficient table
replaced by norm[10] (all 128 words checked against C), imports the accepted R20
quant_stream unchanged, and adds an11-input restart shell (exhaustive) so norm
re-enters its normalize phase with the kept root. The caller replays X three
times; no h vector is stored. Glue is an R110-style parent shell proved alone.

Local graphs exceed the4,000-record limit, so Actions drives the actual graph
reactively with `nl_sim` against32 C cases (edge, random and16 real final-x
rows from `int_run` traces), checks every byte and m, then replays the recorded
clocks in RTL. Faults: output flip, ungated start, restart into the square
phase, and the norm[1] table in place of norm[10].
Accepted in run 37570428378 (32dabb1): 34 complete actual-graph transactions,
every byte and m equal to C; 639,663 RTL clocks; all four faults detected.

### R116: final residual x to token,63,166 NAND /3,893 LATCH

`x_head.py` reconnects the accepted R115 front end and R114 output head through
a parent shell proved alone. The head is started exactly when the front end's
m is final (norm idle, quant waiting for its replay, not yet restarted), which
is the same condition that restarts norm, so no state is added. Inputs reset,
start,x20,xvalid,sample,random32 (held for the transaction); outputs token8,
busy,done,xready,xindex7,mvalid. Actions drives the actual graph with30 C cases
built from `int_run`: the final-x row of a real prompt position, and the token
C `int_pick` returns on int_run's own logits for that position (sampled at a
cumulative boundary and at random, and greedy). RTL replays every clock; an
early head start (before m is final) and an output flip must fail.
First R116 run 37570682841 (fb69d12): case 0 matched C, case 1 (same x, new
random) returned the previous token because the head's DONE stays high until
the head restarts, which happens only when the front end publishes m. The
public `done` is now the head's done AND the front end idle (+2 NAND).

### R117: one shared divider in the final front end,7,453 NAND /567 LATCH

`final_a8s.py` restates the norm_stream and quant_stream constructors as
generators that pause at their DIV import. Run alone, they rebuild the accepted
norm[1] (R39), the R115 norm[10] and quant_stream (R20) graphs byte for byte.
Shared, a single DIV (state in norm's slot) serves both: quant loads only while
norm presents a finished result, and norm's phase 7 waits while quant's
27-step divide runs. R115 8,310 N /682 L -> 7,453 N /567 L (-857 N, -10.3%;
-115 L). Actions drives the actual graph with the same32 C cases and must
reject an output flip, a norm load that does not wait, and a DIV operand mux
that ignores quant.
Accepted in run 37571159444 (44aa41d): 31 actual-graph transactions, every token
equal to C `int_pick` on int_run's own logits; 2,461,082 RTL clocks.

### R118: x to token with the shared-divider front end,62,311 NAND /3,778 LATCH

`x_head_s.py` is R116 with the accepted R117 front end (its quant phase and
restarted bit sit at new state offsets; the parent shell is otherwise
identical) and the same int_run C cases. R116 63,168 N /3,893 L ->
62,311 N /3,778 L.
