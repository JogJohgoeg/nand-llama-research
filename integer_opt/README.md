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

Local construction and small checks:

```sh
python3 integer_opt/ports.py
python3 integer_opt/weights.py --scope small
python3 integer_opt/weights.py --scope layer0
python3 integer_opt/weights.py --scope all
python3 integer_opt/ring_model.py
python3 integer_opt/workspace_model.py
python3 integer_opt/prefix_model.py
```

`--cloud` is guarded by `GITHUB_ACTIONS=true`. No training, numerical-contract
change, local EDA or local gate simulation above 4,000 NAND is part of this work.
