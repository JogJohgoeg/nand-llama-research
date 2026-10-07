#!/usr/bin/env python3
"""One complete tied-E8 logit row: streaming q8, frozen weights, exact RNE.

Construct large graphs locally; full clocked NAND/RTL and body CEC only in CI.
The body has free coefficient/scale ports. Its mechanically reconnected tables
are checked on their complete domains, without a monolithic weight-table CEC.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_VOCAB_ROW_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_VOCAB_ROW_OUT',str(R/'build/integer_opt/vocab_row')))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import vocab_narrow as scalar
import vocab_scale as base
from nand import Builder,metrics,with_state,blif,flip_output,from_yosys
from golden import Netlist
from export import import_net,rtl,MODEL_SHA
from bench import lookup
from embed_scalar import scalar_table
from weight_order import ordered
from weights import cloud_check
from gate_check import verify
from ci import cec
NI,NO,NS,LATENCY=39,50,351,216
sha=lambda b:hashlib.sha256(b).hexdigest()


def mac():
    d=Path(__file__).with_name('vocab_row_units');meta=json.loads((d/'manifest.json').read_text())
    raw=(d/'head_mac.nl').read_bytes();assert sha(raw)==meta['metrics']['sha256']=='cb4f89df8bd696295d202567c0968620a07c9914abecc69e14e9266d2804b1df'
    assert meta['golden_sha256']['int_model.c']==base.C_SOURCES['integer/int_model.c']
    net=Netlist.decode(raw,38,22);assert metrics(net)==meta['metrics'];return net


def tables():
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    e=blob[243348:267924];rows=[int.from_bytes(e[i*128:(i+1)*128],'little') for i in range(192)]
    words=scalar_table(rows,8,7);assert sha(bytes(words))=='3847518676ce60471a6b6d2da0c1beb092e2d90d1cff8edb3f96babf75f1d523'
    table=ordered(words,8,list(reversed(range(8)))+list(reversed(range(8,15))))
    assert metrics(table)['sha256']=='7a93ac29fa53f0fff771a2390b0d18454aae9cb101af7a592d4b360038a67ec2'
    scales=[int.from_bytes(blob[i:i+4],'little') for i in range(267924,268692,4)]+[0]*64
    assert len(scales)==256 and max(scales)<1<<18
    coefficients=[[x if x<128 else x-256 for x in e[row*128:(row+1)*128]] for row in range(192)]+[[0]*128 for _ in range(64)]
    assert max(sum(abs(v) for v in row) for row in coefficients)==7253
    return table,lookup(scales,18,'shannon'),words,scales,coefficients


def control():
    b=Builder(14);col=list(range(2,9));phase=list(range(9,12));reset,start,iv,done=range(12,16)
    def eq(bits,v):return b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    def AND(*v):return b.reduce(v,b.land,1)
    keep=b.inv(reset);idle=eq(phase,0);running=eq(phase,1);launch=AND(keep,eq(phase,2));wait=eq(phase,3);valid=eq(phase,4)
    begin=AND(keep,start,b.lor(idle,valid));ready=AND(keep,running);accept=AND(ready,iv)
    last=AND(accept,eq(col,127));busy=b.reduce([running,eq(phase,2),wait],b.lor,0)
    advance=AND(accept,b.inv(last));nc=b.add(col,[0]*7,advance)[0]
    nc=[AND(keep,b.inv(begin),v) for v in nc]
    np=phase[:]
    for en,value in ((begin,1),(last,2),(launch,3),(AND(keep,wait,done),4)):
        np=[b.mux(en,v,value>>i&1) for i,v in enumerate(np)]
    np=[AND(keep,v) for v in np]
    return b.finish(nc+np+[begin,accept,last,launch,ready,busy,valid])


def control_ref(x):
    col=x&127;phase=x>>7&7;reset=x>>10&1;start=x>>11&1;iv=x>>12&1;done=x>>13&1
    begin=int(not reset and start and phase in (0,4));ready=int(not reset and phase==1)
    accept=ready&iv;last=int(accept and col==127);launch=int(not reset and phase==2)
    nc=0 if reset or begin else col+int(accept and not last)
    np=0 if reset else 1 if begin else 2 if last else 3 if launch else 4 if phase==3 and done else phase
    return nc+(np<<7)+(begin<<10)+(accept<<11)+(last<<12)+(launch<<13)+(ready<<14)+(int(phase in (1,2,3))<<15)+(int(phase==4)<<16)


def body():
    b=Builder(NS+NI+26);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2));ext=list(range(NS+NI+2,NS+NI+28))
    child=q[:291];row=q[291:299];maximum=q[299:319];col=q[319:326];acc=q[326:348];phase=q[348:351]
    reset,start=p[:2];keep=b.inv(reset)
    _,ctl=import_net(b,control(),col+phase+[reset,start,p[30],child[290]])
    begin,accept,last,launch,ready,busy,valid=ctl[10:]
    _,summed=import_net(b,mac(),acc+p[31:39]+ext[:8])
    nxt_child,result=import_net(b,scalar.make()[0],[reset,launch]+acc+maximum+ext[8:],child)
    assert result[32]==child[290]
    nr=[b.land(keep,b.mux(begin,v,w)) for v,w in zip(row,p[2:10])]
    nm=[b.land(keep,b.mux(begin,v,w)) for v,w in zip(maximum,p[10:30])]
    clear=b.lor(reset,begin)
    na=[b.land(b.inv(clear),b.mux(accept,v,w)) for v,w in zip(acc,summed)]
    nxt=nxt_child+nr+nm+ctl[:7]+na+ctl[7:10];assert len(nxt)==NS
    return b.finish(nxt+result[:32]+[valid,busy,ready]+col+row)


def connect(core,embedding,scale):
    b=Builder(NS+NI);p=list(range(2,NS+NI+2));row=p[291:299];col=p[319:326]
    _,e=import_net(b,embedding,row+col);_,g=import_net(b,scale,row)
    _,y=import_net(b,core,p+e+g)
    comb=b.finish(y);return with_state(comb,NS),comb


def reference():
    return '''module top(input [415:0] din,output [400:0] dout);
wire [350:0] q=din[350:0];wire [38:0] p=din[389:351];
wire signed [7:0] coefficient=din[397:390],sample=p[38:31];wire [17:0] scale=din[415:398];
wire [7:0] row=q[298:291];wire [19:0] maximum=q[318:299];wire [6:0] col=q[325:319];
wire signed [21:0] acc=q[347:326];wire [2:0] phase=q[350:348];
wire reset=p[0],start=p[1],iv=p[30],child_valid=q[290];
wire begin_op=!reset && start && (phase==0 || phase==4);
wire ready=!reset && phase==1,accept=ready && iv,last=accept && col==127;
wire launch=!reset && phase==2,valid=phase==4,busy=phase==1 || phase==2 || phase==3;
wire signed [15:0] product=sample*coefficient;
wire [21:0] sum=acc+{{6{product[15]}},product};
wire [7:0] nr=reset?8'b0:begin_op?p[9:2]:row;
wire [19:0] nm=reset?20'b0:begin_op?p[29:10]:maximum;
wire [21:0] na=(reset || begin_op)?22'b0:accept?sum:acc;
reg [6:0] nc;reg [2:0] np;
always @* begin
 nc=col;np=phase;
 if(accept && !last) nc=col+7'd1;
 if(begin_op) begin nc=0;np=1;end
 if(last) np=2;
 if(launch) np=3;
 if(phase==3 && child_valid) np=4;
 if(reset) begin nc=0;np=0;end
end
wire [324:0] child;
narrow_step u(.din({scale,maximum,acc,launch,reset,q[290:0]}),.dout(child));
assign dout={row,col,ready,busy,valid,child[322:291],np,na,nc,nm,nr,child[290:0]};
endmodule
'''+scalar.reference().replace('module top(','module narrow_step(',1)


def fixtures(reference_file):
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    assert sha((R/'integer/int_model.c').read_bytes())==base.C_SOURCES['integer/int_model.c']
    raw=reference_file.read_bytes();assert sha(raw)=='168ae96b2ffacc56808cc76d46aa0f0077f5afbf660ea7ea49b0164ab9a264dc'
    old=json.loads(raw);source=Path(__file__).with_name('vocab_row_golden.c');lib=OUT/'row_golden.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared','-I',str(R/'integer_opt'),str(source),'-o',str(lib)],check=True,timeout=30)
    g=ct.CDLL(str(lib));g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.vocab_input.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8),ct.POINTER(ct.c_uint32)]
    g.vocab_row.argtypes=[ct.POINTER(ct.c_int8),ct.c_uint32,ct.c_uint,ct.POINTER(ct.c_int32)];g.vocab_row.restype=ct.c_int64
    g.vocab_mac.argtypes=[ct.c_int32,ct.c_int8,ct.c_int8];g.vocab_mac.restype=ct.c_uint32
    g.vocab_escale.argtypes=[ct.c_uint];g.vocab_escale.restype=ct.c_uint32
    blocks=[]
    for index,block in enumerate(old['true_weight_rows']):
        q=(ct.c_int8*128)();m=ct.c_uint32();assert g.vocab_input((ct.c_int32*128)(*block['input']),q,ct.byref(m))==0
        blocks.append(dict(kind='norm10_'+str(index),q=list(q),maximum=m.value,rows=list(range(256))))
    rng=random.Random(261004);edge=[[0]*128,[-128]*128,[127]*128,[-128 if i&1 else 127 for i in range(128)],
                                  [-1 if i&1 else 1 for i in range(128)]]
    edge += [[rng.randrange(-128,128) for _ in range(128)] for _ in range(4)]
    for i,q in enumerate(edge):
        blocks.append(dict(kind='q8_boundary_'+str(i),q=q,maximum=[0,1,524288,1048575][i%4],rows=[0,1,2,7,31,63,95,127,159,189,190,191,192,193,254,255]))
    _,_,_,scales,weights=tables();tests=[]
    for bi,block in enumerate(blocks):
        for row in block['rows']:
            partial=(ct.c_int32*128)();result=int(g.vocab_row((ct.c_int8*128)(*block['q']),block['maximum'],row,partial))
            dot=0;py=[]
            for x,e in zip(block['q'],weights[row]):dot+=x*e;py.append(dot)
            assert list(partial)==py and result==base.rne(base.rne(dot*block['maximum'],127)*scales[row],1<<24)
            if bi<4 and row<192:assert [dot,block['maximum'],scales[row],result]==old['true_weight_rows'][bi]['rows'][row]
            tests.append(dict(block=bi,row=row,partial=py,expected=result))
    assert [int(g.vocab_escale(i)) for i in range(256)]==scales
    triples=[(a,q,e) for a in (-(1<<21),(1<<21)-1,0) for q in (-128,-1,0,1,127) for e in (-128,-1,0,1,127)]
    triples += [(rng.randrange(-(1<<21),1<<21),rng.randrange(-128,128),rng.randrange(-128,128)) for _ in range(1024)]
    cm=[int(g.vocab_mac(*x)) for x in triples];assert cm==[(a+q*e)&((1<<22)-1) for a,q,e in triples]
    return dict(blocks=blocks,tests=tests,escale=scales,mac_inputs=triples,mac_expected=cm,old_C_cases_sha256=sha(raw),
                scope='all256 rows for four deterministic norm10 C inputs; nine q8 boundary/random blocks on16 rows; not full prompt inference')


def small(c,scale):
    ctl=control();unit=mac();assert max(len(g.records) for g in (ctl,unit,scale))<=4000
    result=dict(control=verify(ctl,list(range(16384)),[control_ref(i) for i in range(16384)]),
      mac=verify(unit,[(a&((1<<22)-1))+((q&255)<<22)+((e&255)<<30) for a,q,e in c['mac_inputs']],c['mac_expected']),
      escale=verify(scale,list(range(256)),c['escale']))
    assert all(x['status']=='pass' for x in result.values());return result


def vectors(c,weights,scales):
    rng=random.Random(26100419);rows=[];phase=row=maximum=col=acc=last=child_left=child_valid=pending=0
    completed=aborts=ignored=stalls=accepted=0
    def tick(reset=0,start=0,requested=0,m=0,iv=0,value=0,mask=None):
        nonlocal phase,row,maximum,col,acc,last,child_left,child_valid,pending,completed,aborts,ignored,stalls,accepted
        ready=int(not reset and phase==1);busy=int(phase in (1,2,3));valid=int(phase==4)
        x=reset+(start<<1)+(requested<<2)+(m<<10)+(iv<<30)+((value&255)<<31)
        y=(last&0xffffffff)+(valid<<32)+(busy<<33)+(ready<<34)+(col<<35)+(row<<42)
        if mask is None:mask=((1<<NO)-1) if valid else (((1<<NO)-1)^0xffffffff)
        rows.append((x,y,mask));old_phase=phase;old_done=child_valid
        if reset:
            aborts+=busy;phase=row=maximum=col=acc=last=child_left=child_valid=pending=0
            return
        if start and busy:ignored+=1
        if phase in (0,4) and start:phase=1;row=requested;maximum=m;col=acc=0
        elif phase==1:
            if iv:
                acc+=value*weights[row][col];accepted+=1
                if col==127:phase=2
                else:col+=1
            else:stalls+=1
        elif phase==2:phase=3
        elif phase==3 and old_done:phase=4;completed+=1
        if old_phase==2:
            pending=base.rne(base.rne(acc*maximum,127)*scales[row],1<<24);child_left=base.LATENCY-1;child_valid=0
        elif child_left:
            child_left-=1
            if not child_left:last=pending;child_valid=1
    def noise(reset=0):
        tick(reset=reset,start=int(rng.randrange(4)==0),requested=rng.randrange(256),m=rng.randrange(1<<20),iv=rng.randrange(2),value=rng.randrange(-128,128))
    tick(reset=1,mask=0);tick()
    for index,test in enumerate(c['tests']):
        block=c['blocks'][test['block']];start_at=len(rows);stall_count=0
        tick(start=1,requested=test['row'],m=block['maximum'],iv=1,value=127)
        for j,v in enumerate(block['q']):
            if index%5==1 and j%11==3:
                for _ in range(2):tick(start=1,requested=rng.randrange(256),m=rng.randrange(1<<20),iv=0,value=rng.randrange(-128,128));stall_count+=1
            tick(start=int(j%7==2),requested=rng.randrange(256),m=rng.randrange(1<<20),iv=1,value=v)
            assert acc==test['partial'][j] and row==test['row']
        while phase!=4:noise()
        assert len(rows)-start_at==LATENCY+stall_count and last==test['expected']
        if index%7:
            tick();tick(iv=1,value=-128)
    # Abort every controller phase and the multiplier/divider phase boundaries.
    for elapsed in (1,63,128,129,130,150,151,152,193,194,195,196,213,214,215):
        test=c['tests'][383];block=c['blocks'][test['block']]
        tick(start=1,requested=test['row'],m=block['maximum'])
        for t in range(1,elapsed):
            if phase==1:tick(iv=1,value=block['q'][col])
            else:noise()
        noise(reset=1);tick()
    # A complete restart after the last abort.
    test=c['tests'][700];block=c['blocks'][test['block']]
    tick(start=1,requested=test['row'],m=block['maximum'])
    for v in block['q']:tick(iv=1,value=v)
    while phase!=4:noise()
    assert last==test['expected'];tick()
    return rows,dict(clocks=len(rows),no_stall_latency=LATENCY,completed=completed,aborts=aborts,
                     ignored_busy_starts=ignored,input_stalls=stalls,accepted_samples=accepted,
                     checked_valid_results=sum(bool(y>>32&1) for _,y,_ in rows))


def cloud(net,comb,core,embedding,scale,words,c,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    # Rebuild the exact full transition only from archived body/constants.
    reload=lambda name,ni,no:Netlist.decode((OUT/(name+'.nl')).read_bytes(),ni,no)
    rebuilt,recomb=connect(reload('body',416,401),reload('embedding',15,8),reload('escale',8,18))
    assert rebuilt.encode()==net.encode() and recomb.encode()==comb.encode()
    import embed_scalar as e
    old=e.OUT;e.OUT=OUT
    try:parsed,codes=e.c_codes(words)
    finally:e.OUT=old
    constants={}
    for name,g,w in [('embedding',embedding,codes),('escale',scale,c['escale'])]:
        d=OUT/name;d.mkdir(exist_ok=True);constants[name]=cloud_check(d,{name:g},w)
    ys=OUT/'body.ys';ys.write_text(f'read_verilog {OUT}/body.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/body.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'body.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    ref=from_yosys(json.loads((OUT/'body.ref.json').read_text()),416,401)
    for name,g in [('source',core),('reference',ref),('negative',flip_output(core))]:(OUT/(name+'.blif')).write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    good=cec(abc,OUT/'source.blif',OUT/'reference.blif',OUT/'body.cec.log');assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'body.negative.log');assert bad['verdict']=='different'
    proof=dict(status='pass',all_state_bits=NS,all_outputs=NO,free_coefficient_scale_bits=26,arbitrary_transition=good,actual_D_fault=bad,
               constant_domains=constants,constant_C_parser=parsed,actual_graph_reconnection_exact=True)
    (OUT/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    mutant=flip_output(net);wrong=checks.check_nand(rows,mutant.encode())
    valid=sum(bool(y>>32&1) for _,y,_ in rows);assert wrong==valid
    weight_mutant=connect(core,flip_output(embedding),scale)[0]
    weight_wrong=checks.check_nand(rows,weight_mutant.encode());assert weight_wrong>0
    (OUT/'bad.nl').write_bytes(mutant.encode());(OUT/'bad_weights.nl').write_bytes(weight_mutant.encode())
    (OUT/'bad.v').write_text(rtl(mutant,'vocab_row'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'vocab_row',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'row.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',proof=proof,clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
                actual_valid_output_faults=wrong,actual_weight_fault_mismatches=weight_wrong,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--reference-cases',type=Path);ap.add_argument('--reuse-fixtures',type=Path);args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    begin=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    reference_file=args.reference_cases
    if reference_file is None and args.reuse_fixtures is None:
        assert args.cloud,'supply the already frozen C fixture locally'
        prior=base.OUT;base.OUT=OUT/'reference';base.OUT.mkdir(exist_ok=True)
        try:base.cases();reference_file=base.OUT/'cases.json'
        finally:base.OUT=prior
    fixture_sources={p.name:sha(p.read_bytes()) for p in [Path(__file__).with_name('vocab_row_golden.c'),R/'integer_opt/vocab_scale_golden.c',R/'integer/int_model.c',R/'physical/model.bin']}
    if args.reuse_fixtures:
        frozen=json.loads(args.reuse_fixtures.read_text());assert {Path(k).name:v for k,v in frozen['sources'].items()}==fixture_sources;c=frozen['cases']
    else:c=fixtures(reference_file)
    (OUT/'fixture_reuse.json').write_text(json.dumps(dict(sources=fixture_sources,cases=c),indent=2)+'\n')
    (OUT/'cases.json').write_text(json.dumps(c,indent=2)+'\n')
    embedding,scale,words,scales,weights=tables();checked=small(c,scale)
    core=body();net,comb=connect(core,embedding,scale)
    assert (net.n_in,net.n_out,net.n_state)==(NI,NO,NS)
    rows,expected=vectors(c,weights,scales)
    for name,g in [('row',net),('transition',comb),('body',core),('embedding',embedding),('escale',scale)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    # Read back, reconnect, and derive the stateful graph from exactly these files.
    rb=lambda name,g:Netlist.decode((OUT/(name+'.nl')).read_bytes(),g.n_in,g.n_out)
    bound,bound_c=connect(rb('body',core),rb('embedding',embedding),rb('escale',scale))
    assert bound.encode()==net.encode() and bound_c.encode()==comb.encode()
    (OUT/'row.v').write_text(rtl(net,'vocab_row'));(OUT/'body.ref.v').write_text(reference())
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    paths={Path(__file__).resolve(),Path(__file__).with_name('vocab_row_golden.c').resolve()}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for n in ('integer_opt/vocab_scale_golden.c','integer_opt/embed_scalar_golden.c','integer_opt/weights_golden.c','integer/int_model.c',
              'physical/model.bin','physical/verify.py','physical/nl_sim.c'):
        paths.add(R/n)
    paths.update(Path(__file__).with_name('vocab_row_units')/n for n in ('manifest.json','head_mac.nl'))
    def path_name(p):
        p=p.resolve()
        return str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+str(p.relative_to(Path(__file__).resolve().parent))
    report=dict(status='small C components and complete C protocol prepared; large gates and CEC await Actions',metrics=metrics(net),comb_metrics=metrics(comb),body_metrics=metrics(core),
      embedding=metrics(embedding),escale=metrics(scale),head_mac=metrics(mac()),scalar=metrics(scalar.make()[0]),small=checked,expected=expected,
      cases_sha256=sha((OUT/'cases.json').read_bytes()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),model_sha256=MODEL_SHA,
      binding=dict(state_order='scaler291,row8,max20,column7,acc22,phase3',free_body_ports='state351,input39,coefficient8,escale18',
                   coefficient_source='old row[291:299] + old column[319:326]',escale_source='old row[291:299]',actual_reconnection_exact=True),
      range_certificate='max true E8 row L1=7253; arbitrary signed8 input bounds every partial sum by928384<2^20; signed22 MAC and exact R103 scaler preserve all logits',
      contract='reset,start,row8,max20,input_valid,q8 -> logit32,valid,busy,input_ready,column7,captured_row8;128 accepted samples, stalls, ignored busy starts, reset abort; padded rows192..255 give0',
      scope='one autonomous row after external norm/quant; no q storage, vocabulary scheduler, top40 or complete model',
      numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
      sources={path_name(p):sha(p.read_bytes()) for p in sorted(paths)})
    receipt=OUT/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
    if args.cloud:report['verification']=cloud(net,comb,core,embedding,scale,words,c,rows);report['status']='all bound constants/body CEC and complete NAND/RTL/C protocol pass; actual faults rejected'
    report['seconds']=time.monotonic()-begin;save()
    print(json.dumps({k:report[k] for k in ('status','metrics','body_metrics','expected','seconds')},indent=2))


if __name__=='__main__':main()
