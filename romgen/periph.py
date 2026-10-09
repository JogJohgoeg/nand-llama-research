#!/usr/bin/env python3
"""periph.py <array cell name> <outdir>: standard-cell read periphery for one ROM array (64 x 512) and a
one-bank test top (rom_bank_test) for LibreLane.

Read in one machine clock, so the ROM behaves like the combinational table of the netlist. The clock
drives only flops and latches (no clock-as-data, so CTS has nothing to latency-balance). A phase signal
comes from two flops: A toggles on the falling edge, B copies A on the rising edge, P = A ^ B is 1 in the
low phase and 0 in the high phase (edges at fall+tcq / rise+tcq). dP8 / dP16 = P through 8 / 16 delay
gates (~5 / ~10 ns typical; half a cycle is 100 ns), all plain data paths that STA times:
  address + sel flops : sampled on the falling edge     -> stable from fall+tcq to the next fall
  wordline window     : P & dP16 (& sel)                -> fall+tcq+10 .. rise+tcq
  precharge (PRE_N=0) : ~P & ~dP8                       -> rise+tcq+5 .. fall+tcq
  output latches      : transparent while clk is low (GATE_N = clk), q = ~BL
so the window opens only after the address has settled, precharge never overlaps the window, and the
output latches close at the rising edge ~5 ns before the bitlines start to rise (a hold check STA sees,
launched by flop B). rst (synchronous, rising edge) clears A and B; it only matters for simulation, the
pair falls into step by itself after one cycle in silicon.
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
module rom_bank_test (input clk, input rst, input [8:0] addr, input sel, input [63:0] chain_in, output reg [63:0] dout);
    reg [8:0] a_q; reg s_q;
    always @(posedge clk) begin a_q <= addr; s_q <= sel; end
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
    always @(negedge clk) begin a_l <= a_q; s_l <= s_q; end
    wire pre_n = ph | dp[8];
    wire win = s_l & ph & dp[16];
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
