#!/usr/bin/env python3
"""Scan all192 tied-E8 rows using one row engine and an always-rotating q8 bank.

Large graph execution and all EDA are Actions-only. Local work builds the
graph, checks the <=4k shell/bank, and reuses the frozen C logit fixtures.
"""
from pathlib import Path
import argparse,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_VOCAB_SCAN_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_VOCAB_SCAN_OUT',str(R/'build/integer_opt/vocab_scan')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
import circulate
import vocab_row as row
from nand import Builder,metrics,with_state,blif,flip_output,from_yosys
from golden import Netlist
from export import import_net,rtl,MODEL_SHA
from gate_check import verify
from ci import cec
NI,NO,NS=32,44,1420
SHELL_IN=NS+NI+401
SHELL_OUT=NS+NO+39
sha=lambda b:hashlib.sha256(b).hexdigest()


def shell():
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2));y=list(range(NS+NI+2,SHELL_IN+2))
    bank=q[351:1382];cursor=q[1375:1382];index=q[1382:1390];loaded=q[1390:1397];maximum=q[1397:1417];phase=q[1417:1420]
    reset,start=p[:2];keep=b.inv(reset)
    def AND(*xs):return b.reduce(xs,b.land,1)
    def eq(bits,v):return AND(*[w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)])
    def same(a,z):return AND(*[b.inv(b.xor(x,v)) for x,v in zip(a,z)])
    idle,load,scan,seek,done=[eq(phase,i) for i in range(5)]
    begin=AND(keep,start,b.lor(idle,done));ready=AND(keep,load,same(cursor,loaded));write=AND(ready,p[22])
    last=AND(write,eq(loaded,127));launch=AND(keep,seek,eq(cursor,127));child_start=b.lor(last,launch)
    valid=AND(keep,scan,y[383]);ack=AND(valid,p[31]);lastrow=eq(index,191);advance=AND(ack,b.inv(lastrow))
    child_iv=AND(keep,scan,same(cursor,q[319:326]))
    bn,bo=import_net(b,circulate.make(128,8)[1],p[23:31]+[write,b.lor(reset,begin)],bank)
    assert bo==bank[:8]+cursor
    child_call=[reset,child_start]+index+maximum+[child_iv]+bo[:8]
    nr=b.add(index,[0]*8,advance)[0]
    nc=b.add(loaded,[0]*7,AND(write,b.inv(last)))[0]
    clear=b.lor(reset,begin)
    nr=[AND(b.inv(clear),x) for x in nr];nc=[AND(b.inv(clear),x) for x in nc]
    nm=[AND(keep,b.mux(begin,x,z)) for x,z in zip(maximum,p[2:22])]
    np=phase[:]
    for en,value in ((begin,1),(last,2),(advance,3),(AND(ack,lastrow),4),(launch,2)):
        np=[b.mux(en,x,value>>i&1) for i,x in enumerate(np)]
    np=[AND(keep,x) for x in np]
    nxt=y[:351]+bn+nr+nc+nm+np;assert len(nxt)==NS
    return b.finish(nxt+y[351:383]+[valid,b.reduce([load,scan,seek],b.lor,0),ready]+index+[done]+child_call)


def shell_expected(x):
    mask=lambda n:(1<<n)-1
    q=x&mask(NS);p=x>>NS&mask(NI);y=x>>(NS+NI)
    bank=q>>351&mask(1024);cursor=q>>1375&127;index=q>>1382&255;loaded=q>>1390&127;maximum=q>>1397&mask(20);phase=q>>1417&7
    reset=p&1;start=p>>1&1;iv=p>>22&1;data=p>>23&255;out_ready=p>>31&1
    begin=int(not reset and start and phase in (0,4));ready=int(not reset and phase==1 and cursor==loaded);write=ready&iv
    last=int(write and loaded==127);launch=int(not reset and phase==3 and cursor==127)
    valid=int(not reset and phase==2 and (y>>383&1));ack=valid&out_ready;advance=int(ack and index!=191)
    child_iv=int(not reset and phase==2 and cursor==(q>>319&127))
    tail=data if write and not (reset or begin) else bank&255
    nb=(bank>>8)+(tail<<1016);ncu=0 if reset or begin else (cursor+1)&127
    nr=0 if reset or begin else (index+advance)&255;nl=0 if reset or begin else (loaded+int(write and not last))&127
    nm=0 if reset else (p>>2&mask(20)) if begin else maximum
    np=0 if reset else 1 if begin else 2 if last or launch else 3 if advance else 4 if ack and index==191 else phase
    nxt=(y&mask(351))+(nb<<351)+(ncu<<1375)+(nr<<1382)+(nl<<1390)+(nm<<1397)+(np<<1417)
    out=(y>>351&mask(32))+(valid<<32)+(int(phase in (1,2,3))<<33)+(ready<<34)+(index<<35)+(int(phase==4)<<43)
    call=reset+(int(last or launch)<<1)+(index<<2)+(maximum<<10)+(child_iv<<30)+((bank&255)<<31)
    return nxt+(out<<NS)+(call<<(NS+NO))


