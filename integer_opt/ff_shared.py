#!/usr/bin/env python3
"""Share non-overlapping FF arithmetic by routing loads into one MUL and DIV.

The source graph/state layout is pinned. Only duplicate primitive states are
removed; numerical rules, controller states and the complete vector schedule
are retained. Conditional combinational refinement and full source sequences
run on Actions; no local large-gate evaluation or EDA.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'));sys.path.insert(0,str(ROOT))
import ff_stream as base
from export import import_net,load_unit,rtl
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from silu_pipeline import shifted_sat
from bench import verify
from ci import cec

OUT=ROOT/'build/integer_opt/ff_shared'
sha=lambda data:hashlib.sha256(data).hexdigest()


def owners(b,row_phase,quant_phase):
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    mul=b.lor(eq(row_phase,7),eq(row_phase,8))
    div=b.lor(b.lor(eq(quant_phase,3),eq(quant_phase,4)),b.land(eq(quant_phase,2),eq(row_phase,9)))
    return mul,div


def ownership_guard(b,mul,div,scale_mul,silu_mul,scale_div,quant_div):
    return b.reduce([b.inv(b.land(scale_mul,mul)),b.inv(b.land(silu_mul,b.inv(mul))),
                     b.inv(b.land(scale_div,div)),b.inv(b.land(quant_div,b.inv(div)))],b.land,1)


def small_check():
    b=Builder(11);rp=list(range(2,6));qp=list(range(6,9));loads=list(range(9,13))
    mul,div=owners(b,rp,qp);valid=ownership_guard(b,mul,div,*loads);net=b.finish([mul,div,valid])
    values=list(range(2048));expected=[]
    for value in values:
        r=value&15;q=(value>>4)&7;a,c,d,e=[value>>(7+j)&1 for j in range(4)]
        m=int(r in (7,8));v=int(q in (3,4) or q==2 and r==9)
        ok=int(not (a and m or c and not m or d and v or e and not v))
        expected.append(m+(v<<1)+(ok<<2))
    return dict(metrics=metrics(net),verification=verify(net,values,expected))


def make():
    net,parts=base.make();assert sha(net.encode())=='c9d6d758036a5e9fa5d0c90892ca68befb8af1e4d491515c1e4286db227af475'
    removed=set(range(418,610))|set(range(730,845));kept=[i for i in range(3608) if i not in removed];slots={old:new for new,old in enumerate(kept)}
    def primary(i):
     if 418<=i<610:return i-418
     if 730<=i<845:return i-730+192
     return i
    b=Builder(len(kept)+net.n_in);old=[2+slots[primary(i)] for i in range(3608)];ins=list(range(2+len(kept),2+len(kept)+net.n_in))
    ds,out=import_net(b,net,ins,old)
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    rp=old[726:730];qp=old[883:886]
    mul,div=owners(b,rp,qp)
    # Route load operands into single physical primitives; all idle steps use
    # the same unconditional primitive recurrence, so no per-state-D owner mux.
    scale_phase=old[395:397];scale_count=old[389:395]
    silu_phase=old[635:637];silu_count=old[630:635]
    keep=b.inv(ins[0])
    scale_begin=b.land(keep,b.land(b.lor(eq(rp,2),eq(rp,5)),eq(scale_phase,0)))
    scale_second=b.land(eq(scale_phase,2),eq(scale_count,0))
    silu_begin=b.land(keep,b.land(eq(rp,7),eq(silu_phase,0)))
    silu_second=b.land(eq(silu_phase,1),eq(silu_count,17))
    scale_load=b.lor(scale_begin,scale_second);silu_load=b.lor(silu_begin,silu_second)
    temp=old[307:371];alpha=old[371:389];dot=old[658:675];maximum=old[675:695]
    sx=[b.mux(scale_begin,temp[j],dot[j] if j<17 else dot[-1]) for j in range(64)]
    sy=[b.mux(scale_begin,alpha[j] if j<18 else 0,maximum[j] if j<20 else 0) for j in range(64)]
    u=old[610:630];t=shifted_sat(b,old[:37],16);signed_t=t+[t[-1]]
    adjusted=b.add([b.xor(v,u[-1]) for v in signed_t],[0]*21,u[-1])[0]
    mag=b.add([b.xor(v,u[-1]) for v in u],[0]*20,u[-1])[0]
    from silu_pipeline import front
    _,sig=import_net(b,front(),old[706:726])
    gate=old[706:726]
    tx=[b.mux(silu_begin,adjusted[j] if j<21 else adjusted[-1],gate[j] if j<20 else gate[-1]) for j in range(64)]
    ty=[b.mux(silu_begin,mag[j] if j<20 else 0,sig[j] if j<17 else 0) for j in range(64)]
    mx=[b.mux(silu_load,a,v) for a,v in zip(sx,tx)];my=[b.mux(silu_load,a,v) for a,v in zip(sy,ty)]
    md,_=import_net(b,load_unit('serial_mul'),[b.lor(scale_load,silu_load)]+mx+my,old[:192])
    scale_div_load=b.land(eq(scale_phase,3),eq(scale_count,0))
    quant_div_load=b.land(keep,b.land(eq(qp,2),eq(rp,9)))
    raw=old[637:657];numerator=b.add([0]*7+raw,[b.inv(v) for v in raw+[raw[-1]]*7],1)[0]
    nscale=[0]*9+temp[:55];nquant=[0]*37+numerator
    num=[b.mux(quant_div_load,a,v) for a,v in zip(nscale,nquant)]
    den=[b.mux(quant_div_load,33292288>>j&1,old[845+j] if j<20 else 0) for j in range(25)]
    meta=json.loads((ROOT/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
    raw=(ROOT/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
    divnet=Netlist.decode(raw,meta['nIn'],meta['nOut'])
    dd,_=import_net(b,divnet,[b.lor(scale_div_load,quant_div_load)]+num+den,old[192:307])
    nxt=[md[i] if i<192 else dd[i-192] if i<307 else ds[i] for i in kept]
    shared=with_state(b.finish(nxt+out),len(kept))
    valid=ownership_guard(b,mul,div,scale_load,silu_load,scale_div_load,quant_div_load)
    reference=[b.mux(mul,ds[i],ds[418+i]) for i in range(192)]
    reference += [b.mux(div,ds[192+i],ds[730+i]) for i in range(115)]
    proof_ref=b.finish([b.land(valid,v) for v in reference])
    proof_candidate=b.finish([b.land(valid,v) for v in md+dd])
    monitor=with_state(b.finish(nxt+out+[valid]),len(kept))
    return shared,monitor,proof_ref,proof_candidate,dict(before=metrics(net),after=metrics(shared),
        removed_mul_state_bits=192,removed_div_state_bits=115,single_mul_instances=1,single_div_instances=1,
        ff_code_bits=2688,arithmetic_control_and_H_maximum_bits=len(kept)-2688,
        source_state_map=dict(scale_mul=[0,192],scale_div=[192,307],silu_mul=[418,610],quant_div=[730,845],
            row_phase=[726,730],quant_phase=[883,886]),
        proof_scope='single-transition arithmetic-state refinement under explicit load ownership, not an unbounded sequential proof')


def prove(reference,candidate):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    for name,net in [('reference',reference),('candidate',candidate),('negative',flip_output(candidate))]:
        (OUT/(name+'.blif')).write_text(blif(net))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    positive=cec(abc,OUT/'reference.blif',OUT/'candidate.blif',OUT/'cec.log');assert positive['verdict']=='equivalent'
    negative=cec(abc,OUT/'reference.blif',OUT/'negative.blif',OUT/'negative.cec.log');assert negative['verdict']=='different'
    return dict(scope='all shared old states/inputs whose load ownership guard is true; 307 physical arithmetic next-state bits',
                proof=positive,negative=negative,unbounded_ownership_invariant_proved=False)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
    small=small_check();net,monitor,ref,candidate,parts=make();c=base.reference();rows,expected=base.vectors(c)
    (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_stream'))
    (OUT/'ownership.nl').write_bytes(monitor.encode())
    with (OUT/'vectors.txt').open('w') as f:
        for x,y,mask in rows:f.write(f'{x:x} {y:x} {mask:x}\n')
    vector_sha=sha((OUT/'vectors.txt').read_bytes());assert vector_sha=='8c09a3121d390cf818142593a73546eaf38cc3bb66428b3a13bb3df6b4cac149'
    sources=[Path(__file__),ROOT/'integer_opt/ff_stream.py',ROOT/'integer_opt/ff_row.py',ROOT/'integer_opt/ff_store.py',ROOT/'integer_opt/ff_bank.py',
        ROOT/'integer_opt/quant_stream.py',ROOT/'integer_opt/silu_pipeline.py',ROOT/'integer_opt/scale_pipeline.py',ROOT/'integer_opt/prefix_codec.py',
        ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'integer_opt/pilot_units/serial_div.nl',ROOT/'integer/int_model.c',ROOT/'physical/model.bin',
        ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'physical/units/manifest.json',ROOT/'physical/units/serial_mul.nl',
        ROOT/'physical/units/dot32.nl',ROOT/'bench.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']
    report=dict(status='small ownership logic pass; shared graph and unchanged C sequence constructed only',metrics=metrics(net),parts=parts,
        small_ownership=small,expected=expected,vector_sha256=vector_sha,numerical_contract_changed=False,
        conditional_proof_metrics=dict(reference=metrics(ref),candidate=metrics(candidate)),
        scope='single MUL/DIV layer0 gate/up/SwiGLU and final FF A8 bank; H code storage/down/residual/full transformer external',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in sources})
    if args.cloud:
        report['refinement_proof']=prove(ref,candidate)
        report['verification']=base.check(net,rows)
        import verify as checks
        checks.NO=base.NO+1
        watched=[(x,y+(1<<base.NO),mask+(0 if i==0 else 1<<base.NO)) for i,(x,y,mask) in enumerate(rows)]
        assert checks.check_nand(watched,monitor.encode())==0
        report['ownership_sequence']=dict(clocks=len(watched),load_owner_violations=0,
            scope='actual NAND guard observed on the complete C fixture, not an unbounded invariant proof')
        report['status']='shared actual NAND/RTL/C and conditional refinement pass; observed ownership holds'
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='cases'}))


if __name__=='__main__':main()
