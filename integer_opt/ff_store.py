#!/usr/bin/env python3
"""Two-pass raw-FF request port, exact A8 quantizer and scalar-write FF bank.

The producer recomputes the same pure FF row on the second pass. Only A8
codes are retained; no raw vector or word assembly buffer is added. A reset
aborts, invalidates the bank and requires a complete new vector before reads.
"""
import argparse
import ctypes as ct
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
from export import import_net,rtl
from nand import Builder,metrics,with_state,flip_output,verify_state,blif,from_yosys
from ci import cec
from prefix_codec import GOLDEN_SHA
from quant_stream import make as quantizer
from ff_bank import shape

OUT=ROOT/'build/integer_opt/ff_store'
sha=lambda data:hashlib.sha256(data).hexdigest()


def scalar_bank(rows=11,lanes=32,last=16):
    a,width,sizes,ns=shape(8,rows,lanes,last);n=ns//8;iw=(n-1).bit_length()
    b=Builder(ns+8+iw+1+a);memory=list(range(2,ns+2));ins=list(range(ns+2,b.n_in+2))
    code=ins[:8];index=ins[8:8+iw];write=ins[8+iw];address=ins[9+iw:]
    def eq(bits,value):return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    md=[];words=[];offset=0
    for row,size in enumerate(sizes):
        word=memory[offset:offset+size];offset+=size
        for lane in range(size//8):
            take=b.land(write,eq(index,row*lanes+lane))
            md += [b.mux(take,v,z) for v,z in zip(word[lane*8:(lane+1)*8],code)]
        words.append(word+[0]*(width-size))
    words += [[0]*width for _ in range((1<<a)-rows)]
    for bit in address:
        words=[[b.mux(bit,v,z) for v,z in zip(words[i],words[i+1])] for i in range(0,len(words),2)]
    comb=b.finish(md+words[0]);return comb,with_state(comb,ns)


def make(rows=11,lanes=32,last=16):
    assert lanes>1 and lanes&(lanes-1)==0
    a,width,sizes,bank_bits=shape(8,rows,lanes,last);n=bank_bits//8;iw=(n-1).bit_length()
    assert 1<=n<=336
    q=quantizer();_,bank=scalar_bank(rows,lanes,last);ns=q.n_state+bank_bits+1;ni=24+a
    b=Builder(ns+ni);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+ni))
    qs=old[:q.n_state];memory=old[q.n_state:-1];filled=old[-1]
    reset,start=ins[:2];x=ins[2:22];xvalid,enable=ins[22:24];address=ins[24:]
    qd,qo=import_net(b,q,[reset,start]+[n>>j&1 for j in range(9)]+x+[xvalid,enable],qs)
    code=qo[:8];index=qo[8:17];maximum=qo[17:37];xready,valid,busy,replay=qo[37:]
    def eq(bits,value):return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    keep=b.inv(reset);ack=b.land(valid,enable);begin=b.land(keep,b.land(start,b.inv(busy)))
    completed=b.land(ack,eq(index,n-1));clear=b.lor(reset,begin)
    fd=b.land(b.inv(clear),b.lor(filled,completed))
    md,word=import_net(b,bank,code+index[:iw]+[ack]+address,memory)
    available=b.land(keep,b.land(filled,b.inv(busy)))
    legal=b.reduce([eq(address,i) for i in range(rows)],b.lor,0)
    read_ready=b.land(available,b.land(b.inv(start),legal))
    output=word+index+maximum+[xready,replay,busy,available,ack,read_ready]
    net=with_state(b.finish(qd+md+[fd]+output),ns)
    assert net.n_state==bank_bits+q.n_state+1
    return net,dict(raw_producer_external=True,lanes=n,word_lanes=lanes,words=rows,tail_lanes=last,
                    code_state_bits=bank_bits,quantizer_state_bits=q.n_state,valid_state_bits=1,
                    raw_vector_state_bits=0,assembly_buffer_bits=0,quantizer=metrics(q),scalar_bank=metrics(bank))


