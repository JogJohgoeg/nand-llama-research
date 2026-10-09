# romgen: via1-programmed NOR mask ROM for sky130 (tp-2ea.44, prototype)

Goal: replace the machine's constant tables (shared weight table 30,720 words x 64 bits = 1,966,080 bits) with
mask-ROM hard macros so the chip fits ChipFoundry OpenFrame (15.09 mm²). The background is in `h3/ROM_EVAL.md`
in the main repo.

## Cell

`romgen.py` generates the array as GDS plus a reference SPICE netlist. It runs under KLayout in batch mode.

- **Rows and wordlines:**
  - Each row is one bitline: horizontal met2 at a 0.69 µm pitch.
  - Wordlines are vertical poly.
  - Each 2-bit unit, 1.38 µm wide, is the diffusion strip `D_a | WL 2u | G | WL 2u+1 | D_b`.
  - Adjacent units are separated by 0.27 µm of field.
- **Ground:** `G` is ground, carried on a vertical li strip that is tied to met2 ground rails above and below the array.
- **Programming:** `D_a`/`D_b` connect to the row's bitline through via1 if and only if the bit is 1, so every bit can be programmed independently.
- **Devices:** All transistors are sky130_fd_pr__nfet_01v8, W 0.42 µm, L 0.15 µm. There is no nwell.
- **Taps:** A p-tap column follows every 8 units.
- **Pins:**
  - Even wordlines are pinned in met1 on the top edge, odd wordlines on the bottom edge.
  - Bitlines and VGND are pinned in met2 on the left edge.
- **Area:** 0.476 µm²/bit for the bare cell. The 64x64 prototype, including its edges, is 46.4 x 47.9 µm, which is 0.542 µm²/bit.
- **Read:** Precharge the bitlines high, then raise one wordline. Bitline r goes low if and only if bit(r, w) = 1. Precharge and sensing are meant to be standard cells outside the macro.

## Checks (m149, LibreLane 3.0.14 rootfs via bwrap, 2026-10-09)

| check | result | file |
|---|---|---|
| Magic DRC, drc(full) (`drc.tcl`) | 0 | — |
| KLayout sky130A_mr.drc (feol, beol, offgrid) | 0 items | results/p64_kl.lyrdb |
| Magic extract → netgen LVS against `p64.spice` (`lvsx.tcl`) | Circuits match uniquely | results/p64_lvs.out |
| Negative: reference with bit (37, 11) flipped (`bits64neg.txt`) | fails (BL[11] mismatch) | results/n64_lvs.out |
| `ext2bits.py`: contents rebuilt from the extracted netlist alone vs bits file | 4,096 bits, 0 mismatches; the negative reports exactly (37, 11) | — |
| ngspice read, `pex.tcl` extraction with all caps, 100 fF extra per bitline, WL[37] rises at 5 ns (`mktb.py`) | tt/1.8 V/27 °C: bit-1 bitline reaches VDD/2 0.99 ns after WL; bit-0 bitline min 1.738 V. ss/1.6 V/100 °C: 1.43 ns; min 1.546 V | results/sim_tt.log, results/sim_ss.log |

Output hashes are listed in results/sha256.txt.

## v2 (2026-10-09): bank macro, Liberty, read periphery

- **Array v2 (`romgen.py`).**
  - The layout is transposed: bitlines run vertically in met2, wordlines horizontally in poly.
  - A precharge pfet per bitline (`PRE_N`) sits inside the macro.
  - VPWR and VGND are 1.6 µm met4 stripes over the full height, wide enough for via4 so a met5 strap can connect.
  - met3 appears only as landing pads every ~20 µm, so routes can cross the macro on met3.
  - The LEF is written from the final layout.
  - One bank (`banks.py`) is 64 x 512 = 512 words of the shared table. It measures 48.63 x 377.14 µm, 0.56 µm²/bit.
  - `check.sh` runs Magic DRC, KLayout DRC, LVS, and `ext2bits.py`, which rebuilds the stored bits from the extracted netlist.
- **Liberty.** The flow is `reduce.py`, then `chartb.py` and ngspice for each corner, then `romlib.py`.
  - The full 64 x 512 deck needs more than 30 GB in ngspice. `reduce.py` cuts it to one bitline plus one wordline, about 300 devices, which simulates in 23 s.
  - The arcs are one-directional, and `related_pin` lists every WL bit because OpenSTA builds no arcs between buses of different widths.
  - The ss corner is multiplied by 1.5 and the ff corner divided by 1.5. Measurements are in `lib/MEASURED.txt` and `lib/charall.txt`.
- **Read periphery (`periph.py`).** The clock drives only flops and latches. A two-flop phase signal plus data delay chains opens the wordline window about 10 ns after the falling edge and starts precharge about 5 ns after the rising edge. The address is sampled on the falling edge, and the output latches are transparent while the clock is low.
- **One-bank LibreLane test (`bank_test/`, run bt8 on m149).**
  - DRT 0, Magic DRC 0, KLayout DRC 0, LVS 0, antenna 0.
  - Setup is met. Worst hold slack is +0.0095 ns; the worst output-latch D hold slack is 1.85 ns at the ff corner.
  - `gatesim.py` on the final post-CTS netlist with unit delays: 1,998 random cycles, 0 mismatches. A one-bit negative run reports 4 mismatches.
  - Periphery logic is 7,405 µm² after synthesis, including test-only flops.

## Not done yet

- Integration into the machine top: 60 array macros, periphery merged into the body, PDN connected met5 to met4.
- A machine netlist with the ROM, then CEC and acceptance against C.
- A post-route replay model for the array macros and latches.
