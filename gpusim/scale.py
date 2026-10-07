#!/usr/bin/env python3
"""Throughput vs number of u32 words (32 lanes each) for GPU and the CPU reference. m149 only."""
from pathlib import Path
import json,sys,time
import numpy as np
HERE=Path(__file__).resolve().parent;sys.path[:0]=[str(HERE),str(HERE.parent)]
import gpusim as G
from check import CASES
from golden import Netlist
VEC=int(sys.argv[4]) if len(sys.argv)>4 else 1
gpu=G.GPU(VEC);lib=G.ref_lib();rng=np.random.default_rng(7);res={}
for name in sys.argv[1].split(','):
    f,ni,no,_=CASES[name];net=Netlist.decode((HERE/f).read_bytes(),ni,no);nand=len(net.records)-net.n_state
    nw=2+ni+len(net.records);rows=[]
    for W in [int(x) for x in sys.argv[2].split(',')]:
        if 4*nw*W>2.1e9:continue
        T=int(sys.argv[3]) if len(sys.argv)>3 else 16
        inp=rng.integers(0,2**32,size=(T,ni,W),dtype=np.uint32)
        out,st,tm=gpu.run(net,inp);rout,rst,rs=G.ref_run(lib,net,inp)
        assert np.array_equal(out,rout) and np.array_equal(st,rst)
        work=nand*T*32*W
        row=dict(vec=VEC,words=W,lanes=32*W,clocks=T,gpu_kernel_G=work/tm['kernel_s']/1e9,gpu_e2e_G=work/tm['total_s']/1e9,cpu_ref_G=work/rs/1e9,gpu=tm,cpu_s=rs)
        rows.append(row);print(name,W,round(row['gpu_kernel_G'],1),round(row['gpu_e2e_G'],1),round(row['cpu_ref_G'],1),flush=True)
    res[name]=dict(nand=nand,records=len(net.records),rows=rows)
(HERE/'build').mkdir(exist_ok=True);(HERE/'build'/('scale_'+sys.argv[1].replace(',','_')+'_v%d.json'%VEC)).write_text(json.dumps(res,indent=2)+'\n')
