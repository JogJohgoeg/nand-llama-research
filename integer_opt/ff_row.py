#!/usr/bin/env python3
"""Actual layer0 gate/up weights -> shared DOT/scale -> exact SwiGLU row.

An idle start captures row and A8 maximum. Four external32-lane activation
groups are requested for gate, then the same groups for up. The result holds
under backpressure. This is a raw-row producer, without activation storage.
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
from nand import Builder,metrics,with_state,verify_state,flip_output
from bench import lookup
from scale_pipeline import make as scale_net,BOUNDED_LATENCY
from silu_pipeline import make as silu_net,LATENCY as SILU_LATENCY
from prefix_codec import GOLDEN_SHA

NI,NO=289,26
OUT=ROOT/'build/integer_opt/ff_row'
sha=lambda data:hashlib.sha256(data).hexdigest()


def control(b,phase,ins):
    reset,start,legal,last_group,xvalid,yready,scaled,silu_done=ins
    def eq(value):return b.reduce([v if value>>i&1 else b.inv(v) for i,v in enumerate(phase)],b.land,1)
    p=[eq(i) for i in range(10)];keep=b.inv(reset)
    begin=b.land(keep,b.land(p[0],b.land(start,legal)))
    ready=b.land(keep,b.lor(p[1],p[4]));valid=b.land(keep,p[9])
    take=b.land(ready,xvalid);ack=b.land(valid,yready)
    capture=b.land(keep,b.land(p[3],scaled))
    scale_start=b.land(keep,b.lor(p[2],p[5]));silu_start=b.land(keep,p[7])
    nxt=phase[:]
    changes=((begin,1),(b.land(take,b.land(p[1],last_group)),2),(p[2],3),(capture,4),
             (b.land(take,b.land(p[4],last_group)),5),(p[5],6),(b.land(p[6],scaled),7),
             (p[7],8),(b.land(p[8],silu_done),9),(ack,0))
    for enable,value in changes:nxt=[b.mux(enable,v,value>>j&1) for j,v in enumerate(nxt)]
    up=b.lor(p[4],b.lor(p[5],p[6]))
    return [b.land(keep,v) for v in nxt],[begin,take,scale_start,silu_start,capture,ack,ready,valid,b.inv(p[0]),up]


def small_control():
    b=Builder(12);nxt,outs=control(b,list(range(2,6)),list(range(6,14)))
    comb=b.finish(nxt+outs);xs=list(range(4096));ys=[]
    for value in xs:
        phase=value&15;reset,start,legal,last,xvalid,yready,scaled,done=[value>>(4+j)&1 for j in range(8)]
        begin=int(not reset and phase==0 and start and legal)
        ready=int(not reset and phase in (1,4));valid=int(not reset and phase==9)
        take=ready*xvalid;ack=valid*yready;capture=int(not reset and phase==3 and scaled)
        new=phase
        if reset:new=0
        elif begin:new=1
        elif phase in (1,4) and take and last:new=phase+1
        elif phase in (2,5,7):new=phase+1
        elif phase in (3,6) and scaled:new=phase+1
        elif phase==8 and done:new=9
        elif ack:new=0
        observed=[begin,take,int(not reset and phase in (2,5)),int(not reset and phase==7),capture,ack,ready,valid,int(phase!=0),int(phase in (4,5,6))]
        ys.append(new+(sum(v<<j for j,v in enumerate(observed))<<4))
    result=verify_state(comb,xs,ys,4);result.pop('nl_hex');return result


def weight_table():
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    words=[]
    for address in range(4096):
        up=address>>11;row=(address>>2)&511;group=address&3;word=0
        if row<336:
            base=(108544 if up else 65536)+row*128+group*32
            for i in range(32):
                k=base+i;digit=blob[8+k//4]>>(2*(k%4))&3;assert digit!=3;word|=digit<<(2*i)
        words.append(word)
    return lookup(words,64,'shannon'),words


def make(weights):
    scale=scale_net(True);silu=silu_net();dot=load_unit('dot32')
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    factors=[int.from_bytes(blob[243208+4*j:243212+4*j],'little') for j in (4,5)]
    ns=scale.n_state+silu.n_state+17+20+9+2+20+4
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    ss=old[:scale.n_state];ts=old[scale.n_state:scale.n_state+silu.n_state]
    i=scale.n_state+silu.n_state;acc=old[i:i+17];i+=17;m=old[i:i+20];i+=20
    row=old[i:i+9];i+=9;group=old[i:i+2];i+=2;gate=old[i:i+20];phase=old[i+20:i+24]
    reset,start=ins[:2];requested=ins[2:11];maximum=ins[11:31];q=ins[31:287];xvalid,yready=ins[287:]
    _,so=import_net(b,scale,[0]*57,ss);_,to=import_net(b,silu,[0]*42,ts)
    _,invalid_row=b.add(requested,[(~336>>j)&1 for j in range(9)],1)
    legal=b.land(b.inv(invalid_row),b.reduce(maximum,b.lor,0))
    pd,actions=control(b,phase,[reset,start,legal,b.land(*group),xvalid,yready,so[20],to[20]])
    begin,take,scale_start,silu_start,capture,ack,ready,valid,busy,up=actions
    _,trits=import_net(b,weights,group+row+[up]);_,dot_out=import_net(b,dot,q+trits)
    summed=b.add(acc,dot_out[:17])[0]
    alpha=[b.mux(up,factors[0]>>j&1,factors[1]>>j&1) for j in range(18)]
    sd,so=import_net(b,scale,[reset,scale_start]+acc+m+alpha,ss)
    td,to=import_net(b,silu,[reset,silu_start]+gate+so[:20],ts)
    keep=b.inv(reset);clear=b.lor(reset,b.lor(begin,capture))
    nxt=sd+td+[b.land(b.inv(clear),b.mux(take,a,v)) for a,v in zip(acc,summed)]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(m,maximum)]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(row,requested)]
    advance=b.land(take,b.inv(b.land(*group)))
    nxt += [b.land(b.inv(clear),v) for v in b.add(group,[0]*2,advance)[0]]
    nxt += [b.land(keep,b.mux(capture,a,v)) for a,v in zip(gate,so[:20])]+pd
    assert len(nxt)==ns
    net=with_state(b.finish(nxt+to[:20]+group+[up,ready,valid,busy]),ns)
    return net,dict(weights=metrics(weights),dot=metrics(dot),scale=metrics(scale),silu=metrics(silu),
                    separate_mul_instances=2,separate_div_instances=1,activation_storage_bits=0)


def reference():
    p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(ROOT/'integer_opt/weights_golden.c'))+'\n'+'''
uint64_t ff_word(unsigned address) {
    unsigned row=(address>>2)&511,up=address>>11,group=address&3;
    if(row>=336 || up>1)return 0;
    return true_weight_word((up?3392:2048)+4*row+group,0);
}
int32_t row_reference(const int32_t *x,int row,int8_t *q,int32_t *result) {
    int32_t m=quant(x,128,q),dg=0,du=0;
    for(int i=0;i<128;i++) {
        dg+=(int32_t)q[i]*weights[offsets[4]+row*128+i];
        du+=(int32_t)q[i]*weights[offsets[5]+row*128+i];
    }
    int32_t g=sat(int_rne((int64_t)dg*m*alpha[4],33292288));
    int32_t u=sat(int_rne((int64_t)du*m*alpha[5],33292288));
    int64_t j=int_rne(g<0?-(int64_t)g:g,64);if(j>1024)j=1024;
    uint32_t s=g<0?65536-sigtab[j]:sigtab[j];
    result[0]=g;result[1]=u;
    result[2]=sat(int_rne((int64_t)sat(int_rne((int64_t)g*s,65536))*u,4096));
    return m;
}
''')
    lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int]
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA;assert c.int_init(blob,len(blob))==0
    c.ff_word.argtypes=[ct.c_uint];c.ff_word.restype=ct.c_uint64
    c.row_reference.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int8),ct.POINTER(ct.c_int32)];c.row_reference.restype=ct.c_int32
    return c


def vectors(c,words):
    def rne(x,d):
        q,r=divmod(abs(x),d);q+=int(2*r>d or 2*r==d and q%2);return -q if x<0 else q
    sat=lambda x:max(-524288,min(524287,x))
    blob=(ROOT/'physical/model.bin').read_bytes();factors=[int.from_bytes(blob[243208+4*j:243212+4*j],'little') for j in (4,5)]
    sigtab=[int.from_bytes(blob[i:i+4],'little') for i in range(len(blob)-4100,len(blob),4)]
    rng=random.Random(260673);inputs=[(0,[0]*128),(335,[-524288]*128),(1,[524287]*128),(334,[(-524288,524287)[j%2] for j in range(128)])]
    inputs += [(i,[rng.randrange(-4096,4097) for _ in range(128)]) for i in range(336)]
    cases=[]
    for row,x in inputs:
        q=(ct.c_int8*128)();out=(ct.c_int32*3)();m=c.row_reference((ct.c_int32*128)(*x),row,q,out)
        assert m==max(1,max(abs(v) for v in x)) and list(q)==[rne(v*127,m) for v in x]
        scaled=[]
        for up in (0,1):
            dot=0
            for i,code in enumerate(q):
                digit=words[(up<<11)+4*row+i//32]>>(2*(i%32))&3
                dot+=code*(-1 if digit==2 else digit)
            scaled.append(sat(rne(dot*m*factors[up],33292288)))
        g,u=scaled;s=sigtab[min(1024,rne(abs(g),64))];s=65536-s if g<0 else s
        assert list(out)==[g,u,sat(rne(sat(rne(g*s,65536))*u,4096))]
        cases.append(dict(row=row,m=m,q=list(q),result=list(out)))
    rows=[];state=dict(phase=0,group=0,remaining=0,case=None)
    counts=dict(complete=0,groups=0,input_stalls=0,output_stalls=0,busy_starts=0,resets=0,aborts=0)
    def tick(reset=0,start=0,row=0,m=0,qword=0,xvalid=0,yready=0,case=None,first=False):
        phase=state['phase'];ready=int(not reset and phase in (1,4));valid=int(not reset and phase==9)
        up=int(phase in (4,5,6));value=(state['case']['result'][2]&1048575) if valid else 0
        expected=value+(state['group']<<20)+(up<<22)+(ready<<23)+(valid<<24)+(int(phase!=0)<<25)
        mask=((1<<NO)-1)^(0 if valid else 1048575)
        inp=reset+(start<<1)+(row<<2)+(m<<11)+(qword<<31)+(xvalid<<287)+(yready<<288)
        rows.append((inp,expected,0 if first else mask))
        if reset:
            counts['resets']+=1;counts['aborts']+=int(phase!=0);state.update(phase=0,group=0,remaining=0,case=None)
        elif phase==0:
            if start and row<336 and m>0:
                assert case and row==case['row'] and m==case['m'];state.update(phase=1,group=0,case=case)
        else:
            counts['busy_starts']+=int(bool(start))
            if phase in (1,4):
                if xvalid:
                    chunk=state['case']['q'][32*state['group']:32*state['group']+32]
                    assert qword==sum((v&255)<<(8*j) for j,v in enumerate(chunk));counts['groups']+=1
                    if state['group']==3:state['phase']=phase+1
                    else:state['group']+=1
                else:counts['input_stalls']+=1
            elif phase in (2,5):state.update(phase=phase+1,remaining=BOUNDED_LATENCY-1)
            elif phase in (3,6):
                if state['remaining']:state['remaining']-=1
                else:
                    state['phase']=phase+1
                    if phase==3:state['group']=0
            elif phase==7:state.update(phase=8,remaining=SILU_LATENCY-1)
            elif phase==8:
                if state['remaining']:state['remaining']-=1
                else:state['phase']=9
            elif phase==9:
                if yready:state['phase']=0;counts['complete']+=1
                else:counts['output_stalls']+=1
    def active(stalls=True,hold=False):
        chunk=state['case']['q'][32*state['group']:32*state['group']+32]
        qword=sum((v&255)<<(8*j) for j,v in enumerate(chunk)) if state['phase'] in (1,4) else rng.getrandbits(256)
        tick(start=int(stalls and rng.randrange(37)==0),row=rng.randrange(512),m=rng.randrange(1<<20),qword=qword,
             xvalid=int(not stalls or rng.randrange(5)!=0),yready=int(not hold and (not stalls or rng.randrange(4)!=0)))
    tick(reset=1,first=True)
    for row,m in ((336,1),(511,1),(0,0),(335,0)):tick(start=1,row=row,m=m);tick()
    first_cycles=None
    for i,case in enumerate(cases):
        begin=len(rows);tick(start=1,row=case['row'],m=case['m'],case=case)
        while state['phase']:active(stalls=i!=0)
        if i==0:first_cycles=len(rows)-begin;assert first_cycles==251
        tick()
    main_completed=counts['complete'];assert main_completed==340
    for phase,elapsed in ((1,0),(1,2),(3,1),(3,97),(4,0),(4,2),(6,97),(8,1),(8,38),(9,2)):
        case=cases[33];tick(start=1,row=case['row'],m=case['m'],case=case)
        while state['phase']!=phase:active(hold=True)
        for _ in range(elapsed):active(hold=True)
        tick(reset=1,start=1,xvalid=1,yready=1);tick()
    case=cases[-1];tick(start=1,row=case['row'],m=case['m'],case=case)
    while state['phase']:active()
    tick()
    return rows,dict(clocks=len(rows),counts=counts,main_rows=main_completed,all336_weight_rows_checked=True,
        no_stall_start_through_accept_clocks=first_cycles,activation_groups_per_row=8,
        cases=[dict(row=c['row'],maximum=c['m'],gate=c['result'][0],up=c['result'][1],ff=c['result'][2],
                    q_sha256=sha(bytes(x&255 for x in c['q']))) for c in cases])


def check(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask&1)+1]
    bad=flip_output(net);wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
    (OUT/'bad.v').write_text(rtl(bad,'ff_row'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'ff_row',str(OUT/'vectors.txt')))
    normal=checks.compile_rtl('source',OUT/'row.v');checks.run([normal],300)
    mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    return dict(status='actual NAND/RTL/C/Python pass',clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
                negative_prefix_clocks=len(prefix),actual_result_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    small=small_control();weights,words=weight_table();c=reference();golden=[c.ff_word(i) for i in range(4096)];assert golden==words
    net,parts=make(weights);rows,expected=vectors(c,words)
    (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'ff_row'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    weights_out=OUT/'weights';weights_out.mkdir(exist_ok=True);(weights_out/'source.nl').write_bytes(weights.encode())
    (weights_out/'golden.json').write_text(json.dumps(golden)+'\n')
    report=dict(status='small control pass, actual FF weights packed against C; large graph constructed only',metrics=metrics(net),parts=parts,
        small_control=small,expected=expected,weight_addresses=4096,weight_trits=2*336*128,
        contract='din reset0,start1,row[10:2],maximum[30:11],q32x8[286:31],xvalid287,yready288; dout rawFF20,group2,up,xready,yvalid,busy',
        scope='actual layer0 raw FF row; external A8 activation storage, repeated-row scheduler and final FF quantization; contains two separate MUL cores, not whole-chip shared arithmetic',
        numerical_contract_changed=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/silu_pipeline.py',ROOT/'integer_opt/scale_pipeline.py',
          ROOT/'integer_opt/weights.py',ROOT/'integer_opt/weights_golden.c',ROOT/'integer_opt/gate_check.py',ROOT/'integer_opt/prefix_codec.py',
          ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'integer_opt/pilot_units/serial_div.nl',ROOT/'integer/int_model.c',ROOT/'physical/model.bin',
          ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'physical/units/manifest.json',ROOT/'physical/units/serial_mul.nl',
          ROOT/'physical/units/dot32.nl',ROOT/'bench.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    if args.cloud:
        from weights import cloud_check
        report['weight_proof']=cloud_check(weights_out,{'ff_weights':weights},golden)
        report['verification']=check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected','parts','weight_proof')},indent=2));print(json.dumps({k:v for k,v in expected.items() if k!='cases'}));print('parts',json.dumps(parts))


if __name__=='__main__':main()
