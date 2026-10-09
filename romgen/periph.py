#!/usr/bin/env python3
"""periph.py <array cell name> <outdir>: standard-cell read periphery for one ROM array (64 x 512) and a
one-bank test top (rom_bank_test) for LibreLane.

Read in one machine clock, so the ROM behaves like the combinational table of the netlist. With
cd5 / cd10 = clk through 8 / 16 delay gates (~5 / ~10 ns typical; half a cycle is 100 ns):
  address + sel latch : transparent while cd5 is high  -> frozen from fall+5 to rise+5
  precharge (PRE_N=0) : clk & cd5                       -> rise+5 .. fall
  wordline window     : ~clk & ~cd10 (& latched sel)    -> fall+10 .. rise
  output latches      : transparent while clk is low (GATE_N = clk), q = ~BL
so the window opens only after the address is frozen and closes before it can change, precharge never
overlaps the window, and the output latches close at the rising edge ~5 ns before the bitlines start to
rise. The consumer flops sample q at the rising edge (in the test top: dout <= q | chain_in).
Wordline decode: addr[8:4] -> 32 gated lines (with the window), addr[3:0] -> 16 lines, WL = and2.
Files: <array>_bb.v (black box, ports as the LEF), rom_bank_test.v.
"""
import sys
from pathlib import Path
arr,out=sys.argv[1:3];d=Path(out);d.mkdir(parents=True,exist_ok=True)
(d/f'{arr}_bb.v').write_text(f'''(* blackbox *)
module {arr} (
`ifdef USE_POWER_PINS
    inout VPWR, inout VGND,
`endif
    input [511:0] WL, input PRE_N, output [63:0] BL);
endmodule
''')
(d/'rom_bank_test.v').write_text(f'''// one ROM bank (array {arr} + read periphery) between input and output flops, for area/routing and
// gate-level checks; see periph.py for the timing scheme
module rom_bank_test (input clk, input [8:0] addr, input sel, input [63:0] chain_in, output reg [63:0] dout);
    reg [8:0] a_q; reg s_q;
    always @(posedge clk) begin a_q <= addr; s_q <= sel; end
    wire [16:0] cd;
    assign cd[0] = clk;
    genvar i, j, k;
    generate
        for (i = 0; i < 16; i = i + 1) begin : g_dly
            sky130_fd_sc_hd__dlygate4sd3_1 u_dly (.A(cd[i]), .X(cd[i+1]));
        end
    endgenerate
    wire cd5 = cd[8], cd10 = cd[16];
    wire [8:0] a_l; wire s_l;
    generate
        for (i = 0; i < 9; i = i + 1) begin : g_alat
            sky130_fd_sc_hd__dlxtp_1 u_alat (.D(a_q[i]), .GATE(cd5), .Q(a_l[i]));
        end
    endgenerate
    sky130_fd_sc_hd__dlxtp_1 u_slat (.D(s_q), .GATE(cd5), .Q(s_l));
    wire pre_n = ~(clk & cd5);
    wire win = s_l & ~clk & ~cd10;
    wire [31:0] pa; wire [15:0] pb; wire [511:0] wl;
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
    wire [63:0] bl, q;
    {arr} u_arr (.WL(wl), .PRE_N(pre_n), .BL(bl));
    generate
        for (k = 0; k < 64; k = k + 1) begin : g_lat
            sky130_fd_sc_hd__dlxtn_1 u_lat (.D(~bl[k]), .GATE_N(clk), .Q(q[k]));
        end
    endgenerate
    always @(posedge clk) dout <= q | chain_in;
endmodule
''')
print('wrote',d/f'{arr}_bb.v',d/'rom_bank_test.v')
