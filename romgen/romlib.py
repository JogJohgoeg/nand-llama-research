#!/usr/bin/env python3
"""romlib.py <cell> <corner name> <vdd> <temp> <d_read> <s_fall> <c_wl> <d_pre> <s_rise> <c_pre> [margin] > cell.lib
Liberty view of one ROM array macro for STA (OpenSTA via LibreLane), from chartb.py measurements (ns, pF):
  WL[*] -> BL[*]  negative_unate: a rising wordline can pull a bitline low (cell_fall = d_read)
  PRE_N -> BL[*]  negative_unate: a falling PRE_N pulls the bitlines high (cell_rise = d_pre)
Scalar (single-point) tables; the max corner multiplies the measured delays by margin (default 1.5)
and the min corner divides them by it, so the Liberty brackets the measurement on both sides.
Bus pins: WL[511:0], BL[63:0]; the WL arc lists every bit in
related_pin (OpenSTA does not build arcs between buses of different widths)."""
import sys
cell,corner,vdd,temp=sys.argv[1:5];dr,sf,cwl,dp,sr,cpre=map(float,sys.argv[5:11])
m=float(sys.argv[11]) if len(sys.argv)>11 else 1.5
k=m if corner.startswith('max') or corner.startswith('nom') else 1/m
def t(x):return 'values("%.4f");'%x
def arc(rel,sense,d,tr,edge):
    """one-directional arc: edge 'fall' = only the output-falling transition exists (WL rising pulls BL
    low), 'rise' = only the output-rising one (PRE_N falling precharges BL). A wordline falling or PRE_N
    rising does not move the bitline, so those directions are left out (no false hold paths)."""
    return f'''      timing() {{
        related_pin : "{rel}";
        timing_sense : {sense};
        timing_type : combinational;
        cell_{edge}(scalar) {{ {t(d)} }}
        {edge}_transition(scalar) {{ {t(tr)} }}
      }}'''
print(f'''library ({cell}_{corner}) {{
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  capacitive_load_unit (1, pf);
  pulling_resistance_unit : "1kohm";
  leakage_power_unit : "1nW";
  nom_voltage : {vdd};
  nom_temperature : {temp};
  nom_process : 1.0;
  default_cell_leakage_power : 0;
  default_fanout_load : 1;
  default_inout_pin_cap : 0.002;
  default_input_pin_cap : 0.002;
  default_output_pin_cap : 0;
  slew_lower_threshold_pct_fall : 20; slew_upper_threshold_pct_fall : 80;
  slew_lower_threshold_pct_rise : 20; slew_upper_threshold_pct_rise : 80;
  input_threshold_pct_fall : 50; input_threshold_pct_rise : 50;
  output_threshold_pct_fall : 50; output_threshold_pct_rise : 50;
  operating_conditions ({corner}) {{ process : 1; voltage : {vdd}; temperature : {temp}; }}
  default_operating_conditions : {corner};
  voltage_map (VPWR, {vdd});
  voltage_map (VGND, 0);
  type (wl_bus) {{ base_type : array; data_type : bit; bit_width : 512; bit_from : 511; bit_to : 0; downto : true; }}
  type (bl_bus) {{ base_type : array; data_type : bit; bit_width : 64; bit_from : 63; bit_to : 0; downto : true; }}
  cell ({cell}) {{
    area : 0;
    dont_use : true;
    dont_touch : true;
    pg_pin (VPWR) {{ voltage_name : VPWR; pg_type : primary_power; }}
    pg_pin (VGND) {{ voltage_name : VGND; pg_type : primary_ground; }}
    pin (PRE_N) {{ direction : input; capacitance : {cpre:.5f}; related_power_pin : VPWR; related_ground_pin : VGND; }}
    bus (WL) {{
      bus_type : wl_bus;
      direction : input;
      capacitance : {cwl:.5f};
      related_power_pin : VPWR; related_ground_pin : VGND;
    }}
    bus (BL) {{
      bus_type : bl_bus;
      direction : output;
      related_power_pin : VPWR; related_ground_pin : VGND;
{arc(' '.join('WL[%d]'%i for i in range(512)),'negative_unate',dr*k,sf*k,'fall')}
{arc('PRE_N','negative_unate',dp*k,sr*k,'rise')}
    }}
  }}
}}''')
