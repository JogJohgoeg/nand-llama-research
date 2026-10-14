#!/usr/bin/env python3
"""SKY130 whole-machine ORFS designs for the prefix-RAM comparison (H, tp-2ea.48.5). Run in node/ on m64.
  top_s  baseline: v8g top_rom.v + body.v + rom_ctl.v + 60 sky130 mask-ROM banks (rom_v8 macros, v8g positions)
  top_r  the same with the prefix ring's middle rows in RAM: body_wrap.v + body_ram.v + prefix_ram.v + 10 sram8t_64x64
Both: OpenFrame-size die 3166.63 x 4766.63, 200 ns clock (as v8g), tt libraries, same ROM placement, platform PDN
(its default macro grids join the macros' met4 VPWR/VGND stripes to met5)."""
import json,os
ROMS=[(555.22+0,0)]  # placeholder, replaced below
X=[555.22,660.1,765.44,870.32,975.2,1080.54,1185.42,1290.3,1395.18,1500.52,1605.4,1710.28,1815.16,1920.5,2025.38,
   2130.26,2235.14,2340.48,2445.36,2550.24];Y=[78.88,560.32,1039.04]
rom=[f"place_macro -macro_name u_rom_{r*20+c:02d}_arr -location {{{X[c]:.2f} {Y[r]:.2f}}} -orientation R0"
     for r in range(3) for c in range(20)]
ram=[f"place_macro -macro_name u_body.u_ram.g_mac\\[{m}\\].u_arr -location {{{190+290*m:.2f} 1560.00}} -orientation R0"
     for m in range(10)]
R="/h2/rom_v8";S="/h2/node/sram"
for tag,files,extra in (("top_s",["/h2/mrom9/body.v"],False),
                        ("top_r",[f"{S}/body_wrap.v",f"{S}/body_ram.v",f"{S}/prefix_ram.v",f"{S}/sram_bb.v"],True)):
    D=f"designs/sky130hd/{tag}";d="/h2/node/"+D;os.makedirs(D,exist_ok=True)
    open(D+"/macro_place.tcl","w").write("\n".join(rom+(ram if extra else []))+"\n")
    open(D+"/constraint.sdc","w").write("""create_clock -name clk -period 200 [get_ports clk]
set_input_delay -clock clk 20 [get_ports {din*}]
set_output_delay -clock clk 20 [get_ports {dout*}]
""")
    libs=[f"{R}/lib/rom_bank_{n:02d}__tt_025C_1v80.lib" for n in range(60)]
    lefs=[f"{R}/macros/rom_bank_{n:02d}.lef" for n in range(60)]
    gds=[f"{R}/macros/rom_bank_{n:02d}.gds" for n in range(60)]
    if extra:libs.append(f"{S}/sram8t_64x64__nom_tt_025C_1v80.lib");lefs.append(f"{S}/sram8t_64x64.lef");gds.append(f"{S}/sram8t_64x64.gds")
    vf=" ".join(["/h2/mrom9/top_rom.v","/h2/mrom9/rom_ctl.v","/h2/mrom9/rom_bank_bb.v"]+files)
    open(D+"/config.mk","w").write(f"""# SKY130 whole machine ({tag}, H tp-2ea.48.5): {'prefix ring middle rows in 10 sram8t_64x64 macros' if extra else 'baseline v8g netlist'}
export PLATFORM = sky130hd
export DESIGN_NAME = int_c16_machine
export DESIGN_NICKNAME = {tag}
export VERILOG_FILES = {vf}
export SDC_FILE = {d}/constraint.sdc
export ADDITIONAL_LIBS = {' '.join(libs)}
export ADDITIONAL_LEFS = {' '.join(lefs)}
export ADDITIONAL_GDS = {' '.join(gds)}
export DIE_AREA = 0 0 3166.63 4766.63
export CORE_AREA = 20 20 3146.63 4746.63
export MACRO_PLACEMENT_TCL = {d}/macro_place.tcl
export MACRO_PLACE_HALO = 4 4
export PLACE_DENSITY = 0.40
export SYNTH_HIERARCHICAL = 0
export TNS_END_PERCENT = 20
export SKIP_LAST_GASP = 1
export SKIP_INCREMENTAL_REPAIR = 1
export SKIP_CTS_REPAIR_TIMING = 1
export SYNTH_MEMORY_MAX_BITS = 65536
export LEC_CHECK = 0
export GLOBAL_ROUTE_ARGS = -congestion_iterations 30 -congestion_report_iter_step 5 -verbose -allow_congestion
""")
print("ok")
