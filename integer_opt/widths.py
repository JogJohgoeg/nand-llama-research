#!/usr/bin/env python3
"""Construction-only true-weight width tradeoff, with <=4k folded DOT checks.

The existing 32-lane selector pads each 336-column row. Widths 1/8/16 divide
every real matrix row, so dense row-major addresses need no padding/shifter.
Large selector gate evaluation is deferred to Actions; no local EDA.
"""
import argparse
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
from bench import lookup,verify
from nand import Builder,metrics
from physical.export import MODEL_SHA,import_net,load_unit


def dot_unit(lanes):
    b=Builder(10*lanes);q=list(range(2,2+8*lanes));w=list(range(2+8*lanes,2+10*lanes))
    _,out=import_net(b,load_unit('dot32'),q+[0]*(256-len(q))+w+[0]*(64-len(w)))
    net=b.finish(out);assert len(net.records)<=4000
    rng=random.Random(260644+lanes);xs=[];ys=[]
    for case in range(512):
        q=[rng.randint(-128,127) for _ in range(lanes)];w=[rng.randrange(3) for _ in range(lanes)]
        if case<4:q=[[-128,-127,0,127][case]]*lanes
        xs.append(sum((v&255)<<(8*i) for i,v in enumerate(q))+sum(v<<(8*lanes+2*i) for i,v in enumerate(w)))
        ys.append(sum(a*(b if b<2 else -1) for a,b in zip(q,w))&0xffffffff)
    check=verify(net,xs,ys);assert check['status']=='pass'
    return net,check


def main():
    signal.alarm(55);start=time.monotonic()
    ap=argparse.ArgumentParser();ap.add_argument('--lanes',required=True,type=int,choices=(1,8,16));args=ap.parse_args();lanes=args.lanes
    out=ROOT/'build/integer_opt/widths'/str(lanes);out.mkdir(parents=True,exist_ok=True)
    blob=(ROOT/'physical/model.bin').read_bytes();assert hashlib.sha256(blob).hexdigest()==MODEL_SHA
    assert all(n%lanes==0 for n in (128,336));count=972800//lanes
    if lanes==1:words=[blob[8+i//4]>>(2*(i%4))&3 for i in range(972800)]
    else:words=[int.from_bytes(blob[8+i*(lanes//4):8+(i+1)*(lanes//4)],'little') for i in range(count)]
    libpath=out/'golden.so';subprocess.run(['cc','-O2','-std=c99','-fPIC','-shared',str(ROOT/'integer_opt/widths_golden.c'),'-o',str(libpath)],check=True,timeout=30)
    g=ct.CDLL(str(libpath));g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.dense_weight_word.argtypes=[ct.c_uint,ct.c_uint];g.dense_weight_word.restype=ct.c_uint64
    addresses=1<<(count-1).bit_length()
    for i in range(addresses):assert g.dense_weight_word(i,lanes)==(words[i] if i<count else 0)
    dot,check=dot_unit(lanes)
    selector=lookup(words,2*lanes,'shannon')
    for name,net in [('selector',selector),('dot',dot)]:(out/(name+'.nl')).write_bytes(net.encode())
    result=dict(status='large selector construction and C packing only; small folded DOT gate checks pass',lanes=lanes,words=count,
                addresses=addresses,model_sha256=MODEL_SHA,selector=metrics(selector),dot=dict(metrics(dot),verification=check),
                padding='all rows exactly divisible; dense index=(layer*194560+matrix_offset+row*columns+column)/lanes',
                c16_dot_cycles_at_8_per_group=count*16*8,baseline_c16_dot_cycles=30720*16*8,
                numerical_contract_changed=False,seconds=time.monotonic()-start,
                sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'integer_opt/widths_golden.c',ROOT/'integer/int_model.c',ROOT/'physical/export.py',ROOT/'nand.py',ROOT/'bench.py',ROOT/'golden.py']})
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
