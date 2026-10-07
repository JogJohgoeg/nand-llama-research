#!/usr/bin/env python3
"""Final residual x -> token: R115 final norm[10]/A8 front end + R114 output head.

The two accepted graphs are reconnected unchanged through a parent shell that is
proved alone. The head starts exactly when the front end publishes m (the same
condition that restarts norm for the conversion pass), so no state is added.
The caller replays X three times and holds sample/random for the transaction.
The token equals C int_pick on int_run's own logits for that position.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_X_HEAD_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_X_HEAD_OUT',str(R/'build/integer_opt/x_head')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from export import import_net,rtl,MODEL_SHA
import final_a8 as fa,vocab_pick as vp
SF,SV=fa.NS,vp.NS;NS=SF+SV
NI,NO=56,19
FO,VO=fa.NO,vp.NO
SHELL_IN=NS+NI+(SF+FO)+(SV+VO);SHELL_OUT=NS+NO+fa.NI+vp.NI
sha=lambda b:hashlib.sha256(b).hexdigest()


def shell(ungated=False,early=False):
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    f=list(range(NS+NI+2,NS+NI+2+SF+FO));v=list(range(NS+NI+2+SF+FO,SHELL_IN+2))
    qf=q[:SF];fd,fo=f[:SF],f[SF:];vd,vo=v[:SV],v[SV:]
    reset,start=p[0],p[1];x=p[2:22];xvalid,smp=p[22],p[23];rnd=p[24:56]
    eq=lambda bits,k:b.reduce([w if k>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    keep=b.inv(reset);nph=qf[488:492];qph=qf[fa.SN+153:fa.SN+156];restarted=qf[fa.SN+fa.SQ]
    fidle=b.land(eq(nph,0),eq(qph,0));vdone=b.lor(b.inv(vo[8]),vo[10])  # head not busy, or done
    begin=b.reduce([keep,start,fidle]+([] if ungated else [vdone]),b.land,1)
    # m is final exactly when norm is idle and quant waits for the replay (first time).
    # early (fault): start the head together with the front end, before m exists.
    mready=begin if early else b.reduce([keep,eq(nph,0),eq(qph,2),b.inv(restarted)],b.land,1)
    fi=[reset,begin]+x+[xvalid,vo[9]]
    vi=[reset,mready]+fo[8:28]+[fo[28]]+fo[0:8]+[smp]+rnd
    # done belongs to this transaction: the head's done only counts once the front end is idle again.
    outs=vo[0:8]+[b.lor(fo[38],vo[8]),b.land(vo[10],fidle),fo[29]]+fo[30:37]+[fo[37]]
    assert len(fi)==fa.NI and len(vi)==vp.NI and len(outs)==NO
    return b.finish(fd+vd+outs+fi+vi)


def connect(shell_net,front,head):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2));qf,qv=pins[:SF],pins[SF:NS]
    _,s1=import_net(b,shell_net,pins+[0]*(SF+FO+SV+VO));vi0=s1[NS+NO+fa.NI:]
    _,v0=import_net(b,head,vi0,qv);ready=v0[9]           # state/reset-only output
    _,s2=import_net(b,shell_net,pins+[0]*(SF+FO)+[0]*SV+v0);fi=s2[NS+NO:NS+NO+fa.NI]
    fd,fo=import_net(b,front,fi,qf)
    _,s3=import_net(b,shell_net,pins+fd+fo+[0]*SV+v0);vi=s3[NS+NO+fa.NI:];assert s3[NS+NO:NS+NO+fa.NI]==fi
    vd,vo=import_net(b,head,vi,qv);assert vo[9]==ready
    _,full=import_net(b,shell_net,pins+fd+fo+vd+vo);assert full[NS+NO:]==fi+vi
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def reference():
    o=NS+NI;f0=o;v0=o+SF+FO
    return f'''module top(input [{SHELL_IN-1}:0] din,output [{SHELL_OUT-1}:0] dout);
wire [{SF-1}:0] qf=din[{SF-1}:0];wire [55:0] p=din[{NS+55}:{NS}];
wire [{SF-1}:0] fd=din[{f0+SF-1}:{f0}];wire [{FO-1}:0] fo=din[{f0+SF+FO-1}:{f0+SF}];
wire [{SV-1}:0] vd=din[{v0+SV-1}:{v0}];wire [{VO-1}:0] vo=din[{v0+SV+VO-1}:{v0+SV}];
wire reset=p[0],start=p[1],xvalid=p[22],smp=p[23];wire [19:0] x=p[21:2];wire [31:0] rnd=p[55:24];
wire [3:0] nph=qf[491:488];wire [2:0] qph=qf[{fa.SN+155}:{fa.SN+153}];wire restarted=qf[{fa.SN+fa.SQ}];
wire begin_op=!reset && start && nph==0 && qph==0 && (!vo[8] || vo[10]);
wire mready=!reset && nph==0 && qph==2 && !restarted;
wire [23:0] fi={{vo[9],xvalid,x,begin_op,reset}};
wire [63:0] vi={{rnd,smp,fo[7:0],fo[28],fo[27:8],mready,reset}};
wire [18:0] outs={{fo[37],fo[36:30],fo[29],(vo[10] && nph==0 && qph==0),(fo[38] || vo[8]),vo[7:0]}};
assign dout={{vi,fi,outs,vd,fd}};
endmodule
'''


def cases():
    """Real int_run final-x rows with their own logits; tokens from C int_pick."""
    rng=random.Random(26100716);out=[]
    with tempfile.TemporaryDirectory() as tmp:
        g=fa.golden_lib(tmp);g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
        for length in (1,5,16,32):
            toks=[rng.randrange(192) for _ in range(length)];logits=(ct.c_int32*(length*192))();trace=(ct.c_int32*(6*length*128))()
            assert g.int_run((ct.c_int32*length)(*toks),length,logits,trace)==0
            for pos in sorted(rng.sample(range(length),min(3,length))):
                lg=list(logits[pos*192:(pos+1)*192]);x=list(trace[(5*length+pos)*128:(5*length+pos+1)*128])
                order=sorted(range(192),key=lambda i:(-lg[i],i))[:40]
                import sampler;ws=[sampler.weight(lg[order[0]],lg[i]) for i in order];T=sum(ws)
                for r,s in [(rng.getrandbits(32),1),(-(-(sum(ws[:rng.randrange(1,40)])<<32)//T)&0xffffffff,1),(rng.getrandbits(32),0)]:
                    out.append(dict(tokens=toks,position=pos,x=x,random=r,sample=s,token=g.int_pick((ct.c_int32*192)(*lg),r,s)))
    return out


def drive(net,cs,seed=26100717):
    sim=fa.Sim.__new__(fa.Sim);lib=ct.CDLL(str(OUT/'sim.so'));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    raw=net.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;buf=ct.create_string_buffer((NO+7)//8)
    rng=random.Random(seed);rows=[];stats=dict(completed=0,x_accepts=0,ignored_starts=0,aborts=0)
    def tick(x):
        lib.nl_step(x.to_bytes(7,'little'),buf);y=int.from_bytes(buf.raw,'little');rows.append((x,y,(1<<NO)-1));return y
    tick(1);tick(0)
    def run(c,stall,abort_at=None):
        ptr=0;before=len(rows);hold=(c['sample']<<23)|(c['random']<<24)
        tick(2|hold)
        while True:
            el=len(rows)-before
            if abort_at is not None and el==abort_at:tick(1|2);tick(0);stats['aborts']+=1;return None
            xv=int(not stall or rng.randrange(4)!=0);junk=int(5<el and rng.randrange(97)==0)
            y=tick(junk<<1|(c['x'][ptr]&0xfffff)<<2|xv<<22|hold)
            if y>>9&1:break
            if junk:stats['ignored_starts']+=1
            if y>>10&1:
                assert (y>>11&127)==ptr
                if xv:ptr=(ptr+1)&127;stats['x_accepts']+=1
            assert el<200000
        assert y&255==c['token'],(y&255,c['token']);stats['completed']+=1;tick(hold);return len(rows)-before
    spans=[dict(clocks=run(c,stall=k%4==3),stall=k%4==3,sample=c['sample']) for k,c in enumerate(cs)]
    for at in (100,9000,30000,60000):run(cs[0],False,abort_at=at)
    spans.append(dict(clocks=run(cs[0],False),stall=False,sample=cs[0]['sample']))
    return rows,dict(clocks=len(rows),**stats,no_stall_sample_clocks=sorted({s['clocks'] for s in spans if not s['stall'] and s['sample']}),
        no_stall_greedy_clocks=sorted({s['clocks'] for s in spans if not s['stall'] and not s['sample']}))


def cloud(nets,cs):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks,sampler
    from ci import cec
    net=nets['joint']
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    rows,proto=drive(net,cs);(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    prefix=OUT/'proofs/parent';prefix.parent.mkdir(exist_ok=True);prefix.with_suffix('.ref.v').write_text(reference())
    ref=sampler.mapped_reference(prefix,SHELL_IN,SHELL_OUT);sh=nets['shell']
    for kind,g in [('source',sh),('reference',ref),('negative',flip_output(sh)),('ungated',shell(True)),('early',shell(early=True))]:prefix.with_suffix('.'+kind+'.blif').write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc;proofs={}
    for kind,want in [('source','equivalent'),('negative','different'),('ungated','different'),('early','different')]:
        proofs[kind]=cec(abc,prefix.with_suffix('.'+kind+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+kind+'.log'));assert proofs[kind]['verdict']==want,kind
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO;faults={}
    for name,g in [('output_flip',flip_output(net)),('early_head_start',connect(shell(early=True),nets['front'],nets['head'])[0])]:
        faults[name]=checks.check_nand(rows,g.encode());assert faults[name]>0,name;(OUT/('bad_'+name+'.nl')).write_bytes(g.encode())
    bad=flip_output(net);(OUT/'bad.v').write_text(rtl(bad,'x_head'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'x_head',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'x_head.v');checks.run([exe],1800)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=1800)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',protocol=proto,parent_all_input_bits=SHELL_IN,parent_all_output_bits=SHELL_OUT,proofs=proofs,reference_metrics=metrics(ref),
        clocks=len(rows),rtl_clocks=len(rows),actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    nn,_=fa.norm10();front=fa.connect(fa.shell(),nn,fa.quant.make())[0]
    scanner,_,_=vp.scanner_net();head=vp.connect(vp.shell(),scanner,vp.pick_net())[0]
    units=json.loads((R/'integer_opt/x_head_units/manifest.json').read_text())
    assert metrics(front)['sha256']==units['R115_final_a8_sha256'] and metrics(head)['sha256']==units['R114_vocab_pick_sha256']
    sh=shell();net,comb=connect(sh,front,head)
    print('x_head',metrics(net),round(time.monotonic()-t0,1),flush=True)
    cs=cases();(OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    nets=dict(joint=net,transition=comb,shell=sh,front=front,head=head)
    for k,g in nets.items():(OUT/(k+'.nl')).write_bytes(g.encode())
    (OUT/'x_head.v').write_text(rtl(net,'x_head'))
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted({Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/final_a8_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/sample_stream.c','integer_opt/top40_cases.json',
              'integer_opt/x_head_units/manifest.json','integer_opt/vocab_pick_units/manifest.json','integer_opt/vocab_pick_units/cases.json','integer_opt/pick40_units/manifest.json',
              'integer_opt/vocab_row_units/manifest.json','integer_opt/vocab_row_units/head_mac.nl','integer_opt/sample_weight_units/baseline.nl','integer_opt/sample_weight_units/manifest.json',
              'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl'):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='children bound and int_run C cases prepared; actual-graph drive, parent CEC and RTL await Actions',metrics=metrics(net),
        nets={k:metrics(g) for k,g in nets.items()},C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        binding=dict(state_order='front682,head3211',extra_state_bits=0,head_start='front norm idle, quant waiting for replay, not restarted (m final)',
            children='R115 final_a8 and R114 vocab_pick graphs unchanged (SHA bound in x_head_units/manifest.json)'),
        contract='reset,start,x20,xvalid,sample,random32 (held) -> token8,busy,done,xready,xindex7,mvalid',
        scope='final residual x to sampled token; residual store/transformer controller outside; RNG external',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(nets,cs);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='actual-graph int_run tokens, parent CEC and RTL pass; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources',)},indent=1)[:2000])


if __name__=='__main__':main()
