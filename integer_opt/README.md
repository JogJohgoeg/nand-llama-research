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
