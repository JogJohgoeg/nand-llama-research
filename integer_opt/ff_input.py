#!/usr/bin/env python3
"""Original H s20 work slot -> in-place A8 -> actual two-pass FF bank.

Four complete input words refill H after reset/each operation. Last-lane
quantized writes and rotation use separate clocks, so no just-written value
is discarded. H storage is the original work slot, not an added code vector.
"""
import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'));sys.path.insert(0,str(ROOT))
from export import import_net,rtl
from nand import Builder,metrics,with_state,verify_state
from prefix_store import stage,select
from quant_stream import make as quantizer
from ff_shared import make as shared_ff
import ff_stream as base

NI,NO=649,356
OUT=ROOT/'build/integer_opt/ff_input'
sha=lambda data:hashlib.sha256(data).hexdigest()


def controller(b,old,ins):
    phase=old[:2];fills=old[2:5];pending=old[5]
    reset,start,fill,enable,qr,qv,replay,last_lane,last_h,fr,fdone=ins
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    p=[eq(phase,i) for i in range(4)];keep=b.inv(reset)
    fill_ready=b.reduce([keep,p[0],b.inv(start),b.inv(fills[2])],b.land,1)
    fill_ack=b.land(fill_ready,fill)
    qstart=b.reduce([keep,p[0],start,eq(fills,4)],b.land,1)
    fstart=b.reduce([keep,p[2],b.inv(pending)],b.land,1)
    qx=b.reduce([keep,p[1],enable,b.inv(pending)],b.land,1)
    qy=b.reduce([keep,p[1],enable],b.land,1);update=b.land(qv,qy)
    scan_last=b.reduce([qr,qx,b.inv(replay),last_lane],b.land,1)
    ff_take=b.reduce([keep,p[3],fr,enable],b.land,1)
    advance=b.land(keep,b.lor(pending,b.lor(scan_last,ff_take)))
    next_phase=phase[:]
    for action,target in ((qstart,1),(b.land(update,last_h),2),(fstart,3),(b.land(p[3],fdone),0)):
        next_phase=[b.mux(action,v,target>>j&1) for j,v in enumerate(next_phase)]
    next_fills=b.add(fills,[0]*3,fill_ack)[0]
    nxt=[b.land(keep,v) for v in next_phase]
    nxt += [b.land(b.inv(b.lor(reset,qstart)),v) for v in next_fills]
    nxt += [b.land(keep,b.land(update,last_lane))]
    read_allow=b.reduce([keep,p[0],b.inv(start)],b.land,1)
    return nxt,[fill_ready,fill_ack,qstart,fstart,qx,qy,advance,update,read_allow,b.inv(p[0])]


def small_check():
    b=Builder(17);nxt,out=controller(b,list(range(2,8)),list(range(8,19)))
    comb=b.finish(nxt+out);xs=list(range(1<<17));ys=[]
    for x in xs:
        p=x&3;f=(x>>2)&7;pending=(x>>5)&1
        reset,start,fill,enable,qr,qv,replay,last,last_h,fr,done=[x>>(6+j)&1 for j in range(11)]
        ready=int(not reset and p==0 and not start and f<4);ack=ready*fill
        qs=int(not reset and p==0 and start and f==4);fs=int(not reset and p==2 and not pending)
        qx=int(not reset and p==1 and enable and not pending);qy=int(not reset and p==1 and enable)
        update=qv*qy;advance=int(not reset and (pending or qr and qx and not replay and last or p==3 and fr and enable))
        pp=p
        if qs:pp=1
        if update and last_h:pp=2
        if fs:pp=3
        if p==3 and done:pp=0
        ff=0 if reset or qs else (f+ack)&7
        next_value=(0 if reset else pp)+(ff<<2)+(int(not reset and update and last)<<5)
        obs=[ready,ack,qs,fs,qx,qy,advance,update,int(not reset and p==0 and not start),int(p!=0)]
        ys.append(next_value+(sum(v<<j for j,v in enumerate(obs))<<6))
    result=verify_state(comb,xs,ys,6);result.pop('nl_hex');return result


