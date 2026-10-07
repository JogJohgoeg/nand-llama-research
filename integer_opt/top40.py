#!/usr/bin/env python3
"""Stable top40 of192 signed32 logits using a40-entry circulating list.

Tokens arrive in vocabulary order. Once insertion starts, old entries shift
in order; equal logits never pass an earlier token. No EXP or sampler is here.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,struct,subprocess,sys,time
R=Path(os.environ.get('H3_TOP40_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_TOP40_OUT',str(R/'build/integer_opt/top40')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output,from_yosys
from golden import Netlist
from gate_check import Snapshot
from export import rtl,MODEL_SHA
from ci import cec
NI,NO,NS=36,44,1664
sha=lambda b:hashlib.sha256(b).hexdigest()


def make(bad_tie=False):
    b=Builder(NS+NI);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    head=q[:40];cursor=q[1600:1606];index=q[1606:1614];filled=q[1614:1620];carry=q[1620:1660];inserted=q[1660];phase=q[1661:1664]
    reset,start,iv=p[:3];keep=b.inv(reset)
    def AND(*xs):return b.reduce(xs,b.land,1)
    def eq(bits,value):return AND(*[v if value>>i&1 else b.inv(v) for i,v in enumerate(bits)])
    def same(a,z):return AND(*[b.inv(b.xor(x,y)) for x,y in zip(a,z)])
    def gt(a,z):return b.add(a,[b.inv(x) for x in z],0)[1]
    states=[eq(phase,i) for i in range(7)];idle,collect,seek,work,read,present,done=states
    begin=AND(keep,start,b.lor(idle,done));ready=AND(keep,collect);take=AND(ready,iv)
    active=AND(keep,b.lor(work,AND(seek,eq(cursor,0))));finish=AND(active,eq(cursor,39))
    key=carry[:31]+[b.inv(carry[31])];other=head[:31]+[b.inv(head[31])]
    higher=b.add(key,[b.inv(x) for x in other],int(bad_tie))[1]
    swap=AND(active,b.lor(inserted,b.lor(b.inv(gt(filled,cursor)),higher)))
    valid=AND(keep,present);ack=AND(valid,p[35]);last_in=eq(index,191);last_out=eq(index,39)
    inc=b.add(index,[0]*8,1)[0]
    prefetch=AND(ack,b.inv(last_out),same(cursor,inc[:6]));fetch=AND(keep,read,same(cursor,index[:6]))
    head_load=b.lor(swap,b.lor(fetch,prefetch))
    nc=[b.mux(take,b.mux(head_load,x,y),z) for x,y,z in zip(carry,head,p[3:35]+index)]
    bank=q[40:1600]+[b.mux(swap,x,y) for x,y in zip(head,carry)]
    advance=b.lor(AND(finish,b.inv(last_in)),AND(ack,b.inv(last_out)))
    clear=b.lor(reset,b.lor(begin,AND(finish,last_in)))
    nr=[AND(b.inv(clear),b.mux(advance,x,y)) for x,y in zip(index,inc)]
    ncursor=b.add(cursor,[0]*6,1)[0];cursor_clear=b.lor(reset,b.lor(begin,eq(cursor,39)))
    ncursor=[AND(b.inv(cursor_clear),x) for x in ncursor]
    add_one=AND(finish,b.inv(gt(filled,[39>>i&1 for i in range(6)])))
    nf=b.add(filled,[0]*6,add_one)[0];nf=[AND(b.inv(b.lor(reset,begin)),x) for x in nf]
    ninsert=AND(b.inv(b.lor(reset,b.lor(begin,take))),b.lor(inserted,swap))
    np=phase[:]
    def set_phase(enable,value):
        nonlocal np
        np=[b.mux(enable,x,value>>i&1) for i,x in enumerate(np)]
    set_phase(begin,1);set_phase(take,2);set_phase(AND(keep,seek,eq(cursor,0)),3)
    set_phase(AND(finish,b.inv(last_in)),1);set_phase(AND(finish,last_in),4)
    set_phase(fetch,5);set_phase(AND(ack,b.inv(last_out),b.inv(prefetch)),4)
    set_phase(AND(ack,last_out),6)
    np=[AND(keep,x) for x in np]
    nxt=bank+ncursor+nr+nf+nc+[ninsert]+np;assert len(nxt)==NS
    comb=b.finish(nxt+carry+[valid,b.reduce(states[1:6],b.lor,0),ready,done])
    return with_state(comb,NS),comb


def reference():
    return '''module top(input [1699:0] din,output [1707:0] dout);
wire [1663:0] q=din[1663:0];wire [35:0] p=din[1699:1664];
wire [1599:0] bank=q[1599:0];wire [39:0] head=bank[39:0],carry=q[1659:1620];
wire [5:0] cursor=q[1605:1600],filled=q[1619:1614];wire [7:0] index=q[1613:1606];
wire inserted=q[1660];wire [2:0] phase=q[1663:1661];
wire reset=p[0],begin_op=!reset && p[1] && (phase==0 || phase==6);
wire ready=!reset && phase==1,take=ready && p[2];
wire active=!reset && (phase==3 || (phase==2 && cursor==0)),finish=active && cursor==39;
wire higher=$signed(carry[31:0])>$signed(head[31:0]);
wire swap=active && (inserted || cursor>=filled || higher);
wire valid=!reset && phase==5,ack=valid && p[35];
wire [7:0] inc=index+8'd1;
wire prefetch=ack && index!=39 && cursor==inc[5:0];
wire fetch=!reset && phase==4 && cursor==index[5:0];
wire [39:0] nc=take?{index,p[34:3]}:(swap || fetch || prefetch)?head:carry;
wire [1599:0] nb={swap?carry:head,bank[1599:40]};
wire advance=(finish && index!=191) || (ack && index!=39);
wire [7:0] ni=(reset || begin_op || (finish && index==191))?8'd0:advance?inc:index;
wire [5:0] ncursor=(reset || begin_op || cursor==39)?6'd0:cursor+6'd1;
wire [5:0] nf=(reset || begin_op)?6'd0:filled+{5'd0,(finish && filled<40)};
wire ninsert=!(reset || begin_op || take) && (inserted || swap);
reg [2:0] np;
always @* begin
 np=phase;
 if(begin_op) np=1;
 if(take) np=2;
 if(!reset && phase==2 && cursor==0) np=3;
 if(finish) np=index==191?4:1;
 if(fetch) np=5;
 if(ack && index!=39 && !prefetch) np=4;
 if(ack && index==39) np=6;
 if(reset) np=0;
end
wire busy=phase>=1 && phase<=5;
assign dout={{phase==6,ready,busy,valid,carry},np,ninsert,nc,nf,ni,ncursor,nb};
endmodule
'''


def transition(state,inputs):
    mask=lambda n:(1<<n)-1
    bank=state&mask(1600);head=bank&mask(40);cursor=state>>1600&63;index=state>>1606&255;filled=state>>1614&63
    carry=state>>1620&mask(40);inserted=state>>1660&1;phase=state>>1661&7
    reset=inputs&1;start=inputs>>1&1;iv=inputs>>2&1;score=inputs>>3&mask(32);out_ready=inputs>>35&1
    begin=int(not reset and start and phase in (0,6));ready=int(not reset and phase==1);take=ready&iv
    active=int(not reset and (phase==3 or phase==2 and cursor==0));finish=int(active and cursor==39)
    signed=lambda v:(v&mask(31))-(v&(1<<31))
    swap=int(active and (inserted or cursor>=filled or signed(carry)>signed(head)))
    valid=int(not reset and phase==5);ack=valid&out_ready;inc=(index+1)&255
    prefetch=int(ack and index!=39 and cursor==(inc&63));fetch=int(not reset and phase==4 and cursor==(index&63))
    nc=score+(index<<32) if take else head if swap or fetch or prefetch else carry
    nb=(bank>>40)+((carry if swap else head)<<1560)
    ni=0 if reset or begin or finish and index==191 else inc if finish and index!=191 or ack and index!=39 else index
    ncursor=0 if reset or begin or cursor==39 else (cursor+1)&63
    nf=0 if reset or begin else (filled+int(finish and filled<40))&63
    ninsert=int(not(reset or begin or take) and (inserted or swap))
    np=phase
    if begin:np=1
    if take:np=2
    if not reset and phase==2 and cursor==0:np=3
    if finish:np=4 if index==191 else 1
    if fetch:np=5
    if ack and index!=39 and not prefetch:np=4
    if ack and index==39:np=6
    if reset:np=0
    nxt=nb+(ncursor<<1600)+(ni<<1606)+(nf<<1614)+(nc<<1620)+(ninsert<<1660)+(np<<1661)
    out=carry+(valid<<40)+(int(1<=phase<=5)<<41)+(ready<<42)+(int(phase==6)<<43)
    return nxt,out


def cases():
    fixture=Path(__file__).with_name('top40_cases.json');frozen=json.loads(fixture.read_text())
    assert frozen['origin_sha256']=='79506dc56c6af92029dc8b063e77a5bb4d48decaed7ae6f08ef4ac308eb45a72'
    for name,digest in frozen['sources'].items():assert sha((R/name).read_bytes())==digest,name
    rng=random.Random(260648)
    vectors=[('equal',[0]*192),('ascending',list(range(192))),('descending',list(range(191,-1,-1))),
             ('extreme',[(-2147483648,2147483647,0)[i%3] for i in range(192)]),('exp_boundary',[-i*64 for i in range(192)])]
    for i in range(128):
        limit=(4,1000,100000,2147483647)[i%4];vectors.append((f'random{i}',[rng.randint(-limit,limit) for _ in range(192)]))
    result=[]
    for (name,logits),known in zip(vectors,frozen['rows']):
        assert name==known['name'] and sha(struct.pack('<192i',*logits))==known['logits_sha256']
        order=sorted(range(192),key=lambda i:(-logits[i],i))[:40];assert order==known['top40']
        result.append(dict(name=name,logits=logits,order=order))
    assert len(vectors)==len(frozen['rows'])==len(result)==133
    return result,frozen


def vectors(cases):
    rng=random.Random(26100740);state=0;rows=[];spans=[];outputs=[];aborts=ignored=stalls=accepted=0
    def tick(reset=0,start=0,iv=0,score=0,ready=1,mask=None):
        nonlocal state,aborts,ignored,stalls,accepted
        x=reset+(start<<1)+(iv<<2)+((score&0xffffffff)<<3)+(ready<<35)
        old=state;state,y=transition(state,x);phase=old>>1661&7
        valid=y>>40&1;input_ready=y>>42&1
        if mask is None:mask=((1<<NO)-1) if valid else (((1<<NO)-1)^((1<<40)-1))
        rows.append((x,y,mask))
        if reset:aborts+=int(1<=phase<=5)
        elif start and 1<=phase<=5:ignored+=1
        if valid and ready:outputs.append((y>>32&255,(y&0x7fffffff)-(y&0x80000000)))
        stalls+=int(valid and not ready);accepted+=int(input_ready and iv)
    tick(reset=1,mask=0);tick()
    def one(case,stall=False,abort_at=None):
        before=len(rows);oi=len(outputs);next_input=0
        tick(start=1)
        while state>>1661&7!=6:
            if abort_at is not None and len(rows)-before==abort_at:
                tick(reset=1,start=1,iv=1,score=-2147483648);tick();return
            phase=state>>1661&7;send=int(phase==1 and next_input<192 and (not stall or rng.randrange(7)!=0))
            score=case['logits'][next_input] if send else rng.randint(-2147483648,2147483647)
            ready=int(not stall or rng.randrange(3)!=0)
            tick(start=int(rng.randrange(7)==0),iv=send,score=score,ready=ready)
            next_input+=send
            assert len(rows)-before<20000
        expected=[(i,case['logits'][i]) for i in case['order']];assert outputs[oi:]==expected,case['name']
        assert next_input==192
        spans.append(dict(name=case['name'],start=before,end=len(rows),clocks=len(rows)-before,stalls=stall))
        tick();tick(iv=1,score=1)
    for i,c in enumerate(cases):one(c,stall=i%3==2)
    for elapsed in (1,2,39,40,41,80,81,128,400,15360,15361):one(cases[3],abort_at=elapsed)
    one(cases[3])
    no_stall={r['clocks'] for r in spans if not r['stalls']};assert len(no_stall)==1
    return rows,dict(clocks=len(rows),complete_lists=len(spans),complete_C_top40_entries=40*len(spans),
        accepted_scores=accepted,aborts=aborts,ignored_busy_starts=ignored,output_stalls=stalls,
        checked_valid_results=sum(bool(y>>40&1) for _,y,_ in rows),no_stall_clocks=no_stall.pop(),spans=spans)


def small(net,rows,protocol):
    assert len(net.records)<=4000
    g=Snapshot.decode(net.encode(),NI,NO);rng=random.Random(26100741);states=[rng.getrandbits(NS) for _ in range(64)];inputs=[rng.getrandbits(NI) for _ in states]
    got=g.step_simd([bytes(s>>i&1 for i in range(NS)) for s in states],[bytes(x>>i&1 for i in range(NI)) for x in inputs])
    packed=[(sum(v<<i for i,v in enumerate(s)),sum(v<<i for i,v in enumerate(y))) for s,y in got]
    assert packed==[transition(s,x) for s,x in zip(states,inputs)]
    # Two full actual-size lists: equal scores exercise stable ties; ascending
    # scores exercise replacement of every existing entry. No C is rerun.
    end=protocol['spans'][1]['end'];state=bytes(NS);valid=0;faults=0;bad=Snapshot.decode(flip_output(net).encode(),NI,NO)
    for x,want,mask in rows[:end]:
        inp=bytes(x>>i&1 for i in range(NI));old=state;state,y=g.step(state,inp);out=sum(v<<i for i,v in enumerate(y))
        assert not ((out^want)&mask)
        if want>>40&1:
            valid+=1
            if faults<2:
                _,wrong=bad.step(old,inp);assert sum(v<<i for i,v in enumerate(wrong))==out^1;faults+=1
    assert valid==80 and faults==2
    return dict(status='pass',actual_size_records=len(net.records),arbitrary_state_transitions=64,
        full_protocol_clocks=end,full_frozen_C_lists=2,full_frozen_C_entries=80,actual_output_gate_faults_rejected=faults)


def stalled_bank(net):
    records=net.records.copy();assert records[32][0]==1;records[32]=(1,2+NI+32)
    bad=Netlist(NI,NO,records);bad.validate();return bad


def cloud(net,comb,rows,cases):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    # Reuse the existing C refinement, independently accepted against int_pick.
    # This is a cloud replay; no unchanged C fixtures are rerun on the Mac.
    lib=OUT/'stream.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(R/'integer_opt/sample_stream.c'),'-o',str(lib)],check=True,timeout=30)
    g=ct.CDLL(str(lib));g.stream_logit.argtypes=[ct.c_uint,ct.c_int32];g.stream_order.argtypes=[ct.c_uint];g.stream_order.restype=ct.c_uint
    g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
    for case in cases:
        g.stream_begin()
        for i,value in enumerate(case['logits']):g.stream_logit(i,value)
        assert [g.stream_order(i) for i in range(40)]==case['order']
        assert g.int_pick((ct.c_int32*192)(*case['logits']),0,0)==case['order'][0]
    prefix=OUT/'body';ys=OUT/'body.ys'
    ys.write_text(f'read_verilog {prefix}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {prefix}.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(prefix)+'.yosys.log','-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    ref=from_yosys(json.loads((OUT/'body.ref.json').read_text()),NS+NI,NS+NO)
    tie,tie_comb=make(True)
    for name,n in [('source',comb),('reference',ref),('negative',flip_output(comb)),('bad_tie',tie_comb)]:(OUT/('body.'+name+'.blif')).write_text(blif(n))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    proof=cec(abc,OUT/'body.source.blif',OUT/'body.reference.blif',OUT/'body.cec.log');assert proof['verdict']=='equivalent'
    negative=cec(abc,OUT/'body.negative.blif',OUT/'body.reference.blif',OUT/'body.negative.log');assert negative['verdict']=='different'
    wrong_tie=cec(abc,OUT/'body.bad_tie.blif',OUT/'body.reference.blif',OUT/'body.bad_tie.log');assert wrong_tie['verdict']=='different'
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    changed=flip_output(net);wrong=checks.check_nand(rows,changed.encode());valid=sum(bool(y>>40&1) for _,y,_ in rows);assert wrong==valid
    stuck=stalled_bank(net);bank_wrong=checks.check_nand(rows,stuck.encode());assert bank_wrong>0
    tie_wrong=checks.check_nand(rows,tie.encode());assert tie_wrong>0
    for name,n in [('bad',changed),('bad_bank',stuck),('bad_tie',tie)]:(OUT/(name+'.nl')).write_bytes(n.encode())
    (OUT/'bad.v').write_text(rtl(changed,'top40'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'top40',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'top40.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',C_lists=len(cases),C_top40_entries=40*len(cases),all_state_bits=NS,all_output_bits=NO,
        arbitrary_transition=proof,actual_D_fault=negative,actual_tie_fault_proof=wrong_tie,
        clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,actual_valid_output_faults=wrong,
        actual_bank_latch_fault_mismatches=bank_wrong,actual_tie_rule_fault_mismatches=tie_wrong,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    start=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    assert sha((R/'physical/model.bin').read_bytes())==MODEL_SHA
    net,comb=make();c,frozen=cases();rows,protocol=vectors(c);checked=small(net,rows,protocol)
    for name,n in [('top40',net),('transition',comb)]:(OUT/(name+'.nl')).write_bytes(n.encode())
    assert with_state(Netlist.decode((OUT/'transition.nl').read_bytes(),NS+NI,NS+NO),NS).encode()==net.encode()
    (OUT/'top40.v').write_text(rtl(net,'top40'));(OUT/'body.ref.v').write_text(reference())
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    paths=[Path(__file__).resolve(),Path(__file__).with_name('top40_cases.json').resolve()]
    paths += [R/n for n in ('nand.py','golden.py','ci.py','bench.py','integer_opt/gate_check.py','integer_opt/sample_stream.c','integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py','physical/nl_sim.c')]
    def key(p):return str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name
    report=dict(status='actual-size small gate checks and frozen C protocol prepared; universal CEC and full vectors await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),small=checked,expected=protocol,
        fixture_origin_sha256=frozen['origin_sha256'],model_sha256=MODEL_SHA,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        binding=dict(state_order='entries40x(score32,id8),cursor6,index8,filled6,carry40,inserted1,phase3',
            list_bits=1600,control_payload_bits=64,ring='all40 entries rotate every clock; begin invalidates old list, no data reset'),
        contract='reset,start,input_valid,score32,out_ready -> score32,id8,valid,busy,input_ready,done;192 scores in vocabulary order, then40 stable descending entries',
        scope='top40 producer only; no EXP, RNG choice, norm, vocabulary weights or full-model controller',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={key(p):sha(p.read_bytes()) for p in paths})
    receipt=OUT/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
    if args.cloud:report['verification']=cloud(net,comb,rows,c);report['status']='all-state CEC and complete top40 NAND/RTL/C clocks pass; actual gate, state and tie faults rejected'
    report['seconds']=time.monotonic()-start;save()
    print(json.dumps({k:report[k] for k in ('status','metrics','small','seconds')},indent=2))


if __name__=='__main__':main()
