#!/usr/bin/env python3
"""machine_rom.py <banks dir> <outdir> [R C] [pb|seg] [dlxtn|dlxbn]: whole-machine top with the shared weight table in mask ROM.

The R136 machine body (int_c16_body: din = {word[63:0], pins[43:0]}, dout = {addr[14:0], out[14:0]})
reads the shared table through 60 ROM banks instead of the R137 logic macros:
  bank NN (row-major NN = r*C + c) holds words NN*512 .. NN*512+511; sel = (addr[14:9] == NN);
  each row of C banks is one chain (dout = latched word | chain_in, chain_in of column 0 = 0) and the
  R row outputs are OR-ed into the word.
Per bank: array macro u_rom_NN_arr (cell rom_bank_NN, romgen.py) + read periphery u_rom_NN (rom_ctl):
address and sel sampled on the falling edge, two-flop phase signal P (1 in the low phase), wordline
window P & P+10 ns, precharge ~P & ~(P+5 ns), output latches transparent while clk is low, so the word
for the address the body presents in a cycle is valid before the next rising edge -- the same cycle
timing as the combinational table of the netlist (see periph.py). rst of the phase flops is tied low:
in silicon the pair falls into step after one cycle.
Files:
  rom_ctl.v        periphery (sky130 delay gates and latches instantiated, rest behavioural RTL); with
                   decode 'seg' it also holds rom_seg_half (keep this hierarchy through synthesis)
  top_rom.v        module int_c16_machine (input clk, input [43:0] din, output [14:0] dout)
  rom_bank_bb.v    black boxes of the 60 array macros (ports as their LEF)
  sim/arrays.v     behavioural models of the 60 arrays (from the bank files) -- simulation only
  sim/cells.v      behavioural stand-ins for the three sky130 cells rom_ctl uses -- Verilator only
  map.json         NN -> row, column, word range
"""
import json,sys
from pathlib import Path
bd,out=Path(sys.argv[1]),Path(sys.argv[2]);R,C=(int(sys.argv[3]),int(sys.argv[4])) if len(sys.argv)>4 else (3,20)
DECODE=sys.argv[5] if len(sys.argv)>5 else 'seg'   # seg: per-segment local wordline decode (v8b); pb: central pa x pb (v8)
NB=60;assert R*C==NB
out.mkdir(parents=True,exist_ok=True);(out/'sim').mkdir(exist_ok=True)
DECODE=globals().get('DECODE','seg')
SEG_V='''// half-segment wordline decoder: wordlines I*16 + j (j = PAR, PAR+2, ..., 14+PAR) of one ROM bank, on
// the array side that pins them (PAR 0: even, right edge; PAR 1: odd, left edge). Local decode keeps the
// vertical bundle along the strip at a_hi[4:0], a_mid[1:0], lo[3:0] and win (12 lines) instead of 16
// full-height pb lines plus a fan of 32 pa lines.
(* keep_hierarchy *)
module rom_seg_half #(parameter I = 0, parameter PAR = 0) (input win, input [4:0] a_hi, input [1:0] a_mid,
                      input [3:0] lo, output [7:0] wl);
    wire en = win & (a_hi == I);
    wire [3:0] q;
    genvar k, m;
    generate
        for (k = 0; k < 4; k = k + 1) begin : g_q
            assign q[k] = en & (a_mid == k);
        end
        for (m = 0; m < 8; m = m + 1) begin : g_wl
            // wordline j = 2m + PAR: q[j >> 2] & lo[j & 3]
            assign wl[m] = q[(2*m+PAR) >> 2] & lo[(2*m+PAR) & 3];
        end
    endgenerate
endmodule
'''
CTL_HEAD='''// read periphery of one 64 x 512 ROM bank (see machine_rom.py / periph.py)
module rom_ctl (input clk, input rst, input [8:0] addr, input sel, input [63:0] bl, input [63:0] chain_in,
                output [511:0] wl, output pre_n, output [63:0] dout);
    reg ph_a, ph_b;
    always @(negedge clk) ph_a <= rst ? 1'b0 : ~ph_a;
    always @(posedge clk) ph_b <= rst ? 1'b0 : ph_a;
    wire ph = ph_a ^ ph_b;
    wire [16:0] dp;
    assign dp[0] = ph;
    genvar i, j, k;
    generate
        for (i = 0; i < 16; i = i + 1) begin : g_dly
            sky130_fd_sc_hd__dlygate4sd3_1 u_dly (.A(dp[i]), .X(dp[i+1]));
        end
    endgenerate
    reg [8:0] a_l; reg s_l;
    always @(negedge clk) begin a_l <= addr; s_l <= sel; end
    assign pre_n = ph | dp[8];
    wire win = s_l & ph & dp[16];
'''
LATCH=sys.argv[6] if len(sys.argv)>6 else 'dlxtn'   # dlxbn: latch with Q_N takes BL directly (no BL inverter)
LAT_CELL=("            sky130_fd_sc_hd__dlxbn_1 u_lat (.D(bl[k]), .GATE_N(clk), .Q(), .Q_N(q[k]));" if LATCH=='dlxbn'
          else "            sky130_fd_sc_hd__dlxtn_1 u_lat (.D(~bl[k]), .GATE_N(clk), .Q(q[k]));")
