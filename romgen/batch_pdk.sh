#!/bin/sh
# batch_pdk.sh <sg13g2|gf180> <banks dir> <out under ~/llr> [jobs]: DRC + LVS of all 60 bank arrays for one PDK.
# Writes <out>/summary.txt: one line per bank "NN drc=<clean|dirty> lvs=<match|FAIL> size".
P=$1;BD=$2;O=$3;J=${4:-6}
seq -w 0 59 | xargs -P $J -I{} sh -c "sh check_$P.sh $BD/bank_{}.txt rom_bank_{} $O/b{} > /dev/null 2>&1; \
  d=\$(grep -q -E 'Passed|is clean' ~/llr/$O/b{}/drc.log && echo clean || echo dirty); \
  l=\$(grep -q 'Netlists match' ~/llr/$O/b{}/lvs.log && echo match || echo FAIL); \
  echo {} drc=\$d lvs=\$l \$(tail -1 ~/llr/$O/b{}/gen.log | cut -d' ' -f7-) >> ~/llr/$O/summary.txt"
echo ALLDONE >> ~/llr/$O/summary.txt
