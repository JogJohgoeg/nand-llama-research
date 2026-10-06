#!/usr/bin/env python3
"""Layer-0 BitLinear matrix engine: actual weights, DOT, accumulator and scale.

The engine owns weight/row/group traversal and backpressure. An external port
supplies the requested quantized activation group. Quantization and the rest
of transformer inference are outside this block. Large checks are Actions-only.
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
import verify as checks
from export import import_net,load_unit,rtl,MODEL_SHA
from golden import Netlist
from nand import Builder,metrics,with_state,verify_state,flip_output
from weight_cursor import make as cursor_net
from scale_pipeline import make as scale_net,LATENCY,BOUNDED_LATENCY

NI,NO=283,36
OUT=ROOT/'build/integer_opt/linear_engine'
sha=lambda b:hashlib.sha256(b).hexdigest()


def control(b,phase,ins):
    reset,start,legal,active,last_group,last_matrix,xvalid,yready,scaled=ins
    def eq(value):return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(phase)],b.land,1)
    idle,acc,launch,wait,out=[eq(v) for v in range(5)]
    keep=b.inv(reset)
    begin=b.land(keep,b.land(idle,b.land(start,legal)))
    xready=b.land(keep,b.land(acc,active));yvalid=b.land(keep,out)
    take=b.land(xready,xvalid);ack=b.land(yvalid,yready)
    next_phase=phase[:]
    for enable,value in ((begin,1),(b.land(take,last_group),2),(launch,3),
                         (b.land(wait,scaled),4),(ack,1)):
        next_phase=[b.mux(enable,v,value>>j&1) for j,v in enumerate(next_phase)]
    clear=b.lor(reset,b.land(ack,last_matrix))
    return [b.land(b.inv(clear),v) for v in next_phase],[begin,take,ack,launch,xready,yvalid,b.inv(idle)]


def small_control():
    b=Builder(12);nxt,outs=control(b,list(range(2,5)),list(range(5,14)))
    comb=b.finish(nxt+outs);xs=list(range(4096));ys=[]
    for value in xs:
        phase=value&7;reset,start,legal,active,last_group,last_matrix,xvalid,yready,scaled=[value>>(3+i)&1 for i in range(9)]
        begin=int(not reset and phase==0 and start and legal)
        xready=int(not reset and phase==1 and active);yvalid=int(not reset and phase==4)
        take=xready&xvalid;ack=yvalid&yready;new=phase
        if reset:new=0
        elif begin:new=1
        elif take and last_group:new=2
        elif phase==2:new=3
        elif phase==3 and scaled:new=4
        elif ack:new=0 if last_matrix else 1
        observed=begin+(take<<1)+(ack<<2)+(int(phase==2)<<3)+(xready<<4)+(yvalid<<5)+(int(phase!=0)<<6)
        ys.append(new+(observed<<3))
    result=verify_state(comb,xs,ys,3);result.pop('nl_hex');return result


def make(bounded=False):
    _,cursor=cursor_net();scale=scale_net(bounded);dot=load_unit('dot32')
    meta=json.loads((ROOT/'integer_opt/pilot_units/manifest.json').read_text())['weights_layer0']
    raw=(ROOT/'integer_opt/pilot_units/weights_layer0.nl').read_bytes();assert sha(raw)==meta['sha256']
    weights=Netlist.decode(raw,meta['nIn'],meta['nOut'])
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    factors=[int.from_bytes(blob[243208+4*j:243212+4*j],'little') for j in range(7)]+[0]
    ns=cursor.n_state+scale.n_state+17+20+3+9+1+3
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    cs=old[:cursor.n_state];ss=old[cursor.n_state:cursor.n_state+scale.n_state]
    i=cursor.n_state+scale.n_state;acc=old[i:i+17];i+=17;m=old[i:i+20];i+=20
    matrix=old[i:i+3];i+=3;result_row=old[i:i+9];i+=9;last=old[i];phase=old[i+1:i+4]
    reset,start=ins[:2];mat=ins[2:5];maximum=ins[5:25];q=ins[25:281];xvalid,yready=ins[281:]
    # Component observable outputs are state-only, so connect the feedback
    # handshake without introducing any combinational cycle.
    _,co=import_net(b,cursor,[0]*9,cs)
    _,so=import_net(b,scale,[0]*57,ss)
    legal=b.inv(b.reduce(mat,b.land,1))
    phase_d,actions=control(b,phase,[reset,start,legal,co[28],co[29],last,xvalid,yready,so[20]])
    begin,take,ack,launch,xready,yvalid,busy=actions
    cd,co=import_net(b,cursor,[reset,begin,take,0,0,0]+mat,cs)
    _,trits=import_net(b,weights,co[:13]);_,dot_out=import_net(b,dot,q+trits)
    total=b.add(acc,dot_out[:17])[0]
    table=[[a>>j&1 for j in range(18)] for a in factors]
    for select in matrix:table=[[b.mux(select,x,y) for x,y in zip(table[k],table[k+1])] for k in range(0,len(table),2)]
    sd,so=import_net(b,scale,[reset,launch]+acc+m+table[0],ss)
    keep=b.inv(reset);clear_acc=b.lor(reset,b.lor(begin,ack));last_take=b.land(take,co[29])
    nxt=cd+sd+[b.land(b.inv(clear_acc),b.mux(take,a,t)) for a,t in zip(acc,total)]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(m,maximum)]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(matrix,mat)]
    nxt += [b.land(keep,b.mux(last_take,a,v)) for a,v in zip(result_row,co[15:24])]
    nxt += [b.land(keep,b.mux(last_take,last,co[30]))]+phase_d
    assert len(nxt)==ns
    net=with_state(b.finish(nxt+so[:20]+result_row+co[24:28]+[xready,yvalid,busy]),ns)
    assert net.n_in==NI and net.n_out==NO
    return net


def vectors(bounded=False):
    latency=BOUNDED_LATENCY if bounded else LATENCY
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    adapter=OUT/'reference.c';adapter.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+'''
int32_t engine_reference(const int32_t *in,int matrix,int8_t *q,int32_t *out) {
    int n=matrix==6?336:128;
    int32_t m=quant(in,n,q);
    linear(in,0,matrix,out);
    return m;
}
''')
    lib=OUT/'reference.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(lib)],check=True,timeout=30)
    g=ct.CDLL(str(lib));g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.engine_reference.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int8),ct.POINTER(ct.c_int32)]
    g.engine_reference.restype=ct.c_int32
    def rne(n,d):
        q,r=divmod(abs(n),d);q+=int(2*r>d or 2*r==d and q%2);return -q if n<0 else q
    rng=random.Random(260658);cases=[];offset=0
    for matrix in range(7):
        cols=336 if matrix==6 else 128;nrows=336 if matrix in (4,5) else 128
        values=([0]*cols if matrix==0 else [-524288]*cols if matrix==1 else [524287]*cols if matrix==2 else
                [(-524288,524287)[j%2] for j in range(cols)] if matrix==3 else
                [rng.randint(-4096,4096) for _ in range(cols)] if matrix in (4,5) else [rng.randint(-524288,524287) for _ in range(cols)])
        q=(ct.c_int8*cols)();out=(ct.c_int32*nrows)();m=g.engine_reference((ct.c_int32*cols)(*values),matrix,q,out)
        assert m==max([1]+[abs(x) for x in values]);assert list(q)==[rne(x*127,m) for x in values]
        alpha=int.from_bytes(blob[243208+4*matrix:243212+4*matrix],'little')
        for row in range(nrows):
            total=0
            for j,x in enumerate(q):
                index=offset+row*cols+j;digit=blob[8+index//4]>>(2*(index%4))&3
                total+=x*(-1 if digit==2 else digit)
            assert out[row]==max(-524288,min(524287,rne(total*m*alpha,33292288)))
        cases.append(dict(matrix=matrix,cols=cols,rows=nrows,m=m,q=list(q),result=list(out)))
        offset+=cols*nrows
    rows=[];state=dict(busy=0,wait=0,available=0,group=0,row=0,outrow=0,last=False,pending=0,context=None)
    counts=dict(completed_rows=0,accepted_groups=0,input_stalls=0,output_stalls=0,busy_starts=0,resets=0)
    def tick(reset=0,start=0,matrix=0,m=0,qword=0,xvalid=0,yready=0,context=None,first=False):
        ready=int(state['busy'] and not state['wait'] and not state['available'] and not reset)
        valid=int(state['available'] and not reset)
        expected=((state['pending']&1048575)+(state['outrow']<<20)+(state['group']<<29)+
                  (ready<<33)+(valid<<34)+(state['busy']<<35))
        mask=((1<<NO)-1) if valid else ((1<<NO)-1)^((1<<29)-1)
        inputs=reset+(start<<1)+(matrix<<2)+(m<<5)+(qword<<25)+(xvalid<<281)+(yready<<282)
        rows.append((inputs,expected,0 if first else mask))
        if reset:
            counts['resets']+=1;state.update(busy=0,wait=0,available=0,group=0,row=0,outrow=0,last=False,pending=0,context=None)
        elif not state['busy'] and start and matrix<7:
            assert context and context['matrix']==matrix and context['m']==m
            state.update(busy=1,wait=0,available=0,group=0,row=0,context=context)
        elif state['busy']:
            counts['busy_starts']+=int(bool(start))
            if state['available']:
                if yready:
                    counts['completed_rows']+=1;state['available']=0
                    if state['last']:state['busy']=0
                else:counts['output_stalls']+=1
            elif state['wait']:
                state['wait']-=1
                if not state['wait']:state['available']=1
            elif xvalid:
                c=state['context'];begin=32*state['group'];chunk=c['q'][begin:begin+32]
                assert qword&((1<<(8*len(chunk)))-1)==sum((v&255)<<(8*j) for j,v in enumerate(chunk))
                counts['accepted_groups']+=1
                if state['group']==(c['cols']+31)//32-1:
                    state['wait']=latency+1;state['outrow']=state['row'];state['pending']=c['result'][state['row']]
                    state['last']=state['row']==c['rows']-1
                    if not state['last']:state['group']=0;state['row']+=1
                else:state['group']+=1
            else:counts['input_stalls']+=1
    def active_tick():
        context=state['context'];chunk=context['q'][32*state['group']:32*state['group']+32]
        qword=sum((v&255)<<(8*j) for j,v in enumerate(chunk))
        # Padding must have no effect because the down matrix's trailing trits
        # are zero. On non-accepting clocks all external fields may change.
        qword+=rng.getrandbits(8*(32-len(chunk)))<<(8*len(chunk))
        if state['wait'] or state['available']:qword=rng.getrandbits(256)
        tick(start=int(rng.randrange(97)==0),matrix=rng.randrange(8),m=rng.randrange(1<<20),qword=qword,
             xvalid=int(rng.randrange(5)!=0),yready=int(rng.randrange(4)!=0))
    tick(reset=1,first=True);tick(start=1,matrix=7);tick()
    for case in cases:
        tick(start=1,matrix=case['matrix'],m=case['m'],context=case)
        while state['busy']:active_tick()
        tick();tick()
    completed_main=counts['completed_rows'];assert completed_main==1312
    # Abort during input collection, multiply/divide, and output backpressure.
    for elapsed in (1,3,8,70,140,198,210):
        case=cases[4];tick(start=1,matrix=4,m=case['m'],context=case)
        for _ in range(elapsed):active_tick()
        tick(reset=1,start=1);tick();tick()
    case=cases[6];tick(start=1,matrix=6,m=case['m'],context=case)
    target=counts['completed_rows']+1
    while counts['completed_rows']<target:active_tick()
    tick(reset=1);tick()
    return rows,dict(clocks=len(rows),counts=counts,main_matrices=7,main_rows=completed_main,
                     clocks_last_group_to_valid=latency+2,
                     cases=[{k:v for k,v in c.items() if k not in ('q','result')}|
                            dict(q_sha256=sha(bytes(x&255 for x in c['q'])),
                                 result_sha256=sha(b''.join(v.to_bytes(4,'little',signed=True) for v in c['result']))) for c in cases])


def cloud_check(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    checks.run(['cc','-O3','-std=c99','-shared','-fPIC',ROOT/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
    begin=time.monotonic();assert checks.check_nand(rows,net.encode())==0
    normal_seconds=time.monotonic()-begin
    prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask&1)+1]
    bad=flip_output(net);negative=checks.check_nand(prefix,bad.encode());assert negative>0
    receipt=dict(status='NAND pass; RTL pending',clocks=len(rows),nand_mismatches=0,nand_seconds=normal_seconds,
                 negative_prefix_clocks=len(prefix),actual_result_gate_mutation_mismatches=negative)
    (OUT/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    (OUT/'bad.v').write_text(rtl(bad,'linear0'))
    (OUT/'tb.v').write_text(checks.testbench(NI,NO,'linear0',str(OUT/'vectors.txt')))
    normal=checks.compile_rtl('source',OUT/'linear.v');checks.run([normal],300)
    mutant=checks.compile_rtl('negative',OUT/'bad.v')
    run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    receipt.update(status='pass',rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)
    (OUT/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def main():
    global OUT
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--bounded',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    if args.bounded:OUT=ROOT/'build/integer_opt/linear_engine_bounded'
    OUT.mkdir(parents=True,exist_ok=True);small=small_control();net=make(args.bounded);rows,expected=vectors(args.bounded)
    (OUT/'linear.nl').write_bytes(net.encode());(OUT/'linear.v').write_text(rtl(net,'linear0'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    report=dict(status='small control proved; large composition and C/Python expected vectors only',metrics=metrics(net),
                small_control=small,expected=expected,model_sha256=MODEL_SHA,variant='bounded' if args.bounded else 'baseline',
                contract='din reset0,start1,matrix[4:2],m[24:5],q32x8[280:25],xvalid281,yready282; dout result[19:0],result_row[28:20],request_group[32:29],xready33,yvalid34,busy35',
                scope='Layer0 weights and all seven matrices; activation quantization/storage and full transformer scheduler external. Idle valid start captures matrix/m; busy starts ignored; reset aborts; output held under backpressure.',
                vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
                run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/weight_cursor.py',
                    ROOT/'integer_opt/scale_pipeline.py',ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',
                    ROOT/'integer/int_model.c',ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'physical/units/manifest.json',
                    ROOT/'nand.py',ROOT/'golden.py']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=cloud_check(net,rows);report['status']='complete source NAND/RTL/C checks and actual mutations pass'
        (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources',)},indent=2))


if __name__=='__main__':main()
