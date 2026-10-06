#!/usr/bin/env python3
"""Raw H through true layer0 FFN, writing down results into the original H slot.

No extra result vector: the existing scalar-write/rotate port is reused after
all gate/up reads finish. Down arithmetic is not yet shared with the prefix.
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
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from export import import_net,rtl
from nand import Builder,metrics,with_state,verify_state
from prefix_store import stage
from golden import Netlist
import ff_input as reference_input
import ff_stream as ff_reference
import down_engine as down_reference
from ff_input import controller as input_control
from ff_input_shared import make as upstream
from down_engine import make as down_net,weight_table
NI,NO=650,1036
OUT=R/'build/integer_opt/ff_writeback'
sha=lambda data:hashlib.sha256(data).hexdigest()

def controller(b,old,ins):
    phase=old[:2];pending,complete=old[2:];reset,start,filled,ffdone,dvalid,last_lane,dbusy,write_enable,fill_ack,read_advance,fill=ins
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    p=[eq(phase,j) for j in range(4)];keep=b.inv(reset)
    begin=b.reduce([keep,p[0],start,filled],b.land,1)
    dstart=b.land(keep,p[2]);ack=b.reduce([keep,p[3],dvalid,write_enable],b.land,1)
    finish=b.reduce([keep,p[3],pending,b.inv(dbusy)],b.land,1)
    pp=phase[:]
    for enable,target in ((begin,1),(b.land(p[1],ffdone),2),(p[2],3),(finish,0)):
        pp=[b.mux(enable,v,target>>j&1) for j,v in enumerate(pp)]
    avail=b.reduce([keep,p[0],complete,b.inv(start),b.inv(fill)],b.land,1)
    advance=b.land(keep,b.lor(pending,b.land(avail,read_advance)))
    done=b.land(keep,b.lor(finish,b.land(complete,b.inv(b.lor(begin,fill_ack)))))
    return [b.land(keep,v) for v in pp]+[b.land(keep,b.land(ack,last_lane)),done],[begin,dstart,ack,advance,avail,b.inv(p[0])]

def small_check():
    b=Builder(15);nxt,out=controller(b,list(range(2,6)),list(range(6,17)));net=b.finish(nxt+out);xs=list(range(1<<15));ys=[]
    for x in xs:
        p=x&3;pending=x>>2&1;complete=x>>3&1
        reset,start,filled,ffdone,dvalid,last,dbusy,we,ackfill,rd,fill=[x>>(4+j)&1 for j in range(11)]
        begin=int(not reset and p==0 and start and filled);ds=int(not reset and p==2);ack=int(not reset and p==3 and dvalid and we)
        finish=int(not reset and p==3 and pending and not dbusy);pp=p
        if begin:pp=1
        if p==1 and ffdone:pp=2
        if p==2:pp=3
        if finish:pp=0
        avail=int(not reset and p==0 and complete and not start and not fill);advance=int(not reset and (pending or avail and rd))
        done=int(not reset and (finish or complete and not (begin or ackfill)))
        nv=(0 if reset else pp)+(int(not reset and ack and last)<<2)+(done<<3)
        ys.append(nv+(sum(v<<j for j,v in enumerate((begin,ds,ack,advance,avail,int(p!=0))))<<4))
    r=verify_state(net,xs,ys,4);r.pop('nl_hex');return r

def make():
    ff=upstream()[0];assert sha(ff.encode())=='06ee0a5a21e27823ac7224d84d22782369505f9c65253082a8ed283b9056b916'
    weights,_=weight_table();down,dp=down_net(weights);_,work=stage();ns=ff.n_state+down.n_state+4
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI));fs=old[:5921];ds=old[5921:-4];cs=old[-4:]
    reset,start,fill=ins[:3];enable,we=ins[643:645];address=ins[645:649];rd=ins[649]
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    idle=eq(cs[:2],0);down_active=eq(cs[:2],3)
    ff_start=b.land(start,idle);ff_fill=b.land(fill,idle)
    _,dout=import_net(b,down,[reset]+[0]*279,ds)
    bank_address=[b.mux(down_active,a,g) for a,g in zip(address,dout[27:31])]
    # State-only bank readiness permits a feed-forward connection to down.
    _,probe=import_net(b,ff,[reset,ff_start,ff_fill]+ins[3:645]+bank_address,fs)
    filled=eq(fs[5915:5920],16)
    cd,co=controller(b,cs,[reset,start,filled,probe[291],dout[32],b.reduce(dout[20:25],b.land,1),dout[33],we,probe[351],rd,fill])
    begin,dstart,ack,advance,available,busy=co
    ffd,fo=import_net(b,ff,[reset,ff_start,ff_fill]+ins[3:645]+bank_address,fs)
    xvalid=b.reduce([down_active,enable,fo[293]],b.land,1)
    dd,do=import_net(b,down,[reset,dstart]+fo[265:285]+fo[:256]+[xvalid,b.land(down_active,we)],ds)
    # Reconstruct the existing H-slot port, adding down/read actions at its
    # control/data inputs, not a second2560-bit bank or a per-bit D mux.
    hphase=fs[3339:3342];hidx=fs[3330:3339];hcode=fs[3347:3355];hs=fs[3355:5915]
    qr=b.land(b.inv(reset),b.lor(eq(hphase,1),eq(hphase,2)));qv=b.land(b.inv(reset),eq(hphase,4))
    replay=b.lor(eq(hphase,2),b.lor(eq(hphase,3),eq(hphase,4)))
    fr=b.land(b.inv(reset),b.lor(eq(fs[534:538],1),eq(fs[534:538],4)))
    fd=b.reduce([b.inv(reset),fs[3280],eq(fs[576:579],0)],b.land,1)
    _,hc=input_control(b,fs[5915:5921],[reset,ff_start,ff_fill,enable,qr,qv,replay,b.reduce(hidx[:5],b.land,1),eq(hidx,127),fr,fd])
    value=[b.mux(ack,a,v) for a,v in zip(hcode+[hcode[-1]]*12,do[:20])]
    index=[b.mux(ack,a,v) for a,v in zip(hidx[:5],do[20:25])]
    hd,_=import_net(b,work,ins[3:643]+value+index+[hc[1],b.lor(hc[6],advance),b.lor(hc[7],ack)],hs)
    ffd[3355:5915]=hd
    net=with_state(b.finish(ffd+dd+cd+fo+do+hs[:640]+cs+[busy,available]),ns)
    assert net.n_in==NI and net.n_out==NO
    return net,dict(upstream=metrics(ff),down=metrics(down),state_bits=ns,h_result_slot_bits=2560,extra_result_vector_bits=0,
                   separate_mul_instances=2,separate_div_instances=2,separate_dot_instances=2,
                   scope='raw H through gate/up/SwiGLU/down back into original H slot; residual/norm/complete transformer not included; down resources not yet shared')


def vectors(c,d):
    original,expected=reference_input.vectors(c);rng=random.Random(260675)
    values=[[0]*128,[-524288]*128,[rng.randrange(-4096,4097) for _ in range(128)],[524287]*128]
    cases={}
    for raw in values:
        hq=(ct.c_int8*128)();ff=(ct.c_int32*336)();fq=(ct.c_int8*336)();hm=ct.c_int32()
        fm=c.ff_reference((ct.c_int32*128)(*raw),hq,ff,fq,ct.byref(hm))
        dq=(ct.c_int8*336)();out=(ct.c_int32*128)();dm=d.down_reference(ff,dq,out)
        assert dm==fm and list(dq)==list(fq)
        cases[tuple(raw)]=dict(fm=fm,ff=list(fq),out=list(out))
    rng=random.Random(260683);rows=[];incoming=[None]*128;memory=[None]*128
    state=dict(phase=0,pending=0,complete=0);down=dict(phase=0,row=0,group=0,remaining=0)
    active=None;start_clock=None;first_latency=None
    counts=dict(starts=0,complete=0,resets=0,aborts=0,down_groups=0,down_rows=0,
                input_stalls=0,output_stalls=0,ignored_down_starts=0,h_words_read=0,h_write_rotations=0)
    def tick(inp,fo,fmask,inserted=False,read_advance=0):
        nonlocal active,start_clock,first_latency
        reset=inp&1;start=inp>>1&1;fill=inp>>2&1;enable=inp>>643&1;we=inp>>644&1
        p=state['phase'];pending=state['pending'];complete=state['complete'];dp=down['phase'];row=down['row'];group=down['group']
        ready=int(not reset and dp==1);valid=int(not reset and dp==4);dbusy=int(dp!=0)
        begin=int(not reset and p==0 and start and ((fo>>344)&31)==16)
        dstart=int(not reset and p==2);ack=int(not reset and p==3 and valid and we)
        finish=int(not reset and p==3 and pending and not dbusy)
        available=int(not reset and p==0 and complete and not start and not fill)
        fill_ack=fo>>351&1
        if inserted:
            assert active is not None and fo>>291&1
            address=group if p==3 else inp>>645&15
            chunk=active['ff'][32*address:32*address+32]
            word=sum((v&255)<<(8*j) for j,v in enumerate(chunk)) if address<11 else 0
            fo=(fo&~((1<<256)-1))|word
            fo=(fo&~(1<<293))|(int(address<11)<<293)
        take=int(not reset and p==3 and ready and enable and (fo>>293)&1)
        result=active['out'][row] if valid else 0
        dvalue=(result&1048575)+(row<<20)+(group<<27)+(ready<<31)+(valid<<32)+(dbusy<<33)
        dmask=(1<<34)-1 if valid else ((1<<34)-1)^1048575
        hword=0
        if available:
            assert all(v is not None for v in memory)
            hword=sum((v&1048575)<<(20*j) for j,v in enumerate(memory[:32]))
        control=p+(pending<<2)+(complete<<3)
        want=fo+(dvalue<<356)+(hword<<390)+(control<<1030)+(int(p!=0)<<1034)+(available<<1035)
        mask=fmask+(dmask<<356)+(((1<<640)-1)<<390 if available else 0)+(63<<1030)
        rows.append((inp+(read_advance<<649),want,0 if fmask==0 else mask))
        if reset:
            counts['resets']+=1;counts['aborts']+=int(p!=0)
            state.update(phase=0,pending=0,complete=0);down.update(phase=0,row=0,group=0,remaining=0);active=None;return
        if fill_ack:
            word=(inp>>3)&((1<<640)-1);vals=[word>>(20*j)&1048575 for j in range(32)]
            incoming[:]=incoming[32:]+[v-(1<<20) if v&(1<<19) else v for v in vals]
        if begin:
            active=cases[tuple(incoming)];state['phase']=1;start_clock=len(rows)-1;counts['starts']+=1
        if p==1 and (fo>>291)&1:state['phase']=2
        if p==2:
            assert active['fm']==(fo>>265)&1048575;state['phase']=3
        if finish:
            state['phase']=0;state['complete']=1;counts['complete']+=1
            if first_latency is None:first_latency=len(rows)-start_clock
        elif begin or fill_ack:state['complete']=0
        state['pending']=int(ack and row%32==31)
        if dp==0 and dstart:down.update(phase=1,row=0,group=0);memory[:]=[None]*128
        elif dp==1:
            if take:
                assert fo&((1<<256)-1)==sum((v&255)<<(8*j) for j,v in enumerate(active['ff'][32*group:32*group+32]))
                counts['down_groups']+=1
                if group==10:down['phase']=2
                else:down['group']+=1
            elif p==3:counts['input_stalls']+=1
        elif dp==2:down.update(phase=3,remaining=down_reference.BOUNDED_LATENCY-1)
        elif dp==3:
            if down['remaining']:down['remaining']-=1
            else:down['phase']=4
        elif dp==4:
            if ack:
                memory[row%32]=active['out'][row];counts['down_rows']+=1
                down.update(phase=0 if row==127 else 1,row=(row+1)&127,group=0)
            else:counts['output_stalls']+=1
        if pending or available and read_advance:
            assert not ack and not fill_ack
            memory[:]=memory[32:]+memory[:32]
            counts['h_write_rotations']+=int(bool(pending));counts['h_words_read']+=int(bool(available and read_advance))
        if finish:assert memory==active['out']
        if inserted:counts['ignored_down_starts']+=int(start and p!=0)

    for inp,fo,mask in original:
        tick(inp,fo,mask)
        if state['phase']==2:
            # Hold the completed upstream bank while down consumes it. All
            # original upstream clocks resume afterwards, unchanged.
            while state['phase']!=0:
                stalls=counts['complete']!=0
                extra=(int(stalls and rng.randrange(37)==0)<<1)+(int(stalls and rng.randrange(29)==0)<<2)
                extra+=(int(not stalls or rng.randrange(7)!=0)<<643)+(int(not stalls or rng.randrange(5)!=0)<<644)
                tick(extra,fo,mask,inserted=True)
            for _ in range(4):tick(0,fo,mask,inserted=True,read_advance=1)
            assert memory==active['out']
    assert counts['complete']==4 and counts['down_rows']==512 and counts['down_groups']==5632
    assert counts['h_words_read']==16 and counts['h_write_rotations']==16
    return rows,dict(clocks=len(rows),counts=counts,upstream=expected,no_stall_start_to_published_clocks=first_latency,
        extra_result_vector_bits=0,scope='all upstream clocks retained; every down row and every H result word checked against frozen C')


def check(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    checks.OUT=OUT;checks.NI,checks.NO=NI,NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    # Change an actual H readout gate, not an already-covered FF-code bit.
    gates=net.records.copy();target=len(gates)-net.n_out+390;inverse=gates[target][1]
    op,a,b=gates[inverse-net.n_in-2];assert op==0 and a==b;gates[target]=(0,a,a)
    bad=Netlist(net.n_in,net.n_out,gates);prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask>>390&1)+1]
    wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
    (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,'ff_writeback'))
    (OUT/'tb.v').write_text(checks.testbench(NI,NO,'ff_writeback',str(OUT/'vectors.txt')))
    normal=checks.compile_rtl('source',OUT/'stream.v');checks.run([normal],300)
    mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    return dict(status='actual NAND/RTL/C pass',clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
                negative_prefix_clocks=len(prefix),actual_H_readout_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);ff_reference.OUT=OUT;down_reference.OUT=OUT/'down_c';down_reference.OUT.mkdir(exist_ok=True)
    assert sha((R/'integer/int_model.c').read_bytes())==ff_reference.GOLDEN_SHA
    small=small_check();net,parts=make();c=ff_reference.reference();d=down_reference.reference();rows,expected=vectors(c,d)
    (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_writeback'))
    digest=hashlib.sha256()
    with (OUT/'vectors.txt').open('wb') as f:
        for x,y,mask in rows:
            line=f'{x:x} {y:x} {mask:x}\n'.encode();f.write(line);digest.update(line)
    names=['ff_writeback','ff_input_shared','ff_input','ff_shared','ff_stream','ff_row','ff_store','ff_bank','down_engine',
           'linear_engine','quant_stream','silu_pipeline','scale_pipeline','prefix_codec','prefix_store','prefix_writer','prefix_packed',
           'pilot_bank','weight_cursor','pilot_v2']
    paths=[R/'integer_opt'/f'{name}.py' for name in names]
    paths += [R/p for p in ['integer_opt/weights_golden.c','integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py',
      'physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
      'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small control and frozen C reference pass; full graph constructed only',metrics=metrics(net),parts=parts,
        small_control=small,expected=expected,numerical_contract_changed=False,vector_sha256=digest.hexdigest(),
        contract='din original H/FF649,read_advance; dout upstream356,down34,H_head640,parent_state4,busy,available',
        scope='post-norm H through true gate/up/SwiGLU/down into original H slot; norm/residual/full transformer excluded; down arithmetic not yet shared',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in paths})
    if args.cloud:report['verification']=check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='upstream'},indent=2))


if __name__=='__main__':main()
