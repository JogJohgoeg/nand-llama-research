#!/usr/bin/env python3
"""Hierarchical R118 x -> token head: standard-cell rest + K E8 sub-table macros.

The top keeps every record of the original graph except the 9-bit table's pure NAND cone
(the 9 address latches stay in the top: the rest also reads them). Each table word w is
driven by exactly one sub-table instance output, under the same wire name w<id>, so the
top differs from the flat RTL only by moving table gates (and duplicated copies of their
shared decode gates plus replica address latches) into macros. `check()` asserts that:
every original record is either in the top or in the table cone, every table word read by
the top is produced by exactly one instance, and replica latches load the same D wires.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT),str(ROOT/'hier_physical')]
import subtable


def top(net,K,name='int_c16_x_head'):
    t=subtable.Table(net);R=net.records;b=t.base;total=b+len(R)
    groups=[t.group(K,k)[0] for k in range(K)]
    assert sorted(w for g in groups for w in g)==sorted(t.words)
    D=[R[a][1] for a in subtable.ADDR];assert not set(D)&t.pure
    L=[f'module {name}(input clk,input [{net.n_in-1}:0] din,output [{net.n_out-1}:0] dout);',"wire w0=1'b0; wire w1=1'b1;"]
    L+=[f'wire w{i+2}=din[{i}];' for i in range(net.n_in)]
    clk=[];q=0
    for i,g in enumerate(R):
        w=i+b
        if w in t.pure:continue
        if g[0]==1:L.append(f'reg q{q}; wire w{w}=q{q};');clk.append(f'  q{q} <= w{g[1]};');q+=1
        else:L.append(f'wire w{w}=~(w{g[1]}&w{g[2]});')
    for k,words in enumerate(groups):
        L.append(f'wire [{len(words)-1}:0] t{k}; '+' '.join(f'wire w{w}=t{k}[{j}];' for j,w in enumerate(words)))
        L.append(f'int_c16_e8_k{K}_{k} u_t{k}(.clk(clk),.din({{'+','.join(f'w{d}' for d in reversed(D))+f'}}),.dout(t{k}));')
    L+=['always @(posedge clk) begin']+clk+['end']
    L+=[f'assign dout[{i}]=w{total-net.n_out+i};' for i in range(net.n_out)]+['endmodule']
    used={v for i,g in enumerate(R) if i+b not in t.pure for v in g[1:]}
    assert used&t.pure<=set(t.words),'top reads a non-word table gate'
    assert q==net.n_state,(q,net.n_state)
    return '\n'.join(L)+'\n',dict(top_nand=sum(g[0]==0 for g in R)-len(t.pure),top_latches=q,table_nand=len(t.pure),
                                  macros=K,words=len(t.words),address_d=[d for d in D])


if __name__=='__main__':
    from golden import Netlist
    src,K,out=Path(sys.argv[1]),int(sys.argv[2]),Path(sys.argv[3])
    net=Netlist.decode(src.read_bytes(),56,19);v,rep=top(net,K);out.mkdir(parents=True,exist_ok=True)
    (out/'top.v').write_text(v);print(rep)
    for k in range(K):subtable.build(net,K,k,f'int_c16_e8_k{K}_{k}',out/f'k{K}_{k}')
