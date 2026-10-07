#!/usr/bin/env python3
"""Correctness + speed on m149 only.

For each netlist: (1) random independent lanes, every clock and the final state
GPU == CPU reference; (2) the first 64 lanes also == official golden.step_simd for
a few clocks; (3) an actual gate mutation (first output gate flipped) is caught;
(4) where an accepted C vector file exists, a prefix replays bit-exactly under its
mask. Speed: gate*clock*lane per second for GPU, the 64-bit OpenMP reference and
golden.step_simd; Verilator is measured separately by bench_verilator.py.
"""
from pathlib import Path
import argparse,hashlib,json,os,sys,time
import numpy as np
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent)]
import gpusim as G
from golden import Netlist

CASES={  # name: (file, n_in, n_out, vectors file or None)
 'q_matrix_ring_word':('nets/qmatrix_ring_word.nl',33,32,None),
 'norm_cache_R92':('nets/norm_cache_R92.nl',40,40,None),
 'sampler_R112':('nets/sampler_R112.nl',76,12,'nets/sampler_R112.vectors.txt'),
 'x_head_R118':('nets/x_head_R118.nl',56,19,'nets/x_head_R118.vectors.txt'),
}
sha=lambda b:hashlib.sha256(b).hexdigest()


def flip_first_output(net):
    recs=list(net.records);i=len(recs)-net.n_out;op,a,b=recs[i];assert op==0 and a==b
    inv=recs[a-net.n_in-2];assert inv[0]==0 and inv[1]==inv[2]
    recs[i]=(0,inv[1],inv[1]);return Netlist(net.n_in,net.n_out,recs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--only');ap.add_argument('--words',type=int,default=2048);ap.add_argument('--clocks',type=int,default=64)
    ap.add_argument('--replay',type=int,default=20000);a=ap.parse_args()
    gpu=G.GPU();lib=G.ref_lib();rng=np.random.default_rng(26100741);report={'adapter':str(gpu.adapter.info)}
    for name,(f,ni,no,vec) in CASES.items():
        if a.only and name!=a.only:continue
        raw=(HERE/f).read_bytes();net=Netlist.decode(raw,ni,no);n_rec=len(net.records);n_lat=net.n_state
        r=dict(sha256=sha(raw),records=n_rec,latches=n_lat,nand=n_rec-n_lat)
        W,T=a.words,a.clocks
        inp=rng.integers(0,2**32,size=(T,ni,W),dtype=np.uint32)
        out,st,tm=G.GPU.run(gpu,net,inp)
        rout,rst,rs=G.ref_run(lib,net,inp)
        r['random']=dict(words=W,lanes=32*W,clocks=T,outputs_equal=bool(np.array_equal(out,rout)),state_equal=bool(np.array_equal(st,rst)),gpu=tm,ref_seconds=rs)
        assert r['random']['outputs_equal'] and r['random']['state_equal'],name
        # Official interpreter on the first 64 lanes for 6 clocks.
        go,gs=G.golden_lanes(net,inp[:6],64)
        gpu_lanes=[G.lane_bits(out,t,64) for t in range(6)]
        r['golden_64_lanes_6_clocks']=bool(gpu_lanes==go)
        assert r['golden_64_lanes_6_clocks'],name
        bad=flip_first_output(net);bo,_,_=G.GPU.run(gpu,bad,inp[:4])
        r['mutation_caught']=bool(not np.array_equal(bo,rout[:4]));assert r['mutation_caught']
        # Throughput: gate evaluations (NAND records) * clocks * lanes / kernel seconds.
        work=r['nand']*T*32*W
        r['speed']=dict(gpu_kernel_glane_per_s=work/tm['kernel_s']/1e9,gpu_end_to_end_glane_per_s=work/tm['total_s']/1e9,
                        cpu_ref_glane_per_s=work/rs/1e9)
        t=time.perf_counter();G.golden_lanes(net,inp[:2],64);gs_t=time.perf_counter()-t
        r['speed']['golden_step_simd_glane_per_s']=r['nand']*2*64/gs_t/1e9
        if vec:
            rows=[tuple(int(w,16) for w in l.split()) for l in (HERE/vec).read_text().splitlines()[:a.replay]]
            T2=len(rows);x=np.zeros((T2,ni,1),dtype=np.uint32)
            for t,(xi,_,_) in enumerate(rows):
                for i in range(ni):
                    if xi>>i&1:x[t,i,0]=0xffffffff
            o2,_,tm2=G.GPU.run(gpu,net,x,clocks_per_dispatch=2000)
            bad_rows=0
            for t,(_,y,m) in enumerate(rows):
                got=0
                for o in range(no):
                    v=int(o2[t,o,0]);assert v in (0,0xffffffff)
                    got|=(v&1)<<o
                bad_rows+=bool((got^y)&m)
            r['accepted_vectors']=dict(file=Path(vec).name,clocks=T2,mismatches=bad_rows,gpu=tm2)
            assert bad_rows==0,name
        report[name]=r;print(name,json.dumps(r)[:600],flush=True)
    (HERE/'build').mkdir(exist_ok=True);(HERE/'build'/('check'+('_'+a.only if a.only else '')+'.json')).write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
