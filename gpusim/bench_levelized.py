#!/usr/bin/env python3
"""Speed of levelized.GPU on a netlist file: clocks/s and lane-clocks/s for several word counts."""
import sys,json,time
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent;sys.path[:0]=[str(HERE),str(HERE.parent)]
import levelized
from golden import Netlist
path,ni,no=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]);net=Netlist.decode(Path(path).read_bytes(),ni,no)
gpu=levelized.GPU();res=[]
for W in [int(x) for x in sys.argv[4].split(',')]:
    T=int(sys.argv[5]);inp=np.zeros((1,ni,W),dtype=np.uint32)
    out,st,tm=gpu.run(net,inp,T,clocks_per_dispatch=int(sys.argv[6]) if len(sys.argv)>6 else 200)
    res.append(dict(W=W,lanes=32*W,clocks=T,levels=tm['levels'],clocks_per_s=round(tm['clocks_per_s'],1),lane_clocks_per_s=round(32*W*tm['clocks_per_s'])))
    print(json.dumps(res[-1]),flush=True)
