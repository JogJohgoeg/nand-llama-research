#!/bin/sh
# regen_pdk.sh <sg13g2|gf180> <banks dir> <out under ~/llr> [jobs]: regenerate all 60 bank GDS/LEF/schematics with the current
# generator and run DRC on each (layout-only changes that do not touch the array); bank 00 also gets the full LVS.
P=$1;BD=$2;O=$3;J=${4:-8};S=$(cd $(dirname $0);pwd)
case $P in sg13g2) T=/root/.ciel/ihp-sg13g2/libs.tech/klayout/tech/drc/run_drc.py; DA="--no_density --run_mode=flat";;
           gf180) T=/root/.ciel/gf180mcuD/libs.tech/klayout/tech/drc/run_drc.py; DA="--variant=D --run_mode=flat";; esac
mkdir -p ~/llr/$O; : > ~/llr/$O/summary.txt
sh $S/check_$P.sh $BD/bank_00.txt rom_bank_00 $O/b00 > /dev/null 2>&1
seq -w 1 59 | xargs -P $J -I{} sh -c "mkdir -p ~/llr/$O/b{} && cp $BD/bank_{}.txt ~/llr/$O/b{}/bits.txt && cp $S/romgen_$P.py ~/llr/$O/b{}/ && \
  cd ~/llr && ./ll.sh sh -c 'export PATH=/h2/pylib/bin:\$PATH; cd /h2/$O/b{} && klayout -b -r romgen_$P.py -rd bits=bits.txt -rd out=rom_bank_{} -rd name=rom_bank_{} > gen.log 2>&1 && PYTHONPATH=/h2/pylib python3 $T --path=rom_bank_{}.gds --topcell=rom_bank_{} --run_dir=drc $DA > drc.log 2>&1'"
for d in ~/llr/$O/b*; do n=${d##*/b}; dr=$(grep -q -E 'Passed|is clean' $d/drc.log && echo clean || echo dirty)
  l=$( [ $n = 00 ] && (grep -q 'Netlists match' $d/lvs.log && echo match || echo FAIL) || echo n/a); echo "$n drc=$dr lvs=$l lef=$( [ -f $d/rom_bank_$n.lef ] && echo y || echo n)" >> ~/llr/$O/summary.txt; done
echo ALLDONE >> ~/llr/$O/summary.txt
