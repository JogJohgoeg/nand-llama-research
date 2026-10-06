# INT-C16 integer contract v1 / 整数模型契约

SPDX-License-Identifier: CC0-1.0

This specification and the new integer implementation are dedicated to CC0.
The original tiny-tiny-stories weights/tokenizer remain MIT; their notices travel
with the demo. This is an inference conversion, not additional training.

## Representation and exact arithmetic

The architecture is L=5, D=128, F=336, H=4, head dimension=32, V=192,
tied E8 embedding/head. Context C is 16 or 32 **tokens**, not bytes.
Internal weights are the original 972,800 ternary codes. Qf means the signed
integer represents its value divided by 2^f. A field's signed width includes
the sign bit. All sums accumulate in increasing input-index order.

`R(n,d)` for positive integer d is signed round-to-nearest, ties-to-even:
divide |n| into quotient q and remainder r; increment q iff 2r>d or
(2r=d and q odd), then restore n's sign. No arithmetic relies on a signed
right shift, signed overflow, host floating point, or implementation-defined
negative division. `S(n)` clamps to [-524288,524287], signed 20 bits.
Transient multiply/accumulate values use signed 64 bits; persistent activation
vectors use signed 20-bit Q12. Host storage may use int32 without changing
the logical bit width. Constants are exported once from the pinned checkpoint;
integer inference accepts only the hashed exported artifact.

| Operator | Exact integer rule |
|---|---|
| Embedding | E8 row code e in [-127,127], unsigned Q24 row scale g (18 bits); X=S(R(e*g,4096)). |
| A8 | m=max(1,max abs(X)); q=R(127*X,m), signed 8 bits [-127,127]. Retain m as unsigned 20 bits. |
| BitLinear | signed int32 dot a=sum(q[i]*ternary[i]); output S(R(a*m*alpha,127*262144)); alpha is unsigned Q18, 18 bits. |
| RMSNorm | u=2*sum(X[i]^2)+4295 (unsigned 47 bits); r=floor(sqrt(u)), computed by the restoring binary integer square-root algorithm (24 fixed iterations). Learned norm w is signed Q14, 16 bits; output S(R(4*X[i]*w[i],max(1,r))). 4295 is the fixed epsilon term in this integer contract. No reciprocal-square-root LUT or Newton step is used. |
| RoPE | Fixed signed Q15 cos/sin, 17 bits, positions 0..31, 16 split-half pairs per head. (a,b) becomes (S(R(a*c-b*s,32768)),S(R(b*c+a*s,32768))). |
| KV8 | Per head and position, use the A8 rule on K **after** RoPE and on V. Retain two arrays of signed8 codes plus unsigned20 maxima. Dequantize each value as S(R(q*m,127)) when read. |
| Attention score | t=sum(Q[i]*Kdequant[i]) in signed64; score=R(R(t,4096)*46341,262144), signed32 Q12. 46341 is the fixed Q18 inverse-root-32 constant. Only positions <= query position participate. |
| Exponential | Subtract the maximum score. For nonnegative delta, j=R(delta,64). If j>1024 return 0; else use the immutable 1025-entry unsigned17 table EXP[j]. Its offline definition is RNE(65536*exp(-j/64)). There is no runtime exp. |
| Attention reduction | b=sum(EXP[j]); a=sum(EXP[j]*Vdequant[i]); output S(R(a,b)). b>=65536. Division normalizes the weighted sum directly, avoiding an extra probability-rounding stage. |
| SiLU | j=min(1024,R(abs(gate),64)); s=SIGMOID[j] for gate>=0, else 65536-SIGMOID[j]. Table entry is offline RNE(65536/(1+exp(-j/64))), unsigned17. silu=S(R(gate*s,65536)); ff=S(R(silu*up,4096)). |
| Residual | S(old+branch), immediately after attention projection and after FFN down projection. |
| Tied head | RMSNorm then A8. Signed32 dot a=sum(q[i]*E8[row,i]); b=R(a*m,127); logit=R(b*g[row],16777216), signed32 Q12. No activation saturation on the final logit. |

Each addition/product has exact signed64 intermediates. With the specified
field limits, all *used* intermediate values also remain exact integers below
2^53 in magnitude, except RMS sum bounds are handled in unsigned arithmetic
with the same <2^53 guarantee. The browser implementation corrects quotient
and remainder before applying RNE, so binary64 storage introduces no inference
rounding. No transcendental function is called by any inference engine.

## State schedule / 逐层前缀重算

