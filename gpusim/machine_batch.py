#!/usr/bin/env python3
"""Many prompts through the actual whole-machine graph at once on the GPU (levelized kernel).

Each lane is one host session of machine.py's interface: reset, L tokens via tvalid, start with
sample/random held, then the graph runs on its own; after `clocks` the latched token (outputs 0..7)
and done (output 8) of every lane are compared with C int_pick(int_run logits of the last position).
usage: machine_batch.py <machine.nl> <words> <clocks> <seed> [L]
"""
import sys,json,time,random,tempfile,ctypes as ct
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;sys.path[:0]=[str(HERE),str(ROOT),str(ROOT/'integer_opt'),str(ROOT/'physical')]
import levelized
from golden import Netlist


def cases(n,seed,L):
    import final_a8 as fa
    rng=random.Random(seed);cs=[]
    with tempfile.TemporaryDirectory() as t:
        g=fa.golden_lib(t);g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
        for k in range(n):
            toks=[(k+j*37)%192 if j==0 else rng.randrange(192) for j in range(L)];s=k%2;r=rng.getrandbits(32)
            lg=(ct.c_int32*(L*192))();assert g.int_run((ct.c_int32*L)(*toks),L,lg,None)==0
            cs.append(dict(tokens=toks,sample=s,random=r,token=g.int_pick((ct.c_int32*192)(*lg[(L-1)*192:L*192]),r,s)))
    return cs


def stimulus(cs,W,L):
    n_in=44;inp=np.zeros((L+3,n_in,W),dtype=np.uint32)
    def setbit(t,i,lane,v):
        if v:inp[t,i,lane//32]|=np.uint32(1<<(lane%32))
    for lane,c in enumerate(cs):
        setbit(0,0,lane,1)                                   # reset
        for j,tk in enumerate(c['tokens']):
            setbit(1+j,2,lane,1)
            for b in range(8):setbit(1+j,3+b,lane,tk>>b&1)
        for t in range(1+L,L+3):
            setbit(t,1,lane,t==1+L)                          # start pulse, then hold sample/random
            setbit(t,11,lane,c['sample'])
            for b in range(32):setbit(t,12+b,lane,c['random']>>b&1)
    return inp


if __name__=='__main__':
    path,W,T,seed=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4]);L=int(sys.argv[5]) if len(sys.argv)>5 else 1
    net=Netlist.decode(Path(path).read_bytes(),44,15);n=32*W;cs=cases(n,seed,L);inp=stimulus(cs,W,L)
    t0=time.time();last=[0]
    def prog(t,k):
        if time.time()-last[0]>600:last[0]=time.time();print(json.dumps(dict(clock=t,kernel_s=round(k),eta_h=round((T-t)/max(t/k,1e-9)/3600,2))),flush=True)
    out,st,tm=levelized.GPU().run(net,inp,T,clocks_per_dispatch=100,progress=prog)
    bit=lambda o,lane:int(out[o,lane//32])>>(lane%32)&1
    got=[sum(bit(o,lane)<<o for o in range(8)) for lane in range(n)];done=[bit(8,lane) for lane in range(n)]
    bad=[lane for lane in range(n) if not done[lane] or got[lane]!=cs[lane]['token']]
    res=dict(netlist=path,lanes=n,L=L,clocks=T,seed=seed,all_done=all(done),mismatches=len(bad),first_bad=[dict(lane=b,got=got[b],want=cs[b]['token'],done=done[b]) for b in bad[:5]],
        distinct_prompts=len({(tuple(c['tokens']),c['sample'],c['random']) for c in cs}),sampled=sum(c['sample'] for c in cs),kernel_s=round(tm['kernel_s']),levels=tm['levels'])
    print(json.dumps(res),flush=True)
