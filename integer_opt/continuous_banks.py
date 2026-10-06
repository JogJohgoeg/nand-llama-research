#!/usr/bin/env python3
"""Exact continuous-bank costs; actual large state/CEC checks are cloud-only."""
import argparse
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess

from pilot_circulate import R, bank, reference, sha
from nand import metrics, blif, flip_output, from_yosys, verify_state
from ci import cec

OUT=R/'build/integer_opt/continuous_model/banks'


def cases(rows,width):
    rng=random.Random(260715+rows+width);a=(rows-1).bit_length();s=rows*width;ns=s+a
    xs=[];ys=[]
    for j in range(256):
        old=[rng.getrandbits(width) for _ in range(rows)];cursor=rng.randrange(rows)
        address=cursor if j&1 else (cursor+1+rng.randrange(rows-1))%rows
        request=j>>1&1;reset=j>>2&1;write=j>>3&1;data=rng.getrandbits(width)
        packed=data+(address<<width)+(request<<(width+a))+(reset<<(width+a+1))+(write<<(width+a+2))
        x=sum(word<<(width*i) for i,word in enumerate(old))+(cursor<<s)+(packed<<ns)
        accepted=request and not reset and cursor==address and write
        after=old[1:]+[data if accepted else old[0]]
        after_cursor=0 if reset else (cursor+1)%rows
        ready=int(not request or not reset and cursor==address)
        y=sum(word<<(width*i) for i,word in enumerate(after))+(after_cursor<<s)+(old[0]<<ns)+(ready<<(ns+width))
        xs.append(x);ys.append(y)
    return xs,ys


def prove(p,comb,net,rows,width):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    xs,ys=cases(rows,width);state=verify_state(comb,xs,ys,net.n_state);state.pop('nl_hex')
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    ysfile=p.with_suffix('.ys')
    ysfile.write_text(f'read_verilog {p}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {p}.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(p)+'.yosys.log','-s',str(ysfile)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    mapped=from_yosys(json.loads(p.with_suffix('.ref.json').read_text()),comb.n_in,comb.n_out)
    p.with_suffix('.reference.blif').write_text(blif(mapped))
    p.with_suffix('.negative.blif').write_text(blif(flip_output(comb)))
    positive=cec(abc,p.with_suffix('.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.cec.log'))
    negative=cec(abc,p.with_suffix('.negative.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.negative.log'))
    assert positive['verdict']=='equivalent' and negative['verdict']=='different'
    return dict(actual_state=state,all_current_state_and_input_cec=positive,actual_D_output_mutation=negative)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);rows=[]
    for context in (16,32):
        for kind,n,width in [('kv',2*context,276),('prefix',4*context,640)]:
            comb,net=bank(n,width);p=OUT/f'c{context}_{kind}'
            p.with_suffix('.nl').write_bytes(net.encode());p.with_suffix('.ref.v').write_text(reference(n,width))
            p.with_suffix('.blif').write_text(blif(comb))
            row=dict(context=context,kind=kind,rows=n,width=width,metrics=metrics(net))
            if args.cloud:row['verification']=prove(p,comb,net,n,width)
            else:row['verification']='construction only; actual-size state simulation and CEC on Actions'
            rows.append(row)
    paths=[Path(__file__),R/'integer_opt/pilot_circulate.py',R/'integer_opt/pilot_bank.py',R/'nand.py',R/'golden.py',R/'ci.py']
    report=dict(status='cloud all-input CEC, actual state and real mutations pass' if args.cloud else 'construction only',
        results=rows,numerical_contract_changed=False,adopted=False,
        scope='Component proof includes matching request/write/reset and every D/output; no whole-token controller or timing/power proof',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in paths})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