For every invocation, embed the last C input tokens with local positions
0..T-1, T<=C. For each layer, construct that layer's **entire old-prefix K/V**
first. Then process each query in increasing position, recomputing its n1,
performing attention, residual, n2, paired gate/up, down and residual, and
overwriting only its own X row. A single K/V layer and one X prefix survive.
Gate/up rows can be paired to keep one F-vector. Cache states are discarded
between invocations; this matches the sliding-window evaluation convention.
Return logits for all rows for scoring; a generator only needs the last row.
There are no cross-layer KV caches or hidden host-side FP states.
The C implementation reuses two D-vectors: n1 writes H, Q writes A,
attention writes H, and O writes A. Q is dead before O overwrites A.
FFN gate/up are evaluated as paired scalar rows into one F-vector.

## Selection / 输出选择

Greedy argmax breaks ties toward the lowest token ID. Sampled generation has
fixed temperature 4/5, exact top-40 selection (logit descending, ID ascending),
and exponential weights obtained by `R(5*(max-logit),4)` followed by EXP.
Randomness is an **external unsigned32 input**. Select cumulative weight
containing `floor(random*total/2^32)`; this product is evaluated as two 16-bit
limbs in JavaScript and uint64 in C. The reproducibility harness supplies
xorshift32, with shifts 13,17,5 and a nonzero seed. It is not an on-chip RNG
claim. EOS=2 stops generation; PAD=0/BOS=1/UNK=3 retain the original model's
meaning. Prompt tokenization is a front-end operation, outside inference.

## Reproducibility and scope / 验证边界

Python integer, C99 integer, and browser engines must produce identical signed
Q12 logits, not merely matching argmax. Tests include real prefixes, random
tokens, operator bounds, actual table-entry mutation and altered tie handling.
PPL/BPB scoring converts output integers to real logits **after inference**;
log/exp in the statistical scorer are not model operators. Offline extraction
and generation of immutable integer constants may read FP32 weights and use
high-precision transcendental functions. That conversion is not performed on
the chip or in the browser model. Evidence records exporter and blob hashes.

This contract is a new deterministic approximation of A2's FP32-dequantized
`a8_e8_kv8`; equivalence to the latter is not asserted. C16/C32 quality must be
measured on all the same 512 stories. A source gate estimate is not a placed,
routed, timed or formally verified whole-chip implementation.

## References reviewed / 用户补充参考的采用边界

Reviewed after the first v1 implementation, without fetching code or weights:
[I-LLM, arXiv:2405.17849v1, §§3.3–3.4](https://arxiv.org/html/2405.17849v1)
informs the per-token dynamic integer scaling, max-subtracted/clipped softmax,
bitwise integer norm, and SiLU-as-sigmoid-times-input choices. Our fixed LUTs
are **not DI-Exp's shift/interpolation algorithm**; our restoring square root
is not a reproduction of DI-Norm. We retain the tested v1 rules. No FSBR
reconstruction, learned smoothing, or new training is performed.

[nmicic's int-llm article](https://huggingface.co/blog/nmicic/int-llm)
informs the explicit integer-core / offline-conversion boundary and raw-integer
golden hashes. We do not adopt its Q16.48 storage or 128-bit intermediates.
Its reported token agreement does not replace our own tests.

以上参考用于复核动态逐 token 缩放、整数非线性和确定性验证的设计；没有下载其代码或权重。
本版用窄状态、固定表和逐位开方，数值规则与初版质量回执不变。论文中的训练式重构未采用。

## Source NAND cost of the approximations / 近似算子的门数口径

These are source-construction counts, not Yosys-mapped area. Values are shared
across all layers; do not add the shared scalar core again for every operator.

| Function | NAND budget / evidence |
|---|---|
| A8, scale products, RNE division, integer RMS root | One 706-NAND 64-bit carry adder; 6,000 additional mux/sign/compare/shift NAND allowance, and 1,024 arithmetic LATCH bits. MUL/DIV/root microcode is still a budget, not a synthesized complete unit. |
| BitLinear | Existing 3,829-NAND DOT32_T; partial-sum and scale steps reuse the scalar core. |
| Softmax + sigmoid/SiLU tables | Joint immutable table: 3,339 NAND, 4,096 addresses decoded and simulated with an actual-output mutation check. Multiply/normalize reuse the scalar core. |
| Norm/alpha/E8 scale constants | Joint 18-bit table: 6,102 NAND; construction only. |
| C32 RoPE constants (also used by C16) | 4,617 NAND; construction only. |
| Saturating 20-bit residual add | 265 NAND; boundary/random vectors and actual-output mutation pass. |
| Signed RNE divide-by-4096 primitive | 181 NAND; boundary/random vectors and actual-output mutation pass. The latency budget conservatively still charges general division. |
| E8 head | Reuse the scalar integer core; the existing 3,071-NAND column selector and 48,702-NAND E8 constant selector are charged separately. |

The selected serial schedule conservatively charges 128 cycles per 64-bit
multiply, 136 per signed RNE divide, and 96 per integer root. DOT32_T costs
eight scheduled clocks including reads/accumulation. Constant shifts are also
charged as general division; optimizing those exact operations can reduce
latency without altering the numerical contract. No clock frequency is
validated by this specification.
