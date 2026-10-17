#!/usr/bin/env python3
"""ring2_ram.py <outdir> [macro] [dlycell] [lead=2]: periphery for the second ring (tp-2ea.48.6) + body wrapper.

ring2ram.py (rows=2096 width=8, ports rdata2/wdata2) keeps the ring's head H and tail W in the body. The rest of the
ring is a delay line: rdata2(t) = wdata2(t-2094) (H(t+1) = L1(t), L1(t) = W(t-2094)). It is folded 8 entries per
64-bit row into 5 array macros of 64 rows (262 rows used = 2096/8):
  c        free-running cycle counter mod 2096 (no reset, like the prefix RAM pointer)
  write    entry c goes to row c[11:3], slot c[2:0]; slots 0..6 wait in wbuf, the row {W, wbuf} is written in the
           cycle with slot 7 (W is a register, stable all cycle)
  read     entry u = c+2 (= c-2094 mod 2096); when u[2:0] = 0 row u[11:3] is read, slot 0 goes to H and slots 1..7
           to rbuf; in the other cycles H takes rbuf[slot]
A row written in cycle u+7 is read in cycle u+2094 and overwritten in cycle u+2096+7, so reads never race writes.
Timing is the prefix RAM's (prefix_ram.py): write in the high phase after dp[8], read in the low phase after dp[16]
with the address (ra_l) and read select (s_l) latched on the falling edge; those names are what
postroute_extract_rom.py treats as transparent falling-edge flops. Unselected macros keep RBL precharged, so rdata = ~(AND of all RBL).
lead=3 gives the negative control (reads one entry too late).
Files: ring2_ram.v, body_wrap2.v (int_c16_body = body_ram2 + prefix_ram + ring2_ram)."""
import sys
from pathlib import Path
out=Path(sys.argv[1]);MAC=sys.argv[2] if len(sys.argv)>2 else 'sram8t_64x64'
DLY=sys.argv[3] if len(sys.argv)>3 else 'sky130_fd_sc_hd__dlygate4sd3_1';LEAD=int(sys.argv[4]) if len(sys.argv)>4 else 2
DLY,DI,DO=(DLY.split(':')+['A','X'])[:3]   # cell[:in:out], e.g. gf180mcu_fd_sc_mcu9t5v0__dlyb_1:I:Z
N=2096;K=8;B=8;NM=5;ROWS=64;C=K*B
out.mkdir(parents=True,exist_ok=True)
(out/'ring2_ram.v').write_text(f'''// second ring as a folded delay line in {NM} {MAC} macros -- see ring2_ram.py (lead {LEAD})
module ring2_ram (input clk, input [{B-1}:0] wdata, output [{B-1}:0] rdata);
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
    reg [11:0] c;
    always @(posedge clk) c <= (c == 12'd{N-1}) ? 12'd0 : c + 12'd1;
    wire [11:0] u = (c >= 12'd{N-LEAD}) ? c - 12'd{N-LEAD} : c + 12'd{LEAD};
    reg [{C-K-1}:0] wbuf, rbuf;
    always @(posedge clk) begin
        if (c[2:0] != 3'd7) wbuf[c[2:0]*{B} +: {B}] <= wdata;
    end
    reg [8:0] ra_l; reg s_l;
    always @(negedge clk) begin ra_l <= u[11:3]; s_l <= (u[2:0] == 3'd0); end
    wire pre_n = ph | dp[8];
    wire wwin = ~ph & ~dp[8] & (c[2:0] == 3'd7);
    wire rwin = ph & dp[16] & s_l;
    wire [{C-1}:0] wd = {{wdata, wbuf}};
    wire [{C-1}:0] rbl [0:{NM-1}];
    generate
        for (m = 0; m < {NM}; m = m + 1) begin : g_mac2
            wire [{ROWS-1}:0] wwl, rwl;
            for (i = 0; i < {ROWS}; i = i + 1) begin : g_dec
                assign wwl[i] = wwin & (c[11:3] == m*{ROWS}+i);
                assign rwl[i] = rwin & (ra_l == m*{ROWS}+i);
            end
            {MAC} u_arr (.WBL(wd), .WBLB(~wd), .RBL(rbl[m]), .WWL(wwl), .RWL(rwl), .PRE_N(pre_n));
        end
    endgenerate
    wire [{C-1}:0] rd = ~({' & '.join(f'rbl[{m}]' for m in range(NM))});
    always @(posedge clk) if (u[2:0] == 3'd0) rbuf <= rd[{C-1}:{B}];
    assign rdata = (u[2:0] == 3'd0) ? rd[{B-1}:0] : rbuf[(u[2:0]-3'd1)*{B} +: {B}];
endmodule
''')
(out/'body_wrap2.v').write_text('''// int_c16_body with both rings' middle stages in RAM -- tp-2ea.48.6
module int_c16_body(input clk, input [107:0] din, output [29:0] dout);
    wire [639:0] rd, wd; wire [7:0] rd2, wd2;
    int_c16_body_ram2 u_b (.clk(clk), .din(din), .rdata(rd), .dout(dout), .wdata(wd), .rdata2(rd2), .wdata2(wd2));
    prefix_ram u_ram (.clk(clk), .wdata(wd), .rdata(rd));
    ring2_ram u_ram2 (.clk(clk), .wdata(wd2), .rdata(rd2));
endmodule
''')
print('wrote',out,MAC,'lead',LEAD)
