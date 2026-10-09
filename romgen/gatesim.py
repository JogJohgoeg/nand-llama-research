#!/usr/bin/env python3
"""gatesim.py <bits.txt> <array cell> <outdir> [cycles] [seed]: gate-level test of rom_bank_test.

Writes a behavioural model of the array (precharge: every bitline 1; a raised wordline pulls bitline r
to 0 iff bit(r,w)=1; otherwise the bitline keeps its charge) and a testbench that drives random addr /
sel / chain_in on the falling edge and checks dout two rising edges later against
(sel ? word[addr] : 0) | chain_in. Run with the post-route netlist and the sky130 cell models
(iverilog -DFUNCTIONAL -DUNIT_DELAY=#1), so precharge / wordline / latch ordering is exercised.
A negative run (-DNEG) flips one stored bit in the model and must fail.
"""
import random,sys
from pathlib import Path
bits,arr,out=sys.argv[1:4];N=int(sys.argv[4]) if len(sys.argv)>4 else 2000;seed=int(sys.argv[5]) if len(sys.argv)>5 else 1
rows=[l.strip() for l in open(bits) if l.strip()];C=len(rows);R=len(rows[0])
words=[int(r[::-1],2) for r in rows]          # char r = bit r
d=Path(out);d.mkdir(parents=True,exist_ok=True)
mem='\n'.join(f"        mem[{w}] = 64'h{v:016x};" for w,v in enumerate(words))
(d/'array_model.v').write_text(f'''module {arr} (
`ifdef USE_POWER_PINS
    inout VPWR, inout VGND,
`endif
    input [{C-1}:0] WL, input PRE_N, output [{R-1}:0] BL);
    reg [{R-1}:0] mem [0:{C-1}];
    reg [{R-1}:0] q;
    integer w;
    initial begin
{mem}
`ifdef NEG
        mem[137] = mem[137] ^ 64'h0000000000000400;
`endif
        q = {{{R}{{1'b1}}}};
    end
    always @(PRE_N or WL) begin
        if (!PRE_N) begin
            if (WL != 0) $display("ERROR: wordline high during precharge at %0t", $time);
            q = {{{R}{{1'b1}}}};
        end else
            for (w = 0; w < {C}; w = w + 1) if (WL[w]) q = q & ~mem[w];
    end
    assign BL = q;
endmodule
''')
rnd=random.Random(seed);vec=[]
for _ in range(N):vec.append((rnd.randrange(C),int(rnd.random()<0.8),rnd.getrandbits(64) if rnd.random()<0.3 else 0))
(d/'vec.txt').write_text(''.join(f'{a:03x} {s} {c:016x}\n' for a,s,c in vec))
(d/'tb.v').write_text(f'''`timescale 1ns/1ps
module tb;
    reg clk = 0; reg rst = 1; reg [8:0] addr = 0; reg sel = 0; reg [63:0] chain_in = 0; wire [63:0] dout;
    rom_bank_test dut (.clk(clk), .rst(rst), .addr(addr), .sel(sel), .chain_in(chain_in), .dout(dout));
    reg [63:0] mem [0:{C-1}];
    reg [8:0] va [0:{N-1}]; reg vs [0:{N-1}]; reg [63:0] vc [0:{N-1}];
    reg [63:0] want [0:{N+3}];
    integer i, bad, fd, r, a, s; reg [63:0] c;
    always #100 clk = ~clk;
    initial begin
{mem}
        fd = $fopen("vec.txt", "r");
        for (i = 0; i < {N}; i = i + 1) begin r = $fscanf(fd, "%h %d %h\\n", a, s, c); va[i] = a; vs[i] = s; vc[i] = c; end
        bad = 0;
        repeat (3) @(negedge clk); rst = 0;
        for (i = 0; i < {N}; i = i + 1) begin
            @(negedge clk); addr = va[i]; sel = vs[i];
            want[i] = (vs[i] ? mem[va[i]] : 64'd0) | vc[i];
            // dout <= q | chain_in at the rising edge after the read cycle: chain_in for word i is set one cycle later
            if (i > 0) chain_in = vc[i-1];
            if (i >= 2 && dout !== want[i-2]) begin
                bad = bad + 1;
                if (bad < 5) $display("MISMATCH cycle %0d addr %h sel %0d got %h want %h", i-2, va[i-2], vs[i-2], dout, want[i-2]);
            end
        end
        $display("CHECKED %0d cycles, %0d mismatches", {N}-2, bad);
        $finish;
    end
endmodule
''')
print('wrote',d)
