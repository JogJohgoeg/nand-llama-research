#!/usr/bin/env python3
"""prefix_ram.py <outdir> [nmac=10] [cols=64] [rows=64] [macro cell] [delay cell]: periphery RTL + models for the prefix RAM (tp-2ea.48.3).

The body (ring2ram.py -> int_c16_body_ram) keeps the ring's head (H) and tail (W) registers; the RAM holds the
other rows. Physical read row ra is a free-running counter (ra = q+1 in prefix_ram_model.v), write row wa = ra-2.
Timing (same phase scheme as rom_ctl; the clock reaches only flops):
  ph = 1 in the clock-low phase (ph_a toggles on the falling edge, ph_b follows on the rising edge)
  precharge  pre_n = ph | dp[8]      RBL precharged from ~5 ns after the rising edge until the falling edge
  write      wwin = ~ph & ~dp[8]     WWL[wa] in that same high-phase window; W and wa change only at the rising edge
  read       rwin = ph & dp[16]      RWL[ra_l] from ~10 ns after the falling edge until the rising edge; ra_l is
                                      ra sampled on the falling edge, so the decode is still while the window is open
  rdata = ~RBL (cell stores Q = WBL; RBL falls iff Q = 1); body H registers capture it at the rising edge, the RBL
  keeps its value until the next precharge starts ~5 ns later.
Files: prefix_ram.v (synthesis), sram_bb.v (black box of the array macro), sim/sram_model.v (behavioural array),
sim/cells.v (zero-delay sky130 stand-ins), body_wrap.v (int_c16_body = body_ram + prefix_ram).
"""
import sys
from pathlib import Path
out=Path(sys.argv[1]);NM=int(sys.argv[2]) if len(sys.argv)>2 else 10;C=int(sys.argv[3]) if len(sys.argv)>3 else 64
R=int(sys.argv[4]) if len(sys.argv)>4 else 64;W=NM*C;A=(R-1).bit_length()
MAC=sys.argv[5] if len(sys.argv)>5 else 'sram8t_%dx%d'%(R,C)                  # e.g. sram8t_sg_64x64 (IHP)
DLY=sys.argv[6] if len(sys.argv)>6 else 'sky130_fd_sc_hd__dlygate4sd3_1'     # e.g. sg13g2_dlygate4sd3_1
DLY,DI,DO=(DLY.split(':')+['A','X'])[:3]   # cell[:in:out], e.g. gf180mcu_fd_sc_mcu9t5v0__dlyb_1:I:Z
out.mkdir(parents=True,exist_ok=True);(out/'sim').mkdir(exist_ok=True)
v=f'''// prefix RAM periphery: {NM} array macros {MAC} ({R} rows x {C} columns) -- see prefix_ram.py
module prefix_ram (input clk, input [{W-1}:0] wdata, output [{W-1}:0] rdata);
    reg ph_a, ph_b;
    always @(negedge clk) ph_a <= ~ph_a;
    always @(posedge clk) ph_b <= ph_a;
    wire ph = ph_a ^ ph_b;
    wire [16:0] dp;
    assign dp[0] = ph;
    genvar i, m;
    generate
        for (i = 0; i < 16; i = i + 1) begin : g_dly
            {DLY} u_dly (.{DI}(dp[i]), .{DO}(dp[i+1]));
        end
    endgenerate
    reg [{A-1}:0] ra, ra_l;
    always @(posedge clk) ra <= ra + {A}'d1;
    always @(negedge clk) ra_l <= ra;
    wire [{A-1}:0] wa = ra - {A}'d2;
    wire pre_n = ph | dp[8];
    wire wwin = ~ph & ~dp[8];
    wire rwin = ph & dp[16];
    wire [{R-1}:0] wwl, rwl;
    generate
        for (i = 0; i < {R}; i = i + 1) begin : g_dec
            assign wwl[i] = wwin & (wa == i);
            assign rwl[i] = rwin & (ra_l == i);
        end
        for (m = 0; m < {NM}; m = m + 1) begin : g_mac
            wire [{C-1}:0] rbl;
            {MAC} u_arr (.WBL(wdata[m*{C} +: {C}]), .WBLB(~wdata[m*{C} +: {C}]), .RBL(rbl), .WWL(wwl), .RWL(rwl), .PRE_N(pre_n));
            assign rdata[m*{C} +: {C}] = ~rbl;
        end
    endgenerate
endmodule
'''
(out/'prefix_ram.v').write_text(v)
(out/'sram_bb.v').write_text(f'''(* blackbox *)
module {MAC} (
`ifdef USE_POWER_PINS
    inout VPWR, inout VGND,
`endif
    input [{C-1}:0] WBL, input [{C-1}:0] WBLB, output [{C-1}:0] RBL, input [{R-1}:0] WWL, input [{R-1}:0] RWL, input PRE_N);
endmodule
''')
# behavioural array: write while WWL[r] is high (Q = WBL); RBL precharged while PRE_N is low, pulled low by any
# raised RWL whose cell holds 1, otherwise it keeps its value (dynamic node)
(out/'sim'/'sram_model.v').write_text(f'''module {MAC} (input [{C-1}:0] WBL, input [{C-1}:0] WBLB, output reg [{C-1}:0] RBL,
                   input [{R-1}:0] WWL, input [{R-1}:0] RWL, input PRE_N);
    reg [{C-1}:0] mem [0:{R-1}];
    integer r;
    always @* for (r = 0; r < {R}; r = r + 1) if (WWL[r]) mem[r] = WBL;
    always @* begin
        if (!PRE_N) RBL = {{{C}{{1'b1}}}};
        else for (r = 0; r < {R}; r = r + 1) if (RWL[r]) RBL = RBL & ~mem[r];
    end
endmodule
''')
(out/'sim'/'cells.v').write_text(f'''module {DLY} (input {DI}, output {DO}); assign {DO} = {DI}; endmodule
''')
(out/'body_wrap.v').write_text(f'''// int_c16_body with the prefix ring's middle rows in the RAM -- tp-2ea.48
module int_c16_body(input clk, input [107:0] din, output [29:0] dout);
    wire [{W-1}:0] rd, wd;
    int_c16_body_ram u_b (.clk(clk), .din(din), .rdata(rd), .dout(dout), .wdata(wd));
    prefix_ram u_ram (.clk(clk), .wdata(wd), .rdata(rd));
endmodule
''')
print('wrote',out,'macros',NM,MAC,'width',W)
