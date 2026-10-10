module tb;
  reg clk=1, rst=1; reg [8:0] addr=0; reg sel=0; wire [511:0] w1, w2; wire p1, p2; wire [63:0] d1, d2;
  reg [63:0] bl = 64'hffffffffffffffff;
  rom_ctl    u_seg (.clk(clk), .rst(rst), .addr(addr), .sel(sel), .bl(bl), .chain_in(64'd0), .wl(w1), .pre_n(p1), .dout(d1));
  rom_ctl_pb u_pb  (.clk(clk), .rst(rst), .addr(addr), .sel(sel), .bl(bl), .chain_in(64'd0), .wl(w2), .pre_n(p2), .dout(d2));
  integer a, s, bad, ones;
  initial begin
    bad = 0; ones = 0;
    #5 clk = 0; #5 clk = 1; rst = 0;
    for (s = 0; s < 2; s = s + 1)
      for (a = 0; a < 512; a = a + 1) begin
        addr = a; sel = s;
        #5 clk = 0; #2;
        if (w1 !== w2) bad = bad + 1;
        if (s == 1 && (w1 !== (512'd1 << a))) bad = bad + 1;
        ones = ones + (w1 != 0);
        #3 clk = 1; #2;
        if (w1 !== w2 || w1 !== 512'd0) bad = bad + 1;
        #3;
      end
    $display("CHECKED %0d reads, %0d one-hot, mismatches %0d", 1024, ones, bad);
    $finish;
  end
endmodule
