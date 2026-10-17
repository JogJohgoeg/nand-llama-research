#!/usr/bin/env python3
"""SKY130 whole machine with both rotating rings in RAM (H, tp-2ea.48.5): top_r2 = top_r (mktop_sky.py) plus the second
ring in 5 more sram8t_64x64 (ring2_ram.py), on a larger die with a lower placement density: the OpenFrame-size die at
density 0.40 did not route for either top_s or top_r (DRT-0218 / growing violations). Uses the 10-15 macro (power pins
= full-height met4 stripes). Run in node/ on m64 after node/sram holds body_wrap2.v, body_ram2.v, prefix_ram.v,
ring2_ram.v, sram_bb.v and the macro views."""
import os,re
A="designs/sky130hd/top_r";D="designs/sky130hd/top_r2";d="/h2/node/"+D;S="/h2/node/sram"
os.makedirs(D,exist_ok=True)
cfg=open(A+"/config.mk").read()
cfg=cfg.replace("DESIGN_NICKNAME = top_r","DESIGN_NICKNAME = top_r2").replace("/designs/sky130hd/top_r/","/designs/sky130hd/top_r2/")
cfg=cfg.replace(f"{S}/body_wrap.v {S}/body_ram.v",f"{S}/body_wrap2.v {S}/body_ram2.v {S}/ring2_ram.v")
cfg=re.sub(r"export DIE_AREA = .*","export DIE_AREA = 0 0 4000 5200",cfg)
cfg=re.sub(r"export CORE_AREA = .*","export CORE_AREA = 20 20 3980 5180",cfg)
cfg=re.sub(r"export PLACE_DENSITY = .*","export PLACE_DENSITY = 0.35",cfg)
cfg=cfg.replace("# SKY130 whole machine (top_r,","# SKY130 whole machine (top_r2, both rings in 15 macros, die 4000 x 5200, density 0.35;")
open(D+"/config.mk","w").write(cfg)
open(D+"/constraint.sdc","w").write(open(A+"/constraint.sdc").read())
mp=open(A+"/macro_place.tcl").read()
mp+="".join(f"place_macro -macro_name {{u_body.u_ram2.g_mac2\\[{m}\\].u_arr}} -location {{{190+290*m:.2f} 1820.00}} -orientation R0\n" for m in range(5))
open(D+"/macro_place.tcl","w").write(mp)
print("ok")
