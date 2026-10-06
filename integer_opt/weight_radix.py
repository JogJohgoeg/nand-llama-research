#!/usr/bin/env python3
"""One fixed radix-4 constant selector: shared two-address-bit decoders.

Local mode builds/counts real tables and tests only small functions. Large
gate checks and optional mapping reuse the existing Actions-only checker.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench import lookup,verify
from nand import Builder,metrics
from physical.export import MODEL_SHA,weight_words
from weights import c_words,cloud_check


def radix4(table,width):
    n=max(1,(len(table)-1).bit_length());values=table+[0]*((1<<n)-len(table))
    b=Builder(n);memo={};decoders={}
    def node(truth,level):
        mask=(1<<(1<<level))-1
        if truth in (0,mask):return int(bool(truth))
        key=truth,level
        if key in memo:return memo[key]
        if level==1:out=b.mux(2,truth&1,truth>>1)
        else:
            if level not in decoders:
                bits=[level,level+1]
                decoders[level]=[b.reduce([x if j>>i&1 else b.inv(x) for i,x in enumerate(bits)],b.land,1) for j in range(4)]
            size=1<<(level-2);part_mask=(1<<size)-1
            terms=[b.nand(decoders[level][j],node((truth>>(j*size))&part_mask,level-2)) for j in range(4)]
            out=b.inv(b.reduce(terms,b.land,1))
        memo[key]=out;return out
    outputs=[node(sum((v>>bit&1)<<i for i,v in enumerate(values)),n) for bit in range(width)]
    return b.finish(outputs)


def fanout(net):
    counts=[0]*(net.n_in+2+len(net.records))
    for _,a,b in net.records:counts[a]+=1;counts[b]+=1
    peak=max(counts[2:]);wire=counts.index(peak,2)
    return dict(max_sink_pins=peak,wire=wire,input_sink_pins=counts[2:net.n_in+2],
                scope='source NAND input-pin loads, not buffered-cell fanout or routed capacitance')


def small():
    cases=[([int(truth>>i&1) for i in range(8)],1) for truth in range(256)]
    rng=random.Random(260656)
    for n in (1,3,4,7,9,16,29,33):
        cases.append(([rng.getrandbits(8) for _ in range(n)],8))
    receipts=[]
    for table,width in cases:
        net=radix4(table,width);assert len(net.records)<=4000
        expected=table+[0]*((1<<net.n_in)-len(table))
        receipts.append(dict(metrics=metrics(net),verification=verify(net,list(range(len(expected))),expected)))
    return dict(status='pass',cases=len(cases),results=receipts)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scope',required=True,choices=('small','layer0','all'))
    ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    out=ROOT/'build/integer_opt/weight_radix'/args.scope;out.mkdir(parents=True,exist_ok=True);start=time.monotonic()
    if args.scope=='small':result=small()
    else:
        blob=(ROOT/'physical/model.bin').read_bytes();assert hashlib.sha256(blob).hexdigest()==MODEL_SHA
        words=[w for layer in (range(5) if args.scope=='all' else range(1)) for w in weight_words(blob,layer)]
        expected=c_words(out,args.scope,words)
        baseline=lookup(words,64,'shannon');candidate=radix4(words,64)
        (out/'radix4.nl').write_bytes(candidate.encode())
        result=dict(status='construction and C table packing only; large gate checks not run',
                    scope=args.scope,words=len(words),addresses=len(expected),model_sha256=MODEL_SHA,
                    shannon=metrics(baseline),radix4=metrics(candidate),
                    fanout=dict(shannon=fanout(baseline),radix4=fanout(candidate)),
                    numerical_contract_changed=False)
        if args.cloud:result['verification']=cloud_check(out,{'radix4':candidate},expected);result['status']='cloud full-address/mapping/CEC checks pass'
    result.update(seconds=time.monotonic()-start,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                  sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),
                    ROOT/'integer_opt/weights.py',ROOT/'integer_opt/weights_golden.c',ROOT/'physical/export.py',
                    ROOT/'integer/int_model.c',ROOT/'nand.py',ROOT/'bench.py',ROOT/'golden.py',ROOT/'integer_opt/gate_check.py']})
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('sources','results','verification')},indent=2))


if __name__=='__main__':main()
