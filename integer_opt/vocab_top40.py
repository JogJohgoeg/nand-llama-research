#!/usr/bin/env python3
"""Exact tied-E8 scan feeding stable top40, with one shared start contract."""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_VOCAB_TOP40_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_VOCAB_TOP40_OUT',str(R/'build/integer_opt/vocab_top40')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
import vocab_scan as scan
import top40
from nand import Builder,metrics,with_state,blif,flip_output,from_yosys
from golden import Netlist
from gate_check import verify
from export import import_net,rtl,MODEL_SHA
from ci import cec
NI,NO,NS=32,44,3084
SHELL_IN=6288
SHELL_OUT=3196
sha=lambda b:hashlib.sha256(b).hexdigest()


def control():
    b=Builder(8);a=list(range(2,5));z=list(range(5,8));reset,start=8,9
    eq=lambda bits,v:b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    begin=b.reduce([b.inv(reset),start,b.lor(eq(a,0),eq(a,4)),b.lor(eq(z,0),eq(z,6))],b.land,1)
    ready=b.land(b.inv(reset),eq(z,1));return b.finish([begin,ready])


def shell(ungated=False):
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    s=list(range(NS+NI+2,NS+NI+1466));t=list(range(NS+NI+1466,SHELL_IN+2))
    assert len(s)==1464 and len(t)==1708
    sy=s[1420:];ty=t[1664:]
    _,ctl=import_net(b,control(),q[1417:1420]+q[3081:3084]+p[:2]);begin=p[1] if ungated else ctl[0]
    sc=[p[0],begin]+p[2:31]+[ctl[1]];tc=[p[0],begin,sy[32]]+sy[:32]+[p[31]]
    assert len(sc)==32 and len(tc)==36
    outputs=ty[:41]+[b.lor(sy[33],ty[41]),sy[34],ty[43]]
    return b.finish(s[:1420]+t[:1664]+outputs+sc+tc)


def connect(shell_net,scanner,sorter):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2))
    _,first=import_net(b,shell_net,pins+[0]*(1464+1708));sc=first[NS+NO:NS+NO+32]
    sd,sy=import_net(b,scanner,sc,pins[:1420])
    _,second=import_net(b,shell_net,pins+sd+sy+[0]*1708);tc=second[NS+NO+32:]
    assert second[NS+NO:NS+NO+32]==sc
    td,ty=import_net(b,sorter,tc,pins[1420:NS]);assert ty[42]==sc[-1]
    _,full=import_net(b,shell_net,pins+sd+sy+td+ty);assert full[NS+NO:]==sc+tc
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def reference():
    return '''module top(input [6287:0] din,output [3195:0] dout);
wire [3083:0] q=din[3083:0];wire [31:0] p=din[3115:3084];
wire [1463:0] scanner=din[4579:3116];wire [1707:0] sorter=din[6287:4580];
wire [43:0] sy=scanner[1463:1420],ty=sorter[1707:1664];
wire [2:0] sp=q[1419:1417],tp=q[3083:3081];
wire begin_op=!p[0] && p[1] && (sp==0 || sp==4) && (tp==0 || tp==6);
wire sorter_ready=!p[0] && tp==1;
wire [31:0] sc={sorter_ready,p[30:2],begin_op,p[0]};
wire [35:0] tc={p[31],sy[31:0],sy[32],begin_op,p[0]};
wire [43:0] result={ty[43],sy[34],(sy[33] || ty[41]),ty[40:0]};
assign dout={tc,sc,result,sorter[1663:0],scanner[1419:0]};
endmodule
'''


