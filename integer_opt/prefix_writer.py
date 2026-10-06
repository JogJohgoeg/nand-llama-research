#!/usr/bin/env python3
"""Serial P16 write controller reusing the existing 128-lane H/A staging slot.

For each 32-lane chunk, accept and rewrite all lanes with decoded P16 values,
then request one whole-word bank write. The bank's512 data wires come from
bits[19:4] of that existing chunk. No extra vector buffer is instantiated.
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
from nand import Builder,metrics,with_state,flip_output
from prefix_codec import make as codec

NI,NO=29,37
OUT=ROOT/'build/integer_opt/prefix_writer'
sha=lambda data:hashlib.sha256(data).hexdigest()


def make():
    ns=13;b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    position=old[:4];index=old[4:11];phase=old[11:]
    reset,start=ins[:2];pos=ins[2:6];value=ins[6:26];xvalid,stage_ready,bank_ready=ins[26:]
    idle=b.land(b.inv(phase[0]),b.inv(phase[1]));pack=b.land(phase[0],b.inv(phase[1]));commit=b.land(b.inv(phase[0]),phase[1])
    keep=b.inv(reset);begin=b.land(keep,b.land(idle,start))
    source_ready=b.land(keep,b.land(pack,stage_ready));stage_valid=b.land(keep,b.land(pack,xvalid))
    take=b.land(source_ready,xvalid);bank_valid=b.land(keep,commit);ack=b.land(bank_valid,bank_ready)
    end_chunk=b.reduce(index[:5],b.land,1);end_vector=b.reduce(index,b.land,1)
    advance=b.lor(b.land(take,b.inv(end_chunk)),b.land(ack,b.inv(end_vector)))
    index_next=b.add(index,[0]*7,advance)[0]
    phase_next=phase[:]
    for enable,new in ((begin,1),(b.land(take,end_chunk),2),(ack,1)):
        phase_next=[b.mux(enable,v,new>>j&1) for j,v in enumerate(phase_next)]
    clear=b.lor(reset,b.land(ack,end_vector));clear_index=b.lor(reset,begin)
    _,encoded=import_net(b,codec(),value)
    nxt=[b.land(keep,b.mux(begin,a,v)) for a,v in zip(position,pos)]
    nxt += [b.land(b.inv(clear_index),v) for v in index_next]
    nxt += [b.land(b.inv(clear),v) for v in phase_next]
    out=index+[0]*4+encoded+[stage_valid,source_ready]+index[5:]+position+[bank_valid,b.inv(idle)]
    net=with_state(b.finish(nxt+out),ns);assert net.n_in==NI and net.n_out==NO
    return net


def vectors():
    adapter=OUT/'reference.c'
    adapter.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+'''
int32_t stored_prefix(int32_t x) {
    int64_t q=int_rne(x,16);
    if(q>32767)q=32767;
    if(q< -32768)q= -32768;
    return (int32_t)(16*q);
}
''')
    lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.stored_prefix.argtypes=[ct.c_int32];c.stored_prefix.restype=ct.c_int32
    rng=random.Random(260663);rows=[];phase=index=position=0;buffer=[0]*128;wanted=[0]*128;committed={}
    counts=dict(staged_values=0,committed_words=0,completed_vectors=0,stage_stalls=0,bank_stalls=0,busy_starts=0,resets=0,aborts=0)
    def tick(reset=0,start=0,pos=0,value=0,xvalid=0,stage_ready=0,bank_ready=0,first=False):
        nonlocal phase,index,position,buffer
        decoded=c.stored_prefix(value);stage_valid=int(phase==1 and xvalid and not reset)
        source_ready=int(phase==1 and stage_ready and not reset);bank_valid=int(phase==2 and not reset)
        expected=index+((decoded&1048575)<<7)+(stage_valid<<27)+(source_ready<<28)+(((position<<2)+(index>>5))<<29)+(bank_valid<<35)+(int(phase!=0)<<36)
        inputs=reset+(start<<1)+(pos<<2)+((value&1048575)<<6)+(xvalid<<26)+(stage_ready<<27)+(bank_ready<<28)
        rows.append((inputs,expected,0 if first else (1<<NO)-1))
        if reset:
            counts['resets']+=1;counts['aborts']+=int(phase!=0);phase=index=position=0
        elif phase==0:
            if start:phase=1;index=0;position=pos
        else:
            counts['busy_starts']+=int(bool(start))
            if phase==1:
                if xvalid and stage_ready:
                    assert value==buffer[index];buffer[index]=decoded;counts['staged_values']+=1
                    if index%32==31:phase=2
                    else:index+=1
                else:counts['stage_stalls']+=1
            elif phase==2:
                if bank_ready:
                    chunk=index//32;actual=buffer[chunk*32:(chunk+1)*32]
                    assert actual==wanted[chunk*32:(chunk+1)*32]
                    # The bank gets existing staging bits[19:4]. Sign-extended
                    # codes decode identically; no hidden pack buffer.
                    encoded=[(x&1048575)>>4 for x in actual]
                    assert [(v if v<32768 else v-65536)*16 for v in encoded]==actual
                    committed[4*position+chunk]=encoded;counts['committed_words']+=1
                    if index==127:phase=0;counts['completed_vectors']+=1
                    else:phase=1;index+=1
                else:counts['bank_stalls']+=1
    def active():
        tick(start=int(rng.randrange(7)==0),pos=rng.randrange(16),value=buffer[index] if phase==1 else rng.randint(-524288,524287),
             xvalid=int(rng.randrange(4)!=0),stage_ready=int(rng.randrange(3)!=0),bank_ready=int(rng.randrange(5)==0))
    tick(reset=1,first=True)
    cases=[[0]*128,[-524288]*128,[524287]*128,[(j-64)*16+8 for j in range(128)]]
    cases += [[rng.randint(-524288,524287) for _ in range(128)] for _ in range(32)]
    for case in cases:
        buffer=case[:];wanted=[c.stored_prefix(v) for v in case];tick(start=1,pos=rng.randrange(16))
        while phase:active()
        tick()
    for elapsed in (1,31,32,63,95,150):
        buffer=cases[-1][:];wanted=[c.stored_prefix(v) for v in buffer];tick(start=1,pos=15)
        for _ in range(elapsed):active()
        tick(reset=1,start=1);tick()
    buffer=cases[-2][:];wanted=[c.stored_prefix(v) for v in buffer];tick(start=1,pos=0)
    while phase:active()
    tick()
    return rows,dict(clocks=len(rows),counts=counts,main_vectors=len(cases),no_stall_vector_clocks=133,
                     existing_staging_bits=128*20,additional_vector_latch_bits=0)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    import verify as checks
    OUT.mkdir(parents=True,exist_ok=True);net=make();assert metrics(net)['nNand']<=4000
    rows,expected=vectors();(OUT/'writer.nl').write_bytes(net.encode());(OUT/'writer.v').write_text(rtl(net,'prefix_writer'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=30)
    wrong=checks.check_nand(rows,net.encode());assert wrong==0,wrong
    # First output is a real source index bit, so this mutation exercises the
    # control/address path independently of the codec's exhaustive check.
    bad=flip_output(net);negative=checks.check_nand(rows,bad.encode());assert negative>0
    verification=dict(status='actual NAND/C pass',clocks=len(rows),nand_mismatches=wrong,actual_index_gate_mutation_mismatches=negative)
    if args.cloud:
        (OUT/'bad.v').write_text(rtl(bad,'prefix_writer'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'prefix_writer',str(OUT/'vectors.txt')))
        normal=checks.compile_rtl('source',OUT/'writer.v');checks.run([normal],300)
        mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
        (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
        assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
        verification.update(status='actual NAND/RTL/C pass',rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)
    report=dict(metrics=metrics(net),expected=expected,verification=verification,
                contract='din reset0,start1,pos[5:2],x[25:6],xvalid26,stage_ready27,bank_ready28; dout index[6:0],decoded[26:7],stage_valid27,source_ready28,bank_addr[34:29],bank_valid35,busy36',
                scope='serial write controller plus codec; H/A storage and held bank are external ports; reset aborts and requires source reinitialization',
                adopted=False,numerical_contract_changed=True,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/prefix_codec.py',ROOT/'integer/int_model.c',ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'nand.py',ROOT/'golden.py']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
