#!/usr/bin/env python3
"""Actual layer0 FF rows, recomputed twice, directly quantized into s8 state.

H codes remain in the caller's original storage; its maximum is captured for
the complete operation. No raw FF vector or assembly buffer is instantiated.
This composition still has two MUL and two DIV instances, pending arbitration.
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
from export import import_net,rtl,MODEL_SHA
from nand import Builder,metrics,with_state,verify_state,flip_output
from ff_row import make as row_net,weight_table,BOUNDED_LATENCY,SILU_LATENCY
from ff_store import make as store_net
from prefix_codec import GOLDEN_SHA

NI,NO=284,315
OUT=ROOT/'build/integer_opt/ff_stream'
sha=lambda data:hashlib.sha256(data).hexdigest()


def master():
    # Old maximum20, new maximum20, reset/start/store_busy/row_busy/ready.
    b=Builder(45);old=list(range(2,22));new=list(range(22,42))
    reset,start,busy,row_busy,ready=range(42,47);keep=b.inv(reset)
    begin=b.reduce([keep,start,b.inv(busy),b.inv(row_busy),b.reduce(new,b.lor,0)],b.land,1)
    launch=b.reduce([keep,ready,b.inv(row_busy)],b.land,1)
    nxt=[b.land(keep,b.mux(begin,a,v)) for a,v in zip(old,new)]
    comb=b.finish(nxt+[begin,launch]);return comb,with_state(comb,20)


def small_check():
    comb,net=master();rng=random.Random(260674);xs=[];ys=[]
    for i in range(512):
        old=rng.randrange(1<<20);new=0 if i%3==0 else rng.randrange(1,1<<20)
        reset,start,busy,row_busy,ready=[i>>j&1 for j in range(5)]
        begin=int(not reset and start and not busy and not row_busy and new!=0)
        launch=int(not reset and ready and not row_busy)
        inputs=new+(reset<<20)+(start<<21)+(busy<<22)+(row_busy<<23)+(ready<<24)
        nxt=0 if reset else new if begin else old
        xs.append(old+(inputs<<20));ys.append(nxt+(begin<<20)+(launch<<21))
    result=verify_state(comb,xs,ys,20);result.pop('nl_hex');return result


def make():
    weights,_=weight_table();row,row_parts=row_net(weights);store,store_parts=store_net();_,control=master()
    ns=row.n_state+store.n_state+20;b=Builder(ns+NI)
    old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    rs=old[:row.n_state];fs=old[row.n_state:-20];ms=old[-20:]
    reset,start=ins[:2];incoming=ins[2:22];q=ins[22:278];xvalid,write_enable=ins[278:280];address=ins[280:]
    _,rp=import_net(b,row,[reset]+[0]*288,rs)
    _,fp=import_net(b,store,[reset]+[0]*27,fs)
    md,mo=import_net(b,control,incoming+[reset,start,fp[287],rp[25],fp[285]],ms)
    begin,launch=mo
    rd,ro=import_net(b,row,[reset,launch]+fp[256:265]+ms+q+[xvalid,fp[285]],rs)
    fd,fo=import_net(b,store,[reset,begin]+ro[:20]+[ro[24],write_enable]+address,fs)
    output=fo[:285]+ro[20:24]+[fo[286],fo[287],fo[288],fo[289],fo[290],ro[24]]+ro[:20]
    net=with_state(b.finish(rd+fd+md+output),ns);assert net.n_in==NI and net.n_out==NO
    return net,dict(row=metrics(row),store=metrics(store),master=metrics(control),
        actual_trits=row_parts['weights'],h_maximum_bits=20,raw_ff_state_bits=0,assembly_buffer_bits=0,
        ff_code_bits=store_parts['code_state_bits'],external_h_code_bits=128*8,
        separate_mul_instances=2,separate_div_instances=2)


def reference():
    p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+'''
int32_t ff_reference(const int32_t *in,int8_t *qh,int32_t *raw,int8_t *qff,int32_t *hmax) {
    int32_t g[336],u[336];*hmax=quant(in,128,qh);
    linear(in,0,4,g);linear(in,0,5,u);
    for(int i=0;i<336;i++) {
        int64_t j=int_rne(g[i]<0?-(int64_t)g[i]:g[i],64);if(j>1024)j=1024;
        uint32_t s=g[i]<0?65536-sigtab[j]:sigtab[j];
        raw[i]=sat(int_rne((int64_t)sat(int_rne((int64_t)g[i]*s,65536))*u[i],4096));
    }
    return quant(raw,336,qff);
}
''')
    lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int]
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA;assert c.int_init(blob,len(blob))==0
    c.ff_reference.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8),ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8),ct.POINTER(ct.c_int32)]
    c.ff_reference.restype=ct.c_int32;return c


def vectors(c):
    def rne(x,d):
        q,r=divmod(abs(x),d);q+=int(2*r>d or 2*r==d and q%2);return -q if x<0 else q
    rng=random.Random(260675)
    values=[[0]*128,[-524288]*128,[rng.randrange(-4096,4097) for _ in range(128)],[524287]*128]
    cases=[]
    for x in values:
        h=(ct.c_int8*128)();raw=(ct.c_int32*336)();out=(ct.c_int8*336)();hm=ct.c_int32()
        maximum=c.ff_reference((ct.c_int32*128)(*x),h,raw,out,ct.byref(hm))
        assert hm.value==max(1,max(abs(v) for v in x)) and list(h)==[rne(v*127,hm.value) for v in x]
        assert maximum==max(1,max(abs(v) for v in raw)) and list(out)==[rne(v*127,maximum) for v in raw]
        words=[sum((v&255)<<(8*j) for j,v in enumerate(h[i:i+32])) for i in range(0,128,32)]
        result_words=[sum((v&255)<<(8*j) for j,v in enumerate(out[i:i+32])) for i in range(0,336,32)]
        cases.append(dict(x=x,h=list(h),hm=hm.value,raw=list(raw),maximum=maximum,q=list(out),words=words,result_words=result_words))
    row=dict(phase=0,group=0,remaining=0,index=0)
    store=dict(phase=0,index=0,maximum=0,remaining=0,filled=False)
    active_case=None;memory=[None]*336;rows=[]
    counts=dict(scan_rows=0,replay_rows=0,writes=0,complete=0,h_groups=0,input_stalls=0,write_stalls=0,
                busy_starts=0,resets=0,aborts=0,reads=0,invalid_reads=0)
    def tick(reset=0,start=0,maximum=0,qword=0,xvalid=0,enable=0,address=0,case=None,first=False):
        nonlocal active_case
        rp,qp=row['phase'],store['phase'];index=store['index'];rb=int(rp!=0);busy=int(qp!=0)
        ready=int(not reset and rp in (1,4));rvalid=int(not reset and rp==9)
        qready=int(not reset and qp in (1,2));ack=int(not reset and qp==4 and enable)
        begin=int(not reset and start and not busy and not rb and maximum!=0)
        launch=int(not reset and qready and not rb)
        available=int(not reset and store['filled'] and not busy)
        read_ready=int(available and not begin and address<11)
        mask=((1<<NO)-1)^((1<<256)-1)^(((1<<20)-1)<<295);word=0
        if read_ready:
            chunk=memory[address*32:address*32+32];assert all(v is not None for v in chunk)
            word=sum((v&255)<<(8*j) for j,v in enumerate(chunk));mask|=(1<<256)-1;counts['reads']+=1
        elif available and not begin and address>=11:mask|=(1<<256)-1;counts['invalid_reads']+=1
        raw=active_case['raw'][row['index']] if rvalid else 0
        if rvalid:mask|=((1<<20)-1)<<295
        want=word+(index<<256)+(store['maximum']<<265)+(row['group']<<285)
        want+=(int(rp in (4,5,6))<<287)+(ready<<288)+(int(qp in (2,3,4))<<289)
        want+=(busy<<290)+(available<<291)+(ack<<292)+(read_ready<<293)+(rvalid<<294)+((raw&1048575)<<295)
        inp=reset+(start<<1)+(maximum<<2)+(qword<<22)+(xvalid<<278)+(enable<<279)+(address<<280)
        rows.append((inp,want,0 if first else mask))
        if reset:
            counts['resets']+=1;counts['aborts']+=int(busy or rb)
            row.update(phase=0,group=0,remaining=0,index=0);store.update(phase=0,index=0,maximum=0,remaining=0,filled=False)
            active_case=None;return
        if begin:
            assert case is not None and maximum==case['hm'];active_case=case
            store.update(phase=1,index=0,maximum=1,remaining=0,filled=False)
        elif busy:
            counts['busy_starts']+=int(bool(start))
            if qp in (1,2) and rvalid:
                assert row['index']==index
                if qp==1:
                    counts['scan_rows']+=1;store['maximum']=max(store['maximum'],abs(raw))
                    if index==335:
                        assert store['maximum']==active_case['maximum'];store.update(phase=2,index=0)
                    else:store['index']+=1
                else:counts['replay_rows']+=1;store.update(phase=3,remaining=28)
            elif qp==3:
                store['remaining']-=1
                if store['remaining']==0:store['phase']=4
            elif qp==4:
                if enable:
                    memory[index]=active_case['q'][index];counts['writes']+=1
                    if index==335:
                        assert memory==active_case['q'];store.update(phase=0,filled=True);counts['complete']+=1
                    else:store.update(phase=2,index=index+1)
                else:counts['write_stalls']+=1
        if rp==0:
            if launch:row.update(phase=1,group=0,index=index)
        elif rp in (1,4):
            if xvalid:
                assert qword==active_case['words'][row['group']];counts['h_groups']+=1
                if row['group']==3:row['phase']=rp+1
                else:row['group']+=1
            else:counts['input_stalls']+=1
        elif rp in (2,5):row.update(phase=rp+1,remaining=BOUNDED_LATENCY-1)
        elif rp in (3,6):
            if row['remaining']:row['remaining']-=1
            else:
                row['phase']=rp+1
                if rp==3:row['group']=0
        elif rp==7:row.update(phase=8,remaining=SILU_LATENCY-1)
        elif rp==8:
            if row['remaining']:row['remaining']-=1
            else:row['phase']=9
        elif rp==9 and qready:row['phase']=0
    def active(stalls=True,hold_write=False):
        qword=active_case['words'][row['group']] if row['phase'] in (1,4) else 0
        tick(start=int(stalls and rng.randrange(37)==0),maximum=(len(rows)*17)&1048575,qword=qword,
             xvalid=int(not stalls or rng.randrange(9)!=0),enable=int(not hold_write and (not stalls or rng.randrange(4)!=0)))
    def read_all(case):
        assert memory==case['q']
        for address in range(16):tick(address=address)
    tick(reset=1,first=True);tick();tick(start=1,maximum=0);tick()
    no_stall=None
    for i,case in enumerate(cases[:3]):
        begin=len(rows);tick(start=1,maximum=case['hm'],case=case)
        while store['phase']:active(stalls=i!=0)
        if i==0:no_stall=len(rows)-begin;assert no_stall==1+336*(2*251+29)
        read_all(case)
    for rp in (1,3,4,6,8):
        case=cases[-1];tick(start=1,maximum=case['hm'],case=case)
        while row['phase']!=rp:active()
        tick(reset=1,start=1,maximum=1,xvalid=1,enable=1);tick()
    for qp in (3,4):
        case=cases[-1];tick(start=1,maximum=case['hm'],case=case)
        while store['phase']!=qp:active(hold_write=True)
        for _ in range(2):active(hold_write=True)
        tick(reset=1,start=1,maximum=1,xvalid=1,enable=1);tick()
    case=cases[-1];tick(start=1,maximum=case['hm'],case=case)
    while store['phase']:active()
    read_all(case);tick(reset=1);tick()
    assert counts['complete']==4 and counts['aborts']==7
    return rows,dict(clocks=len(rows),counts=counts,no_stall_full_vector_clocks=no_stall,
        no_stall_formula='1 + 336*(2*251 + 29), includes both raw-row passes and final code writes',
        cases=[dict(h_maximum=c['hm'],ff_maximum=c['maximum'],h_code_sha256=sha(bytes(v&255 for v in c['h'])),
            raw_sha256=sha(b''.join(v.to_bytes(4,'little',signed=True) for v in c['raw'])),
            ff_code_sha256=sha(bytes(v&255 for v in c['q']))) for c in cases])


def check(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask&1)+1]
    bad=flip_output(net);wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
    (OUT/'bad.v').write_text(rtl(bad,'ff_stream'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'ff_stream',str(OUT/'vectors.txt')))
    normal=checks.compile_rtl('source',OUT/'stream.v');checks.run([normal],300)
    mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    return dict(status='actual NAND/RTL/C pass',clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
                negative_prefix_clocks=len(prefix),actual_code_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    small=small_check();net,parts=make();c=reference();rows,expected=vectors(c)
    (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_stream'))
    with (OUT/'vectors.txt').open('w') as f:
        for x,y,mask in rows:f.write(f'{x:x} {y:x} {mask:x}\n')
    report=dict(status='small master and C reference pass; full graph constructed only',metrics=metrics(net),parts=parts,
        small_master=small,expected=expected,numerical_contract_changed=False,
        contract='din reset,start,Hmax20,Hq32x8,xvalid,write_enable,word_addr4; dout word256,index9,FFmax20,group2,up,xready,replay,busy,complete,write_ack,read_ready,row_valid,raw20',
        scope='layer0 gate/up/SwiGLU to complete A8 bank; H codes remain external; two MUL and two DIV instances; no down projection or full transformer',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/ff_row.py',ROOT/'integer_opt/ff_store.py',
          ROOT/'integer_opt/ff_bank.py',ROOT/'integer_opt/quant_stream.py',ROOT/'integer_opt/silu_pipeline.py',ROOT/'integer_opt/scale_pipeline.py',
          ROOT/'integer_opt/prefix_codec.py',ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'integer_opt/pilot_units/serial_div.nl',ROOT/'integer/int_model.c',
          ROOT/'physical/model.bin',ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'physical/units/manifest.json',
          ROOT/'physical/units/serial_mul.nl',ROOT/'physical/units/dot32.nl',ROOT/'bench.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    if args.cloud:report['verification']=check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','parts')},indent=2));print('parts',json.dumps(parts))


if __name__=='__main__':main()
