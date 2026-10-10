module tb;
  reg clk=1, rst=1; reg [8:0] addr=0; reg sel=0; reg [63:0] bl=64'hffffffffffffffff, ch=0;
  wire [511:0] w1, w2; wire p1, p2; wire [63:0] d1, d2;
  rom_ctl    u_b (.clk(clk), .rst(rst), .addr(addr), .sel(sel), .bl(bl), .chain_in(ch), .wl(w1), .pre_n(p1), .dout(d1));
  rom_ctl_t  u_t (.clk(clk), .rst(rst), .addr(addr), .sel(sel), .bl(bl), .chain_in(ch), .wl(w2), .pre_n(p2), .dout(d2));
  integer i, bad;
  initial begin
    bad = 0;
    #5 clk = 0; #5 clk = 1; rst = 0;
    for (i = 0; i < 2000; i = i + 1) begin
      addr = $random; sel = $random; ch = {$random, $random} & {64{i[2]}};
      #5 clk = 0; #1 bl = {$random, $random}; #3;
      if (d1 !== d2 || w1 !== w2 || p1 !== p2) bad = bad + 1;
      #1 clk = 1; #1 bl = 64'hffffffffffffffff; #2;
      if (d1 !== d2) bad = bad + 1;
      #2;
    end
    $display("CHECKED 2000 cycles, mismatches %0d", bad);
    $finish;
  end
endmodule
