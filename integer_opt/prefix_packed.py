#!/usr/bin/env python3
"""C16 prefix-bank candidate, 64 rows of 32 signed16 storage codes.

Numerical codec quality is evaluated separately. This checks the actual
storage transition against independent RTL, without changing current GDS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ports
from ci import cec
from gate_check import Snapshot,verify
from nand import blif,flip_output,from_yosys,metrics,verify_state
from prefix_codec import make as codec

OUT=ROOT/'build/integer_opt/prefix_packed'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True)
    small,_=ports.make('ring',4,32);xs,ys=ports.cases('ring',4,32)
    small_check=verify_state(small,xs,ys,128);small_check.pop('nl_hex')
    combin,net=ports.make('ring',64,512);cursor=ports.cursor_counter(64);cursor.pop('nl_hex')
    costs=metrics(net);code=metrics(codec())
    (OUT/'bank.nl').write_bytes(net.encode());(OUT/'bank.blif').write_text(blif(combin))
    (OUT/'bank.ref.v').write_text(ports.reference('ring',64,512))
    report=dict(status='small bank actual checks pass; full-size construction only',metrics=costs,cursor=cursor,
                bank_with_cursor=dict(nNand=costs['nNand']+cursor['nNand'],nLatch=costs['nLatch']+cursor['nLatch']),
                codec=code,small=small_check,numerical_profile='experimental prefix sat16(RNE(x/16)), decode by wiring <<4',
                adopted=False,scope='bank transition and separate serial codec; complete write arbitration/staging and full-chip physical implementation pending')
    if args.cloud:
        xs,ys=ports.cases('ring',64,512);report['combinational_check']=verify(combin,xs,ys)
        decoded=Snapshot.decode(net.encode(),net.n_in,net.n_out);bits=net.n_state
        states=[bytes(x>>i&1 for i in range(bits)) for x in xs]
        inputs=[bytes(x>>(bits+i)&1 for i in range(net.n_in)) for x in xs]
        got=decoded.step_simd(states,inputs)
        assert [sum(v<<i for i,v in enumerate(s+y)) for s,y in got]==ys
        report['latch_check']=dict(vectors=len(xs),mismatches=0)
        report['sequence']=ports.sequence(decoded,64,512)
        abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
        script=OUT/'proof.ys'
        script.write_text(f'read_verilog {OUT}/bank.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/bank.ref.json\n')
        subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=240)
        reference=from_yosys(json.loads((OUT/'bank.ref.json').read_text()),combin.n_in,combin.n_out)
        (OUT/'reference.blif').write_text(blif(reference));(OUT/'negative.blif').write_text(blif(flip_output(combin)))
        proof=cec(abc,OUT/'bank.blif',OUT/'reference.blif',OUT/'cec.log');assert proof['verdict']=='equivalent'
        negative=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.log');assert negative['verdict']=='different'
        report.update(status='actual-size independent RTL CEC, canonical NAND/LATCH and real gate mutations pass',proof=proof,negative=negative)
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                  sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'integer_opt/ports.py',
                      ROOT/'integer_opt/prefix_codec.py',ROOT/'integer_opt/gate_check.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
