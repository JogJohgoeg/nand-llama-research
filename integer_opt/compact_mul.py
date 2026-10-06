#!/usr/bin/env python3
"""Exact signed20 x unsigned17 product using one37-bit shifting register.

Input x is supplied by the stable KV cache throughout the17 step pulses.
No local EDA. An explicit step pin holds the product after completion.
"""
from pathlib import Path
import os,sys,random,hashlib
R=Path(os.environ['H3_COMPACT_VALUE_ROOT']) if 'H3_COMPACT_VALUE_ROOT' in os.environ else Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state,flip_output
from gate_check import Snapshot
MASK=(1<<37)-1


def make():
 b=Builder(76);old=list(range(2,39));pins=list(range(39,78));load,step=pins[:2];x=pins[2:22];y=pins[22:]
 high=old[17:]+[old[36]]
 summand=[b.land(old[0],v) for v in x+[x[-1]]]
 added=b.add(high,summand)[0];shifted=old[1:17]+added
 ds=[b.mux(step,a,c) for a,c in zip(old,shifted)]
 ds=[b.mux(load,a,c) for a,c in zip(ds,y+[0]*20)]
 comb=b.finish(ds+old);net=with_state(comb,37)
 return net,comb


def advance(p,load,step,x,y):
 if load:return y
 if not step:return p
 signed=p-(1<<37) if p>>36 else p
 return ((signed+((x<<17) if p&1 else 0))>>1)&MASK


def small_check():
 net,comb=make();assert metrics(net)['nNand']+net.n_state<=4000
 rng=random.Random(260731);xs=[];ys=[]
 for j in range(4096):
  p=rng.getrandbits(37);x=rng.randint(-524288,524287);y=rng.randrange(131072);load=j&1;step=j>>1&1
  pins=load+(step<<1)+((x&1048575)<<2)+(y<<22)
  xs.append(p+(pins<<37));ys.append(advance(p,load,step,x,y)+(p<<37))
 proof=verify_state(comb,xs,ys,37);proof.pop('nl_hex')
 cases=[(x,y) for x in (-524288,-524287,-1,0,1,524286,524287) for y in (0,1,2,65535,65536,131070,131071)]
 cases += [(rng.randint(-524288,524287),rng.randrange(131072)) for _ in range(207)]
 rows=[];steps=stalls=0
 for x,y in cases:
  rows.append((1+((x&1048575)<<2)+(y<<22),0,0))
  for _ in range(17):
   if rng.randrange(3)==0:
    rows.append(((rng.getrandbits(20)<<2)+(rng.getrandbits(17)<<22),0,0));stalls+=1
   rows.append((2+((x&1048575)<<2)+(rng.getrandbits(17)<<22),0,0));steps+=1
  for _ in range(3):rows.append(((rng.getrandbits(20)<<2)+(rng.getrandbits(17)<<22),(x*y)&MASK,MASK))
 def check(graph):
  g=Snapshot.decode(graph.encode(),graph.n_in,graph.n_out);state=bytes(37);wrong=0
  for x,y,m in rows:
   state,out=g.step(state,bytes(x>>j&1 for j in range(39)))
   wrong+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&m))
  return wrong
 assert check(net)==0;bad=check(flip_output(net));assert bad==3*len(cases)
 return dict(metrics=metrics(net),arbitrary_state=proof,products=len(cases),step_pulses=steps,clocks=len(rows),stall_clocks=stalls,
             actual_product_gate_mutation_mismatches=bad,
             bounds='-524288 <= x <= 524287, 0 <= y <= 131071, product fits signed37; x stable only on17 step pulses')
