#!/bin/sh
# check_sram.sh <rows> <cols> <name> [gen]: generate one 8T array and run Magic DRC, KLayout DRC, Magic extraction +
# netgen LVS (m149, work dir ~/llr/sram/<name>). gen = generator basename (default sramgen_sky130). tp-2ea.48.2
set -e
RW=$1; CL=$2; N=$3; G=${4:-sramgen_sky130}; H=$HOME/llr; X=/h2/sram/$N
P=/root/.ciel/ciel/sky130/versions/8afc8346a57fe1ab7934ba5a6056ea8b43078e71/sky130A
mkdir -p $H/sram/$N; cp $H/sram/$G.py $H/sram/$N/
sh $H/ll.sh klayout -b -r $X/$G.py -rd rows=$RW -rd cols=$CL -rd out=$X/$N -rd name=$N | tail -1
cat > $H/sram/$N/drc.tcl <<T
gds read $X/$N.gds
load $N
select top cell
drc euclidean on
drc style drc(full)
drc check
drc catchup
set r [drc listall why]
set n 0
foreach {why boxes} \$r { puts "DRC: [llength \$boxes] :: \$why :: [lrange \$boxes 0 1]"; incr n [llength \$boxes] }
puts "DRCTOTAL \$n"
quit -noprompt
T
cat > $H/sram/$N/lvsx.tcl <<T
gds read $X/$N.gds
load $N
select top cell
extract do local
extract all
ext2spice lvs
ext2spice -o $X/${N}_ext.spice
quit -noprompt
T
sh $H/ll.sh sh -c "cd $X && magic -dnull -noconsole -T $P/libs.tech/magic/sky130A.tech drc.tcl" 2>&1 | grep -E "^DRC" | sort | uniq -c | sort -rn | head -30
sh $H/ll.sh sh -c "cd $X && klayout -b -r $P/libs.tech/klayout/drc/sky130A_mr.drc -rd input=$X/$N.gds -rd topcell=$N -rd report=$X/${N}_kl.lyrdb -rd feol=true -rd beol=true -rd offgrid=true" > $H/sram/$N/kl.log 2>&1 || true
echo "KLAYOUT_ITEMS $(grep -c '<item>' $H/sram/$N/${N}_kl.lyrdb)"; grep -o '<category>[^<]*</category>' $H/sram/$N/${N}_kl.lyrdb | sort | uniq -c | head
sh $H/ll.sh sh -c "cd $X && magic -dnull -noconsole -T $P/libs.tech/magic/sky130A.tech lvsx.tcl" > $H/sram/$N/ext.log 2>&1
sh $H/ll.sh sh -c "cd $X && netgen -batch lvs \"${N}_ext.spice $N\" \"$N.spice $N\" $P/libs.tech/netgen/sky130A_setup.tcl ${N}_lvs.out" > $H/sram/$N/netgen.log 2>&1 || true
echo "LVS $(grep 'Final result' $H/sram/$N/${N}_lvs.out | tail -1)"
