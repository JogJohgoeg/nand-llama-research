#!/bin/sh
# check_sram_sg13g2.sh <rows> <cols> <name>: generate an SG13G2 8T array and run the IHP KLayout DRC (no density) and
# LVS against the generated schematic (m149, LibreLane rootfs, work dir ~/llr/sram/<name>). tp-2ea.48
RW=$1;CL=$2;N=$3;O=sram/$N;T=/root/.ciel/ihp-sg13g2/libs.tech/klayout/tech
mkdir -p ~/llr/$O && cp ~/llr/sram/sramgen_sg13g2.py ~/llr/$O/
cd ~/llr && ./ll.sh sh -c "cd /h2/$O && klayout -b -r sramgen_sg13g2.py -rd rows=$RW -rd cols=$CL -rd out=$N -rd name=$N > gen.log 2>&1 &&
  python3 $T/drc/run_drc.py --path=$N.gds --topcell=$N --run_dir=drc --no_density --run_mode=flat > drc.log 2>&1;
  PYTHONPATH=/h2/pylib python3 $T/lvs/run_lvs.py --layout=$N.gds --netlist=$N.spice --topcell=$N --run_dir=lvs --run_mode=flat > lvs.log 2>&1"
tail -1 ~/llr/$O/gen.log; grep -i -E "total|violation|error" ~/llr/$O/drc.log | tail -3
for f in ~/llr/$O/drc/*.lyrdb; do python3 - "$f" <<'P'
import re,sys,collections
t=open(sys.argv[1]).read();c=collections.Counter(re.findall(r"<item>\s*<tags/>\s*<category>'?([^<']+)'?</category>",t))
print('DRC items',sum(c.values()),dict(c.most_common(20)))
P
done
grep -i -E "match|error" ~/llr/$O/lvs.log | tail -3
