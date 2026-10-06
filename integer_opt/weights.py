#!/usr/bin/env python3
"""True-weight selector: zero weights leave the sign function unconstrained.

One fixed deterministic construction, no solver or parameter search. Outputs
retain the original 00/01/10 trit code. Full gate checking/CEC stays on Actions.
"""
import argparse
import ctypes as ct
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bench import lookup,verify as legacy_verify
from ci import cec,script
from nand import Builder,blif,flip_output,from_yosys,metrics,verilog
from physical.export import MODEL_SHA,weight_words
from gate_check import verify


def signed_care(table,lanes):
    n=max(1,(len(table)-1).bit_length());values=table+[0]*((1<<n)-len(table))
    b=Builder(n);memo={};partial={}
    def exact(truth,level):
        mask=(1<<(1<<level))-1
        if truth==0 or truth==mask:return int(bool(truth))
        key=truth,level
        if key not in memo:
            half=1<<(level-1);lowmask=(1<<half)-1
            lo=exact(truth&lowmask,level-1);hi=exact(truth>>half,level-1)
            memo[key]=b.mux(level+1,lo,hi)
        return memo[key]
    def sign(pos,neg,level):
        # At a zero trit either sign is valid; decoded outputs mask it by enable.
        if not neg:return 0
        if not pos:return 1
        if neg>pos:return b.inv(sign(neg,pos,level))
        key=pos,neg,level
        if key not in partial:
            half=1<<(level-1);mask=(1<<half)-1
            pl,ph=pos&mask,pos>>half;nl,nh=neg&mask,neg>>half
            if not ((pl&nh)|(ph&nl)):
                # Cofactors never demand opposite signs at the same address.
                # One shared completion satisfies both, eliminating this mux.
                out=sign(pl|ph,nl|nh,level-1)
            else:out=b.mux(level+1,sign(pl,nl,level-1),sign(ph,nh,level-1))
            partial[key]=out
        return partial[key]
    outputs=[]
    for lane in range(lanes):
        pos=sum(int((word>>(2*lane)&3)==1)<<a for a,word in enumerate(values))
        neg=sum(int((word>>(2*lane)&3)==2)<<a for a,word in enumerate(values))
        en=exact(pos|neg,n);sgn=sign(pos,neg,n)
        outputs.extend((b.land(en,b.inv(sgn)),b.land(en,sgn)))
    return b.finish(outputs)


def small():
    checks=[]
    # All 81 four-address one-lane ternary functions, plus multilanes/padding.
    cases=[list(x) for x in itertools.product(range(3),repeat=4)]
    rng=random.Random(260640)
    cases += [[0]*29,[(1<<8)-1 & 0x55]*29,[0xaa]*29,
              [sum(rng.randrange(3)<<(2*i) for i in range(4)) for _ in range(29)]]
    for i,table in enumerate(cases):
        lanes=1 if i<81 else 4;net=signed_care(table,lanes)
        assert len(net.records)<=4000
        expected=table+[0]*((1<<net.n_in)-len(table))
        result=verify(net,list(range(len(expected))),expected)
        assert result==legacy_verify(net,list(range(len(expected))),expected)
        checks.append(dict(case=i,**metrics(net),verification=result))
    return dict(status='pass',cases=len(checks),results=checks)


def c_words(out,scope,words):
    libpath=out/'weights.so'
    subprocess.run(['cc','-O2','-std=c99','-fPIC','-shared',str(ROOT/'integer_opt/weights_golden.c'),'-o',str(libpath)],check=True,timeout=30)
    g=ct.CDLL(str(libpath));g.int_init.argtypes=[ct.c_void_p,ct.c_int]
    blob=(ROOT/'physical/model.bin').read_bytes();assert hashlib.sha256(blob).hexdigest()==MODEL_SHA
    assert g.int_init(blob,len(blob))==0
    g.true_weight_word.argtypes=[ct.c_uint,ct.c_uint];g.true_weight_word.restype=ct.c_uint64
    count=1<<max(1,(len(words)-1).bit_length())
    golden=[g.true_weight_word(i,int(scope=='all')) for i in range(count)]
    assert golden==words+[0]*(count-len(words))
    return golden


