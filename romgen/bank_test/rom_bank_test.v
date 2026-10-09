// one ROM bank (array rom_bank_00 + read periphery) between input and output flops, for area/routing and
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
    rom_bank_00 u_arr (.WL(wl), .PRE_N(pre_n), .BL(bl));
    generate
        for (k = 0; k < 64; k = k + 1) begin : g_lat
            sky130_fd_sc_hd__dlxtn_1 u_lat (.D(~bl[k]), .GATE_N(clk), .Q(q[k]));
        end
    endgenerate
    always @(posedge clk) dout <= q | chain_in;
endmodule
