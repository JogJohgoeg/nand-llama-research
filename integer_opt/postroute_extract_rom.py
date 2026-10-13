#!/usr/bin/env python3
"""Post-route sky130 netlist with mask-ROM banks -> golden NAND/LATCH netlist, one state per clock.

Same as postroute_extract.py (cells rebuilt from the official functional models, dfxtp -> LATCH
records, buffers/inverters/fill drop out), plus the ROM read path of romgen (periph.py / machine_rom.py)
at cycle level, i.e. the behaviour its timing scheme guarantees by the next rising edge:
  rom_bank_NN macro      -> BL[r] = NOT OR{ WL[w] : bit(r,w) = 1 } from the bank file (evaluate phase)
  flop with Q net ph_a   -> constant 1, ph_b -> constant 0   (phase P = 1: precharge off, window open)
  flop with Q net a_l[*] / s_l (falling-edge address/sel) -> transparent (Q = D)
  dlxtn / dlxtp latches  -> transparent (Q = D); dlxbn: Q = D, Q_N = NOT D
Every other dfxtp is a state bit. Ports are read from the top module declaration (clk excluded);
inputs are numbered in declaration order, bit 0 first, likewise outputs.
usage: postroute_extract_rom.py <top.nl.v> <rom banks dir with bank_NN.txt> <sky130 verilog dir | sg13g2_stdcell.v> <out.nl>
IHP SG13G2 (third argument is the sg13g2_stdcell.v model file): cells sg13g2_*; dfrbp* flops and dllrq latches must have
RESET_B tied high (sg13g2_tiehi); tiehi/tielo become constants; fill/decap/antenna cells carry no logic.
"""
from pathlib import Path
import json,re,sys
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'physical'),str(Path(__file__).resolve().parents[1])]
from nand import Builder,with_state,metrics
from bench import lookup
from export import import_net
from postroute_extract import characterise,norm,PIN,NOLOGIC
sys.setrecursionlimit(1000000)
INST=re.compile(r'^\s*(sky130_fd_sc_hd__\w+|sg13g2_\w+|gf180mcu_fd_sc_\w+|rom_bank_\d+)\s+(\S+)\s*\((.*?)\);',re.S|re.M)


def characterise_ihp(vfile,types,pfx='sg13g2_',extra=(),defines=()):
    """truth tables of sg13g2 combinational cells by exhaustive simulation of the PDK functional models (iverilog);
    the models declare ports as comma lists ('input A1, A2, B1;')."""
    import subprocess,tempfile
    lib=Path(vfile).read_text();tab={}
    def ports(name):
        m=re.search(r'module\s+'+re.escape(name)+r'\s*\((.*?)\);(.*?)endmodule',lib,re.S);b=m.group(2)
        lst=lambda k:[x.strip() for d in re.findall(r'^\s*'+k+r'\s+([^;]+);',b,re.M) for x in d.split(',')]
        return lst('input'),lst('output')
    with tempfile.TemporaryDirectory() as t:
        tb=['`timescale 1ns/1ps','module tb;']
        for k,ty in enumerate(types):
            ins,outs=ports(pfx+ty);tab[ty]=dict(ins=ins,outs=outs,rows=[])
            tb.append(f'reg [{max(len(ins),1)-1}:0] i{k};'+''.join(f'wire o{k}_{o};' for o in outs))
            tb.append(f'{pfx}{ty} u{k}('+','.join([f'.{q}(i{k}[{j}])' for j,q in enumerate(ins)]+[f'.{o}(o{k}_{o})' for o in outs])+');')
        tb.append('integer v;initial begin')
        for k,ty in enumerate(types):
            n=len(tab[ty]['ins']);outs=tab[ty]['outs']
            tb.append(f'for(v=0;v<{1<<n};v=v+1) begin i{k}=v;#1;$display("{k} %0d'+' %b'*len(outs)+'",v'+''.join(f',o{k}_{o}' for o in outs)+'); end')
        tb.append('$finish;end endmodule');(Path(t)/'tb.v').write_text('\n'.join(tb)+'\n')
        subprocess.run(['iverilog','-g2012',*[f'-D{d}' for d in defines],'-o',t+'/sim',*extra,vfile,t+'/tb.v'],check=True)
        out=subprocess.run(['vvp','-n',t+'/sim'],capture_output=True,text=True,check=True).stdout
    for line in out.splitlines():
        q=line.split()
        if len(q)<2 or not q[0].isdigit():continue
        bits=q[2:];assert all(b in '01' for b in bits),(types[int(q[0])],line);tab[types[int(q[0])]]['rows'].append([int(b) for b in bits])
    for ty in types:assert len(tab[ty]['rows'])==1<<len(tab[ty]['ins']),ty
    return tab