def cloud_check(out,nets,golden):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    def exhaustive(net):
        # Every address, bounded SIMD memory, real output mutation per batch.
        batches=[]
        for start in range(0,len(golden),2048):
            batches.append(verify(net,list(range(start,min(start+2048,len(golden)))),golden[start:start+2048]))
        assert all(x['status']=='pass' for x in batches)
        return dict(addresses=len(golden),batches=batches)
    result=dict(status='in_progress',cases={})
    for name,net in nets.items():
        before=exhaustive(net);p=out/name;p.with_suffix('.v').write_text(verilog(net))
        ys=p.with_suffix('.ys');ys.write_text(script(p));begin=time.monotonic()
        subprocess.run(['yosys','-Q','-T','-l',str(p)+'.yosys.log','-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=300)
        mapped=from_yosys(json.loads(p.with_suffix('.mapped.yosys.json').read_text()),net.n_in,net.n_out)
        mapping_seconds=time.monotonic()-begin
        p.with_suffix('.mapped.nl').write_bytes(mapped.encode());after=exhaustive(mapped)
        for label,graph in (('source',net),('mapped',mapped),('negative',flip_output(mapped))):
            p.with_suffix('.'+label+'.blif').write_text(blif(graph))
        proof=cec(abc,p.with_suffix('.source.blif'),p.with_suffix('.mapped.blif'),p.with_suffix('.cec.log'))
        negative=cec(abc,p.with_suffix('.source.blif'),p.with_suffix('.negative.blif'),p.with_suffix('.negative.log'))
        assert proof['verdict']=='equivalent' and negative['verdict']=='different',(proof,negative)
        result['cases'][name]=dict(before=before,after=after,mapped=metrics(mapped),mapping_seconds=mapping_seconds,cec=proof,negative=negative)
        (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    result['status']='pass'
    (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scope',choices=('small','layer0','all'),required=True)
    ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true','Large gates and ABC only on Actions'
    else:signal.alarm(55)
    out=ROOT/'build/integer_opt/weights'/args.scope;out.mkdir(parents=True,exist_ok=True);begin=time.monotonic()
    if args.scope=='small':result=small()
    else:
        blob=(ROOT/'physical/model.bin').read_bytes();assert hashlib.sha256(blob).hexdigest()==MODEL_SHA
        words=[w for layer in (range(5) if args.scope=='all' else range(1)) for w in weight_words(blob,layer)]
        golden=c_words(out,args.scope,words)
        nets={method:lookup(words,64,method) for method in ('shannon','phase')}
        counts={k:metrics(v) for k,v in nets.items()}
        rejected=metrics(signed_care(words,32))
        if args.scope=='all':
            assert counts['shannon']['nNand']==323304
            assert counts['shannon']['sha256']=='c897a3290761da3fad1c000bc0d71ad920eeca15e202dc56635da11d09da0774'
        for name,net in nets.items():(out/(name+'.nl')).write_bytes(net.encode())
        result=dict(status='in_progress',scope=args.scope,words=len(words),addresses=len(golden),
                    model_sha256=MODEL_SHA,table_sha256=hashlib.sha256(json.dumps(words).encode()).hexdigest(),
                    metrics=counts,numerical_contract_changed=False,
                    rejected_signed_care=dict(metrics=rejected,reason='more source NAND than Shannon; full-size gate checks not run for rejected candidate'))
        (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
        check=cloud_check(out,nets,golden) if args.cloud else dict(status='construction and C table packing only; full gate checks await Actions')
        result.update(status=check['status'],validation=check)
    result.update(seconds=time.monotonic()-begin,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                  sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),ROOT/'integer_opt/gate_check.py',ROOT/'integer_opt/weights_golden.c',ROOT/'integer/int_model.c',ROOT/'physical/export.py',ROOT/'nand.py',ROOT/'bench.py',ROOT/'golden.py')})
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','validation','sources')},indent=2))


if __name__=='__main__':main()
