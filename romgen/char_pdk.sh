#!/bin/sh
# char_pdk.sh <sg13g2|gf180> <bank bits.txt>: Liberty of the ROM array macro for a new PDK (tp-2ea.46.4).
# Worst-case path: bank bits with bitline 63 programmed on every word (largest bitline load) and word 511 = only
# bit 63 (so chartb measures BL[63]); generate, Magic extraction with all caps, reduce.py to that path, then
# ngspice over 3 corners x bitline loads 2/20/40/60/100 fF -> sweep.txt -> romlib.py load-table Liberty per corner.
# Work dir ~/llr/romsg/char_<pdk>; host ngspice (SG13G2 PSP103 via the OSDI built in ~/llr/osdi_sg13).
P=$1;B=$2;D=romsg/char_$P;W=~/llr/$D;S=$(cd $(dirname $0);pwd)
mkdir -p $W && cp $S/romgen_$P.py $W/
if [ -z "$LIBONLY" ]; then
python3 - $B $W/bits.txt <<'PY'
import sys
r=[l.strip() for l in open(sys.argv[1]) if l.strip()]
r=[x[:63]+'1' for x in r];r[511]='0'*63+'1';open(sys.argv[2],'w').write('\n'.join(r)+'\n')
PY
cd ~/llr && ./ll.sh sh -c "cd /h2/$D && klayout -b -r romgen_$P.py -rd bits=bits.txt -rd out=rom_char -rd name=rom_char > gen.log 2>&1"
sh $S/pex_pdk.sh $P $D rom_char > /dev/null
python3 $S/reduce.py $W/rom_char_pex.spice 511 63 > $W/red.spice
fi
cd $W
M=/home/joj/llr/ciel
case $P in
 sg13g2) LIB=$M/ihp-sg13g2/libs.tech/ngspice/models/cornerMOSlv.lib; INC=
   CORN="tt:mos_tt:1.2:27:nom_tt_025C_1v20 ss:mos_ss:1.08:125:max_ss_125C_1v08 ff:mos_ff:1.32:-40:min_ff_n40C_1v32"
   for o in psp103 psp103_nqs r3_cmc mosvar; do echo "osdi /home/joj/llr/osdi_sg13/$o.osdi"; done > .spiceinit;;
 gf180) LIB=$M/gf180mcuD/libs.tech/ngspice/sm141064.ngspice; INC=$M/gf180mcuD/libs.tech/ngspice/design.ngspice
   CORN="tt:typical:3.3:25:nom_tt_025C_3v30 ss:ss:3.0:125:max_ss_125C_3v00 ff:ff:3.6:-40:min_ff_n40C_3v60";;
esac
if [ -z "$LIBONLY" ]; then         # LIBONLY=1: rebuild the .lib files from an existing sweep.txt
: > sweep.txt
for c in $CORN; do
  k=${c%%:*};r=${c#*:};sec=${r%%:*};r=${r#*:};v=${r%%:*};r=${r#*:};t=${r%%:*}
  for cl in 2 20 40 60 100; do
    python3 $S/chartb.py red.spice bits.txt $LIB $sec $v $t 511 $cl > d_${k}_$cl.sp
    [ -n "$INC" ] && sed -i "2i .include \"$INC\"" d_${k}_$cl.sp
    ngspice -b d_${k}_$cl.sp > d_${k}_$cl.log 2>&1
    echo "cl=$cl $k $(grep -E '^(d_read|s_fall|d_pre|s_rise|q_wl|q_pre) *=' d_${k}_$cl.log | awk '{printf "%s=%s ",$1,$3}')" >> sweep.txt
  done
done
fi
for c in $CORN; do
  k=${c%%:*};r=${c#*:};r=${r#*:};v=${r%%:*};r=${r#*:};t=${r%%:*};name=${r#*:}
  qwl=$(awk -v k=$k '$1=="cl=2"&&$2==k' sweep.txt | grep -o 'q_wl=[^ ]*' | head -1 | cut -d= -f2)
  qpre=$(awk -v k=$k '$1=="cl=2"&&$2==k' sweep.txt | grep -o 'q_pre=[^ ]*' | head -1 | cut -d= -f2)
  cwl=$(python3 -c "print(abs($qwl)/$v*1e12)");cpre=$(python3 -c "print(abs($qpre)/$v*1e12)")
  python3 $S/romlib.py rom_bank_XX $name $v $t --sweep sweep.txt $k $cwl $cpre > rom_bank_XX__$name.lib
done
cat sweep.txt; ls *.lib
