#!/usr/bin/env python3
"""Exact FF buffer: ten32-lane words plus one16-lane tail, no padded LATCH.

Address0..10 selects a word; other addresses read zero and ignore writes.
The last word's high16 lanes are wired zero. State is336*bits exactly.
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
from nand import Builder,blif,flip_output,from_yosys,metrics,verify_state,with_state

OUT=ROOT/'build/integer_opt/ff_bank'
sha=lambda data:hashlib.sha256(data).hexdigest()


def shape(bits,rows=11,lanes=32,last=16):
    assert rows>=2 and 1<=last<=lanes
    a=(rows-1).bit_length();width=lanes*bits;sizes=[width]*(rows-1)+[last*bits]
    return a,width,sizes,sum(sizes)


def make(bits,rows=11,lanes=32,last=16):
    a,width,sizes,ns=shape(bits,rows,lanes,last);b=Builder(ns+width+a+1)
    old=list(range(2,ns+2));data=list(range(ns+2,ns+2+width));addr=list(range(ns+2+width,ns+2+width+a));we=ns+2+width+a
    nxt=[];words=[];offset=0
    for row,size in enumerate(sizes):
        held=old[offset:offset+size];offset+=size
        hit=b.reduce([bit if row>>j&1 else b.inv(bit) for j,bit in enumerate(addr)],b.land,1)
        enable=b.land(we,hit);nxt += [b.mux(enable,x,y) for x,y in zip(held,data)]
        words.append(held+[0]*(width-size))
    words += [[0]*width for _ in range((1<<a)-rows)]
    for bit in addr:words=[[b.mux(bit,x,y) for x,y in zip(words[i],words[i+1])] for i in range(0,len(words),2)]
    comb=b.finish(nxt+words[0]);net=with_state(comb,ns)
    assert net.n_state==ns
    return comb,net


def cases(bits,rows=11,lanes=32,last=16):
    a,width,sizes,ns=shape(bits,rows,lanes,last);rng=random.Random(260668+bits+rows);xs=[];ys=[]
    for i in range(512):
        values=[rng.getrandbits(n) for n in sizes];data=rng.getrandbits(width);addr=i%(1<<a);we=(i//(1<<a))&1
        encoded=0;offset=0
        for value,n in zip(values,sizes):encoded|=value<<offset;offset+=n
        xs.append(encoded+((data+(addr<<width)+(we<<(width+a)))<<ns))
        read=values[addr] if addr<rows else 0
        if we and addr<rows:values[addr]=data&((1<<sizes[addr])-1)
        wanted=0;offset=0
        for value,n in zip(values,sizes):wanted|=value<<offset;offset+=n
        ys.append(wanted+(read<<ns))
    return xs,ys


def reference(bits):
    a,width,sizes,ns=shape(bits);ni=ns+width+a+1
    lines=[f'module top(input [{ni-1}:0] din,output [{ns+width-1}:0] dout);',
           f'wire [{width-1}:0] data=din[{ns} +: {width}];',f'wire [{a-1}:0] addr=din[{ns+width} +: {a}];',f'wire we=din[{ni-1}];',
           f'reg [{width-1}:0] value;']
    offset=0
    for row,size in enumerate(sizes):
        lines.append(f'assign dout[{offset} +: {size}]=(we && addr=={a}\'d{row}) ? data[{size-1}:0] : din[{offset} +: {size}];');offset+=size
    lines+=['always @* begin','  value=0;','  case(addr)'];offset=0
    for row,size in enumerate(sizes):
        read=f'din[{offset} +: {size}]';offset+=size
        if size<width:read='{'+str(width-size)+"'d0,"+read+'}'
        lines.append(f"    {a}'d{row}: value={read};")
    lines+=['    default: value=0;','  endcase','end',f'assign dout[{ns} +: {width}]=value;','endmodule','']
    return '\n'.join(lines)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True)
    comb,net=make(8,3,4,2);assert metrics(net)['nNand']<=4000
    xs,ys=cases(8,3,4,2);small=verify_state(comb,xs,ys,net.n_state);small.pop('nl_hex')
    results={}
    for bits in (20,8):
        target=OUT/str(bits);target.mkdir(exist_ok=True);comb,net=make(bits)
        (target/'bank.nl').write_bytes(net.encode());(target/'bank.blif').write_text(blif(comb));(target/'bank.ref.v').write_text(reference(bits))
        result=dict(metrics=metrics(net),state_bits=336*bits,tail_zero_lanes=16,status='construction only')
        if args.cloud:
            xs,ys=cases(bits);check=verify_state(comb,xs,ys,net.n_state);check.pop('nl_hex')
            abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
            script=target/'proof.ys';script.write_text(f'read_verilog {target}/bank.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {target}/reference.json\n')
            subprocess.run(['yosys','-Q','-T','-l',str(target/'yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=240)
            ref=from_yosys(json.loads((target/'reference.json').read_text()),comb.n_in,comb.n_out)
            (target/'reference.blif').write_text(blif(ref));(target/'negative.blif').write_text(blif(flip_output(comb)))
            proof=cec(abc,target/'bank.blif',target/'reference.blif',target/'cec.log');assert proof['verdict']=='equivalent'
            negative=cec(abc,target/'negative.blif',target/'reference.blif',target/'negative.log');assert negative['verdict']=='different'
            result.update(status='independent arbitrary-state RTL CEC and actual LATCH/gate checks pass',proof=proof,negative=negative,state_check=check)
        results[str(bits)]=result
    report=dict(status='both FF bank constructions, small-state checks; actual-size proof only on Actions',small=small,banks=results,
        contract='data low32*bits,addr next4,we last; read pre-edge; addresses11..15 read0/writeignored; high16 lanes of last word always0',
        scope='raw20 and A8-only storage banks; caller recomputation schedule and controller separate',adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