def connect(shell_net,child):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2))
    _,probe=import_net(b,shell_net,pins+[0]*401);call=probe[NS+NO:]
    nd,outputs=import_net(b,child,call,pins[:351]);values=nd+outputs;assert len(values)==401
    _,out=import_net(b,shell_net,pins+values)
    assert out[NS+NO:]==call,'child input must not depend on its combinational outputs'
    comb=b.finish(out[:NS+NO]);return with_state(comb,NS),comb


def reference():
    return '''module top(input [1852:0] din,output [1502:0] dout);
wire [1419:0] q=din[1419:0];wire [31:0] p=din[1451:1420];wire [400:0] child=din[1852:1452];
wire [1023:0] bank=q[1374:351];wire [6:0] cursor=q[1381:1375],loaded=q[1396:1390];
wire [7:0] row=q[1389:1382];wire [19:0] maximum=q[1416:1397];wire [2:0] phase=q[1419:1417];
wire reset=p[0],start=p[1],begin_op=!reset && start && (phase==0 || phase==4);
wire ready=!reset && phase==1 && cursor==loaded,write=ready && p[22];
wire last=write && loaded==127,launch=!reset && phase==3 && cursor==127;
wire valid=!reset && phase==2 && child[383],ack=valid && p[31],advance=ack && row!=191;
wire child_iv=!reset && phase==2 && cursor==q[325:319];
wire [1023:0] nb={write && !(reset || begin_op)?p[30:23]:bank[7:0],bank[1023:8]};
wire [6:0] ncursor=(reset || begin_op)?7'd0:cursor+7'd1;
wire [7:0] nr=(reset || begin_op)?8'd0:row+{7'b0,advance};
wire [6:0] nl=(reset || begin_op)?7'd0:loaded+{6'b0,(write && !last)};
wire [19:0] nm=reset?20'd0:begin_op?p[21:2]:maximum;
reg [2:0] np;
always @* begin
 np=phase;
 if(begin_op) np=1;
 if(last) np=2;
 if(advance) np=3;
 if(ack && row==191) np=4;
 if(launch) np=2;
 if(reset) np=0;
end
wire [1419:0] nxt={np,nm,nl,nr,ncursor,nb,child[350:0]};
wire [43:0] result={phase==4,row,ready,(phase==1 || phase==2 || phase==3),valid,child[382:351]};
wire [38:0] call={bank[7:0],child_iv,maximum,row,(last || launch),reset};
assign dout={call,result,nxt};
endmodule
'''


def small(shell_net):
    rng=random.Random(261007);xs=[]
    # Every parent phase, reset/start/valid/ready combination, and both cursor
    # matches; the rest of the state and free child outputs stay arbitrary.
    for phase in range(8):
        for control in range(64):
            x=rng.getrandbits(SHELL_IN);x=(x&~(7<<1417))+(phase<<1417)
            p=(x>>NS)&0xffffffff;p=(p&~((1<<31)|(1<<22)|3))+(control&3)+((control>>2&1)<<22)+((control>>3&1)<<31)
            x=(x&~(0xffffffff<<NS))+(p<<NS)
            cursor=x>>1375&127
            if control&16:x=(x&~(127<<1390))+(cursor<<1390)
            if control&32:x=(x&~(127<<319))+(cursor<<319)
            xs.append(x)
    assert len(shell_net.records)<=4000
    shell_check=verify(shell_net,xs,[shell_expected(x) for x in xs]);assert shell_check['status']=='pass'
    comb,bank=circulate.make(128,8);assert max(len(g.records) for g in (comb,bank))<=4000
    bx,by=circulate.cases(128,8)
    bank_check=verify(comb,bx,by);state=circulate.state_check(comb,bank,128,8,bx,by)
    assert bank_check['status']=='pass'
    return dict(shell=shell_check,bank=bank_check,bank_state=state)


