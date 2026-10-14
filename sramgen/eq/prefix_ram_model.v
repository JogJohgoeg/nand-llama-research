// cycle model of the prefix RAM (tp-2ea.48.4): free-running physical pointer q; each cycle the RAM returns
// physical row q+1 (logical row 1 -> body head) and stores wdata (body tail = logical row 63) in row q-1.
// NEG=1 negative control: return row q+2 instead.
module prefix_ram_model #(parameter NEG = 0) (input clk, input [639:0] wdata, output [639:0] rdata);
  reg [5:0] q;
  reg [639:0] m [0:63];
  always @(posedge clk) begin m[q - 6'd1] <= wdata; q <= q + 6'd1; end
  assign rdata = m[q + 6'd1 + NEG];
endmodule
