#!/usr/bin/env python3
"""find_ring.py <body.v>: locate rotating shift rings in the flat body netlist (tp-2ea.48.4).
A ring row is a group of registers whose D is directly another register's Q. Follows reg->reg chains
(D = Q of exactly one other reg, no gate), groups chains by length, prints the longest families."""
import re,sys,collections
src=open(sys.argv[1]).read()
q2w={};w2q={}
for m in re.finditer(r'reg q(\d+); wire w(\d+)=q\1;',src):q2w[int(m.group(1))]=int(m.group(2));w2q[int(m.group(2))]=int(m.group(1))
d={}
for m in re.finditer(r'^\s*q(\d+) <= w(\d+);',src,re.M):d[int(m.group(1))]=int(m.group(2))
print('regs',len(q2w),'d',len(d))
nxt={q:w2q[w] for q,w in d.items() if w in w2q}      # q's D is reg nxt[q]'s Q  (q <- nxt[q])
prev=collections.defaultdict(list)
for a,b in nxt.items():prev[b].append(a)
print('reg->reg D edges',len(nxt),'regs fanning out to >1 reg D',sum(len(v)>1 for v in prev.values()))
# chain heads: regs in nxt-chains whose own Q is read by nobody's D in a chain (start of q <- q' <- q'' ...)
seen=set();chains=[]
for q in nxt:
    if any(p in nxt or True for p in prev.get(q,[])) and prev.get(q):continue   # not a head (someone shifts from it)
    c=[q];x=q
    while x in nxt and nxt[x] not in c:x=nxt[x];c.append(x)
    chains.append(c)
lens=collections.Counter(len(c) for c in chains)
print('chain length histogram (top):',sorted(lens.items(),key=lambda t:-t[1])[:12])
big=[c for c in chains if len(c)>=60]
print('chains >=60:',len(big))
if big:
    c=big[0];print('example head',c[0],'tail',c[-1],'len',len(c),'stride',[c[i+1]-c[i] for i in range(3)])
    strides=collections.Counter(c[1]-c[0] for c in big);print('strides',strides.most_common(5))
    heads=sorted(c[0] for c in big);print('heads range',heads[0],heads[-1])
    tails=sorted(c[-1] for c in big);print('tails range',tails[0],tails[-1])
