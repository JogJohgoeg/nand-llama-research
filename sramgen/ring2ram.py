#!/usr/bin/env python3
"""ring2ram.py <body.v> <out.v> [rows=64] [width=640]: move the prefix bank's middle rows out of the flat body (tp-2ea.48.4).

The prefix bank is a rotating ring: width chains of `rows` registers with stride `width`; in every chain
row 0 (head, read by the logic) <- row 1 <- ... <- row rows-1 (tail, D = write ? data : head). Rows 1..rows-2
are pure shift stages: their Q feeds only the next stage's D. They move into a RAM (sram8t macros + prefix_ram
periphery); the body keeps head (H) and tail (W):
  head.D := rdata[j]           (the RAM returns logical row 1, i.e. physical row q+1)
  wdata[j] := tail.Q           (written to physical row q-1 in the next cycle)
Everything else is byte-identical. Output module int_c16_body_ram(clk, din[107:0], rdata[width-1:0],
dout[29:0], wdata[width-1:0]). Prints the column map (j -> head reg, tail reg) as JSON on stdout.
"""
import re,sys,json,collections
src,dst=sys.argv[1],sys.argv[2];ROWS=int(sys.argv[3]) if len(sys.argv)>3 else 64;WID=int(sys.argv[4]) if len(sys.argv)>4 else 640
text=open(src).read()
q2w={};w2q={}
for m in re.finditer(r'^reg q(\d+); wire w(\d+)=q\1;$',text,re.M):q2w[int(m.group(1))]=int(m.group(2));w2q[int(m.group(2))]=int(m.group(1))
d={int(m.group(1)):int(m.group(2)) for m in re.finditer(r'^  q(\d+) <= w(\d+);$',text,re.M)}
assert len(d)==len(q2w)
nxt={q:w2q[w] for q,w in d.items() if w in w2q}
prev=collections.defaultdict(list)
for a,b in nxt.items():prev[b].append(a)
chains=[]
for q in nxt:
    if prev.get(q):continue
    c=[q];x=q
    while x in nxt:x=nxt[x];c.append(x)
    if len(c)==ROWS and all(c[i+1]-c[i]==WID for i in range(ROWS-1)):chains.append(c)
chains.sort(key=lambda c:c[0])
assert len(chains)==WID,len(chains)
assert [c[0] for c in chains]==list(range(chains[0][0],chains[0][0]+WID)),'heads not contiguous'
# fan-out of every wire: the removed stages' Q must feed nothing but the previous stage's D
refs=collections.Counter(int(x) for x in re.findall(r'w(\d+)',re.sub(r'^(reg q\d+; )?wire w\d+=','',text,flags=re.M)))
mid={q for c in chains for q in c[1:-1]}
for c in chains:
    for i in range(1,ROWS):
        assert refs[q2w[c[i]]]==1,('stage Q read elsewhere',c[i],refs[q2w[c[i]]])
tails={c[-1] for c in chains}
out=[];k=0
for line in text.split('\n'):
    if line.startswith('module int_c16_body('):
        out.append('module int_c16_body_ram(input clk,input [107:0] din,input [%d:0] rdata,output [29:0] dout,output [%d:0] wdata);'%(WID-1,WID-1));continue
    m=re.match(r'^reg q(\d+); wire w\d+=q\1;$',line)
    if m and int(m.group(1)) in mid:continue
    m=re.match(r'^  q(\d+) <= w(\d+);$',line)
    if m:
        q=int(m.group(1))
        if q in mid:continue
        j=q-chains[0][0]
        if 0<=j<WID and chains[j][0]==q:line='  q%d <= rdata[%d];'%(q,j)
    if line=='endmodule':
        out+=['assign wdata[%d]=w%d;'%(j,q2w[c[-1]]) for j,c in enumerate(chains)]
    out.append(line)
open(dst,'w').write('\n'.join(out))
json.dump({'rows':ROWS,'width':WID,'removed':len(mid),'head0':chains[0][0],'tail0':chains[0][-1],
           'columns':[[c[0],c[-1]] for c in chains]},sys.stdout)
print()
