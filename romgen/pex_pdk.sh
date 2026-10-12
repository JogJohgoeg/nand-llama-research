#!/bin/sh
# pex_pdk.sh <sg13g2|gf180> <dir under ~/llr> <cell>: Magic extraction with all parasitic caps of <dir>/<cell>.gds
# -> <dir>/<cell>_pex.spice (inside the LibreLane rootfs, PDK magic tech from /root/.ciel).
P=$1;D=$2;N=$3
case $P in sg13g2) RC=/root/.ciel/ihp-sg13g2/libs.tech/magic/ihp-sg13g2.magicrc;; gf180) RC="/dev/null -T /root/.ciel/gf180mcuD/libs.tech/magic/gf180mcuD.tech";; esac
cat > ~/llr/$D/pex_$N.tcl <<T
gds read /h2/$D/$N.gds
load $N
select top cell
extract do local
extract all
ext2spice cthresh 0
ext2spice subcircuit top on
ext2spice scale off
ext2spice -o /h2/$D/${N}_pex.spice
quit -noprompt
T
cd ~/llr && ./ll.sh sh -c "cd /h2/$D && magic -dnull -noconsole -rcfile $RC pex_$N.tcl > pex_$N.log 2>&1"
sed -i "s/ \*\*[Ff][Ll][Oo][Aa][Tt][Ii][Nn][Gg]//" ~/llr/$D/${N}_pex.spice
grep -c '^X\|^M' ~/llr/$D/${N}_pex.spice; grep -c '^C' ~/llr/$D/${N}_pex.spice
