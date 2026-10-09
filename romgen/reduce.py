#!/usr/bin/env python3
"""reduce.py <pex.spice> <word w> <bitline r> > reduced.spice
Path reduction of the extracted array for characterisation (the full 64x512 deck needs >30 GB in ngspice).
Kept nets: BL[r], WL[w], PRE_N, VPWR, VGND and the internal nodes of kept devices. Kept devices: every
transistor touching BL[r] (all its NOR cells, programmed or not, and its precharge pfet), every
transistor gated by WL[w] (its gate load), every pfet gated by PRE_N (its gate load). Any other net is
tied to ground: capacitors to it become capacitors to ground, device terminals on it go to node 0.
Same subckt name and ports as the input, so chartb.py decks run unchanged."""
import re,sys
pex,w,r=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
txt=open(pex).read().replace('\n+',' ').splitlines()
sub=[l for l in txt if l.startswith('.subckt')][0];ports=sub.split()[2:]
keep={'BL[%d]'%r,'WL[%d]'%w,'PRE_N','VPWR','VGND'}
dev=[l.split() for l in txt if l.startswith('X')]
kd=[p for p in dev if 'BL[%d]'%r in (p[1],p[3]) or p[2] in ('WL[%d]'%w,'PRE_N')]
for p in kd:keep.update(x for x in p[1:5] if x not in ('0',))
m=lambda x:x if x in keep else '0'
out=[sub]
for p in kd:out.append(' '.join([p[0]]+[m(x) for x in p[1:5]]+p[5:]))
cap={}
unit={'f':1e-15,'p':1e-12,'a':1e-18,'n':1e-9}
for l in txt:
    if not l.startswith('C'):continue
    p=l.split();a,b=m(p[1]),m(p[2])
    if a==b:continue
    v=p[3];val=float(v[:-1])*unit[v[-1]] if v[-1] in unit else float(v)
    k=tuple(sorted((a,b)));cap[k]=cap.get(k,0)+val   # parallel capacitors between the same two nets merge
for n,(k,v) in enumerate(sorted(cap.items())):out.append('CR%d %s %s %.6gf'%(n,k[0],k[1],v*1e15))
nc=len(cap)
out.append('.ends')
print('\n'.join(out))
print('* reduced: %d devices, %d capacitors, %d nets kept'%(len(kd),nc,len(keep)),file=sys.stderr)