def vectors(c,weights,scales):
    sm=scan.Protocol(weights,scales);ts=0;rows=[];results=[];handoffs=[];rng=random.Random(261007110)
    starts=ignored=aborts=output_stalls=0;spans=[];active=None;position=0
    expected_rows={(t['block'],t['row']):t['expected'] for t in c['tests']}
    golden=[[expected_rows[block,i] for i in range(192)] for block in range(4)]
    orders=[sorted(range(192),key=lambda i:(-g[i],i))[:40] for g in golden]
    def tick(reset=0,start=0,m=0,iv=0,value=0,out_ready=1,mask=None):
        nonlocal ts,starts,ignored,aborts,output_stalls,position
        tp=ts>>1661&7;begin=int(not reset and start and sm.phase in (0,4) and tp in (0,6));top_ready=int(not reset and tp==1)
        x=reset+(start<<1)+(m<<2)+(iv<<22)+((value&255)<<23)+(out_ready<<31)
        busy=sm.phase in (1,2,3) or 1<=tp<=5
        ignored+=int(start and busy);aborts+=int(reset and busy);starts+=begin
        old_ts=ts
        sm.tick(reset=reset,start=begin,m=m,iv=iv,value=value,out_ready=top_ready)
        sy=sm.rows[-1][1];tc=reset+(begin<<1)+((sy>>32&1)<<2)+((sy&0xffffffff)<<3)+(out_ready<<35)
        ts,ty=top40.transition(ts,tc)
        y=(ty&((1<<41)-1))+((int(bool(sy>>33&1 or ty>>41&1)))<<41)+((sy>>34&1)<<42)+(ty>>43<<43)
        if mask is None:mask=((1<<NO)-1) if ty>>40&1 else (((1<<NO)-1)^((1<<40)-1))
        rows.append((x,y,mask))
        if sy>>32&1 and top_ready:
            index=sy>>35&255;logit=(sy&0x7fffffff)-(sy&0x80000000)
            assert index==(old_ts>>1606&255) and logit==golden[active][index]
            handoffs.append((len(rows)-1,index,logit))
        if ty>>40&1:
            output_stalls+=int(not out_ready)
            if out_ready:
                token=ty>>32&255;score=(ty&0x7fffffff)-(ty&0x80000000)
                assert token==orders[active][position] and score==golden[active][token]
                results.append((len(rows)-1,token,score));position+=1
    tick(reset=1,mask=0);tick()
    def run(block,stall=False,abort_at=None):
        nonlocal active,position
        active=block;position=0;before=len(rows);ri=len(results);hi=len(handoffs);tick(start=1,m=c['blocks'][block]['maximum'])
        while ts>>1661&7!=6:
            elapsed=len(rows)-before
            if abort_at is not None and elapsed==abort_at:
                tick(reset=1,start=1,iv=1,value=-128);tick();return
            value=c['blocks'][block]['q'][sm.loaded] if sm.phase==1 else rng.randrange(-128,128)
            iv=int(not stall or rng.randrange(13)!=0);ready=int(not stall or rng.randrange(4)!=0)
            tick(start=int(rng.randrange(4)==0),m=rng.randrange(1<<20),iv=iv,value=value,out_ready=ready)
            assert len(rows)-before<100000
        assert position==40 and [i for _,i,_ in handoffs[hi:]]==list(range(192))
        assert [i for _,i,_ in results[ri:]]==orders[block]
        spans.append(dict(block=block,start=before,end=len(rows),clocks=len(rows)-before,first_sorted=results[ri][0]-before,stalls=stall))
        tick();tick(iv=1,value=127)
    for block in range(4):run(block,stall=bool(block&1))
    for elapsed in (1,64,128,129,344,345,400,49240,49241,49280,49281,49282,49300):run(2,abort_at=elapsed)
    run(2)
    return rows,dict(clocks=len(rows),completed=len(spans),checked_C_top40_entries=40*len(spans),
        checked_row_handoffs=len(handoffs),accepted_top_entries=len(results),starts=starts,aborts=aborts,
        ignored_busy_starts=ignored,output_stalls=output_stalls,checked_valid_results=sum(bool(y>>40&1) for _,y,_ in rows),
        no_stall_clocks=sorted(set(s['clocks'] for s in spans if not s['stalls'])),
        no_stall_first_sorted=sorted(set(s['first_sorted'] for s in spans if not s['stalls'])),spans=spans)


def prove(name,net,reftext):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True);p.with_suffix('.ref.v').write_text(reftext)
    ref=scan.row.mac_proof.mapped_reference(p,net.n_in,net.n_out)
    for kind,g in [('source',net),('reference',ref),('negative',flip_output(net))]:p.with_suffix('.'+kind+'.blif').write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    good=cec(abc,p.with_suffix('.source.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.cec.log'))
    bad=cec(abc,p.with_suffix('.negative.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.negative.log'))
    assert good['verdict']=='equivalent' and bad['verdict']=='different'
    return dict(metrics=metrics(net),all_input_bits=net.n_in,all_output_bits=net.n_out,proof=good,actual_D_fault=bad)