class Protocol:
    def __init__(self,weights,scales):
        self.weights=weights;self.scales=scales;self.rows=[];self.bank=[0]*128;self.cursor=0
        self.phase=self.index=self.loaded=self.maximum=0
        self.child_phase=self.col=self.acc=self.child_row=self.child_max=0
        self.child_left=self.child_valid=self.pending=self.logit=0
        self.completed=self.aborts=self.ignored=self.loads=self.mac_samples=self.align=0
        self.outputs=[]

    def tick(self,reset=0,start=0,m=0,iv=0,value=0,out_ready=1,mask=None):
        ready=int(not reset and self.phase==1 and self.cursor==self.loaded)
        valid=int(not reset and self.phase==2 and self.child_phase==4)
        out=(self.logit&0xffffffff)+(valid<<32)+(int(self.phase in (1,2,3))<<33)+(ready<<34)+(self.index<<35)+(int(self.phase==4)<<43)
        inp=reset+(start<<1)+(m<<2)+(iv<<22)+((value&255)<<23)+(out_ready<<31)
        if mask is None:mask=((1<<NO)-1) if valid else (((1<<NO)-1)^0xffffffff)
        self.rows.append((inp,out,mask))
        begin=int(not reset and start and self.phase in (0,4));write=ready&iv
        last=int(write and self.loaded==127);launch=int(not reset and self.phase==3 and self.cursor==127)
        child_start=last or launch;child_iv=not reset and self.phase==2 and self.cursor==self.col
        old_index,old_max=self.index,self.maximum;old_phase=self.child_phase;old_valid=self.child_valid
        sample=self.bank[0];head=self.bank.pop(0);self.bank.append(value if write and not (reset or begin) else head)
        self.cursor=0 if reset or begin else (self.cursor+1)&127
        if valid and out_ready:self.outputs.append((len(self.rows)-1,self.index,self.logit))
        if start and self.phase in (1,2,3):self.ignored+=1
        if self.phase==3 and not launch:self.align+=1
        if reset:
            self.aborts+=int(self.phase in (1,2,3));self.phase=self.index=self.loaded=self.maximum=0
            self.child_phase=self.col=self.acc=self.child_row=self.child_max=0
            self.child_left=self.child_valid=self.pending=self.logit=0
            return
        if begin:self.phase=1;self.index=self.loaded=0;self.maximum=m
        elif last:self.phase=2
        elif valid and out_ready:
            if self.index==191:self.phase=4;self.completed+=1
            else:self.index+=1;self.phase=3
        elif launch:self.phase=2
        if write:
            self.loads+=1
            if not last:self.loaded+=1
        if self.child_phase in (0,4) and child_start:
            self.child_phase=1;self.col=self.acc=0;self.child_row=old_index;self.child_max=old_max
        elif self.child_phase==1 and child_iv:
            self.acc+=sample*self.weights[self.child_row][self.col];self.mac_samples+=1
            if self.col==127:self.child_phase=2
            else:self.col+=1
        elif self.child_phase==2:self.child_phase=3
        elif self.child_phase==3 and old_valid:self.child_phase=4
        if old_phase==2:
            self.pending=row.base.rne(row.base.rne(self.acc*self.child_max,127)*self.scales[self.child_row],1<<24)
            self.child_left=row.base.LATENCY-1;self.child_valid=0
        elif self.child_left:
            self.child_left-=1
            if not self.child_left:self.logit=self.pending;self.child_valid=1