def make():
    ff=shared_ff()[0];assert sha(ff.encode())=='47ef78907daa1dde58ea433e87c389c778e2354afe68e9fb9f2ed88a416357a6'
    quant=quantizer();_,work=stage();ns=ff.n_state+quant.n_state+work.n_state+6
    b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    fs=old[:ff.n_state];qs=old[ff.n_state:ff.n_state+quant.n_state]
    hs=old[ff.n_state+quant.n_state:-6];cs=old[-6:]
    reset,start,fill=ins[:3];incoming=ins[3:643];enable,write_enable=ins[643:645];address=ins[645:]
    _,fp=import_net(b,ff,[reset]+[0]*283,fs);_,qp=import_net(b,quant,[reset]+[0]*32,qs)
    index=qp[8:17];last_lane=b.reduce(index[:5],b.land,1)
    last_h=b.reduce(index[:7]+[b.inv(v) for v in index[7:]],b.land,1)
    cd,co=controller(b,cs,[reset,start,fill,enable,qp[37],qp[38],qp[40],last_lane,last_h,fp[288],fp[291]])
    fill_ready,fill_ack,qstart,fstart,qx,qy,advance,update,read_allow,busy=co
    head=hs[:640];value=select(b,[head[j*20:(j+1)*20] for j in range(32)],index[:5])
    qd,qo=import_net(b,quant,[reset,qstart]+[128>>j&1 for j in range(9)]+value+[qx,qy],qs)
    hd,_=import_net(b,work,incoming+qo[:8]+[qo[7]]*12+index[:5]+[fill_ack,advance,update],hs)
    code_word=[head[j*20+k] for j in range(32) for k in range(8)]
    ff_xvalid=b.reduce([b.inv(reset),cs[0],cs[1],enable],b.land,1)
    fd,fo=import_net(b,ff,[reset,fstart]+qo[17:37]+code_word+[ff_xvalid,write_enable]+address,fs)
    fo[291]=b.land(fo[291],read_allow);fo[293]=b.land(fo[293],read_allow)
    output=fo+qo[17:37]+index+cs+[fill_ready,fill_ack,qo[37],qo[38],qo[40],busy]
    net=with_state(b.finish(fd+qd+hd+cd+output),ns);assert net.n_in==NI and net.n_out==NO
    return net,dict(ff=metrics(ff),h_quant=metrics(quant),h_slot=metrics(work),outer_state_bits=6,
        h_raw_or_code_bits=2560,ff_code_bits=2688,extra_h_code_vector_bits=0,
        separate_mul_instances=1,separate_div_instances=2,
        scope='original H slot is included; H and FF quantization still have separate DIV states, ready for later sharing')