CTL_TAIL='''    wire [63:0] q;
    generate
        for (k = 0; k < 64; k = k + 1) begin : g_lat
'''+LAT_CELL+'''
        end
    endgenerate
    assign dout = q | chain_in;
endmodule
'''
DEC_PB='''    wire [31:0] pa; wire [15:0] pb;
    generate
        for (i = 0; i < 32; i = i + 1) begin : g_pa
            assign pa[i] = win & (a_l[8:4] == i);
        end
        for (j = 0; j < 16; j = j + 1) begin : g_pb
            assign pb[j] = (a_l[3:0] == j);
        end
        for (i = 0; i < 32; i = i + 1) begin : g_wl
            for (j = 0; j < 16; j = j + 1) begin : g_wl2
                assign wl[i*16+j] = pa[i] & pb[j];
            end
        end
    endgenerate
'''
DEC_SEG='''    wire [3:0] lo;
    generate
        for (j = 0; j < 4; j = j + 1) begin : g_lo
            assign lo[j] = (a_l[1:0] == j);
        end
        for (i = 0; i < 32; i = i + 1) begin : g_seg
            wire [7:0] we, wo;
            rom_seg_half #(.I(i), .PAR(0)) u_even (.win(win), .a_hi(a_l[8:4]), .a_mid(a_l[3:2]), .lo(lo), .wl(we));
            rom_seg_half #(.I(i), .PAR(1)) u_odd (.win(win), .a_hi(a_l[8:4]), .a_mid(a_l[3:2]), .lo(lo), .wl(wo));
            for (k = 0; k < 8; k = k + 1) begin : g_pin
                assign wl[i*16 + 2*k] = we[k];
                assign wl[i*16 + 2*k + 1] = wo[k];
            end
        end
    endgenerate
'''
(out/'rom_ctl.v').write_text((SEG_V if DECODE=='seg' else '')+CTL_HEAD+(DEC_SEG if DECODE=='seg' else DEC_PB)+CTL_TAIL)

bb=[];top=['// int_c16_machine with the shared weight table in 60 mask-ROM banks (%d rows x %d columns, row-major NN = r*%d + c)'%(R,C,C),
 'module int_c16_machine (input clk, input [43:0] din, output [14:0] dout);',
 '    wire [63:0] word; wire [14:0] addr; wire [29:0] bout;',
 '    int_c16_body u_body (.clk(clk), .din({word, din}), .dout(bout));',
 '    assign dout = bout[14:0]; assign addr = bout[29:15];']
rows=[];mp={}
for r in range(R):
    prev="64'd0"
    for c in range(C):
        n=r*C+c;mp['%02d'%n]=dict(row=r,col=c,words=[n*512,n*512+511])
        top+=['    wire [63:0] bl_%02d, ch_%02d; wire [511:0] wl_%02d; wire pre_n_%02d;'%(n,n,n,n),
              '    rom_bank_%02d u_rom_%02d_arr (.WL(wl_%02d), .PRE_N(pre_n_%02d), .BL(bl_%02d));'%(n,n,n,n,n),
              "    rom_ctl u_rom_%02d (.clk(clk), .rst(1'b0), .addr(addr[8:0]), .sel(addr[14:9] == 6'd%d), .bl(bl_%02d), .chain_in(%s), .wl(wl_%02d), .pre_n(pre_n_%02d), .dout(ch_%02d));"%(n,n,n,prev,n,n,n)]
        prev='ch_%02d'%n
    rows.append(prev)
top+=['    assign word = %s;'%' | '.join(rows),'endmodule']
(out/'top_rom.v').write_text('\n'.join(top)+'\n')
for n in range(NB):
    bb.append('''(* blackbox *)
module rom_bank_%02d (
`ifdef USE_POWER_PINS
    inout VPWR, inout VGND,
`endif
    input [511:0] WL, input PRE_N, output [63:0] BL);
endmodule'''%n)
(out/'rom_bank_bb.v').write_text('\n'.join(bb)+'\n')
# simulation models: precharge -> all ones; otherwise every raised wordline pulls its 1-bits low
sim=[]
for n in range(NB):
    words=[l.strip() for l in open(bd/('bank_%02d.txt'%n)) if l.strip()];assert len(words)==512
    init='\n'.join("        mem[%d] = 64'h%016x;"%(w,int(s[::-1],2)) for w,s in enumerate(words))
    sim.append(f'''module rom_bank_{n:02d} (input [511:0] WL, input PRE_N, output reg [63:0] BL);
    reg [63:0] mem [0:511];
    integer w;
    initial begin
{init}
    end
    always @* begin
        BL = {{64{{1'b1}}}};
        if (PRE_N) for (w = 0; w < 512; w = w + 1) if (WL[w]) BL = BL & ~mem[w];
    end
endmodule''')
(out/'sim'/'arrays.v').write_text('\n'.join(sim)+'\n')
(out/'sim'/'cells.v').write_text('''// behavioural stand-ins (zero delay) for the sky130 cells rom_ctl instantiates -- Verilator only
module sky130_fd_sc_hd__dlygate4sd3_1 (input A, output X); assign X = A; endmodule
module sky130_fd_sc_hd__dlxtn_1 (input D, input GATE_N, output reg Q);
    always @* if (!GATE_N) Q = D;
endmodule
module sky130_fd_sc_hd__dlxbn_1 (input D, input GATE_N, output reg Q, output Q_N);
    always @* if (!GATE_N) Q = D;
    assign Q_N = ~Q;
endmodule
''')
(out/'map.json').write_text(json.dumps(dict(rows=R,cols=C,banks=mp,chain='row r: u_rom_(r*C) -> ... -> u_rom_(r*C+C-1); word = OR of row ends',
    instances='array u_rom_NN_arr (cell rom_bank_NN), periphery u_rom_NN (rom_ctl)'),indent=1)+'\n')
print('wrote',out,'rows',R,'cols',C)
