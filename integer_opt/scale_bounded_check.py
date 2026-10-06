#!/usr/bin/env python3
"""Check shorter exact scaling schedules on actual <=4k NAND arithmetic units."""
import ctypes as ct
import hashlib
import json
from pathlib import Path
import random
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from physical.export import load_unit,MODEL_SHA
from golden import Netlist
from gate_check import Snapshot
from nand import metrics

OUT=ROOT/'build/integer_opt/scale_bounded';sha=lambda b:hashlib.sha256(b).hexdigest()


def check(net,inputs,steps,expected,offset,width):
    assert metrics(net)['nNand']<=4000
    raw=net.encode();dec=Snapshot.decode(raw,net.n_in,net.n_out)
    loads=[bytes(value>>i&1 for i in range(net.n_in)) for value in inputs]
    rng=random.Random(260659);idle=[bytes([0]+[rng.randrange(2) for _ in range(net.n_in-1)]) for _ in inputs]
    def simulate(graph,count):
        state=[bytes(graph.n_state) for _ in inputs]
        state=[s for s,_ in graph.step_simd(state,loads)]
        for _ in range(count):state=[s for s,_ in graph.step_simd(state,idle)]
        return [sum(bit<<j for j,bit in enumerate(out[offset:offset+width])) for _,out in graph.step_simd(state,idle)]
    assert simulate(dec,steps)==expected
    early=sum(x!=y for x,y in zip(simulate(dec,steps-1),expected));assert early>0
    bad=Netlist.decode(raw,net.n_in,net.n_out);index=len(bad.records)-bad.n_out+offset
    inv=bad.records[index][1];_,a,b=bad.records[inv-bad.n_in-2];assert a==b
    bad.records[index]=(0,a,a)
    negative=sum(x!=y for x,y in zip(simulate(Snapshot.decode(bad.encode(),net.n_in,net.n_out),steps),expected));assert negative>0
    return dict(metrics=metrics(net),vectors=len(inputs),steps_after_load=steps,mismatches=0,
                one_too_few_steps_mismatches=early,actual_result_gate_mutation_mismatches=negative,
                negative_sha256=sha(bad.encode()))


def main():
    signal.alarm(55);begin=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    so=OUT/'golden.so';subprocess.run(['cc','-O2','-std=c99','-shared','-fPIC',str(ROOT/'physical/golden_slice.c'),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));g.int_rne.argtypes=[ct.c_int64,ct.c_int64];g.int_rne.restype=ct.c_int64
    g.slice_sat.argtypes=[ct.c_int64];g.slice_sat.restype=ct.c_int32
    rng=random.Random(260659)
    cases=[(d,m,a) for d in (-65536,-1,0,1,65535) for m in (0,1,1048575) for a in (0,1,262143)]
    cases += [(127*k,131072,1) for k in (-7,-5,-3,-1,1,3,5,7)]
    factors=[int.from_bytes(blob[243208+4*j:243212+4*j],'little') for j in range(35)]
    cases += [(rng.randint(-65536,65535),rng.randrange(1<<20),a) for a in factors]
    cases += [(rng.randint(-65536,65535),rng.randrange(1<<20),rng.randrange(1<<18)) for _ in range(128)]
    mask=(1<<64)-1;mul=load_unit('serial_mul');steps={}
    for name,count in (('multiply_dot_maximum',20),('multiply_alpha',18)):
        xy=[(d,m) if count==20 else (d*m,a) for d,m,a in cases]
        inputs=[1+((x&mask)<<1)+(y<<65) for x,y in xy]
        steps[name]=check(mul,inputs,count,[(x*y)&mask for x,y in xy],mul.n_state,64)
    meta=json.loads((ROOT/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
    raw=(ROOT/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
    div=Netlist.decode(raw,meta['nIn'],meta['nOut']);inputs=[];expected=[]
    for dot,m,a in cases:
        n=dot*m*a;assert abs(n)<1<<54 and -(1<<63)<n*(1<<9)<1<<63
        inputs.append(1+(((n<<9)&mask)<<1)+(33292288<<65))
        value=g.int_rne(n,33292288);expected.append((value&mask)+((g.slice_sat(value)&1048575)<<64))
    steps['divide_shifted55']=check(div,inputs,55,expected,div.n_state,84)
    result=dict(status='pass',scope='actual small arithmetic units, not combined pipeline/engine',model_sha256=MODEL_SHA,
                steps=steps,cases=len(cases),seconds=time.monotonic()-begin,
                argument='m<2^20,alpha<2^18; multiplier shifts unsigned RHS. |dot*m*alpha|<2^54, so signed n<<9 fits64. Restoring divide consumes its leading55 bits in55 steps, yielding the original magnitude/remainder/sign for RNE.',
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'physical/golden_slice.c',
                    ROOT/'integer/int_model.c',ROOT/'physical/export.py',ROOT/'integer_opt/gate_check.py',ROOT/'golden.py',ROOT/'nand.py',
                    ROOT/'physical/units/manifest.json',ROOT/'integer_opt/pilot_units/manifest.json']})
    (OUT/'small_units.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
