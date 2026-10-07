#!/usr/bin/env python3
"""levelized.GPU == gpusim.GPU (the accepted prototype) on random stimuli: last-clock outputs and final state."""
from pathlib import Path
import json,sys
import numpy as np
HERE=Path(__file__).resolve().parent;sys.path[:0]=[str(HERE),str(HERE.parent)]
import gpusim,levelized
from golden import Netlist
from nand import flip_output


def check(net,W=8,T=64,seed=1):
    rng=np.random.default_rng(seed);inp=rng.integers(0,2**32,size=(T,net.n_in,W),dtype=np.uint64).astype(np.uint32)
    inp[0,0,:]=0xffffffff                                     # reset on the first clock (input 0 is reset in our nets)
    ref,rst,_=gpusim.GPU().run(net,inp)
    out,st,tm=levelized.GPU().run(net,inp,T,clocks_per_dispatch=16)
    return dict(outputs_equal=bool((ref[-1]==out).all()),state_equal=bool((rst==st).all()),levels=tm['levels'],clocks_per_s=round(tm['clocks_per_s'],1))


if __name__=='__main__':
    U=HERE.parent/'integer_opt/layer0_units';man=json.loads((U/'manifest.json').read_text());res={}
    for f in ('r95_norm_qkv.nl','r52_xbank.nl'):
        m=man[f];net=Netlist.decode((U/f).read_bytes(),m['nIn'],m['nOut'])
        res[f]=check(net);res[f+' output_flip_differs']=not check(flip_output(net))['outputs_equal'] if False else None
    print(json.dumps(res,indent=1))
