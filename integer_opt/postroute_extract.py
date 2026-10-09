#!/usr/bin/env python3
"""Post-route sky130 netlist -> golden NAND/LATCH netlist, for gate-level replay at machine speed.

Every combinational sky130_fd_sc_hd cell type used is characterised by exhaustive simulation of the
official functional model (iverilog, -DFUNCTIONAL) and rebuilt from its truth table; dfxtp flops
become LATCH records (one global clock, as the machine's own netlist; clock-tree cells only feed CLK
pins and drop out), conb ties become constants, fill/decap/tap cells carry no logic. Macro instances
(int_c16_wtc_bNNN) are flattened from their own post-route netlists. The result keeps the top's
ports (clk dropped): din[43:0] -> dout[14:0], and can be replayed with machine.py's testbench.
usage: postroute_extract.py <top.nl.v> <macro_dir> <sky130 verilog dir> <out.nl>
"""
from pathlib import Path
import itertools,json,re,subprocess,sys,tempfile
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'physical'),str(Path(__file__).resolve().parents[1])]
from nand import Builder,with_state,metrics
from bench import lookup
from export import import_net
sys.setrecursionlimit(1000000)
NOLOGIC=('fill','decap','tapvpwrvgnd','diode')

INST=re.compile(r'^\s*(sky130_fd_sc_hd__\w+|int_c16_wtc_b\d+)\s+(\S+)\s*\((.*?)\);',re.S|re.M)
PIN=re.compile(r'\.(\w+)\s*\(\s*(.*?)\s*\)\s*(?:,|$)',re.S)


def cell_ports(lib,name):
    m=re.search(r'module\s+'+re.escape(name)+r'\s*\((.*?)\);(.*?)endmodule',lib,re.S)
    body=m.group(2);outs=re.findall(r'^\s*output\s+(\w+)\s*;',body,re.M);ins=re.findall(r'^\s*input\s+(\w+)\s*;',body,re.M)
    pw={'VPWR','VGND','VPB','VNB'};return [x for x in ins if x not in pw],[x for x in outs if x not in pw]


def characterise(skydir,types):
    lib=(Path(skydir)/'sky130_fd_sc_hd.v').read_text();tab={}
    with tempfile.TemporaryDirectory() as t:
        tb=['`timescale 1ns/1ps','module tb;']
        for k,ty in enumerate(types):
            ins,outs=cell_ports(lib,'sky130_fd_sc_hd__'+ty);tab[ty]=dict(ins=ins,outs=outs,rows=[])
            tb.append(f'reg [{max(len(ins),1)-1}:0] i{k};'+''.join(f'wire o{k}_{o};' for o in outs))
            conn=','.join([f'.{p}(i{k}[{j}])' for j,p in enumerate(ins)]+[f'.{o}(o{k}_{o})' for o in outs])
            tb.append(f'sky130_fd_sc_hd__{ty} u{k}({conn});')
        tb.append('integer v;initial begin')
        for k,ty in enumerate(types):
            n=len(tab[ty]['ins']);outs=tab[ty]['outs']
            tb.append(f'for(v=0;v<{1<<n};v=v+1) begin i{k}=v;#1;$display("{k} %0d'+' %b'*len(outs)+'",v'+''.join(f',o{k}_{o}' for o in outs)+'); end')
        tb.append('$finish;end endmodule')
        (Path(t)/'tb.v').write_text('\n'.join(tb)+'\n')
        subprocess.run(['iverilog','-g2012','-DFUNCTIONAL','-DUNIT_DELAY=#0','-o',t+'/sim',str(Path(skydir)/'primitives.v'),str(Path(skydir)/'sky130_fd_sc_hd.v'),t+'/tb.v'],check=True)
        out=subprocess.run(['vvp','-n',t+'/sim'],capture_output=True,text=True,check=True).stdout
    for line in out.splitlines():
        p=line.split()
        if len(p)<2 or not p[0].isdigit():continue
        ty=types[int(p[0])];bits=p[2:]
        assert all(b in '01' for b in bits),(ty,line)
        tab[ty]['rows'].append([int(b) for b in bits])
    for ty in types:assert len(tab[ty]['rows'])==1<<len(tab[ty]['ins']),ty
    return tab


def parse(text):
    insts=[]
    for ty,name,body in INST.findall(text):
        pins={p:n for p,n in PIN.findall(body+',')}
        insts.append((ty,name,pins))
    assigns=re.findall(r'^\s*assign\s+(.+?)\s*=\s*(.+?)\s*;',text,re.M)
    return insts,assigns


