#!/usr/bin/env python3
"""192 logits -> token: accepted R109 stable top40 + replay shell + R112 sampler.

The R109 sorter graph is imported byte-for-byte. A small shell only changes its
next phase/index when the sorter is DONE and the sampler requests a replay:
phase 6 -> 4 (read) and index -> 0. The ring keeps rotating in DONE, so the
same40 entries are presented again. A common start is accepted only when both
cores are idle/done. The whole path equals int_pick(logits,random,sample).
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_PICK40_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_PICK40_OUT',str(R/'build/integer_opt/pick40')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from gate_check import verify
from export import import_net,rtl,MODEL_SHA
import top40,sampler
SS,SM=top40.NS,sampler.NS;NS=SS+SM
NI,NO=68,11
sha=lambda b:hashlib.sha256(b).hexdigest()


def replay_shell(bad=False):
    """inputs: np3,ni8,phase3,reset,start,replay -> np3,ni8 (17 in / 11 out)."""
    b=Builder(17);np=list(range(2,5));ni=list(range(5,13));ph=list(range(13,16));reset,start,rep=16,17,18
    done=b.reduce([b.inv(ph[0]),ph[1],ph[2]],b.land,1)
    go=b.reduce([b.inv(reset),b.inv(start),rep,done],b.land,1)
    out=[b.mux(go,x,v) for x,v in zip(np,[0,0,1])]+([b.land(b.inv(go),x) for x in ni] if not bad else ni)
    return b.finish(out)


def shell_ref(x):
    np=x&7;ni=x>>3&255;ph=x>>11&7;reset=x>>14&1;start=x>>15&1;rep=x>>16&1
    go=not reset and not start and rep and ph==6
    return (4 if go else np)|((0 if go else ni)<<3)


def sorter_r(bad=False):
    """R109 transition + replay shell; inputs q1664 + reset,start,iv,score32,out_ready,replay."""
    _,comb=top40.make();b=Builder(SS+37);q=list(range(2,SS+2));p=list(range(SS+2,SS+39))
    _,o=import_net(b,comb,q+p[:36])
    _,s=import_net(b,replay_shell(bad),o[1661:1664]+o[1606:1614]+q[1661:1664]+[p[0],p[1],p[36]])
    nxt=o[:1606]+s[3:11]+o[1614:1661]+s[:3]
    return b.finish(nxt+o[SS:]),comb


def connect(sorter_comb,sampler_comb,ungated=False):
    b=Builder(NS+NI);qs=list(range(2,SS+2));qm=list(range(SS+2,NS+2));p=list(range(NS+2,NS+NI+2))
    reset,start,smp=p[0],p[1],p[2];rnd=p[3:35];iv=p[35];score=p[36:68]
    eq=lambda bits,v:b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    sp=qs[1661:1664];mp=qm[0:3]
    begin=b.reduce([b.inv(reset),start,b.lor(eq(sp,0),eq(sp,6))]+([] if ungated else [b.lor(eq(mp,0),eq(mp,4))]),b.land,1)
    # Sampler ready/replay depend only on its state and reset (placeholders fold away).
    _,m0=import_net(b,sampler_comb,qm+[reset,begin,smp]+rnd+[0]*41)
    ready,rep=m0[SM+8],m0[SM+9]
    _,so=import_net(b,sorter_comb,qs+[reset,begin,iv]+score+[ready,rep])
    carry=so[SS:SS+40];valid=so[SS+40]
    _,mo=import_net(b,sampler_comb,qm+[reset,begin,smp]+rnd+[valid]+carry)
    assert mo[SM+8]==ready and mo[SM+9]==rep
    outs=mo[SM:SM+8]+[so[SS+42],b.lor(so[SS+41],mo[SM+10]),b.land(so[SS+43],mo[SM+11])]
    comb=b.finish(so[:SS]+mo[:SM]+outs);return with_state(comb,NS),comb


def transition(state,x):
    qs=state&((1<<SS)-1);qm=state>>SS
    reset=x&1;start=x>>1&1;smp=x>>2&1;rnd=x>>3&0xffffffff;iv=x>>35&1;score=x>>36&0xffffffff
    sp=qs>>1661&7;mp=qm&7
    begin=int(not reset and start and sp in (0,6) and mp in (0,4))
    _,m0=sampler.transition(qm,reset|(begin<<1)|(smp<<2)|(rnd<<3))
    ready=m0>>8&1;rep=m0>>9&1
    ns,so=top40.transition(qs,reset|(begin<<1)|(iv<<2)|(score<<3)|(ready<<35))
    if not reset and not begin and rep and sp==6:ns=(ns&~((7<<1661)|(255<<1606)))|(4<<1661)
    valid=so>>40&1;carry=so&((1<<40)-1)
    nm,mo=sampler.transition(qm,reset|(begin<<1)|(smp<<2)|(rnd<<3)|(valid<<35)|(carry<<36))
    out=(mo&255)|((so>>42&1)<<8)|(int(bool(so>>41&1 or mo>>10&1))<<9)|(int(bool(so>>43&1 and mo>>11&1))<<10)
    return ns|(nm<<SS),out


def golden(cases):
    src=R/'integer_opt/sample_stream.c'
    with tempfile.TemporaryDirectory() as tmp:
        so=Path(tmp)/'s.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(src),'-o',str(so)],check=True,timeout=30)
        g=ct.CDLL(str(so));g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
        blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
        return [g.int_pick((ct.c_int32*192)(*lg),r,s) for lg,r,s in cases]


def cases():
    lists,_=top40.cases();rng=random.Random(26100713);out=[]
    for k,c in enumerate(lists):
        lg=c['logits'];o=c['order'];ws=[sampler.weight(lg[o[0]],lg[i]) for i in o];T=sum(ws)
        if k%4==3:out.append((lg,rng.getrandbits(32),0));continue
        cum=sum(ws[:rng.randrange(1,40)]);r=-(-(cum<<32)//T)-rng.randrange(2)
        out.append((lg,[r,rng.getrandbits(32),0xffffffff][k%3]&0xffffffff,1))
    return out


def vectors(cs,want):
    rng=random.Random(26100714);state=0;rows=[];spans=[];stats=dict(aborts=0,ignored_starts=0,replays=0)
    def tick(x,mask=(1<<NO)-1):
        nonlocal state
        old=state;state,y=transition(state,x);rows.append((x,y,mask))
        if x&2 and not x&1 and ((old>>1661&7) not in (0,6) or (old>>SS&7) not in (0,4)):stats['ignored_starts']+=1
        if not x&1 and old>>1661&7==6 and (state>>1661&7)==4:stats['replays']+=1
        return y
    tick(1,0);tick(0)
    def one(k,lg,r,smp,tok,stall,abort_at=None):
        before=len(rows);sent=0
        y=tick(2|(smp<<2)|(r<<3))
        while True:
            if abort_at is not None and len(rows)-before==abort_at:tick(1|2,0);tick(0);stats['aborts']+=1;return
            send=int(sent<192 and (not stall or rng.randrange(5)!=0))
            sc=lg[sent]&0xffffffff if send else rng.getrandbits(32)
            busy=(state>>1661&7) not in (0,6) or (state>>SS&7) not in (0,4)
            junk=busy and rng.randrange(9)==0
            y=tick((junk<<1)|(smp<<2)|(r<<3)|(send<<35)|(sc<<36))
            # input_ready is a combinational output of the old state; only count accepted sends.
            if send and y>>8&1:sent+=1
            if y>>10&1:break
            assert len(rows)-before<40000
        assert y&255==tok,(k,y&255,tok);assert sent==192
        spans.append(dict(case=k,clocks=len(rows)-before,stall=stall,sample=smp))
        tick(0)
    for k,((lg,r,smp),tok) in enumerate(zip(cs,want)):one(k,lg,r,smp,tok,stall=k%5==4)
    lg,r,smp=cs[1]
    for at in (3,15370,15410,15440,15470):one(-1,lg,r,smp,None,False,abort_at=at)
    one(1,lg,r,smp,want[1],False)
    ns={s['clocks'] for s in spans if not s['stall'] and s['sample']};ng={s['clocks'] for s in spans if not s['stall'] and not s['sample']}
    return rows,dict(clocks=len(rows),picks=len(spans),no_stall_sample_clocks=sorted(ns),no_stall_greedy_clocks=sorted(ng),**stats)


def reference():
    sort=top40.reference().replace('module top(','module sortref(')
    samp=sampler.reference().replace('module top(','module sampref(')
    return sort+samp+'''module top(input [1858:0] din,output [1801:0] dout);
wire [1663:0] qs=din[1663:0];wire [126:0] qm=din[1790:1664];wire [67:0] p=din[1858:1791];
wire reset=p[0],start=p[1],smp=p[2],iv=p[35];wire [31:0] rnd=p[34:3],score=p[67:36];
wire [2:0] sp=qs[1663:1661],mp=qm[2:0];
wire begin_op=!reset && start && (sp==0 || sp==6) && (mp==0 || mp==4);
wire mready=!reset && (mp==1 || mp==3),rep=!reset && mp==3 && qm[8:3]==0;
wire [1707:0] so;wire [138:0] mo;
sortref s(.din({mready,score,iv,begin_op,reset,qs}),.dout(so));
wire go=!reset && !begin_op && rep && sp==6;
wire [1663:0] ns={go?3'd4:so[1663:1661],so[1660:1614],go?8'd0:so[1613:1606],so[1605:0]};
sampref m(.din({so[1703:1664],so[1704],rnd,smp,begin_op,reset,qm}),.dout(mo));
assign dout={mo[138] && so[1707],mo[137] || so[1705],so[1706],mo[134:127],mo[126:0],ns};
endmodule
'''


def cloud(net,comb,rows,parts):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    prefix=OUT/'joint';prefix.with_suffix('.ref.v').write_text(reference())
    ref=sampler.mapped_reference(prefix,NS+NI,NS+NO)
    for kind,g in [('source',comb),('reference',ref),('negative',flip_output(comb)),('no_index_clear',parts['no_index_clear'][1]),('ungated_start',parts['ungated_start'][1])]:
        prefix.with_suffix('.'+kind+'.blif').write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    proofs={}
    for kind,want in [('source','equivalent'),('negative','different'),('no_index_clear','different'),('ungated_start','different')]:
        proofs[kind]=cec(abc,prefix.with_suffix('.'+kind+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+kind+'.log'));assert proofs[kind]['verdict']==want,kind
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    faults={}
    for name,g in [('output_flip',flip_output(net))]+[(k,v[0]) for k,v in parts.items()]:
        faults[name]=checks.check_nand(rows,g.encode());assert faults[name]>0,name;(OUT/('bad_'+name+'.nl')).write_bytes(g.encode())
    bad=flip_output(net);(OUT/'bad.v').write_text(rtl(bad,'pick40'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'pick40',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'pick40.v');checks.run([exe],600)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=600)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',all_state_bits=NS,all_input_bits=NI,all_output_bits=NO,proofs=proofs,reference_metrics=metrics(ref),
        clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    sh=replay_shell();xs=list(range(1<<17));sc=verify(sh,xs,[shell_ref(x) for x in xs]);assert sc['status']=='pass'
    bad=replay_shell(True);assert sum(u!=v for u,v in zip(__import__('gate_check').simulate(bad,xs),[shell_ref(x) for x in xs]))>0
    scomb,orig=sorter_r();_,mcomb=sampler.make()
    net,comb=connect(scomb,mcomb)
    # Bind the imported cores to the accepted R109 sorter and the R112 sampler bytes.
    assert sha(orig.encode())==json.loads((R/'integer_opt/pick40_units/manifest.json').read_text())['R109_transition_sha256']
    assert sha(mcomb.encode())==json.loads((R/'integer_opt/pick40_units/manifest.json').read_text())['R112_transition_sha256']
    rs,_=sorter_r(True);parts=dict(no_index_clear=connect(rs,mcomb),ungated_start=connect(scomb,mcomb,True),no_borrow=connect(scomb,sampler.make('no_borrow')[1]))
    cs=cases();want=golden(cs)
    rows,proto=vectors(cs,want)
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    for name,g in [('pick40',net),('transition',comb),('sorter_replay',scomb),('replay_shell',sh)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'pick40.v').write_text(rtl(net,'pick40'))
    fixture=[dict(logits_sha256=sha(b''.join((v&0xffffffff).to_bytes(4,'little') for v in c[0])),random=c[1],sample=c[2],token=t) for c,t in zip(cs,want)]
    (OUT/'cases.json').write_text(json.dumps(fixture,separators=(',',':'))+'\n')
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted({Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/sample_stream.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/top40_cases.json','integer_opt/pick40_units/manifest.json','integer_opt/sample_weight_units/baseline.nl','integer_opt/sample_weight_units/manifest.json'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='local behavioural C protocol and replay shell pass; universal CEC and NAND/RTL await Actions',metrics=metrics(net),comb_metrics=metrics(comb),
        parts=dict(sorter_replay=metrics(scomb),replay_shell=metrics(sh),sampler=metrics(mcomb)),replay_shell_exhaustive=sc,
        binding=dict(state_order='sorter1664,sampler127',extra_state_bits=0,common_start='both cores idle/done and not reset',
            replay='sorter DONE and sampler replay and not reset/start -> phase read, index0; R109 graph unchanged inside'),
        contract='reset,start,sample,random32,input_valid,score32 -> token8,input_ready,busy,done; 192 logits in vocabulary order',
        C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),expected=proto,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        scope='logits->token selection only; logits stay an external input here (R110 scanner not yet attached); RNG external',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(net,comb,rows,parts);report['status']='universal joint CEC, full NAND/RTL/C picks pass; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources',)},indent=1)[:3000])

if __name__=='__main__':main()
