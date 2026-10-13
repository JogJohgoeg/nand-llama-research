module eqtop(input clk, input [107:0] din, output [29:0] a, output [29:0] b);
  int_c16_body u_ref(.clk(clk), .din(din), .dout(a));
  ext u_ext(.clk(clk), .din(din), .dout(b));
endmodule
