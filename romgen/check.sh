#!/bin/sh
# check.sh <bits.txt> <name> [outdir]: generate one ROM array and run the full check set (m149, in ~/llr/romx)
# Magic DRC, KLayout sky130A_mr DRC, Magic extract + netgen LVS vs reference, ext2bits on the extraction.
set -e
B=$1; N=$2; O=${3:-out}; RX=${RX:-romx}; H=$HOME/llr; X=/h2/$RX; P=/root/.ciel/ciel/sky130/versions/8afc8346a57fe1ab7934ba5a6056ea8b43078e71/sky130A
cd $H/$RX; mkdir -p $O
sh $H/ll.sh klayout -b -r $X/romgen.py -rd bits=$X/$B -rd out=$X/$O/$N -rd name=$N | tail -1
for t in drc lvsx; do sed "s#/h2/romx/p64.gds#$X/$O/$N.gds#; s#rom_array_64x64#$N#g; s#/h2/romx/p64_ext.spice#$X/$O/${N}_ext.spice#" $t.tcl > $O/${N}_$t.tcl; done
sh $H/ll.sh sh -c "cd $X/$O && magic -dnull -noconsole -T $P/libs.tech/magic/sky130A.tech ${N}_drc.tcl" 2>&1 | grep DRCTOTAL
sh $H/ll.sh sh -c "cd $X/$O && klayout -b -r $P/libs.tech/klayout/drc/sky130A_mr.drc -rd input=$X/$O/$N.gds -rd topcell=$N -rd report=$X/$O/${N}_kl.lyrdb -rd feol=true -rd beol=true -rd offgrid=true" > $O/${N}_kl.log 2>&1
echo "KLAYOUT_ITEMS $(grep -c '<item>' $O/${N}_kl.lyrdb)"
sh $H/ll.sh sh -c "cd $X/$O && magic -dnull -noconsole -T $P/libs.tech/magic/sky130A.tech ${N}_lvsx.tcl" > $O/${N}_ext.log 2>&1
sh $H/ll.sh sh -c "cd $X/$O && netgen -batch lvs \"${N}_ext.spice $N\" \"$N.spice $N\" $P/libs.tech/netgen/sky130A_setup.tcl ${N}_lvs.out" > $O/${N}_netgen.log 2>&1
echo "LVS $(grep 'Final result' $O/${N}_lvs.out | tail -1)"
python3 ext2bits.py $O/${N}_ext.spice $B
