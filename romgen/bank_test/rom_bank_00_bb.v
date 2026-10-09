(* blackbox *)
module rom_bank_00 (
`ifdef USE_POWER_PINS
    inout VPWR, inout VGND,
`endif
    input [511:0] WL, input PRE_N, output [63:0] BL);
endmodule
