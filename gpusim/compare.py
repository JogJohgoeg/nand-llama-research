#!/usr/bin/env python3
"""GPU lockstep comparison of two netlists with the same interface (m149 only).

Both graphs start from reset state zero and receive identical random input streams
on every lane; every output bit of every clock must agree. Random streams are biased
so that protocols actually progress (rare reset, frequent start/valid). Used here to
compare the R120 narrowed x -> token head against the accepted R118 head.
"""
from pathlib import Path
import argparse,hashlib,json,sys,time
import numpy as np
HERE=Path(__file__).resolve().parent;sys.path[:0]=[str(HERE),str(HERE.parent)]
import gpusim as G
from golden import Netlist


def bits(rng,shape,p):
    """uint32 words whose bits are 1 with probability p."""
    return np.packbits((rng.random(shape+(32,))<p).astype(np.uint8),axis=-1,bitorder='little').view(np.uint32).reshape(shape)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('a');ap.add_argument('b');ap.add_argument('--ni',type=int);ap.add_argument('--no',type=int)
    ap.add_argument('--words',type=int,default=256);ap.add_argument('--clocks',type=int,default=60000);ap.add_argument('--chunk',type=int,default=2000)
    ap.add_argument('--bias',default='{}');ap.add_argument('--done-bit',dest='done_bit',type=int);ap.add_argument('--out');a=ap.parse_args()
    na=Netlist.decode(Path(a.a).read_bytes(),a.ni,a.no);nb=Netlist.decode(Path(a.b).read_bytes(),a.ni,a.no)
    bias={int(k):v for k,v in json.loads(a.bias).items()}  # input index -> probability of 1
    gpu=G.GPU();rng=np.random.default_rng(26100720);W=a.words
    done_bit=a.done_bit;dones=0;sa=sb=None;mism=0;first=None;t0=time.perf_counter();kernel=0.0;h=hashlib.sha256()
    for t in range(0,a.clocks,a.chunk):
        n=min(a.chunk,a.clocks-t)
        x=rng.integers(0,2**32,size=(n,a.ni,W),dtype=np.uint32)
        for i,p in bias.items():x[:,i,:]=bits(rng,(n,W),p)
        oa,sa,ta=gpu.run(na,x,state=sa);ob,sb,tb=gpu.run(nb,x,state=sb);kernel+=ta['kernel_s']+tb['kernel_s']
        d=np.nonzero((oa!=ob).any(axis=(1,2)))[0]
        if len(d) and first is None:first=int(t+d[0])
        mism+=len(d);h.update(oa.tobytes())
        if done_bit is not None:
            dv=oa[:,done_bit,:];prev=np.concatenate([last[None] if t else np.zeros((1,W),np.uint32),dv[:-1]]) if t else np.concatenate([np.zeros((1,W),np.uint32),dv[:-1]])
            dones+=int(sum(bin(int(v)).count('1') for v in np.bitwise_and(dv,~prev).ravel() if v));last=dv[-1]
    r=dict(a=Path(a.a).name,b=Path(a.b).name,sha_a=hashlib.sha256(Path(a.a).read_bytes()).hexdigest(),sha_b=hashlib.sha256(Path(a.b).read_bytes()).hexdigest(),
           lanes=32*W,clocks=a.clocks,bias=bias,done_rising_edges=dones if done_bit is not None else None,mismatching_clocks=mism,first_mismatch_clock=first,output_stream_sha256=h.hexdigest(),
           gpu_kernel_s=kernel,wall_s=time.perf_counter()-t0,lane_clocks=32*W*a.clocks)
    print(json.dumps(r));Path(a.out).write_text(json.dumps(r,indent=2)+'\n') if a.out else None


if __name__=='__main__':main()
