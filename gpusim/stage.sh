#!/bin/sh
# Build the small m149 work tree (scripts + golden.py + the four accepted netlists/vectors), rsync, run.
# Usage (from the main tapeout repo root): sh circuits/_scratch/tapeout_asic/nand-llama-research/gpusim/stage.sh <stage_dir>
set -e
A=circuits/_scratch/tapeout_asic;R=$A/nand-llama-research;C=$A/h3/ci;S=$1
mkdir -p $S/gpusim/nets
cp $R/golden.py $S/;cp $R/gpusim/*.py $R/gpusim/ref.c $R/gpusim/vlt_main.cpp $R/gpusim/bench_verilator.sh $S/gpusim/
cp $C/qmatrix_ring_37538437313/word/slice.nl $S/gpusim/nets/qmatrix_ring_word.nl
cp $C/norm_cache_root_37550016136/fill.nl $S/gpusim/nets/norm_cache_R92.nl
cp $C/sampler_37569137194/sampler.nl $S/gpusim/nets/sampler_R112.nl
cp $C/sampler_37569137194/vectors.txt $S/gpusim/nets/sampler_R112.vectors.txt
cp $C/x_head_s_37572617795/joint.nl $S/gpusim/nets/x_head_R118.nl
head -200000 $C/x_head_s_37572617795/vectors.txt > $S/gpusim/nets/x_head_R118.vectors.txt
# Verilator inputs are generated text (export.rtl), not simulated locally:
python3 - "$S" <<'PY'
import sys;sys.path[:0]=['circuits/_scratch/tapeout_asic/nand-llama-research/physical','circuits/_scratch/tapeout_asic/nand-llama-research']
from export import rtl;from golden import Netlist
S=sys.argv[1]
for n,i,o in [('sampler_R112',76,12),('qmatrix_ring_word',33,32),('norm_cache_R92',40,40),('x_head_R118',56,19)]:
    open(f'{S}/gpusim/nets/{n}.v','w').write(rtl(Netlist.decode(open(f'{S}/gpusim/nets/{n}.nl','rb').read(),i,o),'dut'))
PY
rsync -a $S/ m149:~/gpusim_work/
echo "on m149: python3 -m venv ~/.tapeout_venvs/gpusim && pip install wgpu numpy; cd ~/gpusim_work/gpusim; python check.py; python scale.py <nets> <words> <clocks> [vec]; ./bench_verilator.sh <net> <bits> <clocks> 1 4 16"
