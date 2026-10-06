#!/usr/bin/env python3
"""Share the entire418-bit scale state between gate/up and down stages.

MUL/DIV are already shared with H/FF A8 and SwiGLU. Down reuses that same
state, temp, alpha, counter and result; only its first MUL load is inserted.
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
import ff_writeback as base
from export import import_net,rtl
from nand import Builder,metrics,with_state,blif,flip_output
from scale_pipeline import make as scale_net
from bench import verify
from ci import cec
OUT=R/'build/integer_opt/ff_scale_shared'
sha=lambda data:hashlib.sha256(data).hexdigest()

def guard(b,parent,ff_begin,silu_load,ff_quant,h_quant):
    owner=parent[1]
    ok=b.inv(b.land(owner,b.reduce([ff_begin,silu_load,ff_quant,h_quant],b.lor,0)))
    return owner,ok

def small_check():
    b=Builder(6);owner,ok=guard(b,[2,3],4,5,6,7);net=b.finish([owner,ok]);xs=list(range(64));ys=[]
    for x in xs:
        who=int((x&3)>=2);valid=int(not(who and x>>2));ys.append(who+2*valid)
    return dict(metrics=metrics(net),verification=verify(net,xs,ys))

def make():
    net,parts=base.make();assert sha(net.encode())=='c5040a22c3d8072f8f336e804aadada0d08a28947e0edf4c26fb005130c28e16'
    removed=set(range(5921,6339));kept=[i for i in range(6394) if i not in removed];slots={old:new for new,old in enumerate(kept)}
    def primary(i):return i-5921 if i in removed else i
    b=Builder(len(kept)+net.n_in);old=[2+slots[primary(i)] for i in range(6394)];ins=list(range(2+len(kept),2+len(kept)+net.n_in))
    ds,out=import_net(b,net,ins,old)
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    parent=old[6390:6392];owner=parent[1];rp=old[534:538];scale_phase=old[395:397];keep=b.inv(ins[0])
    ff_launch=b.lor(eq(rp,2),eq(rp,5));down_launch=b.land(owner,eq(old[6387:6390],2))
    ff_begin=b.reduce([keep,ff_launch,eq(scale_phase,0)],b.land,1)
    down_begin=b.reduce([keep,down_launch,eq(scale_phase,0)],b.land,1)
    ddot=old[6339:6356];dm=old[6356:6376]
    dot=[b.mux(owner,a,v) for a,v in zip(old[466:483],ddot)]
    maximum=[b.mux(owner,a,v) for a,v in zip(old[483:503],dm)]
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==base.down_reference.MODEL_SHA
    factors=[int.from_bytes(blob[243208+4*j:243212+4*j],'little') for j in (4,5,6)]
    up=b.lor(eq(rp,4),b.lor(eq(rp,5),eq(rp,6)))
    alpha=[b.mux(owner,b.mux(up,factors[0]>>j&1,factors[1]>>j&1),factors[2]>>j&1) for j in range(18)]
    sd,_=import_net(b,scale_net(True),[ins[0],b.lor(ff_launch,down_launch)]+dot+maximum+alpha,old[:418])
    # Existing shared primitive routing already handles scale's second MUL
    # and DIV loads using these common count/phase/temp/alpha states. Only
    # the new down first MUL load needs insertion; its full state is known.
    initial=[0]*64+ddot+[ddot[-1]]*47+dm+[0]*44
    md=[b.mux(down_begin,a,v) for a,v in zip(ds[:192],initial)]
    new_scale=md+ds[192:307]+sd[307:418]
    silu_first=b.reduce([keep,eq(rp,7),eq(old[443:445],0)],b.land,1)
    silu_second=b.land(eq(old[443:445],1),eq(old[438:443],17))
    fq=b.reduce([keep,eq(old[576:579],2),eq(rp,9)],b.land,1)
    hq=b.reduce([keep,eq(old[3339:3342],2),eq(old[5915:5917],1),b.inv(old[5920]),ins[643]],b.land,1)
    _,ok=guard(b,parent,ff_begin,b.lor(silu_first,silu_second),fq,hq)
    nxt=[new_scale[i] if i<418 else ds[i] for i in kept]
    candidate=with_state(b.finish(nxt+out),len(kept));monitor=with_state(b.finish(nxt+out+[ok]),len(kept))
    ref=b.finish([b.land(ok,b.mux(owner,ds[i],ds[5921+i])) for i in range(418)])
    proof=b.finish([b.land(ok,v) for v in new_scale])
    return candidate,monitor,ref,proof,dict(before=metrics(net),after=metrics(candidate),removed_scale_state_bits=418,
      single_mul_instances=1,single_div_instances=1,separate_dot_instances=2,h_and_ff_storage_bits=5248,arithmetic_control_bits=len(kept)-5248,
      proof_scope='418 shared scale next-state bits under no overlapping FF/H/Silu load during down ownership; not unbounded reachability')


def prove(reference,candidate):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    for name,net in [('reference',reference),('candidate',candidate),('negative',flip_output(candidate))]:
        (OUT/(name+'.blif')).write_text(blif(net))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    positive=cec(abc,OUT/'reference.blif',OUT/'candidate.blif',OUT/'cec.log');assert positive['verdict']=='equivalent'
    negative=cec(abc,OUT/'reference.blif',OUT/'negative.blif',OUT/'negative.cec.log');assert negative['verdict']=='different'
    return dict(scope='418 shared scale next-state bits under explicit down ownership and no competing FF/H/SwiGLU loads',
                proof=positive,negative=negative,unbounded_ownership_invariant_proved=False)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT;base.ff_reference.OUT=OUT
    base.down_reference.OUT=OUT/'down_c';base.down_reference.OUT.mkdir(exist_ok=True)
    assert sha((R/'integer/int_model.c').read_bytes())==base.ff_reference.GOLDEN_SHA
    small=small_check();net,monitor,reference,candidate,parts=make()
    c=base.ff_reference.reference();d=base.down_reference.reference();rows,expected=base.vectors(c,d)
    (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_writeback'))
    (OUT/'ownership.nl').write_bytes(monitor.encode());digest=hashlib.sha256()
    with (OUT/'vectors.txt').open('wb') as f:
        for x,y,mask in rows:
            line=f'{x:x} {y:x} {mask:x}\n'.encode();f.write(line);digest.update(line)
    vector_sha=digest.hexdigest();assert vector_sha=='8c6ad9ee22ed61e4dd73896d0a283615f4b7100ba2152cdb70acddfa3a97f285'
    names=['ff_scale_shared','ff_writeback','ff_input_shared','ff_input','ff_shared','ff_stream','ff_row','ff_store','ff_bank','down_engine',
           'linear_engine','quant_stream','silu_pipeline','scale_pipeline','prefix_codec','prefix_store','prefix_writer','prefix_packed',
           'pilot_bank','weight_cursor','pilot_v2']
    paths=[R/'integer_opt'/f'{name}.py' for name in names]
    paths += [R/p for p in ['integer_opt/weights_golden.c','integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py',
      'physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
      'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small guard and unchanged C fixture pass; large shared graphs constructed only',metrics=metrics(net),parts=parts,
        small_ownership=small,expected=expected,numerical_contract_changed=False,vector_sha256=vector_sha,
        conditional_proof_metrics=dict(reference=metrics(reference),candidate=metrics(candidate)),
        scope='complete FF three-matrix path and original H writeback, one MUL/DIV, two DOT; norm/residual/full transformer excluded',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in paths})
    if args.cloud:
        report['refinement_proof']=prove(reference,candidate);report['verification']=base.check(net,rows)
        import verify as checks
        checks.NO=base.NO+1
        observed=[(x,y+(1<<base.NO),mask+(1<<base.NO) if mask else 0) for x,y,mask in rows]
        assert checks.check_nand(observed,monitor.encode())==0
        report['ownership_sequence']=dict(clocks=len(rows),load_owner_violations=0,scope='actual guard observed, not unbounded proof')
        report['status']='actual NAND/RTL/C and guarded complete-scale refinement pass; observed ownership holds'
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))


if __name__=='__main__':main()
