#!/usr/bin/env python3
"""Final norm[10] + A8 front end for the output head: x20 stream -> q8 stream + m.

The accepted R39 norm_stream constructor is reused with only its coefficient
table replaced by norm[10] (as norm_cache_fill.norm0 does for norm[0]); the
accepted quant_stream (n=128) is imported unchanged. A small restart shell lets
norm re-enter its normalize phase with the kept root, so the caller replays X
three times (squares, normalize for the maximum, normalize for conversion) and
no h vector is stored. Output is exactly C quant(norm(x,10)): q8 bytes and m.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_FINAL_A8_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_FINAL_A8_OUT',str(R/'build/integer_opt/final_a8')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from gate_check import verify
from export import import_net,rtl,MODEL_SHA
import norm_stream as norm
import quant_stream as quant
SN,SQ=512,169;NS=SN+SQ+1
NI,NO=24,39
sha=lambda b:hashlib.sha256(b).hexdigest()


def words(which):
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    return [int.from_bytes(blob[268692+2*(which*128+i):268694+2*(which*128+i)],'little') for i in range(128)]


def norm10(which=10):
    original=norm.lookup;calls=[]
    def lookup(values,width,method):
        assert values==words(1) and width==16 and method=='shannon';calls.append(True)
        return original(words(which),width,method)
    norm.lookup=lookup
    try:net,weights,_,_=norm.make()
    finally:norm.lookup=original
    assert len(calls)==1;return net,weights


def restart_shell(bad=False):
    """inputs np4, phase4, reset, start, restart -> np4 (11 in / 4 out): idle+restart -> 5.
    bad: re-enter the square phase 1 instead (an actual integration fault)."""
    b=Builder(11);np=list(range(2,6));ph=list(range(6,10));reset,start,rs=10,11,12
    idle=b.reduce([b.inv(v) for v in ph],b.land,1)
    go=b.reduce([b.inv(reset),b.inv(start),rs,idle],b.land,1)
    return b.finish([b.mux(go,x,v) for x,v in zip(np,[1,0,0,0] if bad else [1,0,1,0])])


def shell_ref(x):
    np=x&15;ph=x>>4&15;reset=x>>8&1;start=x>>9&1;rs=x>>10&1
    return 5 if (not reset and not start and rs and ph==0) else np


NO_N,NO_Q=55,41
SHELL_IN=NS+NI+(SN+NO_N)+(SQ+NO_Q);SHELL_OUT=NS+NO+24+33


def shell(ungated=False,bad_restart=False):
    """Glue only, with free child-output inputs. State: norm512, quant169, restarted1."""
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    nb=list(range(NS+NI+2,NS+NI+2+SN+NO_N));qb=list(range(NS+NI+2+SN+NO_N,SHELL_IN+2))
    qn=q[:SN];qq=q[SN:SN+SQ];done=q[SN+SQ];nd,no=nb[:SN],nb[SN:];qd,qo=qb[:SQ],qb[SQ:]
    reset,start=p[0],p[1];x=p[2:22];xvalid,qready=p[22],p[23]
    eq=lambda bits,v:b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    nph=qn[488:492];qph=qq[153:156];keep=b.inv(reset)
    nidle=eq(nph,0);qidle=eq(qph,0);qrecv=eq(qph,2)
    begin=b.reduce([keep,start,nidle,qidle] if not ungated else [keep,start],b.land,1)
    restart=b.reduce([keep,nidle,qrecv,b.inv(done)],b.land,1)
    qx_ready=b.land(keep,b.lor(eq(qph,1),qrecv))
    _,s=import_net(b,restart_shell(bad_restart),nd[488:492]+nph+[reset,begin,restart])
    n128=[128>>i&1 for i in range(9)]
    ni=[reset,begin]+x+[xvalid,qx_ready];qi=[reset,begin]+n128+no[0:20]+[no[53],qready]
    ndone=b.land(keep,b.mux(begin,b.lor(done,restart),0))
    outs=qo[0:8]+qo[17:37]+[qo[38],no[51]]+no[20:27]+[qo[40],b.lor(no[54],qo[39])]
    assert len(outs)==NO and len(ni)==24 and len(qi)==33
    return b.finish(nd[:488]+s+nd[492:]+qd+[ndone]+outs+ni+qi)


def connect(shell_net,normnet,quantnet):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2))
    _,first=import_net(b,shell_net,pins+[0]*(SN+NO_N+SQ+NO_Q));ni=first[NS+NO:NS+NO+24]
    nd,no=import_net(b,normnet,ni,pins[:SN])
    _,second=import_net(b,shell_net,pins+nd+no+[0]*(SQ+NO_Q));qi=second[NS+NO+24:]
    assert second[NS+NO:NS+NO+24]==ni
    qd,qo=import_net(b,quantnet,qi,pins[SN:SN+SQ]);assert qo[37]==ni[23]
    _,full=import_net(b,shell_net,pins+nd+no+qd+qo);assert full[NS+NO:]==ni+qi
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def reference():
    o=NS+NI;nb=o;qb=o+SN+NO_N
    return f'''module top(input [{SHELL_IN-1}:0] din,output [{SHELL_OUT-1}:0] dout);
wire [{SN-1}:0] qn=din[{SN-1}:0];wire [{SQ-1}:0] qq=din[{SN+SQ-1}:{SN}];wire done=din[{NS-1}];
wire [23:0] p=din[{NS+23}:{NS}];
wire [{SN-1}:0] nd=din[{nb+SN-1}:{nb}];wire [54:0] no=din[{nb+SN+54}:{nb+SN}];
wire [{SQ-1}:0] qd=din[{qb+SQ-1}:{qb}];wire [40:0] qo=din[{qb+SQ+40}:{qb+SQ}];
wire reset=p[0],start=p[1],xvalid=p[22],qready=p[23];wire [19:0] x=p[21:2];
wire [3:0] nph=qn[491:488];wire [2:0] qph=qq[155:153];
wire begin_op=!reset && start && nph==0 && qph==0;
wire restart=!reset && nph==0 && qph==2 && !done;
wire qxr=!reset && (qph==1 || qph==2);
wire go=!reset && !begin_op && restart && nph==0;
wire [3:0] np=go?4'd5:nd[491:488];
wire ndone=!reset && !begin_op && (done || restart);
wire [38:0] outs={{(no[54] || qo[39]),qo[40],no[26:20],no[51],qo[38],qo[36:17],qo[7:0]}};
wire [23:0] ni={{qxr,xvalid,x,begin_op,reset}};
wire [32:0] qi={{qready,no[53],no[19:0],9'd128,begin_op,reset}};
assign dout={{qi,ni,outs,ndone,qd,nd[{SN-1}:492],np,nd[487:0]}};
endmodule
'''


def golden_lib(tmp):
    so=Path(tmp)/'g.so';src=R/'integer_opt/final_a8_golden.c'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer'),str(src),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.final_a8.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8)];g.final_a8.restype=ct.c_int32
    g.int_run.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32)]
    return g


def cases():
    rng=random.Random(26100715);xs=[]
    xs += [[0]*128,[-524288]*128,[524287]*128,[(-524288,524287)[i%2] for i in range(128)],[0]*127+[-524288],[524287]+[0]*127,[1]*128,[-1]*128,list(range(-64,64))]
    xs += [[rng.randrange(-524288,524288)>>sh for _ in range(128)] for sh in (0,1,3,5,8,12,16)]
    with tempfile.TemporaryDirectory() as tmp:
        g=golden_lib(tmp);real=[]
        for length in (8,32):
            toks=[rng.randrange(192) for _ in range(length)];logits=(ct.c_int32*(length*192))();trace=(ct.c_int32*(6*length*128))()
            assert g.int_run((ct.c_int32*length)(*toks),length,logits,trace)==0
            for pos in sorted(rng.sample(range(length),min(8,length))):
                real.append(dict(tokens=toks,position=pos,x=list(trace[(5*length+pos)*128:(5*length+pos+1)*128])))
        out=[]
        for k,x in enumerate(xs+[r['x'] for r in real]):
            h=(ct.c_int32*128)();q=(ct.c_int8*128)();m=g.final_a8((ct.c_int32*128)(*x),h,q)
            out.append(dict(x=x,q=list(q),m=m,source='edge/random' if k<len(xs) else 'int_run final x'))
    return out


class Sim:
    def __init__(self,net):
        lib=ct.CDLL(str(OUT/'sim.so'));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
        raw=net.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;self.lib=lib;self.buf=ct.create_string_buffer((NO+7)//8)
    def step(self,x):
        self.lib.nl_step(x.to_bytes(3,'little'),self.buf);return int.from_bytes(self.buf.raw,'little')


def drive(net,cs,seed=26100716,aborts=True):
    """Reactively drive the actual graph; every accepted byte and m must equal C."""
    sim=Sim(net);rng=random.Random(seed);rows=[];stats=dict(completed=0,bytes=0,x_accepts=0,ignored_starts=0,aborts=0,x_stalls=0,q_stalls=0)
    def tick(x):
        y=sim.step(x);rows.append((x,y,(1<<NO)-1));return y
    tick(1);tick(0)
    def run(case,stall,abort_at=None):
        ptr=0;passes=0;got=[];before=len(rows)
        y=tick(2)
        while True:
            if abort_at is not None and len(rows)-before==abort_at:tick(1|2);tick(0);stats['aborts']+=1;return None
            xv=int(not stall or rng.randrange(4)!=0);qr=int(not stall or rng.randrange(3)!=0)
            # Spurious starts only while certainly busy (a start at DONE legally restarts).
            junk=int(len(got)<120 and len(rows)-before>2 and rng.randrange(50)==0);xin=case['x'][ptr]&0xfffff
            y=tick(junk<<1|xin<<2|xv<<22|qr<<23)
            busy=y>>38&1
            if junk and busy:stats['ignored_starts']+=1
            if y>>29&1:
                assert (y>>30&127)==ptr,(len(rows),ptr,y>>30&127)
                if xv:ptr+=1;stats['x_accepts']+=1
                else:stats['x_stalls']+=1
                if ptr==128:ptr=0;passes+=1
            if y>>28&1:
                if qr:got.append((y&255)-((y&128)<<1));stats['bytes']+=1
                else:stats['q_stalls']+=1
            if not busy and len(got)==128:break
            assert len(rows)-before<80000
        m=(y>>8)&0xfffff
        assert got==case['q'] and m==case['m'] and passes==3,(got[:4],case['q'][:4],m,case['m'],passes)
        stats['completed']+=1;tick(0);return len(rows)-before
    spans=[]
    for k,c in enumerate(cs):spans.append(dict(case=k,clocks=run(c,stall=k%3==2),stall=k%3==2))
    if aborts:
        for at in (5,3000,9000,15000,20000):run(cs[-1],False,abort_at=at)
        spans.append(dict(case=len(cs)-1,clocks=run(cs[-1],False),stall=False))
    return rows,dict(clocks=len(rows),**stats,no_stall_clocks=sorted({s['clocks'] for s in spans if not s['stall']})[:6])


def cloud(net,comb,cs):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    from ci import cec
    import sampler
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    rows,proto=drive(net,cs)
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    prefix=OUT/'proofs/parent';prefix.parent.mkdir(exist_ok=True);prefix.with_suffix('.ref.v').write_text(reference())
    ref=sampler.mapped_reference(prefix,SHELL_IN,SHELL_OUT);sh=shell()
    for kind,g in [('source',sh),('reference',ref),('negative',flip_output(sh)),('ungated',shell(True)),('bad_restart',shell(bad_restart=True))]:prefix.with_suffix('.'+kind+'.blif').write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc;proofs={}
    for kind,want in [('source','equivalent'),('negative','different'),('ungated','different'),('bad_restart','different')]:
        proofs[kind]=cec(abc,prefix.with_suffix('.'+kind+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+kind+'.log'));assert proofs[kind]['verdict']==want,kind
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    nn,_=norm10();qn=quant.make();faults={}
    for name,g in [('output_flip',flip_output(net)),('ungated_start',connect(shell(True),nn,qn)[0]),('restart_to_squares',connect(shell(bad_restart=True),nn,qn)[0]),('norm1_table',connect(shell(),norm.make()[0],qn)[0])]:
        faults[name]=checks.check_nand(rows,g.encode());assert faults[name]>0,name;(OUT/('bad_'+name+'.nl')).write_bytes(g.encode())
    bad=flip_output(net);(OUT/'bad.v').write_text(rtl(bad,'final_a8'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'final_a8',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'final_a8.v');checks.run([exe],600)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=600)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',protocol=proto,parent_all_input_bits=SHELL_IN,parent_all_output_bits=SHELL_OUT,proofs=proofs,reference_metrics=metrics(ref),
        clocks=len(rows),rtl_clocks=len(rows),nand_C_bytes=proto['bytes'],actual_fault_mismatches=faults,actual_rtl_fault_rejected=True),rows


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    n1,_,_,_=norm.make();qn=quant.make();nn,wt=norm10();sh=restart_shell()
    # Children bound to accepted R39 norm_stream (37447245278) and quant_stream (37427393412).
    assert metrics(n1)['sha256']=='dd7e4fa2a97a0242ca8ee2d91139fac07c1dd00387157b0f4add8cbe4dcc9cfd'
    assert metrics(qn)['sha256']=='0caf2626931329b14df86526aa4e294d020d7b4004a408c897045ce916e3550b'
    xs=list(range(1<<11));sc=verify(sh,xs,[shell_ref(v) for v in xs]);assert sc['status']=='pass'
    bad=restart_shell(True);from gate_check import simulate
    assert sum(u!=v for u,v in zip(simulate(bad,xs),[shell_ref(v) for v in xs]))>0
    wc=verify(wt,list(range(128)),words(10));assert wc['status']=='pass' and words(10)!=words(1)
    net,comb=connect(shell(),nn,qn)
    cs=cases();(OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    for k,g in dict(final_a8=net,transition=comb,shell=shell(),norm10=nn,quant=qn,restart_shell=sh).items():(OUT/(k+'.nl')).write_bytes(g.encode())
    (OUT/'final_a8.v').write_text(rtl(net,'final_a8'))
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted({Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/final_a8_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/sampler.py',
              'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl'):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='restart shell exhaustive, norm[10] table and C cases prepared; actual-graph C drive, parent CEC and RTL await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),parts=dict(norm10=metrics(nn),quant=metrics(qn),shell=metrics(shell()),restart_shell=metrics(sh)),
        restart_shell_exhaustive=sc,norm10_table=wc,C_cases=len(cs),real_final_x_cases=sum(c['source']!='edge/random' for c in cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        binding=dict(state_order='norm512,quant169,restarted1',children='R39 norm_stream constructor with only the norm[10] table swapped; R20 quant_stream (run37427393412) unchanged',
            schedule='X replayed 3x by caller: squares, normalize->max scan, normalize->RNE(h*127/m) bytes'),
        contract='reset,start,x20,xvalid,qready -> q8,m20,qvalid,xready,xindex7,mvalid,busy',
        scope='final norm[10]+A8 front end of the output head; X producer (residual store) remains outside',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification'],_=cloud(net,comb,cs);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='actual-graph C bytes/m, parent CEC and RTL pass; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources',)},indent=1)[:2500])


if __name__=='__main__':main()
