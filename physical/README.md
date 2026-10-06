# INT-C16 physical pilot

This macro contains the **true layer-0 weights** (all 194,560 trits), their
32-lane constant NAND selector, DOT32, one C16 attention head's K/V bank
(32 rows × 276 bits = 8,832 LATCH bits), and the exact serial divide, multiply,
square root, saturated residual and integer exponential primitives. No weight
ROM/SRAM is used. The model blob is the published MIT tiny-tiny-stories
integer export; see `../docs/LICENSE.txt`. New glue is CC0.

It is a representative micro-operation slice with external scheduling. It is
not yet a complete layer, autonomous inference controller, or whole-model GDS.
The bank covers both K and V for one of four heads; a layer needs four such
banks. The full model also needs X-prefix/workspace, embedding/head selection,
all five weight sets, other arithmetic routing and a controller. This boundary
must be retained in viewer labels and area extrapolations.

`export.py` mechanically imports canonical TapeOut NAND/LATCH bytecode and
builds the state ports/constant network. The approved operand rules are in
`../docs/INT_SPEC.md`. `units/manifest.json` pins the independently checked
claude-h3a primitives and the chain-equivalent DOT32_T. Each logical LATCH is
one positive-edge state bit in the Verilog translation. SKY130 mapping may use
equivalent standard cells; source NAND counts and physical cell counts differ.

`din[12:0]` is the weight address and its low five bits are the KV address;
`din[288:13]` is 276 data bits (32 signed8 codes followed by unsigned20 scale).
`din[289]` enables a KV write on the next edge. `din[290]` loads all three serial
arithmetic units. `din[293:291]` selects the zero-extended 276-bit output:

| View | Result | Operand / latency |
|---:|---|---|
| 0 | signed32 DOT32 | lower 256 data bits × selected 64-bit trit word |
| 1 | 64-bit trit word | 6,144 words in q,k,v,o,gate,up,down order; down's last block is zero padded; addresses 6,144..8,191 return zero |
| 2 | KV row | read-before-write; rows 0..15 K, 16..31 V; initialize each row before observing |
| 3 | signed64 RNE result then saturated20 | numerator=data[63:0], positive denominator=data[88:64]; sample after load plus 64 step edges |
| 4 | low64 product | operands=data[63:0] and data[127:64]; load plus 64 step edges |
| 5 | unsigned24 integer root | data[47:0]; load plus 24 step edges |
| 6 | saturated20 residual | signed20 operands=data[19:0], data[39:20] |
| 7 | unsigned17 exponential | maximum=data[31:0], score=data[63:32], signed32 and maximum≥score |

All serial units step on every non-load edge, independently of view. Their
result is sampled at the documented iteration; this test macro has no hidden
host memory and no autonomous microcode. Clock target is a provisional 200 ns.

On **GitHub Actions only**, the `Integer layout` workflow:

1. Constructs the complete NAND/LATCH netlist and decodes its real bytes in
   an independent C gate interpreter. Checks every weight address, DOT vectors,
   KV read/write sequences and arithmetic edges against the model's C99 golden.
2. Simulates the mechanically generated structural Verilog on the same vectors.
   A real NAND output-inverter mutation must fail both checks.
3. Uses LibreLane 3.0.14's pinned container/PDK (SKY130A, sky130_fd_sc_hd), four
   workers, a five-hour flow cap, Magic and KLayout DRC, XOR and Netgen LVS.
   Retains resolved tool/PDK configuration, Docker digest, each tool's peak RSS
   and runtime, and sampled runner memory. Job limit is 350 minutes.
4. Repeats the vectors on the post-route standard-cell netlist, then uses
   KLayout 0.30.4.post1 to convert actual GDS into OAS and reopens both layouts.

The `gds-site` artifact contains GDS, OAS, metrics/hashes and a viewer landing
page. After inspection it is copied to `docs/gds/` and included in the next
ordinary Pages deployment, preserving the existing integer demo. No fake
geometries or estimator-based 3D model are generated. `integer-layout-logs`
retains unsuccessful partial runs as well. A green source test is required
before spending runner time on routing. No paid runner or tapeout is enabled.

Flow references: [LibreLane Docker installation](https://librelane.readthedocs.io/en/stable/installation/docker_installation/installation_linux.html),
[3.0.14 source](https://github.com/librelane/librelane/tree/3.0.14),
[Tiny Tapeout viewer action](https://github.com/TinyTapeout/tt-gds-action/blob/ttsky26d/viewer/action.yml).