def vectors(c,weights,scales):
    model=Protocol(weights,scales);rng=random.Random(26100711);no_stall=[];backpressure=0
    tests={(t['block'],t['row']):t for t in c['tests']}
    model.tick(reset=1,mask=0);model.tick()
    def run(block_id,stall=False,abort_at=None):
        nonlocal backpressure
        block=c['blocks'][block_id];start_at=len(model.rows);before=len(model.outputs)
        model.tick(start=1,m=block['maximum'])
        while model.phase!=4:
            elapsed=len(model.rows)-start_at
            if abort_at is not None and elapsed==abort_at:
                model.tick(reset=1,start=1,iv=1,value=-128);model.tick();return
            value=block['q'][model.loaded] if model.phase==1 else rng.randrange(-128,128)
            iv=int(not stall or rng.randrange(13)!=0)
            ready=int(not stall or rng.randrange(5)!=0)
            backpressure+=int(model.phase==2 and model.child_phase==4 and not ready)
            model.tick(start=int(rng.randrange(4)==0),m=rng.randrange(1<<20),iv=iv,value=value,out_ready=ready)
            assert len(model.rows)-start_at<100000
        observed=model.outputs[before:];assert [r for _,r,_ in observed]==list(range(192))
        assert [v for _,_,v in observed]==[tests[block_id,i]['expected'] for i in range(192)]
        if not stall:
            actual=[t-start_at for t,_,_ in observed]
            assert actual==[344+256*i for i in range(192)]
            assert len(model.rows)-start_at==49241
            no_stall.append(dict(block=block_id,first_valid=actual[0],last_valid=actual[-1],done_observation=len(model.rows)-start_at))
        model.tick();model.tick(iv=1,value=127)
    for block in range(4):run(block,stall=bool(block&1))
    # Abort load, MAC, scaling, output backpressure, and the inter-row alignment.
    for elapsed in (1,64,128,129,192,257,258,300,343,344,345,346,383,384,49240):run(2,abort_at=elapsed)
    run(2)
    return model.rows,dict(clocks=len(model.rows),complete_scans=model.completed,aborts=model.aborts,loads=model.loads,
        accepted_mac_samples=model.mac_samples,ignored_busy_starts=model.ignored,alignment_waits=model.align,
        output_backpressure=backpressure,accepted_logits=len(model.outputs),no_stall=no_stall,
        checked_valid_results=sum(bool(y>>32&1) for _,y,_ in model.rows),
        completed_C_rows=192*model.completed,scope='five complete scans of four frozen norm10 inputs plus15 aborted scans; no full prompt inference')


def stalled_bank(net):
    records=net.records.copy();assert records[351][0]==1
    records[351]=(1,2+NI+351)
    bad=Netlist(NI,NO,records);bad.validate();return bad


