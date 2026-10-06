#!/usr/bin/env python3
"""Pilot with a held KV ring and an actual seek/accept controller.

View 2 seeks din[4:0]. dout[276] acknowledges the requested row; a write is
accepted only on that ready edge. Other views hold the bank. View 2 + load
resets the cursor and discards old data validity, without clearing the bank.
Local work constructs the large graph and C vectors; only Actions evaluates it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'));sys.path.insert(0,str(ROOT))
import export as physical
import verify as checks
from ports import make as ring_bank
from ci import cec
from golden import Netlist
from nand import Builder,blif,flip_output,from_yosys,metrics,verify_state,with_state

OUT=ROOT/'build/integer_opt/pilot_bank'
NI,NO=294,277
LOW=(1<<276)-1
sha=lambda b:hashlib.sha256(b).hexdigest()


def bank(rows,width):
    a=(rows-1).bit_length();data_bits=rows*width;ns=data_bits+a
    _,ring=ring_bank('ring',rows,width)
    b=Builder(ns+width+a+3);old=list(range(2,ns+2))
    data=list(range(ns+2,ns+2+width));addr=list(range(ns+2+width,ns+2+width+a))
    kv,reset,we=range(ns+2+width+a,ns+2+width+a+3)
    cursor=old[data_bits:]
    match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cursor,addr)],b.land,1)
    active=b.land(kv,b.inv(reset))
    advance=b.land(active,b.lor(b.inv(match),we))
    write=b.land(active,b.land(match,we))
    nxt,head=physical.import_net(b,ring,data+[advance,write],old[:data_bits])
    nxt += [b.land(b.inv(reset),x) for x in b.add(cursor,[0]*a,advance)[0]]
    ready=b.lor(b.inv(kv),b.land(b.inv(reset),match))
    comb=b.finish(nxt+head+[ready])
    return comb,with_state(comb,ns)


def reference(rows,width):
    a=(rows-1).bit_length();s=rows*width;ns=s+a;ni=ns+width+a+3
    lines=[f'module top(input [{ni-1}:0] din,output [{ns+width}:0] dout);',
           f'wire [{a-1}:0] cursor=din[{ns-1}:{s}],addr=din[{ns+width+a-1}:{ns+width}];',
           f'wire [{width-1}:0] data=din[{ns+width-1}:{ns}];',
           f'wire kv=din[{ni-3}],reset=din[{ni-2}],we=din[{ni-1}];',
           'wire match_row=(cursor==addr);',
           'wire advance=kv && !reset && (!match_row || we);',
           'wire write_head=kv && !reset && match_row && we;']
    for i in range(rows):
        incoming=f'din[{(i+1)%rows*width} +: {width}]'
        if i==rows-1:incoming=f'(write_head ? data : {incoming})'
        lines.append(f'assign dout[{i*width} +: {width}]=advance ? {incoming} : din[{i*width} +: {width}];')
    lines += [f"assign dout[{s} +: {a}]=reset ? {a}'d0 : cursor + advance;",
              f'assign dout[{ns} +: {width}]=din[0 +: {width}];',
              f'assign dout[{ns+width}]=!kv || (!reset && match_row);','endmodule','']
    return '\n'.join(lines)


def small_check():
    rows,width=4,8;a=2;s=rows*width;ns=s+a;comb,net=bank(rows,width)
    assert metrics(net)['nNand']<=4000
    rng=random.Random(260655);xs=[];ys=[]
    for _ in range(512):
        values=[rng.randrange(256) for _ in range(rows)];cursor=rng.randrange(rows)
        data=rng.randrange(256);addr=rng.randrange(rows);kv,reset,we=[rng.randrange(2) for _ in range(3)]
        inputs=data+(addr<<width)+(kv<<(width+a))+(reset<<(width+a+1))+(we<<(width+a+2))
        xs.append(sum(x<<(i*width) for i,x in enumerate(values))+(cursor<<s)+(inputs<<ns))
        ready=int(not kv or not reset and cursor==addr);head=values[0]
        if reset:cursor=0
        elif kv and (cursor!=addr or we):
            values=values[1:]+[data if cursor==addr and we else values[0]]
            cursor=(cursor+1)%rows
        ys.append(sum(x<<(i*width) for i,x in enumerate(values))+(cursor<<s)+(head<<ns)+(ready<<(ns+width)))
    receipt=verify_state(comb,xs,ys,ns);receipt.pop('nl_hex')
    return receipt


def make():
    directory=ROOT/'integer_opt/pilot_units';manifest=json.loads((directory/'manifest.json').read_text())
    replacements={}
    for name,meta in manifest.items():
        raw=(directory/(name+'.nl')).read_bytes();assert sha(raw)==meta['sha256']
        replacements[name]=Netlist.decode(raw,meta['nIn'],meta['nOut'])
    units={n:replacements[n] if n=='serial_div' else physical.load_unit(n)
           for n in ('serial_div','serial_mul','serial_sqrt','resid','exp','dot32')}
    comb,storage=bank(32,276)
    ns=storage.n_state+sum(units[n].n_state for n in ('serial_div','serial_mul','serial_sqrt'))
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    addr=ins[:13];data=ins[13:289];we=ins[289];load=ins[290];view=ins[291:294]
    kv=b.land(b.inv(view[2]),b.land(view[1],b.inv(view[0])))
    nxt,bank_out=physical.import_net(b,storage,data+addr[:5]+[kv,b.land(kv,load),we],old[:storage.n_state])
    _,trits=physical.import_net(b,replacements['weights_layer0'],addr)
    _,dot=physical.import_net(b,units['dot32'],data[:256]+trits)
    scalar={};offset=storage.n_state
    for name in ('serial_div','serial_mul','serial_sqrt'):
        u=units[name];state=old[offset:offset+u.n_state];offset+=u.n_state
        operand={'serial_div':data[:64]+data[64:89],
                 'serial_mul':data[:128],'serial_sqrt':data[:48]}[name]
        ds,out=physical.import_net(b,u,[load]+operand,state);nxt.extend(ds);scalar[name]=out[u.n_state:]
    _,resid=physical.import_net(b,units['resid'],data[:40])
    _,exp=physical.import_net(b,units['exp'],data[:64])
    choices=[dot,trits,bank_out[:276],scalar['serial_div'],scalar['serial_mul'],scalar['serial_sqrt'],resid,exp]
    choices=[x+[0]*(276-len(x)) for x in choices]
    for select in view:
        choices=[[b.mux(select,x,y) for x,y in zip(choices[i],choices[i+1])] for i in range(0,len(choices),2)]
    net=with_state(b.finish(nxt+choices[0]+bank_out[-1:]),ns)
    assert net.n_in==NI and net.n_out==NO
    return net,comb,storage,manifest


def vectors(g):
    baseline,groups=checks.vectors(g);rows=[];cursor=0;values=[0]*32;known=[False]*32
    counts=dict(reset=0,seek=0,read=0,write=0,other=0);rng=random.Random(260655)
    def tick(value,want=0,mask=LOW):
        nonlocal cursor,values,known
        addr=value&31;data=(value>>13)&LOW;we=(value>>289)&1;load=(value>>290)&1;view=value>>291
        ready=int(view!=2 or not load and cursor==addr)
        if view==2:
            want=values[cursor];mask=LOW if ready and known[cursor] else 0
        rows.append((value,want+(ready<<276),mask|(1<<276)))
        if view==2 and load:
            values=values[cursor:]+values[:cursor];known=[False]*32;cursor=0;counts['reset']+=1
        elif view==2:
            if cursor!=addr:cursor=(cursor+1)%32;counts['seek']+=1
            elif we:values[cursor]=data;known[cursor]=True;cursor=(cursor+1)%32;counts['write']+=1
            else:counts['read']+=1
        else:counts['other']+=1
    def request(addr,data=0,we=0):
        value=addr+(data<<13)+(we<<289)+(2<<291)
        while cursor!=addr:tick(value)
        tick(value)
    tick((2<<291)+(1<<290))
    for value,want,mask in baseline:
        if value>>291==2:
            addr=value&31
            while cursor!=addr:tick(value)
            if mask:assert want==values[addr]
        tick(value,want,mask)
    # Change requests mid-seek, change unaccepted write data, and interrupt with
    # non-bank operations. Only an acknowledged write may alter stored values.
    for _ in range(256):
        tick(rng.randrange(32)+(rng.getrandbits(276)<<13)+(rng.randrange(2)<<289)+(2<<291))
        addr=rng.randrange(8192);tick(addr+(1<<291),g.slice_word(addr))
    for addr in rng.sample(list(range(32)),32):request(addr)
    # Reset discards logical validity; initialize all words before observing.
    tick(((cursor+7)%32)+(1<<289)+(1<<290)+(2<<291))
    for addr in range(32):request(addr,rng.getrandbits(276),1)
    for addr in rng.sample(list(range(32)),32):request(addr)
    return rows,dict(baseline_clocks=len(baseline),baseline_groups=groups,clocks=len(rows),operations=counts,
                     protocol='pre-edge ready; while waiting no data acceptance; writes move the cursor by one',
                     startup='explicit view2+load cursor reset, then all words written before any data comparison')


def cloud_check(net,comb,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    ys=OUT/'bank.ys';ys.write_text(f'read_verilog {OUT}/bank.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/bank.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'bank.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    mapped=from_yosys(json.loads((OUT/'bank.ref.json').read_text()),comb.n_in,comb.n_out)
    (OUT/'bank.reference.blif').write_text(blif(mapped));(OUT/'bank.negative.blif').write_text(blif(flip_output(comb)))
    positive=cec(abc,OUT/'bank.blif',OUT/'bank.reference.blif',OUT/'bank.cec.log')
    negative=cec(abc,OUT/'bank.negative.blif',OUT/'bank.reference.blif',OUT/'bank.negative.log')
    assert positive['verdict']=='equivalent' and negative['verdict']=='different'
    bank_proof=dict(cec=positive,negative=negative)
    (OUT/'bank_verification.json').write_text(json.dumps(bank_proof,indent=2)+'\n')
    checks.NO=NO
    checks.run(['cc','-O3','-std=c99','-fPIC','-shared',physical.HERE/'nl_sim.c','-o',OUT/'sim.so'],60)
    wrong_normal=checks.check_nand(rows,net.encode())
    assert wrong_normal==0,f'{wrong_normal} clocks differ from C/logical-bank expectations'
    bad=Netlist.decode(net.encode(),NI,NO);inv=bad.records[-1][1];op,a,b=bad.records[inv-NI-2]
    assert op==0 and a==b;bad.records[-1]=(0,a,a)  # Actual ready output NAND mutation.
    wrong=checks.check_nand(rows,bad.encode());assert wrong>0
    (OUT/'bad.v').write_text(physical.rtl(bad,'int_c16_ring_slice'))
    (OUT/'tb.v').write_text(checks.testbench(NI,NO,'int_c16_ring_slice',str(OUT/'vectors.txt')))
    executable=checks.compile_rtl('source',OUT/'slice.v');checks.run([executable],300)
    executable=checks.compile_rtl('negative',OUT/'bad.v')
    mutation=subprocess.run([str(executable)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(mutation.stdout+mutation.stderr)
    assert mutation.returncode!=0 and 'C99 comparison failed' in mutation.stdout+mutation.stderr
    return dict(status='pass',bank=bank_proof,clocks=len(rows),nand_mismatches=0,
                actual_ready_gate_mutation_mismatches=wrong,rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    assert sha((physical.HERE/'model.bin').read_bytes())==physical.MODEL_SHA
    OUT.mkdir(parents=True,exist_ok=True);checks.OUT=OUT
    small=small_check();net,comb,storage,replacements=make()
    (OUT/'slice.nl').write_bytes(net.encode());(OUT/'slice.v').write_text(physical.rtl(net,'int_c16_ring_slice'))
    (OUT/'bank.blif').write_text(blif(comb));(OUT/'bank.ref.v').write_text(reference(32,276))
    rows,expected=vectors(checks.golden())
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    report=dict(status='small bank checked; large composition and C expectations only',metrics=metrics(net),
                bank=metrics(storage),small_bank=small,expected=expected,replacements=replacements,
                baseline_pilot_nand=155655,mapped_pilot_nand=145629,
                scope='externally scheduled representative slice; KV read latency changed, not single-cycle equivalence',
                contract='din unchanged; extra dout[276] ready. View2 auto-seeks; we writes only on ready edge. View2+load resets cursor and invalidates old contents. Other views hold bank.')
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),model_sha256=physical.MODEL_SHA,
                  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
                  sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/ports.py',
                    physical.HERE/'export.py',physical.HERE/'verify.py',physical.HERE/'nl_sim.c',physical.HERE/'golden_slice.c',
                    ROOT/'integer/int_model.c',ROOT/'nand.py',ROOT/'golden.py',ROOT/'bench.py',ROOT/'ci.py',
                    physical.HERE/'units/manifest.json',ROOT/'integer_opt/pilot_units/manifest.json']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=cloud_check(net,comb,rows)
        report['status']='bank arbitrary-state CEC plus complete source NAND/RTL/C checks and actual mutations pass'
        (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','metrics','bank','expected')},indent=2))


if __name__=='__main__':main()
