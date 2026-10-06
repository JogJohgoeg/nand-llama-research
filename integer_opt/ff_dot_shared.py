#!/usr/bin/env python3
"""Share one DOT32 between gate/up H codes and down FF codes.

The existing accumulators and their clearing/acceptance schedules stay intact.
Proofs split on the owner bit and state their no-competing-take precondition.
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
import ff_scale_shared as source
from ff_row import weight_table as ff_weights
from down_engine import weight_table as down_weights
from export import import_net,load_unit,rtl
from nand import Builder,metrics,with_state,blif,flip_output
from bench import verify
from ci import cec
OUT=R/'build/integer_opt/ff_dot_shared'
sha=lambda data:hashlib.sha256(data).hexdigest()

def guard(b,owner,ff_take,down_take):
    return b.inv(b.lor(b.land(owner,ff_take),b.land(b.inv(owner),down_take)))

def small_check():
    b=Builder(3);net=b.finish([guard(b,2,3,4)]);xs=list(range(8))
    ys=[int(not ((x&1 and x>>1&1) or (not x&1 and x>>2&1))) for x in xs]
    return dict(metrics=metrics(net),verification=verify(net,xs,ys))


def make():
 net=source.make()[0];assert sha(net.encode())=='a01d3700b893a3448f8817c3819a50494a75f69e89e3341fc5eba537f47652eb'
 ns=net.n_state;b=Builder(ns+net.n_in);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+net.n_in))
 ds,out=import_net(b,net,ins,old)
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 keep=b.inv(ins[0]);rp=old[534:538];dp=old[5969:5972];parent=old[5972:5974];owner=parent[1]
 up=b.lor(eq(rp,4),b.lor(eq(rp,5),eq(rp,6)))
 _,ft=import_net(b,ff_weights()[0],old[512:514]+old[503:512]+[up])
 _,dt=import_net(b,down_weights()[0],old[5965:5969]+old[5958:5965])
 fq=[old[3355+20*j+k] for j in range(32) for k in range(8)]
 inp=[b.mux(owner,a,v) for a,v in zip(fq+ft,out[:256]+dt)]
 _,dot=import_net(b,load_unit('dot32'),inp)
 ftake=b.reduce([keep,b.lor(eq(rp,1),eq(rp,4)),eq(old[5915:5917],3),ins[643]],b.land,1)
 requested=old[567:576];_,invalid=b.add(requested,[(~336>>j)&1 for j in range(9)],1)
 fbegin=b.reduce([keep,eq(rp,0),b.lor(eq(old[576:579],1),eq(old[576:579],2)),b.inv(invalid),b.reduce(old[3281:3301],b.lor,0)],b.land,1)
 capture=b.reduce([keep,eq(rp,3),old[417]],b.land,1)
 fclear=b.lor(ins[0],b.lor(fbegin,capture))
 dtake=b.reduce([keep,eq(dp,1),eq(parent,3),ins[643],out[293]],b.land,1)
 dbegin=b.reduce([keep,eq(dp,0),eq(parent,2),b.reduce(out[265:285],b.lor,0)],b.land,1)
 dack=b.reduce([keep,eq(dp,4),eq(parent,3),ins[644]],b.land,1)
 dclear=b.lor(ins[0],b.lor(dbegin,dack))
 fa=old[466:483];da=old[5921:5938]
 fn=[b.land(b.inv(fclear),b.mux(ftake,a,v)) for a,v in zip(fa,b.add(fa,dot[:17])[0])]
 dn=[b.land(b.inv(dclear),b.mux(dtake,a,v)) for a,v in zip(da,b.add(da,dot[:17])[0])]
 ok=guard(b,owner,ftake,dtake)
 nxt=ds[:];nxt[466:483]=fn;nxt[5921:5938]=dn
 candidate=with_state(b.finish(nxt+out),ns)
 monitor=with_state(b.finish(nxt+out+[ok]),ns)
 ref=b.finish([b.land(ok,v) for v in ds[466:483]+ds[5921:5938]])
 proof=b.finish([b.land(ok,v) for v in fn+dn])
 return candidate,monitor,ref,proof,dict(before=metrics(net),after=metrics(candidate),single_mul_instances=1,single_div_instances=1,single_dot_instances=1,
  h_and_ff_storage_bits=5248,arithmetic_control_bits=ns-5248,
  proof_scope='34 accumulator next-state bits, explicit no-competing-take guard, both owner cofactors; not unbounded reachability')


def cofactor(net,owner):
    b=Builder(net.n_in);inputs=list(range(2,net.n_in+2));inputs[5973]=owner
    _,out=import_net(b,net,inputs);return b.finish(out)


def prove(reference,candidate):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    cases=[]
    for owner in (0,1):
        ref=cofactor(reference,owner);trial=cofactor(candidate,owner)
        prefix='owner'+str(owner)
        for suffix,net in [('reference',ref),('candidate',trial),('negative',flip_output(trial))]:
            (OUT/(prefix+'.'+suffix+'.blif')).write_text(blif(net))
        positive=cec(abc,OUT/(prefix+'.reference.blif'),OUT/(prefix+'.candidate.blif'),OUT/(prefix+'.cec.log'))
        assert positive['verdict']=='equivalent'
        negative=cec(abc,OUT/(prefix+'.reference.blif'),OUT/(prefix+'.negative.blif'),OUT/(prefix+'.negative.cec.log'))
        assert negative['verdict']=='different'
        cases.append(dict(owner=owner,reference=metrics(ref),candidate=metrics(trial),proof=positive,negative=negative))
    return dict(scope='34 accumulator next-state bits under explicit DOT take ownership, both exhaustive owner cofactors',
                cases=cases,unbounded_ownership_invariant_proved=False)


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
    names=['ff_dot_shared','ff_scale_shared','ff_writeback','ff_input_shared','ff_input','ff_shared','ff_stream','ff_row','ff_store','ff_bank','down_engine',
           'linear_engine','quant_stream','silu_pipeline','scale_pipeline','prefix_codec','prefix_store','prefix_writer','prefix_packed',
           'pilot_bank','weight_cursor','pilot_v2']
    paths=[R/'integer_opt'/f'{name}.py' for name in names]
    paths += [R/p for p in ['integer_opt/weights_golden.c','integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py',
      'physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
      'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small guard and unchanged C fixture pass; large shared graphs constructed only',metrics=metrics(net),parts=parts,
        small_ownership=small,expected=expected,numerical_contract_changed=False,vector_sha256=vector_sha,
        conditional_proof_metrics=dict(reference=metrics(reference),candidate=metrics(candidate)),
        scope='complete FF three-matrix path and original H writeback, one MUL/DIV/DOT; norm/residual/full transformer excluded',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in paths})
    if args.cloud:
        report['refinement_proof']=prove(reference,candidate);report['verification']=base.check(net,rows)
        import verify as checks
        checks.NO=base.NO+1
        observed=[(x,y+(1<<base.NO),mask+(1<<base.NO) if mask else 0) for x,y,mask in rows]
        assert checks.check_nand(observed,monitor.encode())==0
        report['ownership_sequence']=dict(clocks=len(rows),load_owner_violations=0,scope='actual guard observed, not unbounded proof')
        report['status']='actual NAND/RTL/C and guarded shared-DOT refinement pass; observed ownership holds'
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))


if __name__=='__main__':main()