def prove_bank():
    assert os.getenv('GITHUB_ACTIONS')=='true'
    comb,bank=scalar_bank();a,width,sizes,ns=shape(8);n=ns//8;iw=(n-1).bit_length()
    rng=random.Random(260670);xs=[];ys=[]
    for i in range(512):
        old=rng.getrandbits(ns);code=rng.randrange(256);index=i;write=(i//16)&1;address=rng.randrange(16)
        x=code+(index<<8)+(write<<(8+iw))+(address<<(9+iw));xs.append(old+(x<<ns))
        nxt=old
        if write and index<n:nxt=(old&~(255<<(index*8)))+(code<<(index*8))
        word=((old>>(address*width))&((1<<sizes[address])-1)) if address<len(sizes) else 0
        ys.append(nxt+(word<<ns))
    checked=verify_state(comb,xs,ys,ns);checked.pop('nl_hex')
    lines=[f'module top(input [{comb.n_in-1}:0] din,output [{comb.n_out-1}:0] dout);',
           f'wire [7:0] code=din[{ns} +: 8];',f'wire [{iw-1}:0] index=din[{ns+8} +: {iw}];',
           f'wire we=din[{ns+8+iw}];',f'wire [3:0] addr=din[{ns+9+iw} +: 4];',f'reg [{width-1}:0] word;']
    for i in range(n):lines.append(f"assign dout[{8*i} +: 8]=(we && index=={iw}'d{i}) ? code : din[{8*i} +: 8];")
    lines+=['always @* begin',' word=0;',' case(addr)']
    offset=0
    for row,size in enumerate(sizes):
        value=f'din[{offset} +: {size}]';offset+=size
        if size<width:value='{'+str(width-size)+"'d0,"+value+'}'
        lines.append(f"  4'd{row}: word={value};")
    lines+=['  default: word=0;',' endcase','end',f'assign dout[{ns} +: {width}]=word;','endmodule','']
    (OUT/'scalar.ref.v').write_text('\n'.join(lines));(OUT/'scalar.blif').write_text(blif(comb))
    script=OUT/'scalar.ys';script.write_text(f'read_verilog {OUT}/scalar.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/scalar.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'scalar.yosys.log'),'-s',str(script)],stdout=subprocess.DEVNULL,check=True,timeout=240)
    ref=from_yosys(json.loads((OUT/'scalar.ref.json').read_text()),comb.n_in,comb.n_out)
    (OUT/'scalar.ref.blif').write_text(blif(ref));(OUT/'scalar.negative.blif').write_text(blif(flip_output(comb)))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    proof=cec(abc,OUT/'scalar.blif',OUT/'scalar.ref.blif',OUT/'scalar.cec.log');assert proof['verdict']=='equivalent'
    negative=cec(abc,OUT/'scalar.negative.blif',OUT/'scalar.ref.blif',OUT/'scalar.negative.log');assert negative['verdict']=='different'
    return dict(metrics=metrics(bank),state_check=checked,proof=proof,negative=negative)


def vectors(rows=11,lanes=32,last=16):
    a,width,sizes,bank_bits=shape(8,rows,lanes,last);n=bank_bits//8;no=width+35
    adapter=OUT/'reference.c';adapter.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+
        'int32_t ff_quant(const int32_t *x,int n,int8_t *q){return quant(x,n,q);}\n')
    library=OUT/'reference.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(library)],check=True,timeout=30)
    c=ct.CDLL(str(library));c.ff_quant.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int8)];c.ff_quant.restype=ct.c_int32
    rng=random.Random(260669+n)
    values=[[0]*n,[-524288]*n,[524287]*n,[(-524288,524287)[i%2] for i in range(n)],
            [254]+[(i%127)*2-125 for i in range(n-1)],
            [rng.randint(-524288,524287) for _ in range(n)],
            [rng.randint(-255,255) for _ in range(n)]]
    cases=[]
    for x in values:
        out=(ct.c_int8*n)();m=c.ff_quant((ct.c_int32*n)(*x),n,out)
        assert m==max(1,max(abs(v) for v in x));cases.append(dict(x=x,m=m,q=list(out)))
    state=dict(phase=0,index=0,m=0,remaining=0,q=0,filled=False,case=None)
    memory=[None]*n;clocks=[]
    counts=dict(scan=0,replay=0,writes=0,complete=0,reads=0,input_stalls=0,write_stalls=0,busy_starts=0,resets=0,aborts=0)
    def tick(reset=0,start=0,x=0,xvalid=0,enable=0,addr=0,case=None,first=False):
        phase=state['phase'];index=state['index'];busy=int(phase!=0)
        ready=int(not reset and phase in (1,2));valid=int(not reset and phase==4)
        ack=valid*enable;available=int(not reset and state['filled'] and not busy)
        read_ready=int(available and not start and addr<rows)
        word=0;mask=((1<<no)-1)^((1<<width)-1)
        if read_ready:
            for j in range(sizes[addr]//8):
                v=memory[addr*lanes+j];assert v is not None;word|=(v&255)<<(8*j)
            mask|=(1<<width)-1;counts['reads']+=1
        elif available and not start and addr>=rows:
            mask|=(1<<width)-1 # invalid word reads are also required to be0
        want=word+(index<<width)+(state['m']<<(width+9))+(ready<<(width+29))
        want+=(int(phase in (2,3,4))<<(width+30))+(busy<<(width+31))+(available<<(width+32))+(ack<<(width+33))+(read_ready<<(width+34))
        inp=reset+(start<<1)+((x&1048575)<<2)+(xvalid<<22)+(enable<<23)+(addr<<24)
        clocks.append((inp,want,0 if first else mask))
        if reset:
            counts['resets']+=1;counts['aborts']+=busy
            state.update(phase=0,index=0,m=0,remaining=0,q=0,filled=False,case=None)
        elif phase==0:
            if start:state.update(phase=1,index=0,m=1,filled=False,case=case)
        else:
            counts['busy_starts']+=int(bool(start))
            if phase in (1,2):
                if not xvalid:counts['input_stalls']+=1
                else:
                    assert x==state['case']['x'][index]
                    if phase==1:
                        counts['scan']+=1;state['m']=max(state['m'],abs(x))
                        if index==n-1:
                            assert state['m']==state['case']['m'];state.update(phase=2,index=0)
                        else:state['index']+=1
                    else:counts['replay']+=1;state.update(phase=3,remaining=28)
            elif phase==3:
                state['remaining']-=1
                if state['remaining']==0:state.update(phase=4,q=state['case']['q'][index])
            elif phase==4:
                if not enable:counts['write_stalls']+=1
                else:
                    memory[index]=state['q'];counts['writes']+=1
                    if index==n-1:state.update(phase=0,filled=True);counts['complete']+=1
                    else:state['index']+=1;state['phase']=2
    def active(stalls=True,hold=False):
        phase=state['phase'];x=state['case']['x'][state['index']] if phase in (1,2) else rng.randint(-524288,524287)
        tick(start=int(stalls and rng.randrange(13)==0),x=x,xvalid=int(not stalls or rng.randrange(5)!=0),
             enable=int(not hold and (not stalls or rng.randrange(4)!=0)),addr=rng.randrange(1<<a))
    def read_all(case):
        assert memory==case['q']
        for addr in range(1<<a):tick(addr=addr)
    tick(reset=1,first=True);tick()
    for i,case in enumerate(cases):
        begin=len(clocks);tick(start=1,case=case)
        while state['phase']:active(stalls=i!=0)
        if i==0:assert len(clocks)-begin==1+31*n
        read_all(case)
    for phase,elapsed in ((1,0),(1,3),(2,0),(3,0),(3,1),(3,27),(4,2)):
        case=cases[-1];tick(start=1,case=case)
        while state['phase']!=phase:active(hold=True)
        for _ in range(elapsed):active(hold=True)
        tick(reset=1,start=1,x=-524288,xvalid=1,enable=1);tick()
        assert not state['filled']
    case=cases[-1];tick(start=1,case=case)
    while not (state['phase']==4 and state['index']==n-2):active()
    tick(reset=1);tick() # discard almost-complete contents
    tick(start=1,case=case)
    while state['phase']:active()
    read_all(case);tick(reset=1);tick()
    return clocks,dict(clocks=len(clocks),counts=counts,main_vectors=len(cases),no_stall_clocks=1+31*n,
        cases=[dict(maximum=c['m'],input_sha256=sha(b''.join(v.to_bytes(4,'little',signed=True) for v in c['x'])),
                    code_sha256=sha(bytes(v&255 for v in c['q']))) for c in cases])


def check(net,rows,cloud):
    import verify as checks
    if cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:assert metrics(net)['nNand']<=4000
    checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=30)
    assert checks.check_nand(rows,net.encode())==0
    bad=flip_output(net);wrong=checks.check_nand(rows,bad.encode());assert wrong>0
    result=dict(status='actual NAND/C pass',clocks=len(rows),nand_mismatches=0,actual_read_gate_mutation_mismatches=wrong)
    if cloud:
        (OUT/'bad.v').write_text(rtl(bad,'ff_store'));(OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'ff_store',str(OUT/'vectors.txt')))
        normal=checks.compile_rtl('source',OUT/'store.v');checks.run([normal],300)
        mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
        (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
        assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
        result.update(status='actual NAND/RTL/C pass',rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)
    return result


def main():
    global OUT
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--small',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    shape_args=(3,4,2) if args.small else (11,32,16)
    if args.small:OUT=ROOT/'build/integer_opt/ff_store_small'
    OUT.mkdir(parents=True,exist_ok=True);net,parts=make(*shape_args);rows,expected=vectors(*shape_args)
    (OUT/'store.nl').write_bytes(net.encode());(OUT/'store.v').write_text(rtl(net,'ff_store'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    report=dict(status='construction and C reference only',metrics=metrics(net),parts=parts,expected=expected,
        contract='din reset0,start1,x[21:2],xvalid22,write_enable23,read_addr next; dout code_word,index9,max20,xready,replay,busy,complete,write_ack,read_ready; all outputs pre-edge',
        scope='raw FF producer external and must recompute same indexed rows on replay; no full FF arithmetic or shared-DIV arbitration; bank invalid until complete; invalid addresses read0',
        adopted=False,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/ff_bank.py',ROOT/'integer_opt/quant_stream.py',
          ROOT/'integer_opt/prefix_codec.py',ROOT/'integer_opt/pilot_units/manifest.json',ROOT/'integer_opt/pilot_units/serial_div.nl',
          ROOT/'integer/int_model.c',ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    if args.small or args.cloud:report['verification']=check(net,rows,args.cloud);report['status']=report['verification']['status']
    if args.cloud and not args.small:report['bank_proof']=prove_bank()
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2));print(json.dumps({k:v for k,v in expected.items() if k!='cases'}))


if __name__=='__main__':main()
