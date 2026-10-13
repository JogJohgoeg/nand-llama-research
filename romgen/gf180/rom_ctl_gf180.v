// read periphery of one 64 x 512 ROM bank, GF180MCU 3.3 V (gf180mcu_fd_sc_mcu9t5v0 at the tt_025C_3v30 corner, matching
// the 3.3 V nfet_03v3/pfet_03v3 array of romgen_gf180.py). Same logic as the SKY130 v8g rom_ctl (machine_rom.py), only the
// delay taps change: dlyb_1 is about 1.2 ns at 3.3 V, so pre_n = ph | dp[1] and win = ph & dp[2]. Precharge window =
// T/2 - ~1.2 ns, wordline window = T/2 - ~2.4 ns; the array Liberty (romgen/lib/gf180) needs at ss 11.62 ns precharge and
// 5.00 ns read (x1.5 in the .lib), so any period >= ~38 ns is safe for the ROM side. Output latch: latq_1 (enable high)
// on ~clk, i.e. transparent while clk is low, as dlxtn in the SKY130 version.
module rom_ctl (input clk, input rst, input [8:0] addr, input sel, input [63:0] bl, input [63:0] chain_in,
                output [511:0] wl, output pre_n, output [63:0] dout);
    reg ph_a, ph_b;
    always @(negedge clk) ph_a <= rst ? 1'b0 : ~ph_a;
    always @(posedge clk) ph_b <= rst ? 1'b0 : ph_a;
    wire ph = ph_a ^ ph_b;
    wire [2:0] dp;
    assign dp[0] = ph;
    genvar i, j, k;
    generate
        for (i = 0; i < 2; i = i + 1) begin : g_dly
            gf180mcu_fd_sc_mcu9t5v0__dlyb_1 u_dly (.I(dp[i]), .Z(dp[i+1]));
        end
    endgenerate
    reg [8:0] a_l; reg s_l;
    always @(negedge clk) begin a_l <= addr; s_l <= sel; end
    assign pre_n = ph | dp[1];
    wire win = s_l & ph & dp[2];
    wire [31:0] pa; wire [15:0] pb;
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
    wire [63:0] q;
    generate
        for (k = 0; k < 64; k = k + 1) begin : g_lat
            gf180mcu_fd_sc_mcu9t5v0__latq_1 u_lat (.D(~bl[k]), .E(~clk), .Q(q[k]));
        end
    endgenerate
    assign dout = q | chain_in;
endmodule
