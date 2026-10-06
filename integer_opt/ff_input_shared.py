#!/usr/bin/env python3
"""One physical DIV for H A8, FF scale and FF A8, with explicit ownership.

Original H/FF code storage and all numerical/controller rules are retained.
Only the duplicate115-bit divider state and duplicated recurrence are removed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import ff_input as base
from export import import_net
from nand import Builder,metrics,with_state,blif,flip_output
from bench import verify
from ci import cec
from golden import Netlist
from prefix_store import select
OUT=R/'build/integer_opt/ff_input_shared'
sha=lambda data:hashlib.sha256(data).hexdigest()

def guard(b,phase,ff_scale,ff_quant,h_load):
    owner=b.land(phase[0],b.inv(phase[1]))
    ok=b.land(b.inv(b.land(owner,b.lor(ff_scale,ff_quant))),b.inv(b.land(b.inv(owner),h_load)))
    return owner,ok


def small_check():
    b=Builder(5);owner,ok=guard(b,[2,3],4,5,6);net=b.finish([owner,ok])
    xs=list(range(32));ys=[]
    for x in xs:
        p=x&3;scale=(x>>2)&1;quant=(x>>3)&1;h=(x>>4)&1;who=int(p==1)
        valid=int(not(who and (scale or quant) or not who and h))
        ys.append(who+(valid<<1))
    return dict(metrics=metrics(net),verification=verify(net,xs,ys))

def make():
    net,parts=base.make();assert sha(net.encode())=='2aa8e52288382cbe391a3a2ff710fe19f21a025a099a01ad4b9b95ce7ae7abee'
    removed=set(range(3301,3416));kept=[i for i in range(6036) if i not in removed];slots={old:new for new,old in enumerate(kept)}
    def primary(i):return i-3301+192 if i in removed else i
    b=Builder(len(kept)+net.n_in);old=[2+slots[primary(i)] for i in range(6036)]
    ins=list(range(2+len(kept),2+len(kept)+net.n_in));ds,out=import_net(b,net,ins,old)
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    keep=b.inv(ins[0]);rp=old[534:538];fq=old[576:579];hp=old[3454:3457];outer=old[6030:6032]
    scale=b.land(eq(old[395:397],3),eq(old[389:395],0))
    ff_quant=b.reduce([keep,eq(fq,2),eq(rp,9)],b.land,1)
    h_quant=b.reduce([keep,eq(hp,2),eq(outer,1),b.inv(old[6035]),ins[643]],b.land,1)
    raw=old[445:465];ff_num=b.add([0]*7+raw,[b.inv(v) for v in raw+[raw[-1]]*7],1)[0]
    head=old[3470:4110];hraw=select(b,[head[j*20:(j+1)*20] for j in range(32)],old[3445:3450])
    hn=b.add([0]*7+hraw,[b.inv(v) for v in hraw+[hraw[-1]]*7],1)[0]
    sn=[0]*9+old[307:362];fn=[0]*37+ff_num;hn=[0]*37+hn
    num=[b.mux(h_quant,b.mux(ff_quant,a,f),h) for a,f,h in zip(sn,fn,hn)]
    den=[b.mux(h_quant,b.mux(ff_quant,33292288>>j&1,old[538+j] if j<20 else 0),old[3416+j] if j<20 else 0) for j in range(25)]
    meta=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div'];data=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(data)==meta['sha256']
    div=Netlist.decode(data,meta['nIn'],meta['nOut']);dd,_=import_net(b,div,[b.lor(scale,b.lor(ff_quant,h_quant))]+num+den,old[192:307])
    nxt=[dd[i-192] if 192<=i<307 else ds[i] for i in kept]
    candidate=with_state(b.finish(nxt+out),len(kept));owner,ok=guard(b,outer,scale,ff_quant,h_quant)
    ref=b.finish([b.land(ok,b.mux(owner,ds[192+i],ds[3301+i])) for i in range(115)])
    proof=b.finish([b.land(ok,d) for d in dd]);monitor=with_state(b.finish(nxt+out+[ok]),len(kept))
    return candidate,monitor,ref,proof,dict(before=metrics(net),after=metrics(candidate),removed_div_state_bits=115,
      single_mul_instances=1,single_div_instances=1,h_slot_bits=2560,ff_code_bits=2688,arithmetic_control_bits=len(kept)-2560-2688,
      proof_scope='115 next-state bits under load ownership; no unbounded reachability proof')


def prove(reference,candidate):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    for name,net in [('reference',reference),('candidate',candidate),('negative',flip_output(candidate))]:
        (OUT/(name+'.blif')).write_text(blif(net))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    positive=cec(abc,OUT/'reference.blif',OUT/'candidate.blif',OUT/'cec.log');assert positive['verdict']=='equivalent'
    negative=cec(abc,OUT/'reference.blif',OUT/'negative.blif',OUT/'negative.cec.log');assert negative['verdict']=='different'
    return dict(scope='all shared old states and inputs satisfying explicit H/FF DIV load ownership; 115 next-state bits',
                proof=positive,negative=negative,unbounded_ownership_invariant_proved=False)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);base.base.OUT=OUT
    assert sha((R/'integer/int_model.c').read_bytes())==base.base.GOLDEN_SHA
    small=small_check();net,monitor,reference,candidate,parts=make()
    c=base.base.reference();rows,expected=base.vectors(c)
    (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(base.rtl(net,'ff_stream'))
    (OUT/'ownership.nl').write_bytes(monitor.encode())
    with (OUT/'vectors.txt').open('w') as f:
        for x,y,mask in rows:f.write(f'{x:x} {y:x} {mask:x}\n')
    vector_sha=sha((OUT/'vectors.txt').read_bytes());assert vector_sha=='6f10a6d43d64ae35b72d061941b122d198b6a7c9fc8e5ebd4a28e7c139d1f7c5'
    names=['ff_input_shared','ff_input','ff_shared','ff_stream','ff_row','ff_store','ff_bank','quant_stream','silu_pipeline',
           'scale_pipeline','prefix_codec','prefix_store','prefix_writer','prefix_packed','pilot_bank','weight_cursor','pilot_v2']
    paths=[R/'integer_opt'/f'{name}.py' for name in names]
    paths += [R/p for p in ['integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py',
      'physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
      'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small ownership logic and C fixture pass; large shared graph constructed only',metrics=metrics(net),parts=parts,
        small_ownership=small,expected=expected,numerical_contract_changed=False,vector_sha256=vector_sha,
        conditional_proof_metrics=dict(reference=metrics(reference),candidate=metrics(candidate)),
        scope='original H slot and complete two-pass layer0 FF code bank, single MUL and DIV; down/residual outside',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in paths})
    if args.cloud:
        report['refinement_proof']=prove(reference,candidate)
        base.base.NI,base.base.NO=base.NI,base.NO
        report['verification']=base.base.check(net,rows)
        import verify as checks
        checks.NO=base.NO+1
        observed=[(x,y+(1<<base.NO),mask+(1<<base.NO) if mask else 0) for x,y,mask in rows]
        assert checks.check_nand(observed,monitor.encode())==0
        report['ownership_sequence']=dict(clocks=len(rows),load_owner_violations=0,scope='actual guard observed, not unbounded proof')
        report['status']='actual NAND/RTL/C and guarded DIV refinement pass; observed H/FF ownership holds'
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))


if __name__=='__main__':main()
