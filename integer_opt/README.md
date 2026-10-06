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
The source files do not imply those pending cloud checks have passed.

Local construction and small checks:

```sh
python3 integer_opt/ports.py
python3 integer_opt/weights.py --scope small
python3 integer_opt/weights.py --scope layer0
python3 integer_opt/weights.py --scope all
```

`--cloud` is guarded by `GITHUB_ACTIONS=true`. No training, numerical-contract
change, local EDA or local gate simulation above 4,000 NAND is part of this work.
