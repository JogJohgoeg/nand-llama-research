#!/usr/bin/env python3
"""Actual layer0 down projection, streamed from the existing FF A8 bank.

Only the required128x336 trits are selected; no second activation vector is
stored. DOT/scale are standalone here, for subsequent whole-FF arbitration.
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
from nand import Builder,metrics,with_state
from bench import lookup
from linear_engine import control,small_control
from scale_pipeline import make as scale_net,BOUNDED_LATENCY
from prefix_codec import GOLDEN_SHA
import ff_row as checks

NI,NO=280,34
OUT=ROOT/'build/integer_opt/down_engine'
sha=lambda data:hashlib.sha256(data).hexdigest()


def weight_table():
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    words=[]
    for address in range(2048):
        row=address>>4;group=address&15;word=0
        if group<11:
            for lane in range(min(32,336-32*group)):
                k=151552+row*336+32*group+lane;digit=blob[8+k//4]>>(2*(k%4))&3
                assert digit!=3;word|=digit<<(2*lane)
        words.append(word)
    return lookup(words,64,'shannon'),words


def make(weights):
    scale=scale_net(True);dot=load_unit('dot32')
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    alpha=int.from_bytes(blob[243232:243236],'little');assert 0<alpha<1<<18
    ns=scale.n_state+17+20+7+4+3;b=Builder(ns+NI)
    old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI));ss=old[:scale.n_state]
    i=scale.n_state;acc=old[i:i+17];m=old[i+17:i+37];row=old[i+37:i+44];group=old[i+44:i+48];phase=old[i+48:]
    reset,start=ins[:2];maximum=ins[2:22];q=ins[22:278];xvalid,yready=ins[278:]
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    _,so=import_net(b,scale,[0]*57,ss)
    pd,actions=control(b,phase,[reset,start,b.reduce(maximum,b.lor,0),1,eq(group,10),b.reduce(row,b.land,1),xvalid,yready,so[20]])
    begin,take,ack,launch,xready,yvalid,busy=actions
    _,trits=import_net(b,weights,group+row);_,dot_out=import_net(b,dot,q+trits)
    summed=b.add(acc,dot_out[:17])[0]
    sd,so=import_net(b,scale,[reset,launch]+acc+m+[alpha>>j&1 for j in range(18)],ss)
    clear=b.lor(reset,b.lor(begin,ack));keep=b.inv(reset)
    nxt=sd+[b.land(b.inv(clear),b.mux(take,a,v)) for a,v in zip(acc,summed)]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(m,maximum)]
    nxt += [b.land(b.inv(b.lor(reset,begin)),v) for v in b.add(row,[0]*7,ack)[0]]
    nxt += [b.land(b.inv(clear),v) for v in b.add(group,[0]*4,b.land(take,b.inv(eq(group,10))))[0]]
    nxt += pd
    net=with_state(b.finish(nxt+so[:20]+row+group+[xready,yvalid,busy]),ns)
    assert net.n_in==NI and net.n_out==NO
    return net,dict(weights=metrics(weights),scale=metrics(scale),dot=metrics(dot),alpha=alpha,
                    extra_activation_state_bits=0,separate_mul_instances=1,separate_div_instances=1,
                    state_layout=dict(scale=[0,418],acc=[418,435],maximum=[435,455],row=[455,462],group=[462,466],phase=[466,469]))


def reference():
    p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(ROOT/'integer_opt/weights_golden.c'))+'\n'+'''
uint64_t down_word(unsigned address) {
    unsigned row=address>>4,group=address&15;
    return row<128 && group<11 ? true_weight_word(4736+row*11+group,0) : 0;
}
int32_t down_reference(const int32_t *x,int8_t *q,int32_t *result) {
    int32_t m=quant(x,336,q);linear(x,0,6,result);return m;
}
''')
    lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int]
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA;assert c.int_init(blob,len(blob))==0
    c.down_word.argtypes=[ct.c_uint];c.down_word.restype=ct.c_uint64
    c.down_reference.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8),ct.POINTER(ct.c_int32)];c.down_reference.restype=ct.c_int32
    return c


def vectors(c,words,alpha):
    rng=random.Random(260680)
    raw=[[0]*336,[-524288]*336,[524287]*336,[rng.randrange(-4096,4097) for _ in range(336)],
         [(-524288,524287)[j%2] for j in range(336)]]
    def rne(x,d):
        q,r=divmod(abs(x),d);q+=int(2*r>d or 2*r==d and q%2);return -q if x<0 else q
    cases=[]
    for x in raw:
        q=(ct.c_int8*336)();out=(ct.c_int32*128)();m=c.down_reference((ct.c_int32*336)(*x),q,out)
        assert m==max(1,max(abs(v) for v in x));assert list(q)==[rne(v*127,m) for v in x]
        for row in range(128):
            total=0
            for j,code in enumerate(q):
                digit=words[(row<<4)+j//32]>>(2*(j%32))&3;total+=code*(-1 if digit==2 else digit)
            assert out[row]==max(-524288,min(524287,rne(total*m*alpha,33292288)))
        cases.append(dict(q=list(q),out=list(out),m=m))
    state=dict(phase=0,row=0,group=0,remaining=0,case=None);rows=[]
    counts=dict(rows=0,groups=0,complete=0,input_stalls=0,output_stalls=0,busy_starts=0,aborts=0)
    def tick(reset=0,start=0,m=0,qword=0,xvalid=0,yready=0,case=None,first=False):
        p,row,g=state['phase'],state['row'],state['group'];ready=int(not reset and p==1);valid=int(not reset and p==4)
        result=state['case']['out'][row] if valid else 0
        want=(result&1048575)+(row<<20)+(g<<27)+(ready<<31)+(valid<<32)+(int(p!=0)<<33)
        mask=(1<<NO)-1 if valid else ((1<<NO)-1)^1048575
        inp=reset+(start<<1)+(m<<2)+(qword<<22)+(xvalid<<278)+(yready<<279)
        rows.append((inp,want,0 if first else mask))
        if reset:
            counts['aborts']+=int(p!=0);state.update(phase=0,row=0,group=0,remaining=0,case=None);return
        if p==0 and start and m:
            assert case and m==case['m'];state.update(phase=1,row=0,group=0,case=case)
        elif p:
            counts['busy_starts']+=int(bool(start))
            if p==1:
                if xvalid:
                    chunk=state['case']['q'][g*32:g*32+32]
                    assert qword&((1<<(8*len(chunk)))-1)==sum((v&255)<<(8*j) for j,v in enumerate(chunk))
                    counts['groups']+=1
                    if g==10:state['phase']=2
                    else:state['group']+=1
                else:counts['input_stalls']+=1
            elif p==2:state.update(phase=3,remaining=BOUNDED_LATENCY-1)
            elif p==3:
                if state['remaining']:state['remaining']-=1
                else:state['phase']=4
            elif p==4:
                if yready:
                    counts['rows']+=1;state.update(phase=0 if row==127 else 1,row=(row+1)&127,group=0)
                    counts['complete']+=int(row==127)
                else:counts['output_stalls']+=1
    def active(stalls=True,hold=False):
        chunk=state['case']['q'][32*state['group']:32*state['group']+32]
        word=sum((v&255)<<(8*j) for j,v in enumerate(chunk))
        word|=rng.getrandbits(8*(32-len(chunk)))<<(8*len(chunk))
        tick(start=int(stalls and rng.randrange(37)==0),m=rng.randrange(1<<20),qword=word,
             xvalid=int(not stalls or rng.randrange(7)!=0),yready=int(not hold and (not stalls or rng.randrange(5)!=0)))
    tick(reset=1,first=True);tick(start=1,m=0);tick();no_stall=None
    for i,case in enumerate(cases):
        begin=len(rows);tick(start=1,m=case['m'],case=case)
        while state['phase']:active(stalls=i!=0)
        if i==0:no_stall=len(rows)-begin;assert no_stall==1+128*112
        tick()
    for phase,elapsed in ((1,0),(1,5),(2,0),(3,0),(3,20),(3,70),(4,3)):
        case=cases[3];tick(start=1,m=case['m'],case=case)
        while state['phase']!=phase:active(hold=True)
        for _ in range(elapsed):active(hold=True)
        tick(reset=1,start=1,m=1,xvalid=1,yready=1);tick()
    case=cases[-1];tick(start=1,m=case['m'],case=case)
    while state['phase']:active()
    tick();assert counts['complete']==6 and counts['aborts']==7
    return rows,dict(clocks=len(rows),counts=counts,no_stall_matrix_clocks=no_stall,
        cases=[dict(maximum=c['m'],q_sha256=sha(bytes(v&255 for v in c['q'])),
                    result_sha256=sha(b''.join(v.to_bytes(4,'little',signed=True) for v in c['out']))) for c in cases])


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    small=small_control();weights,words=weight_table();c=reference();golden=[c.down_word(i) for i in range(2048)];assert golden==words
    net,parts=make(weights);rows,expected=vectors(c,words,parts['alpha'])
    (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'ff_row'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    wo=OUT/'weights';wo.mkdir(exist_ok=True);(wo/'source.nl').write_bytes(weights.encode());(wo/'golden.json').write_text(json.dumps(golden)+'\n')
    paths=[Path(__file__)]+[ROOT/'integer_opt'/f for f in ['linear_engine.py','weight_cursor.py','ff_row.py','scale_pipeline.py',
      'weights.py','weights_golden.c','gate_check.py','prefix_codec.py','pilot_units/manifest.json','pilot_units/serial_div.nl']]
    paths += [ROOT/p for p in ['integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py','physical/nl_sim.c',
      'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small controller and true-weight C/Python reference pass; full graph constructed only',metrics=metrics(net),parts=parts,
        small_control=small,expected=expected,weight_addresses=2048,weight_trits=128*336,numerical_contract_changed=False,
        contract='din reset,start,maximum20,q32x8,xvalid,yready; dout result20,row7,group4,xready,yvalid,busy',
        scope='layer0 down projection only; FF code storage, H result writeback and resource sharing with gate/up external',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths})
    if args.cloud:
        from weights import cloud_check
        report['weight_proof']=cloud_check(wo,{'down_weights':weights},golden)
        checks.OUT=OUT;checks.NI,checks.NO=NI,NO;report['verification']=checks.check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected','weight_proof')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
