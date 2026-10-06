#!/usr/bin/env python3
"""P16/P12 writer + existing H/A staging slot + actual held prefix bank.

Four640-bit staging rows hold one128-lane s20 vector. Encoding rewrites a
single lane in place; accepted prefix writes rotate the staging chunk.
This is a storage macro, not the full transformer controller.
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
from export import import_net,rtl
from nand import Builder,flip_output,metrics,verify_state,with_state
from pilot_bank import bank
from prefix_codec import GOLDEN_SHA
from prefix_writer import make as writer

NI,NO=656,1169
OUT=ROOT/'build/integer_opt/prefix_store'
sha=lambda b:hashlib.sha256(b).hexdigest()


def select(b,words,address):
    for bit in address:
        words=[[b.mux(bit,x,y) for x,y in zip(words[i],words[i+1])]
               for i in range(0,len(words),2)]
    assert len(words)==1
    return words[0]


def stage(lanes=32):
    """Four held rows; fill/advance rotate, otherwise rewrite one head lane."""
    width=lanes*20;a=(lanes-1).bit_length();ns=4*width
    b=Builder(ns+width+20+a+3);old=list(range(2,ns+2))
    ins=list(range(ns+2,ns+2+width+20+a+3))
    incoming=ins[:width];value=ins[width:width+20];index=ins[width+20:width+20+a]
    fill,advance,update=ins[-3:];rotate=b.lor(fill,advance)
    head=old[:width];nxt=[]
    head_write=[]
    for lane in range(lanes):
        match=b.reduce([x if lane>>j&1 else b.inv(x) for j,x in enumerate(index)],b.land,1)
        enable=b.land(update,match)
        head_write += [b.mux(enable,x,y) for x,y in zip(head[lane*20:(lane+1)*20],value)]
    for row in range(4):
        held=head_write if row==0 else old[row*width:(row+1)*width]
        shifted=old[((row+1)%4)*width:((row+1)%4+1)*width]
        if row==3:shifted=[b.mux(fill,x,y) for x,y in zip(shifted,incoming)]
        nxt += [b.mux(rotate,x,y) for x,y in zip(held,shifted)]
    selected=select(b,[head[j*20:(j+1)*20] for j in range(lanes)],index)
    comb=b.finish(nxt+head+selected)
    return comb,with_state(comb,ns)


def small_check():
    lanes=4;width=80;ns=320;comb,net=stage(lanes);assert metrics(net)['nNand']<=4000
    rng=random.Random(260664);xs=[];ys=[];mask=(1<<width)-1
    for _ in range(512):
        old=[rng.getrandbits(width) for _ in range(4)];incoming=rng.getrandbits(width)
        value=rng.getrandbits(20);index=rng.randrange(lanes);fill,advance,update=[rng.randrange(2) for _ in range(3)]
        inputs=incoming+(value<<width)+(index<<(width+20))+(fill<<(width+22))+(advance<<(width+23))+(update<<(width+24))
        xs.append(sum(v<<(j*width) for j,v in enumerate(old))+(inputs<<ns))
        nxt=old[:]
        if fill or advance:nxt=old[1:]+[incoming if fill else old[0]]
        elif update:nxt[0]=(old[0]&~(1048575<<(index*20)))+(value<<(index*20))
        expected=sum(v<<(j*width) for j,v in enumerate(nxt))+(old[0]<<ns)+(((old[0]>>(index*20))&1048575)<<(ns+width))
        assert all(0<=v<=mask for v in nxt);ys.append(expected)
    result=verify_state(comb,xs,ys,ns);result.pop('nl_hex');return result


def make(storage_bits=16):
    width=32*storage_bits;shift=20-storage_bits
    wr=writer(storage_bits);_,st=stage();_,prefix=bank(64,width)
    ns=wr.n_state+st.n_state+prefix.n_state
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    wstate=old[:13];sstate=old[13:2573];pstate=old[2573:]
    reset,start=ins[:2];position=ins[2:6];fill,pack_enable,bank_enable,read_request=ins[6:10]
    read_addr=ins[10:16];incoming=ins[16:]
    index=wstate[4:11];phase=wstate[11:13];busy=b.lor(*phase)
    commit=b.land(b.inv(phase[0]),phase[1]);active=b.inv(reset)
    fill_ok=b.land(active,b.land(fill,b.land(b.inv(busy),b.inv(start))))
    read_mode=b.land(active,b.land(read_request,b.land(b.inv(busy),b.land(b.inv(start),b.inv(fill)))))
    waddr=index[5:]+wstate[:4]
    match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(pstate[-6:],waddr)],b.land,1)
    bank_ack=b.land(active,b.land(bank_enable,match))
    head=sstate[:640];value=select(b,[head[j*20:(j+1)*20] for j in range(32)],index[:5])
    wd,wo=import_net(b,wr,[reset,start]+position+value+[1,pack_enable,bank_ack],wstate)
    stage_ack=b.land(wo[27],wo[28]);write_ack=b.land(wo[35],bank_ack)
    sd,so=import_net(b,st,incoming+wo[7:27]+index[:5]+[fill_ok,write_ack,stage_ack],sstate)
    codes=[x for j in range(32) for x in head[j*20+shift:(j+1)*20]]
    address=[b.mux(busy,x,y) for x,y in zip(read_addr,waddr)]
    writing=b.land(active,b.land(commit,bank_enable));request=b.lor(read_mode,writing)
    pd,po=import_net(b,prefix,codes+address+[request,reset,writing],pstate)
    read_ready=b.land(read_mode,po[-1])
    out=po[:width]+[read_ready,wo[36]]+so[:640]+[stage_ack,write_ack]+index+waddr
    net=with_state(b.finish(wd+sd+pd+out),ns)
    assert net.n_in==NI and net.n_out==width+657 and net.n_state==2579+64*width
    return net,dict(writer=metrics(wr),stage=metrics(st),prefix=metrics(prefix))


def vectors(storage_bits=16):
    width=32*storage_bits;scale=1<<(20-storage_bits);half=1<<(storage_bits-1);no=width+657
    adapter=OUT/'reference.c';adapter.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+'''
int32_t stored_prefix(int32_t x) {
    int64_t q=int_rne(x,16);
    if(q>32767)q=32767;
    if(q< -32768)q= -32768;
    return (int32_t)(16*q);
}
'''.replace('int_rne(x,16)',f'int_rne(x,{scale})').replace('32767',str(half-1)).replace('32768',str(half)).replace('(16*q)',f'({scale}*q)'))
    lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.stored_prefix.argtypes=[ct.c_int32];c.stored_prefix.restype=ct.c_int32
    rng=random.Random(260664);rows=[];phase=index=position=cursor=0
    staging=[[0]*32 for _ in range(4)];stage_known=[False]*4;memory=[None]*64
    counts=dict(fills=0,staged_values=0,committed_words=0,completed_vectors=0,seeks=0,reads=0,
                stage_stalls=0,bank_stalls=0,busy_starts=0,busy_fills=0,resets=0,aborts=0)
    def pack(values,width):return sum((v&((1<<width)-1))<<(j*width) for j,v in enumerate(values))
    def tick(reset=0,start=0,pos=0,fill=0,pack_enable=0,bank_enable=0,read=0,addr=0,data=None,first=False):
        nonlocal phase,index,position,cursor,staging,stage_known,memory
        data=[0]*32 if data is None else data
        busy=int(phase!=0);write_addr=4*position+index//32
        fill_ok=int(fill and not reset and not busy and not start)
        read_mode=int(read and not reset and not busy and not start and not fill)
        stage_ack=int(phase==1 and pack_enable and not reset)
        writing=int(phase==2 and bank_enable and not reset)
        write_ack=int(writing and cursor==write_addr)
        bank_target=write_addr if busy else addr
        request=bool(read_mode or writing);read_ready=int(read_mode and cursor==addr)
        want=(read_ready<<width)+(busy<<(width+1))+(pack(staging[0],20)<<(width+2))+(stage_ack<<(width+642))+(write_ack<<(width+643))+(index<<(width+644))+(write_addr<<(width+651))
        mask=((1<<no)-1)^((1<<width)-1)
        if not stage_known[0]:mask &= ~(((1<<640)-1)<<(width+2))
        if read_ready and memory[cursor] is not None:
            want+=pack(memory[cursor],storage_bits);mask|=(1<<width)-1;counts['reads']+=1
        inputs=reset+(start<<1)+(pos<<2)+(fill<<6)+(pack_enable<<7)+(bank_enable<<8)+(read<<9)+(addr<<10)+(pack(data,20)<<16)
        rows.append((inputs,want,0 if first else mask))
        if reset:
            counts['resets']+=1;counts['aborts']+=busy;phase=index=position=cursor=0
            memory=[None]*64;stage_known=[False]*4
            return
        if request and (cursor!=bank_target or writing):
            if cursor==bank_target and writing:
                assert stage_known[0]
                assert all(v==c.stored_prefix(v) for v in staging[0])
                memory[cursor]=[v//scale for v in staging[0]];counts['committed_words']+=1
            else:counts['seeks']+=1
            cursor=(cursor+1)%64
        if fill_ok:
            staging=staging[1:]+[data[:]];stage_known=stage_known[1:]+[True];counts['fills']+=1
        elif write_ack:
            staging=staging[1:]+staging[:1];stage_known=stage_known[1:]+stage_known[:1]
        elif stage_ack:
            assert stage_known[0];lane=index%32
            staging[0][lane]=c.stored_prefix(staging[0][lane]);counts['staged_values']+=1
        if phase==0:
            if start:assert all(stage_known);phase=1;index=0;position=pos
        else:
            counts['busy_starts']+=int(bool(start));counts['busy_fills']+=int(bool(fill))
            if phase==1:
                if stage_ack:
                    if index%32==31:phase=2
                    else:index+=1
                else:counts['stage_stalls']+=1
            elif phase==2:
                if write_ack:
                    if index==127:phase=0;counts['completed_vectors']+=1
                    else:phase=1;index+=1
                else:counts['bank_stalls']+=1
    def load(values):
        for j in range(4):tick(fill=1,data=values[j*32:(j+1)*32],read=1,addr=rng.randrange(64))
        assert sum(staging,[])==values
    def active():
        tick(start=int(rng.randrange(7)==0),pos=rng.randrange(16),fill=int(rng.randrange(11)==0),
             data=[rng.randint(-524288,524287) for _ in range(32)],
             pack_enable=int(rng.randrange(4)!=0),bank_enable=int(rng.randrange(3)!=0),read=1,addr=rng.randrange(64))
    def read_word(addr):
        while cursor!=addr:tick(read=1,addr=addr)
        tick(read=1,addr=addr)
    tick(reset=1,first=True)
    cases=[[0]*128,[-524288]*128,[524287]*128,[(j-64)*scale+scale//2 for j in range(128)]]
    cases += [[rng.randint(-524288,524287) for _ in range(128)] for _ in range(32)]
    for case_id,case in enumerate(cases):
        pos=case_id%16;load(case);tick(start=1,pos=pos,read=1,addr=(pos*4+1)%64)
        while phase:active()
        decoded=[c.stored_prefix(v) for v in case];assert sum(staging,[])==decoded
        assert sum(memory[4*pos:4*pos+4],[])==[v//scale for v in decoded]
        for chunk in (3,0,2,1):read_word(4*pos+chunk)
        if case_id in (15,35):
            for addr in rng.sample(list(range(64)),64):read_word(addr)
        for _ in range(3):tick(data=[rng.randint(-524288,524287) for _ in range(32)])
    for elapsed in (1,31,32,63,95,150):
        load(cases[-1]);tick(start=1,pos=15)
        for _ in range(elapsed):active()
        tick(reset=1,start=1,fill=1,pack_enable=1,bank_enable=1,read=1)
        load(cases[-2]);tick(start=1,pos=0)
        while phase:active()
        for addr in range(4):read_word(addr)
    tick()
    return rows,dict(clocks=len(rows),counts=counts,main_vectors=len(cases),stage_bits=2560,prefix_bits=64*width,
                     control_bits=19,extra_vector_buffer_bits=0,readback='all64 words after initial fill and after main cases; updated words after every vector')


def main():
    global OUT,NO
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
    ap.add_argument('--storage-bits',type=int,choices=(16,12),default=16);args=ap.parse_args()
    bits=args.storage_bits;width=32*bits;NO=width+657
    if bits==12:OUT=OUT.with_name('prefix_store12')
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    import verify as checks
    OUT.mkdir(parents=True,exist_ok=True);small=small_check();net,parts=make(bits)
    (OUT/'store.nl').write_bytes(net.encode());(OUT/'store.v').write_text(rtl(net,'prefix_store'))
    rows,expected=vectors(bits);(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    if bits==16:
        assert sha(net.encode())=='cadb7ceef123ef5a875a161f3df9d42a73a3cb014cd22ed74fb3b32cf7dc5ab3'
        assert sha((OUT/'vectors.txt').read_bytes())=='029e0d64285bed570f958b57fc3ba42c0e8d350ebbaf5f98c42ebc6fc17b739b'
    report=dict(status='small staging checks pass; complete graph and C vectors constructed only',storage_bits=bits,metrics=metrics(net),parts=parts,small=small,expected=expected,
        contract=f'din reset0,start1,pos[5:2],fill6,pack_enable7,bank_enable8,read_request9,read_addr[15:10],chunk[655:16]; dout prefix_codes[{width-1}:0],read_ready{width},busy{width+1},stage_head[{width+641}:{width+2}],stage_ack{width+642},write_ack{width+643},index[{width+650}:{width+644}],write_addr[{width+656}:{width+651}]',
        scope=f'actual P{bits} storage macro with one existing128*s20 staging slot; fill four chunks before start, reset invalidates prefix and requires refilling staging; no full transformer or shared H/A arbitration',
        adopted=False,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/prefix_writer.py',ROOT/'integer_opt/prefix_codec.py',ROOT/'integer_opt/prefix_codec12.py',ROOT/'integer_opt/pilot_bank.py',ROOT/'integer_opt/ports.py',ROOT/'integer/int_model.c',ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'nand.py',ROOT/'golden.py']})
    if args.cloud:
        checks.OUT=OUT;checks.NI=NI;checks.NO=NO
        subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
        wrong=checks.check_nand(rows,net.encode());assert wrong==0,wrong
        bad=flip_output(net);negative=checks.check_nand(rows,bad.encode());assert negative>0
        (OUT/'bad.v').write_text(rtl(bad,'prefix_store'))
        (OUT/'tb.v').write_text(checks.testbench(NI,NO,'prefix_store',str(OUT/'vectors.txt')))
        normal=checks.compile_rtl('source',OUT/'store.v');checks.run([normal],300)
        mutant=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
        (OUT/'negative_verilator.log').write_text(result.stdout+result.stderr)
        assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
        report.update(status='complete actual NAND/RTL/C storage checks and real output mutation pass',verification=dict(clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),actual_read_gate_mutation_mismatches=negative,actual_rtl_mutation_rejected=True))
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','metrics','parts','small','expected')},indent=2))


if __name__=='__main__':main()
