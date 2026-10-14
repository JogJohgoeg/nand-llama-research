#!/usr/bin/env python3
"""sramlib.py <cell> <corner name> <vdd> <temp> <rows> <cols> <res.txt> <char corner> [margin] > cell.lib  (tp-2ea.48.3)
Liberty view of one 8T array macro for STA, from sram_char.py results (res.txt lines
"RES <corner> <vdd> <temp> cl=.. d_read=.. s_fall=.. d_pre=.. s_rise=.. q_rwl=.. q_pre=.. q_wwl=.. q_wbl=..", SI units).
  RWL[*] -> RBL[*]  negative_unate, fall only (a raised read wordline pulls a bitline low)
  PRE_N  -> RBL[*]  negative_unate, rise only (a falling PRE_N precharges the bitlines)
Write pins (WWL, WBL, WBLB) carry capacitance only: the write is timed by construction in prefix_ram.v (WWL window
opens ~5 ns after W and the write row settle and closes at the falling edge; the cell flips in < 0.1 ns, see
cell8t_tb.py), not by STA. Scalar tables; max/nom corners multiply delays by margin (default 1.5), min divides."""
import re,sys
cell,corner,vdd,temp,R,C,res,ck=sys.argv[1:9];R,C=int(R),int(C);m=float(sys.argv[9]) if len(sys.argv)>9 else 1.5
v=None
for l in open(res):
    if l.startswith('RES '+ck+' '):v={a:abs(float(b)) for a,b in re.findall(r'(\w+)=(\S+)',l) if a!='cl'}
assert v,ck
vd=float(vdd);k=m if corner.startswith(('max','nom')) else 1/m
ns=lambda x:x*1e9*k;pf=lambda q:q/vd*1e12          # charge (C) over the swing -> pF
def arc(rel,d,tr,edge):
    return f'''      timing() {{
        related_pin : "{rel}";
        timing_sense : negative_unate;
        timing_type : combinational;
        cell_{edge}(scalar) {{ values("{d:.4f}"); }}
        {edge}_transition(scalar) {{ values("{tr:.4f}"); }}
      }}'''
def bus(name,bt,d,cap=None,arcs=''):
    c='      capacitance : %.5f;\n'%cap if cap is not None else ''
    return f'''    bus ({name}) {{
      bus_type : {bt};
      direction : {d};
{c}      related_power_pin : VPWR; related_ground_pin : VGND;
{arcs}
    }}'''
print(f'''library ({cell}_{corner}) {{
  delay_model : table_lookup;
  time_unit : "1ns"; voltage_unit : "1V"; current_unit : "1mA"; capacitive_load_unit (1, pf);
  pulling_resistance_unit : "1kohm"; leakage_power_unit : "1nW";
  nom_voltage : {vdd}; nom_temperature : {temp}; nom_process : 1.0;
  default_cell_leakage_power : 0; default_fanout_load : 1;
  default_inout_pin_cap : 0.002; default_input_pin_cap : 0.002; default_output_pin_cap : 0;
  slew_lower_threshold_pct_fall : 20; slew_upper_threshold_pct_fall : 80;
  slew_lower_threshold_pct_rise : 20; slew_upper_threshold_pct_rise : 80;
  input_threshold_pct_fall : 50; input_threshold_pct_rise : 50;
  output_threshold_pct_fall : 50; output_threshold_pct_rise : 50;
  operating_conditions ({corner}) {{ process : 1; voltage : {vdd}; temperature : {temp}; }}
  default_operating_conditions : {corner};
  voltage_map (VPWR, {vdd}); voltage_map (VGND, 0);
  type (row_bus) {{ base_type : array; data_type : bit; bit_width : {R}; bit_from : {R-1}; bit_to : 0; downto : true; }}
  type (col_bus) {{ base_type : array; data_type : bit; bit_width : {C}; bit_from : {C-1}; bit_to : 0; downto : true; }}
  cell ({cell}) {{
    area : 0; dont_use : true; dont_touch : true;
    pg_pin (VPWR) {{ voltage_name : VPWR; pg_type : primary_power; }}
    pg_pin (VGND) {{ voltage_name : VGND; pg_type : primary_ground; }}
    pin (PRE_N) {{ direction : input; capacitance : {pf(v['q_pre']):.5f}; related_power_pin : VPWR; related_ground_pin : VGND; }}
{bus('WWL','row_bus','input',pf(v['q_wwl']))}
{bus('RWL','row_bus','input',pf(v['q_rwl']))}
{bus('WBL','col_bus','input',pf(v['q_wbl']))}
{bus('WBLB','col_bus','input',pf(v['q_wbl']))}
{bus('RBL','col_bus','output',None,arc(' '.join('RWL[%d]'%i for i in range(R)),ns(v['d_read']),ns(v['s_fall']),'fall')+chr(10)+arc('PRE_N',ns(v['d_pre']),ns(v['s_rise']),'rise'))}
  }}
}}''')