PHASE={'ph_a':1,'ph_b':0}
TRANSPARENT=re.compile(r'(^|[./])(a_l\[\d+\]|s_l)$')


def ports(text,kind):
    m=re.search(r'module\s+\w+\s*\(.*?\);(.*?)endmodule',text,re.S);out=[]
    for rng,name in re.findall(r'^\s*'+kind+r'\s+(?:\[(\d+:\d+)\]\s*)?(\\?\S+?)\s*;',m.group(1),re.M):
        name=norm(name)
        if name=='clk':continue
        if rng:
            hi,lo=map(int,rng.split(':'));out+=[f'{name}[{i}]' for i in range(lo,hi+1)]
        else:out.append(name)
    return out


def expand(s):
    s=s.strip()
    if s.startswith('{'):
        res=[]
        for p in s[1:-1].split(','):res+=expand(p)
        return res
    m=re.match(r'^(\\?\S+?)\s*\[(\d+):(\d+)\]$',s)
    if m:
        b,hi,lo=m.group(1),int(m.group(2)),int(m.group(3));return [f'{b}[{i}]' for i in range(hi,lo-1,-1)]
    return [s]


def main():
    top,bdir,skydir,out=sys.argv[1:5]
    text=Path(top).read_text()
    insts=[(ty,name,{p:n for p,n in PIN.findall(body+',')}) for ty,name,body in INST.findall(text)]
    assigns=re.findall(r'^\s*assign\s+(.+?)\s*=\s*(.+?)\s*;',text,re.M)
    pins_in,pins_out=ports(text,'input'),ports(text,'output')
    GF='gf180mcu' in skydir;IHP=skydir.endswith('.v') and not GF
    PFX='gf180mcu_fd_sc_mcu9t5v0__' if GF else 'sg13g2_' if IHP else 'sky130_fd_sc_hd__'
    NOL=('fill','endcap','antenna') if GF else ('fill','decap','antenna') if IHP else NOLOGIC
    SEQ=('dffq','latq','tieh','tiel') if GF else ('dfrbp','dllrq','tiehi','tielo') if IHP else ('dfxtp','conb','dlxtn','dlxtp','dlxbn')
    types=sorted({ty[len(PFX):] for ty,_,_ in insts if ty.startswith(PFX)})
    comb=[t for t in types if not t.startswith(NOL) and not t.startswith(SEQ)]
    if GF:tab=characterise_ihp(skydir,comb,PFX,[str(Path(skydir).parent/'primitives.v')],['FUNCTIONAL'])
    else:tab=(characterise_ihp if IHP else characterise)(skydir,comb)
    print('cell types',len(types),'characterised',len(comb),flush=True)
    resets=[]                                                      # IHP RESET_B nets, checked constant 1 below
    banks={}
    def N(x):
        x=norm(x)
        if x in ("1'b0","1'h0"):return ('const',0)
        if x in ("1'b1","1'h1"):return ('const',1)
        return x
    drv={};flops=[];counts=dict(phase=0,transparent_flops=0,latches=0,rom_banks=0)
    for ty,name,pins in insts:
        if ty.startswith('rom_bank_'):
            nn=ty[len('rom_bank_'):]
            if nn not in banks:banks[nn]=[l.strip() for l in open(Path(bdir)/f'bank_{nn}.txt') if l.strip()]
            wl=expand(pins['WL']);wl=[N(x) for x in wl[::-1]]          # bit 0 first
            bl=expand(pins['BL']);bl=[N(x) for x in bl[::-1]]
            for r,b in enumerate(bl):drv[b]=('rom',nn,r,wl)
            counts['rom_banks']+=1;continue
        t=ty[len(PFX):]
        if t.startswith(NOL):continue
        if GF:
            if t.startswith('tieh'):drv[N(pins['Z'])]=('const',1);continue
            if t.startswith('tiel'):drv[N(pins['ZN'])]=('const',0);continue
            if t.startswith('latq'):drv[N(pins['Q'])]=('alias',N(pins['D']));counts['latches']+=1;continue
            if t.startswith('dffq'):
                q=N(pins['Q']);d=N(pins['D']);leaf=re.split(r'[./]',q)[-1]
                if leaf in PHASE:drv[q]=('const',PHASE[leaf]);counts['phase']+=1;continue
                if TRANSPARENT.search(q):drv[q]=('alias',d);counts['transparent_flops']+=1;continue
                flops.append((q,d));drv[q]=('flop',len(flops)-1);continue
        if IHP:
            if t.startswith('tiehi'):drv[N(pins['L_HI'])]=('const',1);continue
            if t.startswith('tielo'):drv[N(pins['L_LO'])]=('const',0);continue
            if t.startswith('dllrq'):
                resets.append(N(pins['RESET_B']));drv[N(pins['Q'])]=('alias',N(pins['D']));counts['latches']+=1;continue
            if t.startswith('dfrbp'):
                resets.append(N(pins['RESET_B']));q=N(pins['Q']);d=N(pins['D']);leaf=re.split(r'[./]',q)[-1]
                if norm(pins.get('Q_N','')):raise SystemExit('dfrbp Q_N used: '+name)
                if leaf in PHASE:drv[q]=('const',PHASE[leaf]);counts['phase']+=1;continue
                if TRANSPARENT.search(q):drv[q]=('alias',d);counts['transparent_flops']+=1;continue
                flops.append((q,d));drv[q]=('flop',len(flops)-1);continue
        if t.startswith('conb'):
            for p,n in pins.items():
                if p=='HI':drv[N(n)]=('const',1)
                if p=='LO':drv[N(n)]=('const',0)
            continue
        if t.startswith(('dlxtn','dlxtp')):
            drv[N(pins['Q'])]=('alias',N(pins['D']));counts['latches']+=1;continue
        if t.startswith('dlxbn'):
            if norm(pins.get('Q','')):drv[N(pins['Q'])]=('alias',N(pins['D']))
            if norm(pins.get('Q_N','')):drv[N(pins['Q_N'])]=('not',N(pins['D']))
            counts['latches']+=1;continue
        if t.startswith('dfxtp'):
            q=N(pins['Q']);d=N(pins['D']);leaf=re.split(r'[./]',q)[-1]
            if leaf in PHASE:drv[q]=('const',PHASE[leaf]);counts['phase']+=1;continue
            if TRANSPARENT.search(q):drv[q]=('alias',d);counts['transparent_flops']+=1;continue
            flops.append((q,d));drv[q]=('flop',len(flops)-1);continue
        c=tab[t];ins=[N(pins[p]) for p in c['ins']]
        for k,o in enumerate(c['outs']):
            if o in pins and norm(pins[o]):drv[N(pins[o])]=('cell',t,k,ins)
    for l,r in assigns:
        for a,bb in zip(expand(l),expand(r)):drv[N(a)]=('alias',N(bb))
    print(json.dumps(counts),flush=True)
    def const_of(n):
        while not isinstance(n,tuple) and drv.get(n,(None,))[0]=='alias':n=drv[n][1]
        return n if isinstance(n,tuple) else drv.get(n)
    bad=[r for r in set(resets) if const_of(r)!=('const',1)]
    assert not bad,('RESET_B not tied high',bad[:5])
    NI=len(pins_in);b=Builder(len(flops)+NI);state={i:2+i for i in range(len(flops))};pin={p:2+len(flops)+i for i,p in enumerate(pins_in)}
    memo={};cellnets={};rommemo={}
    def wire(n):
        if isinstance(n,tuple):return n[1]
        if n in memo:return memo[n]
        if n in pin:memo[n]=pin[n];return memo[n]
        d=drv.get(n)
        if d is None:raise KeyError('undriven net '+n)
        if d[0]=='const':w=d[1]
        elif d[0]=='flop':w=state[d[1]]
        elif d[0]=='alias':w=wire(d[1])
        elif d[0]=='not':w=b.inv(wire(d[1]))
        elif d[0]=='rom':
            _,nn,r,wl=d;rows=banks[nn]
            ones=[wire(wl[w]) for w in range(len(rows)) if rows[w][r]=='1']
            w=b.inv(b.reduce(ones,b.lor,0)) if ones else 1
        else:
            _,t,k,ins=d;key=(t,tuple(ins))
            if key not in cellnets:
                ws=[wire(x) for x in ins];c=tab[t];outs=[]
                for kk in range(len(c['outs'])):
                    col=[row[kk] for row in c['rows']]
                    if all(v==col[0] for v in col):outs.append(col[0]);continue
                    sub=lookup(col,1,'shannon');_,y=import_net(b,sub,ws);outs.append(y[0])
                cellnets[key]=outs
            w=cellnets[key][k]
        memo[n]=w;return w
    ds=[wire(d) for _,d in flops];outs=[wire(p) for p in pins_out]
    net=with_state(b.finish(ds+outs),len(flops))
    Path(out).write_bytes(net.encode())
    print(json.dumps(dict(inputs=pins_in[:3]+['...'],n_in=NI,n_out=len(pins_out),flops=len(flops),metrics=metrics(net))),flush=True)


if __name__=='__main__':main()
