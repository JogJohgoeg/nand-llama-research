#!/usr/bin/env python3
"""Exact constant-weight compression: five base-3 digits per eight bits.

The constants still form NAND logic. A small combinational decoder restores
the unchanged 32-lane 00/01/10 interface; there is no ROM or runtime division.
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
from physical.export import MODEL_SHA,import_net,weight_words
from weights import c_words,cloud_check

GROUPS=(5,5,5,5,5,5,2)
WIDTHS=tuple((3**n-1).bit_length() for n in GROUPS)


def pack(word):
    result=offset=lane=0
    for n,width in zip(GROUPS,WIDTHS):
        digits=[word>>(2*(lane+i))&3 for i in range(n)]
        assert all(d<3 for d in digits)
        result+=sum(d*3**i for i,d in enumerate(digits))<<offset
        offset+=width;lane+=n
    assert lane==32 and offset==52
    return result


def decoder(n):
    width=(3**n-1).bit_length();table=[]
    for code in range(1<<width):
        value=code;word=0
        if code<3**n:
            for i in range(n):
                value,digit=divmod(value,3);word+=digit<<(2*i)
        table.append(word)
    return lookup(table,2*n,'shannon'),table


def make(words):
    packed=lookup([pack(w) for w in words],sum(WIDTHS),'shannon')
    b=Builder(packed.n_in);_,selected=import_net(b,packed,list(range(2,packed.n_in+2)))
    offset=0;outputs=[]
    for n,width in zip(GROUPS,WIDTHS):
        _,trits=import_net(b,decoder(n)[0],selected[offset:offset+width])
        outputs.extend(trits);offset+=width
    return b.finish(outputs),packed


def small():
    decoders={}
    for n in (2,5):
        net,table=decoder(n)
        # Independent weighted-sum inverse, including all unused binary codes.
        for code,word in enumerate(table):
            if code<3**n:assert sum((word>>(2*i)&3)*3**i for i in range(n))==code
            else:assert word==0
        decoders[str(n)]=dict(metrics=metrics(net),verification=verify(net,list(range(len(table))),table))
    rng=random.Random(260657);cases=[]
    tables=[[0,0x5555555555555555,0xaaaaaaaaaaaaaaaa]]
    tables += [[sum(rng.randrange(3)<<(2*i) for i in range(32)) for _ in range(n)] for n in (1,3,7,15)]
    for words in tables:
        net,_=make(words);assert metrics(net)['nNand']<=4000
        expected=words+[0]*((1<<net.n_in)-len(words))
        cases.append(dict(metrics=metrics(net),verification=verify(net,list(range(len(expected))),expected)))
    return dict(status='pass',decoders=decoders,tables=cases)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scope',required=True,choices=('small','layer0','all'))
    ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    out=ROOT/'build/integer_opt/weight_pack'/args.scope;out.mkdir(parents=True,exist_ok=True);start=time.monotonic()
    if args.scope=='small':result=small()
    else:
        blob=(ROOT/'physical/model.bin').read_bytes();assert hashlib.sha256(blob).hexdigest()==MODEL_SHA
        words=[w for layer in (range(5) if args.scope=='all' else range(1)) for w in weight_words(blob,layer)]
        expected=c_words(out,args.scope,words);net,packed=make(words)
        (out/'packed_decoded.nl').write_bytes(net.encode())
        result=dict(status='construction and C table packing only; full-size gates not evaluated',scope=args.scope,
                    words=len(words),addresses=len(expected),model_sha256=MODEL_SHA,metrics=metrics(net),
                    packed_selector=metrics(packed),decode5=metrics(decoder(5)[0]),decode2=metrics(decoder(2)[0]),
                    packing='six groups of five trits in u8, then two trits in u4; little endian within each group',
                    numerical_contract_changed=False)
        if args.cloud:result['validation']=cloud_check(out,{'packed_decoded':net},expected);result['status']='full source/mapped address and CEC checks pass'
    result.update(seconds=time.monotonic()-start,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                  sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),
                    ROOT/'integer_opt/weights.py',ROOT/'integer_opt/weights_golden.c',ROOT/'physical/export.py',
                    ROOT/'integer/int_model.c',ROOT/'nand.py',ROOT/'bench.py',ROOT/'golden.py',ROOT/'integer_opt/gate_check.py']})
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('sources','tables','validation')},indent=2))


if __name__=='__main__':main()
