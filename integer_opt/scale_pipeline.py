#!/usr/bin/env python3
"""Exact scheduled BitLinear scale: S(R(dot*m*alpha, 33292288)).

Uses one serial multiplier twice, then one serial divider. Inputs are captured
on an idle start; busy starts are ignored, reset aborts, and the last result is
held until the next completion/reset. Large gate and RTL checks stay on Actions.
"""
import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'));sys.path.insert(0,str(ROOT))
from export import import_net,load_unit,rtl,MODEL_SHA
from golden import Netlist
from nand import Builder,metrics,with_state,flip_output

NI,NO=57,22
LATENCY=198
BOUNDED_LATENCY=99
OUT=ROOT/'build/integer_opt/scale_pipeline'


def make(bounded=False):
    mul=load_unit('serial_mul')
    unit_dir=ROOT/'integer_opt/pilot_units';meta=json.loads((unit_dir/'manifest.json').read_text())['serial_div']
    raw=(unit_dir/'serial_div.nl').read_bytes();assert hashlib.sha256(raw).hexdigest()==meta['sha256']
    div=Netlist.decode(raw,meta['nIn'],meta['nOut'])
    # Bounded mode preserves the same s17/u20/u18 input domain. Multipliers
    # consume only the nonzero RHS width. Signed n<<9 fits64; the divider
    # consumes its leading55 bits, leaving the exact original quotient/rem.
    steps=(20,18,55) if bounded else (64,64,64)
    cw=6 if bounded else 7
    ns=mul.n_state+div.n_state+64+18+cw+2+20+1
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    ms=old[:mul.n_state];ds=old[mul.n_state:mul.n_state+div.n_state]
    i=mul.n_state+div.n_state;temp=old[i:i+64];i+=64
    alpha=old[i:i+18];i+=18;count=old[i:i+cw];i+=cw
    phase=old[i:i+2];i+=2;result=old[i:i+20];valid=old[-1]
    reset,start=ins[:2];dot=ins[2:19];maximum=ins[19:39];factor=ins[39:57]
    def eq(bits,value):return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    busy=b.lor(*phase);begin=b.land(b.land(start,b.inv(busy)),b.inv(reset))
    end1=b.land(eq(phase,1),eq(count,steps[0]));end2=b.land(eq(phase,2),eq(count,steps[1]+1))
    finish=b.land(eq(phase,3),eq(count,steps[2]+1));capture=b.lor(end1,end2)
    load2=b.land(eq(phase,2),eq(count,0));load_div=b.land(eq(phase,3),eq(count,0))
    mx=[b.mux(begin,t,dot[j] if j<17 else dot[-1]) for j,t in enumerate(temp)]
    my=[b.mux(begin,alpha[j] if j<18 else 0,maximum[j] if j<20 else 0) for j in range(64)]
    mul_d,mul_out=import_net(b,mul,[b.lor(begin,load2)]+mx+my,ms)
    numerator=[0]*9+temp[:55] if bounded else temp
    div_d,div_out=import_net(b,div,[load_div]+numerator+[(33292288>>j)&1 for j in range(25)],ds)
    product=mul_out[mul.n_state:];scaled=div_out[div.n_state+64:]
    assert len(product)==64 and len(scaled)==20
    clear_count=b.lor(reset,b.lor(begin,b.lor(capture,finish)))
    count_next=b.add(count,[0]*cw,busy)[0]
    phase_next=[b.mux(begin,p,1 if j==0 else 0) for j,p in enumerate(phase)]
    for enable,value in ((end1,2),(end2,3),(finish,0)):
        phase_next=[b.mux(enable,p,(value>>j)&1) for j,p in enumerate(phase_next)]
    keep=b.inv(reset)
    nxt=mul_d+div_d
    nxt += [b.land(keep,b.mux(capture,t,p)) for t,p in zip(temp,product)]
    nxt += [b.land(keep,b.mux(begin,a,f)) for a,f in zip(alpha,factor)]
    nxt += [b.land(b.inv(clear_count),v) for v in count_next]
    nxt += [b.land(keep,p) for p in phase_next]
    nxt += [b.land(keep,b.mux(finish,r,s)) for r,s in zip(result,scaled)]
    nxt += [b.land(keep,b.land(b.inv(begin),b.lor(valid,finish)))]
    assert len(nxt)==ns
    net=with_state(b.finish(nxt+result+[valid,busy]),ns)
    return net