def cloud(nets,rows,c,words):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    net=nets['joint'];combined,recomb=connect(nets['shell'],nets['scanner'],nets['sorter'])
    assert combined.encode()==net.encode() and recomb.encode()==nets['transition'].encode()
    source_row=scan.row.connect(nets['row_body'],nets['embedding'],nets['escale'])[0]
    source_scan=scan.connect(nets['scan_shell'],source_row)[0]
    assert source_row.encode()==nets['row'].encode() and source_scan.encode()==nets['scanner'].encode()
    assert with_state(nets['sort_transition'],1664).encode()==nets['sorter'].encode()
    for key,g in nets.items():assert (OUT/(key+'.nl')).read_bytes()==g.encode()
    proof={}
    for name,g,ref in [('parent',nets['shell'],reference()),('scan',nets['scan_shell'],scan.reference()),('sort',nets['sort_transition'],top40.reference())]:
        proof[name]=prove('proofs/'+name,g,ref)
    folder=OUT/'row_proof';folder.mkdir(exist_ok=True)
    prepared=scan.row.mac_proof.prepare(nets['row_body'],scan.row.mac(),scan.row.reference(),folder)
    proof['MAC']=scan.row.mac_proof.prove(nets['row_body'],scan.row.mac(),folder);proof['MAC_preparation']=prepared
    import embed_scalar as e
    previous=e.OUT;e.OUT=folder
    try:parsed,codes=e.c_codes(words)
    finally:e.OUT=previous
    constants={}
    for name,w in [('embedding',codes),('escale',c['escale'])]:
        d=folder/name;d.mkdir(exist_ok=True);constants[name]=scan.row.cloud_check(d,{name:nets[name]},w)
    proof.update(status='pass',constants=constants,constant_C_parser=parsed,all_actual_graph_reconnections_exact=True)
    # Independent C sorting of the exact four frozen norm10/logit blocks.
    lib=OUT/'sort.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(R/'integer_opt/sample_stream.c'),'-o',str(lib)],check=True,timeout=30)
    g=ct.CDLL(str(lib));g.stream_logit.argtypes=[ct.c_uint,ct.c_int32];g.stream_order.argtypes=[ct.c_uint];g.stream_order.restype=ct.c_uint
    g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
    expected={(t['block'],t['row']):t['expected'] for t in c['tests']};golden=[]
    for block in range(4):
        logits=[expected[block,i] for i in range(192)];order=sorted(range(192),key=lambda i:(-logits[i],i))[:40]
        g.stream_begin()
        for i,value in enumerate(logits):g.stream_logit(i,value)
        assert [g.stream_order(i) for i in range(40)]==order
        assert g.int_pick((ct.c_int32*192)(*logits),0,0)==order[0]
        golden.append(dict(block=block,logits=logits,top40=order))
    (OUT/'C_top40.json').write_text(json.dumps(golden,indent=2)+'\n');proof['C_lists']=len(golden)
    (OUT/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    changed=flip_output(net);wrong=checks.check_nand(rows,changed.encode());valid=sum(bool(y>>40&1) for _,y,_ in rows);assert wrong==valid
    unsafe=connect(shell(True),nets['scanner'],nets['sorter'])[0];start_wrong=checks.check_nand(rows,unsafe.encode());assert start_wrong>0
    unstable=connect(nets['shell'],nets['scanner'],top40.make(True)[0])[0];tie_wrong=checks.check_nand(rows,unstable.encode());assert tie_wrong>0
    for name,g in [('bad',changed),('bad_start',unsafe),('bad_tie',unstable)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'bad.v').write_text(rtl(changed,'vocab_top40'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'vocab_top40',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'joint.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',proof=proof,clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,
        actual_valid_output_faults=wrong,actual_busy_start_fault_mismatches=start_wrong,
        actual_tie_rule_fault_mismatches=tie_wrong,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--reuse-fixtures',type=Path);args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    begin=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    fixture_sources={p.name:sha(p.read_bytes()) for p in [R/'integer_opt/vocab_row_golden.c',R/'integer_opt/vocab_scale_golden.c',R/'integer/int_model.c',R/'physical/model.bin']}
    if args.reuse_fixtures:
        frozen=json.loads(args.reuse_fixtures.read_text());assert {Path(k).name:v for k,v in frozen['sources'].items()}==fixture_sources;c=frozen['cases']
    else:
        assert args.cloud,'reuse the existing C fixtures locally'
        old_base,old_row=scan.row.base.OUT,scan.row.OUT;scan.row.base.OUT=OUT/'C_reference';scan.row.base.OUT.mkdir(exist_ok=True);scan.row.OUT=OUT
        try:scan.row.base.cases();c=scan.row.fixtures(scan.row.base.OUT/'cases.json')
        finally:scan.row.base.OUT=old_base;scan.row.OUT=old_row
    (OUT/'cases.json').write_text(json.dumps(c,indent=2)+'\n')
    e,g,words,scales,weights=scan.row.tables();core=scan.row.body();one=scan.row.connect(core,e,g)[0]
    ss=scan.shell();scanner=scan.connect(ss,one)[0];sorter,sort_comb=top40.make();common=shell();net,comb=connect(common,scanner,sorter)
    assert metrics(scanner)['sha256']=='e4e9359812c48a131645b1ecc3f81746c14124f0839518a732b8c5f91edbe9ab'
    assert metrics(sorter)['sha256']=='18fababd3736a460e23e7278e7c99d22110ea169290ac67382b9914ef30b2528'
    actions=control();expected=[]
    for x in range(256):
        sp=x&7;tp=x>>3&7;reset=x>>6&1;start=x>>7&1
        expected.append(int(not reset and start and sp in (0,4) and tp in (0,6))+(int(not reset and tp==1)<<1))
    checked=verify(actions,list(range(256)),expected);assert checked['status']=='pass'
    nets=dict(joint=net,transition=comb,shell=common,scanner=scanner,scan_shell=ss,sorter=sorter,sort_transition=sort_comb,row=one,row_body=core,embedding=e,escale=g)
    for key,value in nets.items():(OUT/(key+'.nl')).write_bytes(value.encode())
    (OUT/'joint.v').write_text(rtl(net,'vocab_top40'));(OUT/'parent.ref.v').write_text(reference())
    rows,protocol=vectors(c,weights,scales);(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if filename:
            p=Path(filename).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for n in ('integer_opt/vocab_row_golden.c','integer_opt/vocab_scale_golden.c','integer_opt/embed_scalar_golden.c','integer_opt/weights_golden.c','integer_opt/sample_stream.c',
              'integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/vocab_row_units/manifest.json','integer_opt/vocab_row_units/head_mac.nl'):
        paths.add(R/n)
    key=lambda p:str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name
    report=dict(status='bound scan/top40 and complete C protocol prepared; large graph proof awaits Actions',metrics=metrics(net),nets={k:metrics(n) for k,n in nets.items()},
        small_control=checked,expected=protocol,model_sha256=MODEL_SHA,cases_sha256=sha((OUT/'cases.json').read_bytes()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        binding=dict(state_order='scanner1420,sorter1664',extra_state_bits=0,common_start='both children idle/done and not reset',
            scanner_ready='actual top40 input_ready equals !reset && top40 phase==collect',
            child_interface='scanner logit32/valid -> top40 score32/input_valid; all192 ascending IDs checked'),
        contract='reset,start,max20,input_valid,q8,out_ready -> sorted score32,id8,valid,busy,input_ready,done',
        scope='norm/A8 inputs remain external; integrated192 tied-E8 rows and stable top40; no EXP/RNG or full-model controller',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={key(p):sha(p.read_bytes()) for p in sorted(paths)})
    receipt=OUT/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
    if args.cloud:report['verification']=cloud(nets,rows,c,words);report['status']='all fresh component/parent proofs and full joint NAND/RTL/C clocks pass; actual integration faults rejected'
    report['seconds']=time.monotonic()-begin;save()
    print(json.dumps({k:report[k] for k in ('status','metrics','small_control','seconds')},indent=2))


if __name__=='__main__':main()
