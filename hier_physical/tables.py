#!/usr/bin/env python3
"""Find constant-table logic in a NAND/LATCH graph: NAND gates whose support (transitive
fan-in up to latches/inputs) is at most S signals. Such cones are ROM-like: wire-bound when
placed flat (R118 E8 table: 42k gates on 9 latches routes only at ~8-10%), but route at
50-60% once cut into ~2k-gate sub-table macros (h3/HIER.md). Pure python, no EDA.

Groups gates by their exact support set; reports every group above --min gates.
"""
from pathlib import Path
import argparse,json,sys
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
from golden import Netlist


def tables(net,S=16):
    base=2+net.n_in;R=net.records;sup=[None]*len(R)
    BIG=None
    def s(w):
        if w<2:return frozenset()
        if w<base:return frozenset([w])
        g=R[w-base]
        return frozenset([w]) if g[0]==1 else sup[w-base]
    for i,g in enumerate(R):
        if g[0]==1:continue
        a,b=s(g[1]),s(g[2])
        sup[i]=BIG if a is BIG or b is BIG or len(a|b)>S else a|b
    groups=defaultdict(int)
    for i,g in enumerate(R):
        if g[0]==0 and sup[i] is not None:groups[sup[i]]+=1
    return groups,base


def merge(groups,base,net,minimum):
    """Merge each support set into the largest superset group (a table's sub-cones have smaller supports)."""
    big=sorted(groups,key=len,reverse=True);owner={}
    for g in big:
        if g in owner:continue
        owner[g]=g
        for h in big:
            if h not in owner and h<=g:owner[h]=g
    tot=defaultdict(int)
    for g,n in groups.items():tot[owner[g]]+=n
    def name(w):return f'in{w-2}' if w<base else f'L{w-base}'
    out=[dict(support=len(g),gates=n,signals=[name(w) for w in sorted(g)][:24]) for g,n in sorted(tot.items(),key=lambda x:-x[1]) if n>=minimum]
    return out


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('netlist',type=Path);ap.add_argument('--ni',type=int,required=True);ap.add_argument('--no',type=int,required=True)
    ap.add_argument('--support',type=int,default=16);ap.add_argument('--min',type=int,default=500);ap.add_argument('--json',type=Path)
    a=ap.parse_args();net=Netlist.decode(a.netlist.read_bytes(),a.ni,a.no)
    groups,base=tables(net,a.support);res=merge(groups,base,net,a.min)
    nand=sum(g[0]==0 for g in net.records);inside=sum(r['gates'] for r in res)
    rep=dict(nand=nand,latch=net.n_state,support_cap=a.support,table_nand=inside,table_share=round(inside/nand,4),tables=res)
    if a.json:a.json.write_text(json.dumps(rep,indent=2)+'\n')
    print(json.dumps(dict(rep,tables=[{k:v for k,v in t.items() if k!='signals'} for t in res[:20]]),indent=1))
