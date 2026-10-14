module eqtop(input clk, input [107:0] din, output [29:0] a, output [29:0] b);
  wire [639:0] rd, wd;
  int_c16_body u_ref(.clk(clk), .din(din), .dout(a));
  int_c16_body_ram u_ram(.clk(clk), .din(din), .rdata(rd), .dout(b), .wdata(wd));
  prefix_ram_model #(.NEG(`NEG)) u_mem(.clk(clk), .wdata(wd), .rdata(rd));
endmodule
