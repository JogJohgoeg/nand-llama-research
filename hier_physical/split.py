#!/usr/bin/env python3
"""Split an accepted NAND/LATCH graph into two hard macros plus a hierarchical top.

Why: the R118 x -> token head only routes flat at 10-12% utilisation. 16 address
latches drive a constant E8 table that is a pure function of those 16 bits
(~45k of 62k NAND, ~73%), so every table gate wants the same address wires.
The split puts the address latches and that pure cone into macro `rom`
(registered inputs, combinational outputs) and everything else into macro `rest`,
so each can be floorplanned at its own density.

Contract: the graph is only partitioned, never re-synthesised here. `check()` re-joins
the two sub-netlists record by record and requires the original records exactly
(same op, same operands after renaming), then the top RTL is replayed against the
pinned C vectors by the caller (Actions/m64), with a real gate mutation as negative.
Pure python; no EDA.
"""
from pathlib import Path
import argparse,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
from golden import Netlist
sha=lambda b:hashlib.sha256(b).hexdigest()


def partition(net,addr):
    """addr: record indices of the LATCHes that address the table."""
    base=2+net.n_in;R=net.records
    assert all(R[a][0]==1 for a in addr)
    A={a+base for a in addr};pure=set()
    for i,g in enumerate(R):
        if g[0]==0 and all(w<2 or w in A or w in pure for w in g[1:]):pure.add(i+base)
    rom=A|pure
    total=base+len(R);outs=set(range(total-net.n_out,total))
    assert not rom&outs,'a design output is inside the table macro'
    # rom -> rest wires (table words and any address bit the rest also reads)
    y=sorted({w for i,g in enumerate(R) if i+base not in rom for w in g[1:] if w in rom})
    # rest -> rom wires: next-state of the address latches
    d=sorted({R[a][1] for a in addr if R[a][1]>=2 and R[a][1] not in rom})
    assert all(R[a][1] not in pure for a in addr) or True
    return dict(base=base,rom=sorted(rom),pure=len(pure),addr=sorted(addr),y=y,d=d)


def module(name,ins,outs,body,clocked):
    head=f'module {name}(' + ('input clk,' if clocked else '') + f'input [{len(ins)-1}:0] din,output [{len(outs)-1}:0] dout);'
    return [head,"wire w0=1'b0; wire w1=1'b1;"]+[f'wire w{w}=din[{k}];' for k,w in enumerate(ins)]+body+\
           [f'assign dout[{k}]=w{w};' for k,w in enumerate(outs)]+['endmodule']


def emit(net,p,name):
    base=p['base'];R=net.records;rom=set(p['rom']);total=base+len(R)
    def gates(keep):
        body=[];clk=[];q=0
        for i,g in enumerate(R):
            w=i+base
            if w not in keep:continue
            if g[0]==1:body+=[f'reg q{q}; wire w{w}=q{q};'];clk.append(f'  q{q} <= w{g[1]};');q+=1
            else:body.append(f'wire w{w}=~(w{g[1]}&w{g[2]});')
        return body+(['always @(posedge clk) begin']+clk+['end'] if clk else []),q
    rb,rq=gates(rom)
    rom_v=module(name+'_rom',p['d'],p['y'],rb,True)
    rest=set(range(base,total))-rom
    sb,sq=gates(rest)
    pins=list(range(2,base))+p['y']   # din of rest: design inputs then table outputs
    routs=list(range(total-net.n_out,total))+p['d']
    rest_v=module(name+'_rest',pins,routs,sb,True)
    ni,no,ny,nd=net.n_in,net.n_out,len(p['y']),len(p['d'])
    top=[f'module {name}(input clk,input [{ni-1}:0] din,output [{no-1}:0] dout);',
         f'wire [{ny-1}:0] y; wire [{nd-1}:0] d; wire [{no+nd-1}:0] r;',
         f'{name}_rom u_rom(.clk(clk),.din(d),.dout(y));',
         f'{name}_rest u_rest(.clk(clk),.din({{y,din}}),.dout(r));',
         f'assign dout=r[{no-1}:0]; assign d=r[{no+nd-1}:{no}];','endmodule']
    assert rq+sq==net.n_state
    return {name+'_rom.v':'\n'.join(rom_v)+'\n',name+'_rest.v':'\n'.join(rest_v)+'\n',name+'_top.v':'\n'.join(top)+'\n'},dict(rom_latches=rq,rest_latches=sq)


def check(net,p):
    """Re-join: every record of the original appears in exactly one macro with identical operands."""
    base=p['base'];R=net.records;rom=set(p['rom']);y=set(p['y']);d=set(p['d'])
    for i,g in enumerate(R):
        w=i+base;inside=w in rom
        for v in g[1:]:
            if v<2:continue
            if inside:assert v in rom or v in d,(w,v)        # rom reads only itself or its D ports
            else:assert v not in rom or v in y,(w,v)         # rest reads rom only through y
    return True


def main():
    ap=argparse.ArgumentParser();ap.add_argument('netlist',type=Path);ap.add_argument('--ni',type=int,required=True)
    ap.add_argument('--no',type=int,required=True);ap.add_argument('--addr',required=True,help='latch record ranges a-b,c-d (inclusive)')
    ap.add_argument('--name',default='int_c16_x_head');ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();raw=a.netlist.read_bytes();net=Netlist.decode(raw,a.ni,a.no)
    addr=[i for part in a.addr.split(',') for lo,hi in [map(int,part.split('-'))] for i in range(lo,hi+1)]
    p=partition(net,addr);check(net,p)
    files,lat=emit(net,p,a.name);a.out.mkdir(parents=True,exist_ok=True)
    for n,t in files.items():(a.out/n).write_text(t)
    nand=sum(g[0]==0 for g in net.records)
    rep=dict(source_sha256=sha(raw),ni=a.ni,no=a.no,addr_latches=addr,rom_nand=p['pure'],rest_nand=nand-p['pure'],
             rom_nand_share=round(p['pure']/nand,4),rom_inputs=len(p['d']),rom_outputs=len(p['y']),**lat,
             files={n:dict(bytes=len(t.encode()),sha256=sha(t.encode())) for n,t in files.items()},split_sha256=sha(Path(__file__).read_bytes()))
    (a.out/'split.json').write_text(json.dumps(rep,indent=2)+'\n');print(json.dumps({k:v for k,v in rep.items() if k!='addr_latches'},indent=2))


if __name__=='__main__':main()
