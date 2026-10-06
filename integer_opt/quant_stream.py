#!/usr/bin/env python3
"""Exact two-pass A8 frontend: maximum scan, then streamed RNE(x*127/m).

Inputs are signed20. The caller retains and replays the same vector; accepted
outputs may replace dead inputs after the maximum pass. No vector RAM is hidden
in this module. The existing divider consumes a shifted signed27 numerator in
27 iterations. Local actual-gate checks require at most 4,000 NAND.
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
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'));sys.path.insert(0,str(ROOT))
from export import import_net,rtl,MODEL_SHA
from golden import Netlist
from nand import Builder,metrics,with_state,flip_output

NI,NO=33,41
OUT=ROOT/'build/integer_opt/quant_stream'
sha=lambda value:hashlib.sha256(value).hexdigest()


def make():
    directory=ROOT/'integer_opt/pilot_units'
    meta=json.loads((directory/'manifest.json').read_text())['serial_div']
    raw=(directory/'serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
    div=Netlist.decode(raw,meta['nIn'],meta['nOut'])
    ns=div.n_state+20+9+9+3+5+8
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    ds=old[:div.n_state];i=div.n_state
    maximum=old[i:i+20];i+=20;length=old[i:i+9];i+=9;index=old[i:i+9];i+=9
    phase=old[i:i+3];i+=3;count=old[i:i+5];i+=5;result=old[i:i+8]
    reset,start=ins[:2];n=ins[2:11];x=ins[11:31];xvalid,yready=ins[31:]
    def eq(bits,value):
        return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    idle,scan,receive,run,send=[eq(phase,v) for v in range(5)]
    _,too_large=b.add(n,[(~337>>j)&1 for j in range(9)],1)
    legal=b.land(b.inv(eq(n,0)),b.inv(too_large));keep=b.inv(reset)
    begin=b.land(keep,b.land(idle,b.land(start,legal)))
    xready=b.land(keep,b.lor(scan,receive));yvalid=b.land(keep,send)
    take=b.land(xready,xvalid);scan_take=b.land(take,scan);load=b.land(take,receive)
    ack=b.land(yvalid,yready);finish=b.land(run,eq(count,27))
    next_index=b.add(index,[0]*9,1)[0]
    last=b.reduce([b.inv(b.xor(a,c)) for a,c in zip(next_index,length)],b.land,1)
    magnitude=b.add([b.xor(v,x[-1]) for v in x],[0]*20,x[-1])[0]
    _,larger=b.add(magnitude,[b.inv(v) for v in maximum],1)
    max_next=[b.mux(larger,a,v) for a,v in zip(maximum,magnitude)]
    # x*127=(x<<7)-x is exact in signed27. Wiring <<37 puts the complete
    # magnitude at the divider's MSB; 27 restoring steps preserve RNE ties.
    numerator=b.add([0]*7+x,[b.inv(v) for v in x+[x[-1]]*7],1)[0]
    dd,do=import_net(b,div,[load]+[0]*37+numerator+maximum+[0]*5,ds)
    phase_next=phase[:]
    for enable,value in ((begin,1),(b.land(scan_take,last),2),(load,3),(finish,4),(ack,2)):
        phase_next=[b.mux(enable,v,value>>j&1) for j,v in enumerate(phase_next)]
    clear=b.lor(reset,b.land(ack,last))
    index_clear=b.lor(reset,b.lor(begin,b.land(scan_take,last)))
    index_advance=b.lor(scan_take,b.land(ack,b.inv(last)))
    count_clear=b.lor(reset,b.lor(begin,b.lor(load,finish)))
    nxt=dd
    nxt += [b.land(keep,b.mux(begin,b.mux(scan_take,a,v),int(j==0))) for j,(a,v) in enumerate(zip(maximum,max_next))]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(length,n)]
    nxt += [b.land(b.inv(index_clear),b.mux(index_advance,a,v)) for a,v in zip(index,next_index)]
    nxt += [b.land(b.inv(clear),v) for v in phase_next]
    nxt += [b.land(b.inv(count_clear),v) for v in b.add(count,[0]*5,run)[0]]
    nxt += [b.land(keep,b.mux(finish,a,v)) for a,v in zip(result,do[div.n_state:div.n_state+8])]
    assert len(nxt)==ns
    replay=b.lor(receive,b.lor(run,send))
    net=with_state(b.finish(nxt+result+index+maximum+[xready,yvalid,b.inv(idle),replay]),ns)
    assert net.n_in==NI and net.n_out==NO
    return net


def vectors():
    adapter=OUT/'reference.c'
    adapter.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+
                       'int32_t quant_reference(const int32_t *x,int n,int8_t *q){return quant(x,n,q);}\n')
    lib=OUT/'reference.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(lib)],check=True,timeout=30)
    golden=ct.CDLL(str(lib));golden.quant_reference.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int8)]
    golden.quant_reference.restype=ct.c_int32
    def rne(n,d):
        q,r=divmod(abs(n),d);q+=int(2*r>d or 2*r==d and q%2);return -q if n<0 else q
    rng=random.Random(260660);values=[[0],[-524288],[524287],[-1],[1,254],[-1,254],[3,254],[-3,254]]
    for n in (32,128,336):
        values += [[0]*n,[-524288]*n,[524287]*n,[(-524288,524287)[j%2] for j in range(n)],
                   [254]+[(j%127)*2-125 for j in range(n-1)],
                   [rng.randint(-524288,524287) for _ in range(n)],[rng.randint(-255,255) for _ in range(n)]]
    cases=[]
    for x in values:
        n=len(x);q=(ct.c_int8*n)();m=golden.quant_reference((ct.c_int32*n)(*x),n,q)
        assert m==max([1]+[abs(v) for v in x]);assert list(q)==[rne(v*127,m) for v in x]
        cases.append(dict(x=x,q=list(q),m=m,n=n))
    rows=[];state=dict(phase=0,m=0,index=0,n=0,q=0,remaining=0,case=None)
    counts=dict(scan_inputs=0,replay_inputs=0,outputs=0,completed_vectors=0,input_stalls=0,
                output_stalls=0,busy_starts=0,resets=0,aborts=0)
    def tick(reset=0,start=0,n=0,x=0,xvalid=0,yready=0,case=None,first=False):
        phase=state['phase'];ready=int(phase in (1,2) and not reset);valid=int(phase==4 and not reset)
        expected=((state['q']&255)+(state['index']<<8)+(state['m']<<17)+(ready<<37)+
                  (valid<<38)+(int(phase!=0)<<39)+(int(phase in (2,3,4))<<40))
        mask=((1<<NO)-1)^(0 if valid else 255)
        value=reset+(start<<1)+(n<<2)+((x&1048575)<<11)+(xvalid<<31)+(yready<<32)
        rows.append((value,expected,0 if first else mask))
        if reset:
            counts['resets']+=1;counts['aborts']+=int(phase!=0)
            state.update(phase=0,m=0,index=0,n=0,q=0,remaining=0,case=None)
        elif phase==0:
            if start and 1<=n<=336:
                assert case and case['n']==n
                state.update(phase=1,m=1,index=0,n=n,case=case)
        else:
            counts['busy_starts']+=int(bool(start))
            if phase in (1,2):
                if not xvalid:counts['input_stalls']+=1
                else:
                    assert x==state['case']['x'][state['index']]
                    if phase==1:
                        counts['scan_inputs']+=1;state['m']=max(state['m'],abs(x))
                        if state['index']==state['n']-1:
                            assert state['m']==state['case']['m'];state.update(phase=2,index=0)
                        else:state['index']+=1
                    else:
                        counts['replay_inputs']+=1;state.update(phase=3,remaining=28)
            elif phase==3:
                state['remaining']-=1
                if state['remaining']==0:state.update(phase=4,q=state['case']['q'][state['index']])
            elif phase==4:
                if not yready:counts['output_stalls']+=1
                else:
                    counts['outputs']+=1
                    if state['index']==state['n']-1:
                        state['phase']=0;counts['completed_vectors']+=1
                    else:state['index']+=1;state['phase']=2
    def active_tick(hold=False):
        x=state['case']['x'][state['index']] if state['phase'] in (1,2) else rng.randint(-524288,524287)
        tick(start=int(rng.randrange(17)==0),n=rng.randrange(512),x=x,
             xvalid=int(rng.randrange(5)!=0),yready=int(not hold and rng.randrange(4)!=0))
    tick(reset=1,first=True)
    for n in (0,337,511):tick(start=1,n=n,xvalid=1,yready=1);tick()
    for case in cases:
        tick(start=1,n=case['n'],case=case)
        while state['phase']:active_tick()
        tick();tick()
    assert counts['completed_vectors']==len(cases)
    for phase,elapsed in ((1,0),(1,3),(2,0),(3,0),(3,1),(3,26),(3,27),(4,3)):
        case=cases[14];tick(start=1,n=case['n'],case=case)
        while state['phase']!=phase:active_tick(hold=True)
        for _ in range(elapsed):active_tick(hold=True)
        tick(reset=1,start=1,n=128,x=-524288,xvalid=1,yready=1);tick()
    case=cases[-1];tick(start=1,n=case['n'],case=case)
    while state['phase']:active_tick()
    tick()
    return rows,dict(clocks=len(rows),counts=counts,main_vectors=len(cases),
                     accepted_replay_to_valid_clocks=29,no_stall_vector_clocks='1+31*n through final output acceptance',
                     cases=[dict(n=c['n'],m=c['m'],input_sha256=sha(b''.join(v.to_bytes(4,'little',signed=True) for v in c['x'])),
                                 result_sha256=sha(bytes(v&255 for v in c['q']))) for c in cases])


def check(net,rows,cloud):
    import verify as checks
    if cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:assert metrics(net)['nNand']<=4000
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=30)
    begin=time.monotonic();wrong=checks.check_nand(rows,net.encode());assert wrong==0,wrong
    prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask&1)+1]
    bad=flip_output(net);negative=checks.check_nand(prefix,bad.encode());assert negative>0
    result=dict(status='actual NAND/C99 pass',clocks=len(rows),nand_mismatches=wrong,
                nand_seconds=time.monotonic()-begin,negative_prefix_clocks=len(prefix),actual_result_gate_mutation_mismatches=negative)
    if cloud:
        (OUT/'bad.v').write_text(rtl(bad,'quant_a8'))
        (OUT/'tb.v').write_text(checks.testbench(NI,NO,'quant_a8',str(OUT/'vectors.txt')))
        normal=checks.compile_rtl('source',OUT/'quant.v');checks.run([normal],300)
        mutant=checks.compile_rtl('negative',OUT/'bad.v')
        run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
        (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
        assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
        result.update(status='actual NAND/RTL/C99 pass',rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);net=make();rows,expected=vectors()
    (OUT/'quant.nl').write_bytes(net.encode());(OUT/'quant.v').write_text(rtl(net,'quant_a8'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    report=dict(metrics=metrics(net),expected=expected,verification=check(net,rows,args.cloud),
                model_sha256=MODEL_SHA,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
                run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                contract='din reset0,start1,n[10:2],x[30:11],xvalid31,yready32; dout q[7:0],index[16:8],m[36:17],xready37,yvalid38,busy39,replay40',
                scope='1<=n<=336 signed20 inputs, same vector replayed. Busy starts ignored; reset aborts; invalid idle n ignored. External vector storage and shared arithmetic arbitration are not integrated.',
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer/int_model.c',
                    ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'nand.py',ROOT/'golden.py']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