def vectors(bounded=False):
    # No gate evaluation: independent C and Python integer expected values.
    blob=(ROOT/'physical/model.bin').read_bytes();assert hashlib.sha256(blob).hexdigest()==MODEL_SHA
    lib=OUT/'golden.so'
    subprocess.run(['cc','-O2','-std=c99','-shared','-fPIC',str(ROOT/'physical/golden_slice.c'),'-o',str(lib)],check=True,timeout=30)
    g=ct.CDLL(str(lib));g.int_rne.argtypes=[ct.c_int64,ct.c_int64];g.int_rne.restype=ct.c_int64
    g.slice_sat.argtypes=[ct.c_int64];g.slice_sat.restype=ct.c_int32
    latency=BOUNDED_LATENCY if bounded else LATENCY
    rng=random.Random(260650);rows=[];remaining=0;last=0;valid=0;pending=0;completed=aborts=ignored=0
    def result(dot,m,alpha):
        n=dot*m*alpha;v=g.slice_sat(g.int_rne(n,33292288))
        q,r=divmod(abs(n),33292288);q+=int(2*r>33292288 or 2*r==33292288 and q%2)
        want=max(-524288,min(524287,-q if n<0 else q));assert v==want;return v&1048575
    def tick(dot=0,m=0,alpha=0,start=0,reset=0,mask=(1<<NO)-1):
        nonlocal remaining,last,valid,pending,completed,aborts,ignored
        inputs=reset+(start<<1)+((dot&131071)<<2)+(m<<19)+(alpha<<39)
        expected=last+(valid<<20)+(int(remaining>0)<<21)
        rows.append((inputs,expected,mask))
        if reset:
            aborts+=int(remaining>0);remaining=0;last=valid=0
        elif not remaining and start:
            remaining=latency-1;valid=0;pending=result(dot,m,alpha)
        elif remaining:
            ignored+=int(bool(start));remaining-=1
            if not remaining:last=pending;valid=1;completed+=1
    tick(reset=1,mask=0)
    cases=[(127*k,131072,1) for k in (-7,-5,-3,-1,1,3,5,7)]
    cases += [(d,m,a) for d,m,a in ((0,1,0),(65535,1048575,262143),(-65536,1048575,262143),(1,1,1),(-1,1,1))]
    alphas=[int.from_bytes(blob[243208+4*i:243212+4*i],'little') for i in range(35)]
    cases += [(rng.randint(-43008,43008),rng.randint(1,524288),a) for a in alphas]
    cases += [(rng.randint(-4096,4096),rng.randint(1,4096),rng.randint(0,65536)) for _ in range(80)]
    for dot,m,alpha in cases:
        tick(dot,m,alpha,start=1)
        for _ in range(latency-1):tick(rng.randint(-65536,65535),rng.randrange(1048576),rng.randrange(262144),start=int(rng.randrange(17)==0))
        assert remaining==0 and valid
        for _ in range(3):tick()
    for elapsed in ((1,20,21,22,40,41,42,97) if bounded else (1,64,65,66,130,131,132,196)):
        tick(381,131072,1,start=1)
        for _ in range(elapsed-1):tick()
        tick(reset=1,start=1);tick()
    tick(-127,131072,1,start=1)
    for _ in range(latency-1):tick()
    tick();assert valid
    return rows,dict(cases=len(cases)+1,completed=completed,aborted=aborts,
                     busy_starts_ignored=ignored,clocks=len(rows),latency_clocks=latency,
                     input_domain='signed17 dot, unsigned20 maximum, unsigned18 alpha; product fits signed55')


def check_cloud(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true','Large graph/RTL checks only on Actions'
    import verify as verifier
    verifier.OUT=OUT
    sim=OUT/'sim.so';subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(sim)],check=True,timeout=60)
    lib=ct.CDLL(str(sim));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32]
    lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    def count_mismatches(graph):
        raw=graph.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;buf=ct.create_string_buffer(3);wrong=0
        for value,want,mask in rows:
            lib.nl_step(value.to_bytes(8,'little'),buf);wrong+=int(bool((int.from_bytes(buf.raw,'little')^want)&mask))
        return wrong
    assert count_mismatches(net)==0
    bad=flip_output(net);negative=count_mismatches(bad);assert negative>0
    (OUT/'bad.v').write_text(rtl(bad,'scale20'))
    (OUT/'tb.v').write_text(verifier.testbench(NI,NO,'scale20',str(OUT/'vectors.txt')))
    normal=verifier.compile_rtl('source',OUT/'scale.v');verifier.run([normal],300)
    mutant=verifier.compile_rtl('negative',OUT/'bad.v')
    run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    return dict(status='pass',clocks=len(rows),nand_mismatches=0,negative_nand_mismatches=negative,
                rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)


def main():
    global OUT
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--bounded',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    if args.bounded:OUT=ROOT/'build/integer_opt/scale_pipeline_bounded'
    OUT.mkdir(parents=True,exist_ok=True);net=make(args.bounded);assert net.n_in==NI and net.n_out==NO
    (OUT/'scale.nl').write_bytes(net.encode());(OUT/'scale.v').write_text(rtl(net,'scale20'))
    rows,expected=vectors(args.bounded);(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    report=dict(status='construction and independent C/Python expected vectors only',metrics=metrics(net),expected=expected,
                contract='reset,start,dot17,m20,alpha18 -> result20,valid,busy; start accepted only idle; reset aborts; result holds',
                scope='standalone scheduled BitLinear scaling, not DOT, state ports or full inference',
                variant='bounded' if args.bounded else 'baseline')
    if args.cloud:report['verification']=check_cloud(net,rows);report['status']='complete source NAND/RTL/C99 checks and actual mutations pass'
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                  sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'physical/golden_slice.c',ROOT/'integer/int_model.c',ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'physical/units/manifest.json',ROOT/'nand.py',ROOT/'golden.py']},
                  model_sha256=MODEL_SHA,vector_sha256=hashlib.sha256((OUT/'vectors.txt').read_bytes()).hexdigest())
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
