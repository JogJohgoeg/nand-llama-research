#!/usr/bin/env python3
"""ext2bits.py <extracted.spice> <bits.txt>: rebuild the ROM contents from the layout extraction alone
(bit(r,w)=1 iff some nfet has gate WL[w] and one diffusion terminal on BL[r], the other on VGND) and
compare with the intended bits file. Prints mismatches; exit 1 on any."""
import re,sys
ext,want=sys.argv[1:3]
rows=[l.strip() for l in open(want) if l.strip()];C=len(rows);R=len(rows[0])
txt=open(ext).read().replace('\n+',' ')
got=[['0']*R for _ in range(C)];n=0
for l in txt.splitlines():
    if not l.startswith('X'):continue
    p=l.split();d,g,s=p[1],p[2],p[3]
    mg=re.fullmatch(r'WL\[(\d+)\]',g)
    if not mg:continue
    n+=1
    for a,b in ((d,s),(s,d)):
        mb=re.fullmatch(r'BL\[(\d+)\]',a)
        if mb and b=='VGND':got[int(mg.group(1))][int(mb.group(1))]='1'
bad=[(w,r) for w in range(C) for r in range(R) if got[w][r]!=rows[w][r]]
print('devices',n,'bits',R*C,'ones',sum(x.count('1') for x in rows),'mismatches',len(bad),bad[:5])
sys.exit(1 if bad else 0)
