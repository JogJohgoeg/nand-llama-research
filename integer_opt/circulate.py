#!/usr/bin/env python3
"""Always-rotating NAND/LATCH bank candidate; large checks run only on Actions.

Each clock rotates every row. A write replaces the departing head; the cursor
advances even during unrelated computation. Reset initializes only the cursor
and discards the logical meaning of old data. It is not a live-data rewind.
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
from ci import cec
from gate_check import Snapshot,verify
from nand import Builder,blif,flip_output,from_yosys,metrics,verilog,with_state


def make(rows,width):
    assert rows>1 and rows&(rows-1)==0
    a=(rows-1).bit_length();s=rows*width+a
    b=Builder(s+width+2)
    old=[list(range(2+i*width,2+(i+1)*width)) for i in range(rows)]
    cursor=list(range(2+rows*width,2+s));data=list(range(2+s,2+s+width))
    we,reset=2+s+width,3+s+width
    write=b.land(we,b.inv(reset))
    tail=[b.mux(write,x,y) for x,y in zip(old[0],data)]
    advance=b.add(cursor,[0]*a,1)[0]
    next_cursor=[b.land(b.inv(reset),x) for x in advance]
    nxt=[x for row in old[1:] for x in row]+tail+next_cursor
    combin=b.finish(nxt+old[0]+cursor)
    return combin,with_state(combin,s)


def reference(rows,width):
    a=(rows-1).bit_length();s=rows*width+a;ni=s+width+2
    return '\n'.join([
        f'module top(input [{ni-1}:0] din,output [{s+width+a-1}:0] dout);',
        f'wire [{rows*width-1}:0] old=din[{rows*width-1}:0];',
        f'wire [{a-1}:0] cursor=din[{s-1}:{rows*width}];',
        f'wire [{width-1}:0] data=din[{s+width-1}:{s}];',
        f'wire write=din[{ni-2}] & ~din[{ni-1}];',
        f'assign dout[{(rows-1)*width-1}:0]=old[{rows*width-1}:{width}];',
        f'assign dout[{(rows-1)*width} +: {width}]=write ? data : old[0 +: {width}];',
        f"assign dout[{rows*width} +: {a}]=din[{ni-1}] ? {a}'d0 : cursor+{a}'d1;",
        f'assign dout[{s} +: {width}]=old[0 +: {width}];',
        f'assign dout[{s+width} +: {a}]=cursor;',
        'endmodule',''])


def cases(rows,width):
    rng=random.Random(260646);a=(rows-1).bit_length();s=rows*width+a;xs=[];ys=[]
    for case in range(256):
        old=[rng.getrandbits(width) for _ in range(rows)]
        cursor=rng.randrange(rows);data=rng.getrandbits(width)
        we=(case>>1)&1;reset=case&1
        nxt=old[1:]+[data if we and not reset else old[0]]
        c=0 if reset else (cursor+1)%rows
        x=sum(v<<(i*width) for i,v in enumerate(old))+(cursor<<(rows*width))
        x+=(data<<s)+(we<<(s+width))+(reset<<(s+width+1))
        y=sum(v<<(i*width) for i,v in enumerate(nxt))+(c<<(rows*width))
        y+=(old[0]<<s)+(cursor<<(s+width));xs.append(x);ys.append(y)
    return xs,ys


def state_check(combin,net,rows,width,xs,ys):
    s=rows*width+(rows-1).bit_length()
    decoded=Snapshot.decode(net.encode(),net.n_in,net.n_out)
    ss=[bytes(x>>i&1 for i in range(s)) for x in xs]
    ins=[bytes(x>>(s+i)&1 for i in range(net.n_in)) for x in xs]
    out=decoded.step_simd(ss,ins)
    assert [sum(v<<i for i,v in enumerate(state+y)) for state,y in out]==ys
    # A real write-path NAND is stuck at zero, not a changed expected value.
    d=decoded.records[(rows-1)*width][1]
    index=d-(decoded.n_in+2);assert decoded.records[index][0]==0
    records=list(decoded.records);records[index]=(0,1,1)
    bad=Snapshot(decoded.n_in,decoded.n_out,records)
    bad_out=bad.step_simd(ss,ins);mismatches=sum(a!=b for a,b in zip(out,bad_out))
    assert mismatches>0
    return dict(vectors=len(xs),mismatches=0,actual_tail_nand_mutation_rejected=mismatches,
                negative_sha256=hashlib.sha256(bad.encode()).hexdigest())


def sequence(net,rows,width):
    decoded=Snapshot.decode(net.encode(),net.n_in,net.n_out)
    state=bytes(decoded.n_state);logical=[0]*rows;cursor=0;clocks=reads=writes=gaps=waits=0
    rng=random.Random(260647);a=(rows-1).bit_length()
    def step(data=0,we=False):
        nonlocal state,cursor,clocks
        packed=data+(int(we)<<width)
        state,out=decoded.step(state,bytes(packed>>i&1 for i in range(width+2)))
        got=sum(v<<i for i,v in enumerate(out));assert got==logical[cursor]+(cursor<<width)
        if we:logical[cursor]=data
        cursor=(cursor+1)%rows;clocks+=1
        expect=b''.join(bytes(word>>i&1 for i in range(width)) for word in logical[cursor:]+logical[:cursor])
        expect+=bytes(cursor>>i&1 for i in range(a));assert state==expect
    # Fill all rows, then inject unrelated computation clocks before requests.
    for _ in range(rows):step(rng.getrandbits(width),True)
    for _ in range(32):
        idle=rng.randrange(2*rows);gaps+=idle
        for _ in range(idle):step()
        address=rng.randrange(rows);write=bool(rng.randrange(2))
        while cursor!=address:step();waits+=1
        step(rng.getrandbits(width),write)
        writes+=int(write);reads+=int(not write)
    return dict(requests=32,physical_clocks=clocks,unrelated_clocks=gaps,
                alignment_clocks=waits,request_reads=reads,request_writes=writes,
                all_physical_rows_and_cursor_checked=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true','Large gate checks and EDA only on Actions'
    else:signal.alarm(55)
    out=ROOT/'build/integer_opt/circulate';out.mkdir(parents=True,exist_ok=True);results=[]
    for rows,width in ((4,8),(32,276),(64,640)):
        combin,net=make(rows,width);name=f'circulate_{rows}x{width}';p=out/name
        row=dict(name=name,rows=rows,width=width,combinational=metrics(combin),stateful=metrics(net))
        p.with_suffix('.nl').write_bytes(net.encode());p.with_suffix('.v').write_text(verilog(combin))
        p.with_suffix('.ref.v').write_text(reference(rows,width));p.with_suffix('.blif').write_text(blif(combin))
        if rows*width<=32 or args.cloud:
            xs,ys=cases(rows,width);row['vectors']=verify(combin,xs,ys)
            row['latch_check']=state_check(combin,net,rows,width,xs,ys)
            row['access_sequence']=sequence(net,rows,width)
        else:row['verification']='construction only; actual-size gate evaluation deferred to Actions'
        if args.cloud:
            abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
            script=p.with_suffix('.ys')
            script.write_text(f'read_verilog {p}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {p}.ref.json\n')
            subprocess.run(['yosys','-Q','-T','-l',str(p)+'.yosys.log','-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=240)
            mapped=from_yosys(json.loads(p.with_suffix('.ref.json').read_text()),combin.n_in,combin.n_out)
            p.with_suffix('.reference.blif').write_text(blif(mapped))
            proof=cec(abc,p.with_suffix('.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.cec.log'))
            assert proof['verdict']=='equivalent';row['proof_against_independent_rtl']=proof
            p.with_suffix('.negative.blif').write_text(blif(flip_output(combin)))
            bad=cec(abc,p.with_suffix('.negative.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.negative.log'))
            assert bad['verdict']=='different';row['cec_negative']=bad
        results.append(row)
    report=dict(status='cloud component proof pass' if args.cloud else 'small component passes; actual sizes construction only',
                scope='candidate bank; full-model clock scheduling and physical power not yet evaluated',
                clock_contract='Every clock rotates; reads consume a clock. Reset zeros cursor but discards old logical data validity.',
                results=results,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'integer_opt/gate_check.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    for row in results:print(row['name'],row['stateful']['nNand'],row['stateful']['nLatch'],row.get('latch_check'))


if __name__=='__main__':main()
