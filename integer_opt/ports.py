#!/usr/bin/env python3
"""Exact state-port experiment: addressed bank versus cursor-controlled ring.

Construction and <=4k checks are local. Real-size gate simulation and ABC CEC
run only on Actions. A ring changes access latency, never the stored bits.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench import verify
from ci import cec
from golden import Netlist
from nand import Builder,blif,flip_output,from_yosys,metrics,verilog,verify_state,with_state


def make(kind,rows,width):
    assert rows>1 and rows&(rows-1)==0
    a=(rows-1).bit_length();s=rows*width
    ni=s+width+(a+1 if kind=='addressed' else 2)
    b=Builder(ni);old=[list(range(2+i*width,2+(i+1)*width)) for i in range(rows)]
    data=list(range(2+s,2+s+width));ctl=2+s+width
    if kind=='addressed':
        addr=list(range(ctl,ctl+a));we=ctl+a;nxt=[]
        for i,row in enumerate(old):
            hit=b.reduce([w if i>>j&1 else b.inv(w) for j,w in enumerate(addr)],b.land,1)
            write=b.land(we,hit);nxt.extend(b.mux(write,x,y) for x,y in zip(row,data))
        read=old
        for sel in addr:read=[[b.mux(sel,x,y) for x,y in zip(read[i],read[i+1])] for i in range(0,len(read),2)]
        out=read[0]
    else:
        advance,we=ctl,ctl+1;nxt=[]
        for i,row in enumerate(old):
            incoming=old[(i+1)%rows]
            if i==rows-1:incoming=[b.mux(we,x,y) for x,y in zip(old[0],data)]
            nxt.extend(b.mux(advance,x,y) for x,y in zip(row,incoming))
        out=old[0]
    combin=b.finish(nxt+out)
    return combin,with_state(combin,s)


def reference(kind,r,w):
    s=r*w;a=(r-1).bit_length();ni=s+w+(a+1 if kind=='addressed' else 2)
    lines=[f'module top(input [{ni-1}:0] din,output [{s+w-1}:0] dout);',
           f'wire [{s-1}:0] old=din[{s-1}:0];',f'wire [{w-1}:0] data=din[{s+w-1}:{s}];']
    if kind=='addressed':
        lines += [f'wire [{a-1}:0] addr=din[{s+w+a-1}:{s+w}];',f'wire we=din[{ni-1}];']
        for i in range(r):lines.append(f'assign dout[{i*w} +: {w}]=(we && addr=={a}\'d{i}) ? data : old[{i*w} +: {w}];')
        lines += [f'reg [{w-1}:0] read_data;',"always @* begin read_data=0;case(addr)"]
        for i in range(r):lines.append(f"{a}'d{i}:read_data=old[{i*w} +: {w}];")
        lines += ['default:begin end','endcase end',f'assign dout[{s} +: {w}]=read_data;']
    else:
        lines += [f'wire advance=din[{s+w}],we=din[{s+w+1}];']
        for i in range(r):
            incoming=f'old[{(i+1)%r*w} +: {w}]' if i<r-1 else f'(we ? data : old[0 +: {w}])'
            lines.append(f'assign dout[{i*w} +: {w}]=advance ? {incoming} : old[{i*w} +: {w}];')
        lines.append(f'assign dout[{s} +: {w}]=old[0 +: {w}];')
    return '\n'.join(lines+['endmodule',''])


def cases(kind,r,w,seed=260640):
    rng=random.Random(seed);s=r*w;a=(r-1).bit_length();mask=(1<<w)-1;xs=[];ys=[]
    for _ in range(256):
        old=[rng.getrandbits(w) for _ in range(r)];data=rng.getrandbits(w);we=rng.randrange(2)
        flat=sum(v<<(i*w) for i,v in enumerate(old));nxt=list(old)
        if kind=='addressed':
            addr=rng.randrange(r);x=flat+(data<<s)+(addr<<(s+w))+(we<<(s+w+a));out=old[addr]
            if we:nxt[addr]=data
        else:
            advance=rng.randrange(2);x=flat+(data<<s)+(advance<<(s+w))+(we<<(s+w+1));out=old[0]
            if advance:nxt=old[1:]+[data if we else old[0]]
        xs.append(x);ys.append(sum(v<<(i*w) for i,v in enumerate(nxt))+(out<<s))
    return xs,ys


def cursor_counter(rows):
    bits=(rows-1).bit_length();b=Builder(bits+1)
    old=list(range(2,bits+2));advance=bits+2
    nxt=b.add(old,[0]*bits,advance)[0]
    net=b.finish(nxt+old);xs=list(range(1<<(bits+1)))
    ys=[((x%rows+x//rows)%rows)+((x%rows)<<bits) for x in xs]
    receipt=verify_state(net,xs,ys,bits)
    return receipt


def sequence(net,r,w):
    # One conceptual C array, one physical ring. The cursor makes permutation
    # explicit; every arbitrary-address request is lowered to rotations.
    rng=random.Random(260640);bank=[0]*r;state=bytes(net.n_state);cursor=0;clocks=0
    def step(data,advance,we):
        nonlocal state,cursor,clocks
        packed=data+(advance<<w)+(we<<(w+1));ins=bytes(packed>>i&1 for i in range(w+2))
        state,out=net.step(state,ins);clocks+=1
        value=sum(b<<i for i,b in enumerate(out));assert value==bank[cursor]
        if advance:
            if we:bank[cursor]=data
            cursor=(cursor+1)%r
        for row in range(r):
            got=sum(state[row*w+i]<<i for i in range(w))
            assert got==bank[(cursor+row)%r]
    requests=64
    for _ in range(requests):
        addr=rng.randrange(r);data=rng.getrandbits(w);we=rng.randrange(2)
        while cursor!=addr:step(0,1,0)
        step(data,we,we)
    return dict(requests=requests,physical_clocks=clocks,all_physical_rows_checked=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true','Large simulation/EDA only on Actions'
    else:signal.alarm(55)
    out=ROOT/'build/integer_opt/ports';out.mkdir(parents=True,exist_ok=True)
    results=[]
    # One fixed proposal at a small verification size and the actual KV size.
    for r,w in ((4,8),(32,276)):
        for kind in ('addressed','ring'):
            net,state=make(kind,r,w);name=f'{kind}_{r}x{w}';p=out/name
            row=dict(name=name,rows=r,width=w,combinational=metrics(net),stateful=metrics(state),
                     controller_cursor_bits=(r-1).bit_length() if kind=='ring' else 0,
                     cursor_scope='external scheduling state, additional unless an existing loop counter supplies it')
            if kind=='ring':
                cursor=cursor_counter(r)
                (out/(name+'.cursor.json')).write_text(json.dumps(cursor,indent=2)+'\n')
                row['cursor']={k:v for k,v in cursor.items() if k!='nl_hex'}
                row['bank_with_cursor']=dict(nNand=row['stateful']['nNand']+cursor['nNand'],
                                            nLatch=row['stateful']['nLatch']+cursor['nLatch'])
            p.with_suffix('.v').write_text(verilog(net));p.with_suffix('.ref.v').write_text(reference(kind,r,w))
            p.with_suffix('.nl').write_bytes(state.encode());p.with_suffix('.blif').write_text(blif(net))
            if len(net.records)<=4000 or args.cloud:
                xs,ys=cases(kind,r,w);row['vectors']=verify(net,xs,ys);row['latch_check']=verify_state(net,xs,ys,r*w)
                row['latch_check'].pop('nl_hex')
                if kind=='ring':row['access_sequence']=sequence(state,r,w)
            else:row['verification']='construction only; large gate evaluation deferred to Actions'
            if args.cloud:
                abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
                ys=p.with_suffix('.ys')
                ys.write_text(f'read_verilog {p}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {p}.ref.json\n')
                subprocess.run(['yosys','-Q','-T','-l',str(p)+'.yosys.log','-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
                mapped=from_yosys(json.loads(p.with_suffix('.ref.json').read_text()),net.n_in,net.n_out)
                p.with_suffix('.reference.blif').write_text(blif(mapped))
                proof=cec(abc,p.with_suffix('.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.cec.log'))
                assert proof['verdict']=='equivalent';row['proof_against_independent_rtl']=proof
                p.with_suffix('.negative.blif').write_text(blif(flip_output(net)))
                bad=cec(abc,p.with_suffix('.negative.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.negative.log'))
                assert bad['verdict']=='different';row['cec_negative']=bad
                row['mapped_reference']=metrics(with_state(mapped,r*w))
            results.append(row)
    report=dict(status='cloud proof pass' if args.cloud else 'small gate checks pass; actual-size construction only',
                numerical_contract_changed=False,results=results,
                run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    for row in results:print(row['name'],row['stateful']['nNand'],row['stateful']['nLatch'])


if __name__=='__main__':main()