def vectors(c):
    # Reuse every independent frozen-C FF transaction from R30, inserting
    # H-slot refill/quantization before each accepted operation. The FF words
    # from that fixture are assertions on the work slot, never DUT inputs.
    original,expected=base.vectors(c);rng=random.Random(260675)
    values=[[0]*128,[-524288]*128,[rng.randrange(-4096,4097) for _ in range(128)],[524287]*128]
    cases={}
    for raw in values:
        code=(ct.c_int8*128)();ff=(ct.c_int32*336)();fq=(ct.c_int8*336)();m=ct.c_int32()
        c.ff_reference((ct.c_int32*128)(*raw),code,ff,fq,ct.byref(m))
        cases[m.value]=dict(raw=raw,code=list(code),m=m.value)
    rng=random.Random(260678);rows=[];slot=[None]*128
    h=dict(phase=0,index=0,maximum=0,remaining=0,result=0)
    state=dict(phase=0,fills=0,pending=0);active=None
    counts=dict(fills=0,h_scans=0,h_replays=0,h_writes=0,h_rotations=0,ff_rotations=0,
                h_stalls=0,h_aborts=0,ff_launches=0,ff_completed=0,early_starts=0,ignored_fills=0)
    start_clock=None;first_latency=None
    # Low FF word is checked by the unchanged complete-vector C fixture.
    idle_mask=((1<<315)-1)^((1<<256)-1)^(((1<<20)-1)<<295)
    def tick(fw,fmask,reset=0,start=0,fill=0,word=0,enable=0,we=0,address=0,base_word=None,case=None):
        nonlocal active,start_clock,first_latency
        p,nf,pending=state['phase'],state['fills'],state['pending'];qp=h['phase'];idx=h['index']
        qr=int(not reset and qp in (1,2));qv=int(not reset and qp==4);replay=int(qp in (2,3,4))
        fill_ready=int(not reset and p==0 and not start and nf<4);fill_ack=fill_ready*fill
        qs=int(not reset and p==0 and start and nf==4);fs=int(not reset and p==2 and not pending)
        take=int(not reset and p==1 and enable and not pending and qr)
        update=int(not reset and p==1 and enable and qv)
        scan_rotate=int(take and qp==1 and idx%32==31)
        ff_take=int(not reset and p==3 and enable and (fw>>288)&1)
        read_allow=int(not reset and p==0 and not start)
        available=(fw>>291)&1
        # During inserted idle cycles the original fixture's start was not
        # applied: recompute read permission, masking the unobserved old word.
        fw&=~((1<<291)|(1<<293));fw|=(available*read_allow)<<291
        fw|=(available*read_allow*int(address<11))<<293
        cs=p+(nf<<2)+(pending<<5)
        want=fw+(h['maximum']<<315)+(idx<<335)+(cs<<344)
        want+=(fill_ready<<350)+(fill_ack<<351)+(qr<<352)+(qv<<353)+(replay<<354)+(int(p!=0)<<355)
        mask=fmask|(((1<<41)-1)<<315)
        inp=reset+(start<<1)+(fill<<2)+(word<<3)+(enable<<643)+(we<<644)+(address<<645)
        rows.append((inp,want,0 if fmask==0 else mask))
        if reset:
            counts['h_aborts']+=int(p in (1,2));state.update(phase=0,fills=0,pending=0)
            h.update(phase=0,index=0,maximum=0,remaining=0,result=0);active=None;return
        counts['early_starts']+=int(p==0 and start and nf<4)
        counts['ignored_fills']+=int(fill and not fill_ack)
        if fill_ack:
            incoming=[(word>>(20*j))&1048575 for j in range(32)]
            incoming=[v-(1<<20) if v&(1<<19) else v for v in incoming]
            slot[:]=slot[32:]+incoming;state['fills']+=1;counts['fills']+=1
        if qs:
            assert case is not None and slot==case['raw'];active=case
            h.update(phase=1,index=0,maximum=1);state.update(phase=1,fills=0);start_clock=len(rows)-1
        elif qp in (1,2) and take:
            assert slot[idx%32]==active['raw'][idx],(idx,slot[idx%32],active['raw'][idx])
            if qp==1:
                counts['h_scans']+=1;h['maximum']=max(h['maximum'],abs(slot[idx%32]))
                if idx==127:
                    assert h['maximum']==active['m'];h.update(phase=2,index=0)
                else:h['index']+=1
            else:counts['h_replays']+=1;h.update(phase=3,remaining=28)
        elif qp==3:
            h['remaining']-=1
            if h['remaining']==0:h.update(phase=4,result=active['code'][idx])
        elif qp==4 and update:
            assert slot[idx%32]==active['raw'][idx]
            slot[idx%32]=h['result'];counts['h_writes']+=1
            if idx==127:h['phase']=0;state['phase']=2
            else:h.update(phase=2,index=idx+1)
        if p==1 and (qr or qv) and not enable:counts['h_stalls']+=1
        state['pending']=int(update and idx%32==31)
        if pending or scan_rotate:
            assert not update;slot[:]=slot[32:]+slot[:32];counts['h_rotations']+=1
        if fs:
            assert h['maximum']==active['m'] and slot==active['code']
            state['phase']=3;counts['ff_launches']+=1
        if ff_take:
            actual=sum((v&255)<<(8*j) for j,v in enumerate(slot[:32]))
            assert actual==base_word,(counts['ff_rotations'],actual,base_word)
            slot[:]=slot[32:]+slot[:32];counts['ff_rotations']+=1
        if p==3 and available:
            assert slot==active['code'];state['phase']=0;counts['ff_completed']+=1
            if first_latency is None:first_latency=len(rows)-start_clock

    def refill(fw,case):
        for j in range(4):
            tick(fw,idle_mask,start=1)  # incomplete refill must not launch
            tick(fw,idle_mask,fill=1,word=sum((v&1048575)<<(20*k) for k,v in enumerate(case['raw'][32*j:32*j+32])))
        tick(fw,idle_mask,fill=1,word=(1<<640)-1)  # full slot ignores a fifth fill
        tick(fw,idle_mask,start=1,case=case)

    did_aborts=False
    for original_index,(inp,fw,mask) in enumerate(original):
        reset=inp&1;start=(inp>>1)&1;maximum=(inp>>2)&1048575
        valid_start=bool(not reset and start and maximum and not ((fw>>290)&1))
        if valid_start:
            assert state['phase']==0
            if not did_aborts:
                # Reset during scan, receive, division, blocked output, and
                # deferred word rotation; complete refills follow every abort.
                for target in ('scan','receive','run','send','pending'):
                    case=cases[4094];refill(fw,case)
                    def reached():
                        return dict(scan=h['phase']==1 and h['index']==3,receive=h['phase']==2,
                                    run=h['phase']==3 and h['remaining']==1,send=h['phase']==4,
                                    pending=bool(state['pending']))[target]
                    while not reached():tick(fw,idle_mask,enable=1)
                    tick(fw,idle_mask,reset=1,start=1,fill=1,enable=1)
                    tick(0,idle_mask)
                did_aborts=True
            case=cases[maximum];refill(fw,case)
            while state['phase']==1 or state['pending']:
                tick(fw,idle_mask,start=int(rng.randrange(17)==0),fill=int(rng.randrange(23)==0),
                     enable=int(counts['ff_launches']==0 or rng.randrange(7)!=0))
            assert state['phase']==2 and not state['pending']
            start=0  # the outer FSM now launches FF internally
        if state['phase']==3 and (fw>>291)&1:
            tick(fw,idle_mask)  # publish completed FF bank on the following clock
        tick(fw,mask,reset=reset,start=start,enable=(inp>>278)&1,we=(inp>>279)&1,
             address=(inp>>280)&15,base_word=(inp>>22)&((1<<256)-1))
    assert counts['ff_completed']==4 and counts['h_aborts']==5 and counts['ff_launches']==11
    assert counts['ff_rotations']==expected['counts']['h_groups']
    return rows,dict(clocks=len(rows),counts=counts,ff_fixture=expected,
        no_stall_start_to_published_clocks=first_latency,
        complete_ff_fixture_clocks=len(original),added_h_and_publish_clocks=len(rows)-len(original),
        scope='all original FF fixture clocks retained; H codes are read from the original work slot and checked at every FF acceptance')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
    assert sha((ROOT/'integer/int_model.c').read_bytes())==base.GOLDEN_SHA
    small=small_check();net,parts=make();c=base.reference();rows,expected=vectors(c)
    (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_stream'))
    with (OUT/'vectors.txt').open('w') as f:
        for x,y,mask in rows:f.write(f'{x:x} {y:x} {mask:x}\n')
    sources=['ff_input','ff_shared','ff_stream','ff_row','ff_store','ff_bank','quant_stream','silu_pipeline',
             'scale_pipeline','prefix_codec','prefix_store','prefix_writer','prefix_packed','pilot_bank','weight_cursor','pilot_v2']
    paths=[ROOT/'integer_opt'/f'{name}.py' for name in sources]
    paths += [ROOT/p for p in ['integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py',
      'physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
      'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small controller and C fixture pass; full graph constructed only',metrics=metrics(net),parts=parts,
        small_controller=small,expected=expected,numerical_contract_changed=False,
        contract='din reset,start,fill,raw_H_word640,access_enable,FF_write_enable,FF_word_addr4; dout original FF315,Hmax20,Hindex9,outer_state6,fill_ready,fill_ack,Hready,Hvalid,Hreplay,busy',
        scope='layer0 raw H through in-place A8, gate/up/SwiGLU, two-pass FF A8 bank; down/residual remain outside; one MUL, two DIV',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths})
    if args.cloud:
        base.NI,base.NO=NI,NO;report['verification']=base.check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='ff_fixture'},indent=2))


if __name__=='__main__':main()
