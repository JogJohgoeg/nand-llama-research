#!/bin/sh
# check_sg13g2.sh <bits.txt> <cell name> <outdir under ~/llr>: generate the SG13G2 ROM array, run the IHP KLayout
# DRC (no density; an array macro is filled at chip level) and LVS against the generated schematic.
# Runs inside the LibreLane rootfs (~/llr/ll.sh; ~/llr is /h2 there, the PDK is /root/.ciel/ihp-sg13g2).
B=$1;N=$2;O=$3;T=/root/.ciel/ihp-sg13g2/libs.tech/klayout/tech
mkdir -p ~/llr/$O && cp $B ~/llr/$O/bits.txt && cp romgen_sg13g2.py ~/llr/$O/
cd ~/llr && ./ll.sh sh -c "cd /h2/$O && klayout -b -r romgen_sg13g2.py -rd bits=bits.txt -rd out=$N -rd name=$N > gen.log 2>&1 &&
  python3 $T/drc/run_drc.py --path=$N.gds --topcell=$N --run_dir=drc --no_density --run_mode=flat > drc.log 2>&1;
  PYTHONPATH=/h2/pylib python3 $T/lvs/run_lvs.py --layout=$N.gds --netlist=$N.spice --topcell=$N --run_dir=lvs --run_mode=flat > lvs.log 2>&1"
tail -1 ~/llr/$O/gen.log; grep -i -E "total|violation|error" ~/llr/$O/drc.log | tail -3; grep -i -E "match|error" ~/llr/$O/lvs.log | tail -3
