#!/usr/bin/env python3
"""Cut the R118 head's constant E8 table into K sub-table macros (Actions harden; local = pure python).

The 9 address latches (records 857-865) and every NAND that depends only on them form the
table: 42,168 NAND, 1,043 words, each word read by exactly one gate of the 7-bit select tree.
Words are taken in the order the select tree reads them and cut into K contiguous groups.
Each sub-table copies the 9 address latches and every table NAND its words need, record for
record (operands unchanged), so shared decode gates are duplicated per group (+62% at K=32)
and no decode net crosses a macro boundary. m64 GRT sweep: K=32 routes at 60% utilisation.

Exhaustive check: the table is a function of 9 bits, so every macro is replayed on all 512
addresses (two passes, ascending then a fixed shuffle) against the original graph evaluated
in python. Post-route the same vectors run on the routed standard-cell netlist.
"""
from pathlib import Path
import hashlib,json,random,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
ADDR=list(range(857,866))
sha=lambda b:hashlib.sha256(b).hexdigest()


class Table:
    def __init__(self,net):
        self.net=net;base=self.base=2+net.n_in;R=net.records
        assert all(R[a][0]==1 for a in ADDR)
        A={a+base for a in ADDR};P=set()
        for i,g in enumerate(R):
            if g[0]==0 and all(w<2 or w in A or w in P for w in g[1:]):P.add(i+base)
        self.pure=P;pos={}
        for i,g in enumerate(R):
            if i+base not in P:
                for w in g[1:]:
                    if w in P:
                        assert w not in pos,'table word with more than one consumer';pos[w]=i
        self.words=sorted(pos,key=pos.get)

    def cone(self,w):
        s=set();st=[w];R=self.net.records;b=self.base
        while st:
            x=st.pop()
            if x in self.pure and x not in s:s.add(x);st+=list(R[x-b][1:])
        return s

    def group(self,K,k):
        size=-(-len(self.words)//K);words=self.words[k*size:(k+1)*size];keep=set()
        for w in words:keep|=self.cone(w)
        return words,keep

    def verilog(self,name,words,keep):
        R=self.net.records;b=self.base
        L=[f'module {name}(input clk,input [8:0] din,output [{len(words)-1}:0] dout);',"wire w0=1'b0; wire w1=1'b1;"]
        L+=[f'reg q{j}; wire w{a+b}=q{j};' for j,a in enumerate(ADDR)]
        L+=[f'wire w{i+b}=~(w{R[i][1]}&w{R[i][2]});' for i in range(len(R)) if i+b in keep]
        L+=['always @(posedge clk) begin']+[f'  q{j} <= din[{j}];' for j in range(9)]+['end']
        L+=[f'assign dout[{j}]=w{w};' for j,w in enumerate(words)]+['endmodule']
        return '\n'.join(L)+'\n'

    def evaluate(self,words,keep,address):
        R=self.net.records;b=self.base;v={0:0,1:1}
        for j,a in enumerate(ADDR):v[a+b]=address>>j&1
        for w in sorted(keep):g=R[w-b];v[w]=1-(v[g[1]]&v[g[2]])
        return sum(v[w]<<j for j,w in enumerate(words))

    def vectors(self,words,keep):
        order=list(range(512))+random.Random(118).sample(range(512),512)
        lines=[];prev=None;full=(1<<len(words))-1
        for a in order:
            exp=0 if prev is None else self.evaluate(words,keep,prev)
            lines.append(f'{a:x} {exp:x} {0 if prev is None else full:x}\n');prev=a
        return ''.join(lines)


def build(net,K,k,name,out):
    t=Table(net);words,keep=t.group(K,k);out.mkdir(parents=True,exist_ok=True)
    files={'slice.v':t.verilog(name,words,keep),'vectors.txt':t.vectors(words,keep)}
    for n,s in files.items():(out/n).write_text(s)
    rep=dict(design=name,K=K,k=k,words=len(words),table_nand=len(t.pure),macro_nand=len(keep),address_latches=ADDR,
             word_records=[w-t.base for w in words],files={n:dict(bytes=len(s.encode()),sha256=sha(s.encode())) for n,s in files.items()},
             subtable_sha256=sha(Path(__file__).read_bytes()))
    (out/'subtable.json').write_text(json.dumps(rep,indent=2)+'\n')
    return rep


if __name__=='__main__':
    from golden import Netlist
    src,K,k,out=Path(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3]),Path(sys.argv[4])
    r=build(Netlist.decode(src.read_bytes(),56,19),K,k,f'int_c16_e8_k{K}_{k}',out)
    print(json.dumps({x:y for x,y in r.items() if x not in ('word_records','address_latches')},indent=2))