def cloud(net,comb,shell_net,child,core,embedding,scale,words,c,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    reload=lambda name,g:Netlist.decode((OUT/(name+'.nl')).read_bytes(),g.n_in,g.n_out)
    bound,bound_comb=connect(reload('shell',shell_net),reload('child',child))
    assert bound.encode()==net.encode() and bound_comb.encode()==comb.encode()
    # The shell is proved with all401 child next-state/output bits free. Its
    # child-call outputs are proved too; source and independent RTL share no
    # generated control logic. Mechanical reconnection binds both transitions.
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    ref=row.mac_proof.mapped_reference(OUT/'shell',SHELL_IN,SHELL_OUT)
    for name,g in [('source',shell_net),('reference',ref),('negative',flip_output(shell_net))]:(OUT/('shell.'+name+'.blif')).write_text(blif(g))
    good=cec(abc,OUT/'shell.source.blif',OUT/'shell.reference.blif',OUT/'shell.cec.log');assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/'shell.negative.blif',OUT/'shell.reference.blif',OUT/'shell.negative.log');assert bad['verdict']=='different'
    mutated=connect(flip_output(shell_net),child)[0];assert mutated.encode()!=net.encode()
    folder=OUT/'child_proof';folder.mkdir(exist_ok=True)
    prepared=row.mac_proof.prepare(core,row.mac(),row.reference(),folder)
    child_proof=row.mac_proof.prove(core,row.mac(),folder)
    import embed_scalar as e
    previous=e.OUT;e.OUT=folder
    try:parsed,codes=e.c_codes(words)
    finally:e.OUT=previous
    constants={}
    for name,g,w in [('embedding',embedding,codes),('escale',scale,c['escale'])]:
        d=folder/name;d.mkdir(exist_ok=True);constants[name]=row.cloud_check(d,{name:g},w)
    rebuilt,recomb=row.connect(reload('child_body',core),reload('embedding',embedding),reload('escale',scale))
    assert rebuilt.encode()==child.encode()
    proof=dict(status='pass',shell_all_input_bits=SHELL_IN,shell_all_output_bits=SHELL_OUT,
        shell_CEC=good,actual_shell_D_fault=bad,child_all_state_bits=351,child_all_outputs=50,
        shell_and_child_reconnection_exact=True,child_preparation=prepared,child_MAC_decomposition=child_proof,
        constant_domains=constants,constant_C_parser=parsed,
        scope='free401 child D/output shell proof + full38-bit child MAC partition proof + free common body + all constant domains; exact graph recomposition')
    (OUT/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    negative=flip_output(net);wrong=checks.check_nand(rows,negative.encode())
    valid=sum(bool(y>>32&1) for _,y,_ in rows);assert wrong==valid
    stuck=stalled_bank(net);bank_wrong=checks.check_nand(rows,stuck.encode());assert bank_wrong>0
    (OUT/'bad.nl').write_bytes(negative.encode());(OUT/'bad_bank.nl').write_bytes(stuck.encode())
    (OUT/'bad.v').write_text(rtl(negative,'vocab_scan'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'vocab_scan',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'scan.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',proof=proof,clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,
        actual_valid_output_faults=wrong,actual_bank_latch_fault_mismatches=bank_wrong,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--reuse-fixtures',type=Path);args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    begin=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    fixture_sources={p.name:sha(p.read_bytes()) for p in [R/'integer_opt/vocab_row_golden.c',R/'integer_opt/vocab_scale_golden.c',R/'integer/int_model.c',R/'physical/model.bin']}
    if args.reuse_fixtures:
        frozen=json.loads(args.reuse_fixtures.read_text());assert {Path(k).name:v for k,v in frozen['sources'].items()}==fixture_sources;c=frozen['cases']
    else:
        assert args.cloud,'local work must reuse the already frozen C fixtures'
        old_base,old_row=row.base.OUT,row.OUT;row.base.OUT=OUT/'C_reference';row.base.OUT.mkdir(exist_ok=True);row.OUT=OUT
        try:row.base.cases();c=row.fixtures(row.base.OUT/'cases.json')
        finally:row.base.OUT=old_base;row.OUT=old_row
    (OUT/'fixture_reuse.json').write_text(json.dumps(dict(sources=fixture_sources,cases=c),indent=2)+'\n')
    (OUT/'cases.json').write_text(json.dumps(c,indent=2)+'\n')
    embedding,scale,words,scales,weights=row.tables();core=row.body();child=row.connect(core,embedding,scale)[0]
    assert metrics(child)['sha256']=='491c562094a23cdc6dd444219a8e07704405c90ab692e5d3ab15e6e15cc28c5a'
    shell_net=shell();checked=small(shell_net);net,comb=connect(shell_net,child)
    assert (net.n_in,net.n_out,net.n_state)==(NI,NO,NS)
    rows,expected=vectors(c,weights,scales)
    for name,g in [('scan',net),('transition',comb),('shell',shell_net),('child',child),('child_body',core),('embedding',embedding),('escale',scale)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'scan.v').write_text(rtl(net,'vocab_scan'));(OUT/'shell.ref.v').write_text(reference());(OUT/'child_body.ref.v').write_text(row.reference())
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for n in ('integer_opt/vocab_row_golden.c','integer_opt/vocab_scale_golden.c','integer_opt/embed_scalar_golden.c','integer_opt/weights_golden.c',
              'integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/vocab_row_units/manifest.json','integer_opt/vocab_row_units/head_mac.nl'):
        paths.add(R/n)
    def key(p):return str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name
    report=dict(status='small shell/bank and frozen C protocol prepared; complete graph and CEC await Actions',metrics=metrics(net),comb_metrics=metrics(comb),shell_metrics=metrics(shell_net),
        child_metrics=metrics(child),child_body_metrics=metrics(core),embedding=metrics(embedding),escale=metrics(scale),bank=metrics(circulate.make(128,8)[1]),
        small=checked,expected=expected,model_sha256=MODEL_SHA,cases_sha256=sha((OUT/'cases.json').read_bytes()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        binding=dict(state_order='child351,q8_ring1024,cursor7,row8,loaded7,max20,phase3',shell_ports='state1420,input32,child351D+50out',
            call_ports='reset,start,row8,max20,valid,head_q8',actual_child_sha256=metrics(child)['sha256'],q_rotation='every clock including idle/scale/backpressure; reset invalidates contents; reload128 before use'),
        contract='reset,start,max20,input_valid,q8,out_ready -> logit32,valid,busy,input_ready,row8,done;192 ascending rows, backpressure, full reload after abort',
        scope='autonomous192-row tied-E8 scanner after external norm/quant; no norm/A8,top40 or complete model',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={key(p):sha(p.read_bytes()) for p in sorted(paths)})
    receipt=OUT/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
    if args.cloud:report['verification']=cloud(net,comb,shell_net,child,core,embedding,scale,words,c,rows);report['status']='full scanner NAND/RTL/C, compositional CEC, and actual faults pass'
    report['seconds']=time.monotonic()-begin;save()
    print(json.dumps({k:report[k] for k in ('status','metrics','shell_metrics','expected','seconds')},indent=2))


if __name__=='__main__':main()