def norm(n):
    n=n.strip()
    if n.startswith('\\'):n=n[1:].strip()
    return n


def main():
    top,mdir,skydir,out=sys.argv[1:5]
    ttext=Path(top).read_text();tinst,tas=parse(ttext)
    macros={}
    for f in sorted(Path(mdir).glob('*.nl.v')):
        t=f.read_text();mname=re.search(r'module\s+(\w+)',t).group(1);macros[mname]=parse(t)
    types=sorted({ty[len('sky130_fd_sc_hd__'):] for ty,_,_ in tinst if ty.startswith('sky130')}|{ty[len('sky130_fd_sc_hd__'):] for m in macros.values() for ty,_,_ in m[0]})
    comb=[t for t in types if not t.startswith(NOLOGIC) and not t.startswith('dfxtp') and not t.startswith('conb')]
    tab=characterise(skydir,comb);print('cell types',len(types),'characterised',len(comb),flush=True)
    # flatten: driver[net] = (kind, ...)
    drv={};flops=[]
    def add_insts(insts,assigns,prefix,portmap):
        def N(x):
            x=norm(x)
            if x in ("1'b0","1'h0"):return ('const',0)
            if x in ("1'b1","1'h1"):return ('const',1)
            if prefix and x in portmap:return portmap[x]
            return prefix+x
        for ty,name,pins in insts:
            if ty.startswith('int_c16_wtc_b'):
                mi,ma=macros[ty];pm={}
                for p,n in pins.items():
                    bits=[N(b) for b in expand(n)];
                    for j,b in enumerate(bits):pm[f'{p}[{len(bits)-1-j}]' if len(bits)>1 else p]=b
                add_insts(mi,ma,prefix+norm(name)+'/',pm);continue
            t=ty[len('sky130_fd_sc_hd__'):]
            if t.startswith(NOLOGIC):continue
            if t.startswith('conb'):
                for p,n in pins.items():
                    if p=='HI':drv[N(n)]=('const',1)
                    if p=='LO':drv[N(n)]=('const',0)
                continue
            if t.startswith('dfxtp'):
                q=N(pins['Q']);d=N(pins['D']);flops.append((q,d));drv[q]=('flop',len(flops)-1);continue
            c=tab[t]
            ins=[N(pins[p]) for p in c['ins']]
            for k,o in enumerate(c['outs']):
                if o in pins and norm(pins[o]):drv[N(pins[o])]=('cell',t,k,ins)
        for l,r in assigns:
            for a,bb in zip(expand(l),expand(r)):drv[N(a)]=('alias',N(bb))
    def expand(s):
        s=s.strip()
        if s.startswith('{'):
            parts=[p.strip() for p in s[1:-1].split(',')];res=[]
            for p in parts:res+=expand(p)
            return res
        m=re.match(r'^(\\?\S+?)\s*\[(\d+):(\d+)\]$',s)
        if m:
            b,hi,lo=m.group(1),int(m.group(2)),int(m.group(3));return [f'{b}[{i}]' for i in range(hi,lo-1,-1)]
        return [s]
    add_insts(tinst,tas,'',{})
    NI=44;b=Builder(len(flops)+NI);state={i:2+i for i in range(len(flops))};pin={f'din[{i}]':2+len(flops)+i for i in range(NI)}
    memo={};cellnets={}
    def wire(n):
        if isinstance(n,tuple):return n[1]
        if n in memo:return memo[n]
        if n in pin:memo[n]=pin[n];return memo[n]
        d=drv.get(n)
        if d is None:raise KeyError('undriven net '+n)
        if d[0]=='const':w=d[1]
        elif d[0]=='flop':w=state[d[1]]
        elif d[0]=='alias':w=wire(d[1])
        else:
            _,t,k,ins=d;key=(t,tuple(ins))
            if key not in cellnets:
                ws=[wire(x) for x in ins];c=tab[t]
                outs=[]
                for kk in range(len(c['outs'])):
                    col=[row[kk] for row in c['rows']]
                    if all(v==col[0] for v in col):outs.append(col[0]);continue
                    sub=lookup(col,1,'shannon');_,y=import_net(b,sub,ws);outs.append(y[0])
                cellnets[key]=outs
            w=cellnets[key][k]
        memo[n]=w;return w
    ds=[wire(d) for _,d in flops];outs=[wire(f'dout[{i}]') for i in range(15)]
    net=with_state(b.finish(ds+outs),len(flops))
    Path(out).write_bytes(net.encode());print(json.dumps(dict(flops=len(flops),metrics=metrics(net))),flush=True)


if __name__=='__main__':main()
