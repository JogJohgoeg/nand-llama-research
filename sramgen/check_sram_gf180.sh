#!/bin/sh
# check_sram_gf180.sh <rows> <cols> <cell name> <outdir under ~/llr>: generate the GF180MCU 8T array, run the GF180
# KLayout DRC (variant D, no density) and LVS (substrate net VGND) against the generated schematic (LibreLane rootfs).
R=$1;C=$2;N=$3;O=$4;T=/root/.ciel/gf180mcuD/libs.tech/klayout/tech
mkdir -p ~/llr/$O && cp sramgen_gf180.py ~/llr/$O/
cd ~/llr && ./ll.sh sh -c "export PATH=/h2/pylib/bin:\$PATH; cd /h2/$O && klayout -b -r sramgen_gf180.py -rd rows=$R -rd cols=$C -rd out=$N -rd name=$N > gen.log 2>&1 &&
  PYTHONPATH=/h2/pylib python3 $T/drc/run_drc.py --path=$N.gds --variant=D --topcell=$N --run_dir=drc --run_mode=flat > drc.log 2>&1;
  PYTHONPATH=/h2/pylib python3 $T/lvs/run_lvs.py --layout=$N.gds --netlist=$N.spice --variant=D --topcell=$N --run_dir=lvs --run_mode=flat --lvs_sub=VGND > lvs.log 2>&1"
tail -1 ~/llr/$O/gen.log; grep -i -E "total|violation|error" ~/llr/$O/drc.log | tail -3; grep -i -E "match|error" ~/llr/$O/lvs.log | tail -2
