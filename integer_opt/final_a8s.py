#!/usr/bin/env python3
"""R117: R115 final norm[10] + A8 front end with ONE shared serial DIV.

The norm_stream and quant_stream constructors are restated as generators that
pause where they import the DIV. Run alone with their own DIV they rebuild the
accepted graphs byte for byte (checked), so the restatement is exact. Shared,
one DIV serves both: quant loads only when it takes a finished norm output (norm
is then not dividing), and norm's own load waits while quant's 27-step divide
runs. Numerics unchanged; only norm may wait a few clocks per element.
"""
from pathlib import Path
import argparse,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_FINAL_A8S_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_FINAL_A8S_OUT',str(R/'build/integer_opt/final_a8s')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from bench import lookup
from export import import_net,load_unit,rtl,MODEL_SHA
import final_a8 as fa
import norm_stream as norm
import quant_stream as quant
sha=lambda b:hashlib.sha256(b).hexdigest()


def div_unit():
    meta=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
    raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
    return Netlist.decode(raw,90,199)


def norm_gen(b,old,ins,weights,stall=None):
    """Body of norm_stream.make (state 512 incl. its DIV slot 192:307). Yields DIV inputs, receives (dd,do)."""
    mul=load_unit('serial_mul');sqrt=load_unit('serial_sqrt')
    ms=old[:192];ds=old[192:307];rs=old[307:405];total=old[405:451];root=old[451:475];index=old[475:482];count=old[482:488];phase=old[488:492];result=old[492:]
    reset,start=ins[:2];x=ins[2:22];xvalid,yready=ins[22:]
    pd,ix,cn,acts=control(b,phase,index,count,[reset,start,xvalid,yready],stall)
    begin,sq_take,norm_take,sq_end,root_end,mul_end,div_end,sqrt_load,div_load,ready,valid,busy,replay=acts
    _,weight=import_net(b,weights,index)
    mag=b.add([b.xor(v,x[-1]) for v in x],[0]*20,x[-1])[0]
    adjusted=b.add([b.xor(v,weight[-1]) for v in x+[x[-1]]],[0]*21,weight[-1])[0]
    wmag=b.add([b.xor(v,weight[-1]) for v in weight],[0]*16,weight[-1])[0]
    mx=[b.mux(norm_take,mag[j] if j<20 else 0,adjusted[j] if j<21 else adjusted[-1]) for j in range(64)]
    my=[b.mux(norm_take,mag[j] if j<20 else 0,wmag[j] if j<16 else 0) for j in range(64)]
    md,mo=import_net(b,mul,[b.lor(sq_take,norm_take)]+mx+my,ms)
    summed=b.add(total,mo[192:232]+[0]*6)[0]
    epsilon=[4295>>j&1 for j in range(48)]
    radicand=b.add([0]+total+[0],epsilon)[0]
    rd,ro=import_net(b,sqrt,[sqrt_load]+radicand,rs)
    dd,do=yield ([div_load]+[0]*28+mo[192:228]+root+[0],ds)
    clear=b.lor(reset,begin);keep=b.inv(reset)
    nxt=md+dd+rd
    nxt += [b.land(b.inv(clear),b.mux(sq_end,a,v)) for a,v in zip(total,summed)]
    nxt += [b.land(keep,b.mux(root_end,a,v)) for a,v in zip(root,ro[98:122])]
    nxt += ix+cn+pd
    nxt += [b.land(keep,b.mux(div_end,a,v)) for a,v in zip(result,do[179:199])]
    assert len(nxt)==512
    yield nxt,result+index+root+[ready,replay,valid,busy]


def control(b,phase,index,count,inputs,stall=None):
    """norm_stream.control; with stall, phase 7 (DIV load) waits while stall is high."""
    if stall is None:return norm.control(b,phase,index,count,inputs)
    reset,start,xvalid,yready=inputs
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    p=[eq(phase,j) for j in range(10)];keep=b.inv(reset);last=b.reduce(index,b.land,1)
    begin=b.reduce([keep,p[0],start],b.land,1)
    ready=b.land(keep,b.lor(p[1],p[5]));take=b.land(ready,xvalid)
    sq_take=b.land(take,p[1]);norm_take=b.land(take,p[5])
    sq_end=b.land(p[2],eq(count,20));root_end=b.land(p[4],eq(count,24))
    mul_end=b.land(p[6],eq(count,16));div_end=b.land(p[8],eq(count,38))
    valid=b.land(keep,p[9]);ack=b.land(valid,yready)
    go7=b.land(p[7],b.inv(stall))
    pn=phase[:]
    for enabled,target in ((begin,1),(sq_take,2),(sq_end,1),(b.land(sq_end,last),3),(p[3],4),
                           (root_end,5),(norm_take,6),(mul_end,7),(go7,8),(div_end,9),(ack,5),(b.land(ack,last),0)):
        pn=[b.mux(enabled,v,target>>j&1) for j,v in enumerate(pn)]
    running=b.reduce([p[2],p[4],p[6],p[8]],b.lor,0)
    cc=b.reduce([reset,begin,take,p[3],go7,sq_end,root_end,mul_end,div_end],b.lor,0)
    cn=[b.land(b.inv(cc),v) for v in b.add(count,[0]*6,running)[0]]
    ic=b.reduce([reset,begin,root_end],b.lor,0)
    advance=b.lor(sq_end,ack)
    ix=[b.land(b.inv(ic),v) for v in b.add(index,[0]*7,advance)[0]]
    return [b.land(keep,v) for v in pn],ix,cn,[begin,sq_take,norm_take,sq_end,root_end,mul_end,div_end,p[3],go7,ready,valid,b.inv(p[0]),b.reduce(p[5:],b.lor,0)]


def quant_gen(b,old,ins,shared=False):
    """Body of quant_stream.make; state = DIV115 (absent when shared) + 54 own bits."""
    if shared:ds=None;i=0
    else:ds=old[:115];i=115
    maximum=old[i:i+20];i+=20;length=old[i:i+9];i+=9;index=old[i:i+9];i+=9
    phase=old[i:i+3];i+=3;count=old[i:i+5];i+=5;result=old[i:i+8]
    reset,start=ins[:2];n=ins[2:11];x=ins[11:31];xvalid,yready=ins[31:]
    def eq(bits,value):
        return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    idle,scan,receive,run,send=[eq(phase,v) for v in range(5)]
    _,too_large=b.add(n,[(~337>>j)&1 for j in range(9)],1)
    legal=b.land(b.inv(eq(n,0)),b.inv(too_large));keep=b.inv(reset)
    begin=b.land(keep,b.land(idle,b.land(start,legal)))
    xready=b.land(keep,b.lor(scan,receive));yvalid=b.land(keep,send)
    take=b.land(xready,xvalid);scan_take=b.land(take,scan);load=b.land(take,receive)
    ack=b.land(yvalid,yready);finish=b.land(run,eq(count,27))
    next_index=b.add(index,[0]*9,1)[0]
    last=b.reduce([b.inv(b.xor(a,c)) for a,c in zip(next_index,length)],b.land,1)
    magnitude=b.add([b.xor(v,x[-1]) for v in x],[0]*20,x[-1])[0]
    _,larger=b.add(magnitude,[b.inv(v) for v in maximum],1)
    max_next=[b.mux(larger,a,v) for a,v in zip(maximum,magnitude)]
    numerator=b.add([0]*7+x,[b.inv(v) for v in x+[x[-1]]*7],1)[0]
    dd,do=yield ([load]+[0]*37+numerator+maximum+[0]*5,ds)
    phase_next=phase[:]
    for enable,value in ((begin,1),(b.land(scan_take,last),2),(load,3),(finish,4),(ack,2)):
        phase_next=[b.mux(enable,v,value>>j&1) for j,v in enumerate(phase_next)]
    clear=b.lor(reset,b.land(ack,last))
    index_clear=b.lor(reset,b.lor(begin,b.land(scan_take,last)))
    index_advance=b.lor(scan_take,b.land(ack,b.inv(last)))
    count_clear=b.lor(reset,b.lor(begin,b.lor(load,finish)))
    nxt=[] if shared else dd
    nxt += [b.land(keep,b.mux(begin,b.mux(scan_take,a,v),int(j==0))) for j,(a,v) in enumerate(zip(maximum,max_next))]
    nxt += [b.land(keep,b.mux(begin,a,v)) for a,v in zip(length,n)]
    nxt += [b.land(b.inv(index_clear),b.mux(index_advance,a,v)) for a,v in zip(index,next_index)]
    nxt += [b.land(b.inv(clear),v) for v in phase_next]
    nxt += [b.land(b.inv(count_clear),v) for v in b.add(count,[0]*5,run)[0]]
    nxt += [b.land(keep,b.mux(finish,a,v)) for a,v in zip(result,do[115:123])]
    replay=b.lor(receive,b.lor(run,send))
    yield nxt,result+index+maximum+[xready,yvalid,b.inv(idle),replay]


def drive_alone(gen,b,div):
    inputs,ds=next(gen);dd,do=import_net(b,div,inputs,ds);return gen.send((dd,do))


def norm_alone(which=10):
    weights=lookup(fa.words(which),16,'shannon');ns=512;b=Builder(ns+24)
    old=list(range(2,ns+2));ins=list(range(ns+2,ns+26))
    nxt,outs=drive_alone(norm_gen(b,old,ins,weights),b,div_unit())
    return with_state(b.finish(nxt+outs),ns)


def quant_alone():
    ns=169;b=Builder(ns+33);old=list(range(2,ns+2));ins=list(range(ns+2,ns+35))
    nxt,outs=drive_alone(quant_gen(b,old,ins),b,div_unit())
    return with_state(b.finish(nxt+outs),ns)


SN,SQ=512,54;NS=SN+SQ+1
NI,NO=fa.NI,fa.NO


def connect(fault=None):
    """state: norm512 (its DIV slot 192:307 is the shared DIV), quant54, restarted1."""
    b=Builder(NS+NI);qn=list(range(2,SN+2));qq=list(range(SN+2,SN+SQ+2));done=SN+SQ+2;p=list(range(NS+2,NS+NI+2))
    reset,start=p[0],p[1];x=p[2:22];xvalid,qready=p[22],p[23]
    eq=lambda bits,v:b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    nph=qn[488:492];qph=qq[38:41];keep=b.inv(reset)
    nidle=eq(nph,0);qidle=eq(qph,0);qrecv=eq(qph,2);qrun=eq(qph,3)
    begin=b.reduce([keep,start,nidle,qidle],b.land,1)
    restart=b.reduce([keep,nidle,qrecv,b.inv(done)],b.land,1)
    qx_ready=b.land(keep,b.lor(eq(qph,1),qrecv))
    nvalid=b.land(keep,eq(nph,9))
    weights=lookup(fa.words(10),16,'shannon')
    ng=norm_gen(b,qn,[reset,begin]+x+[xvalid,qx_ready],weights,stall=0 if fault=='no_stall' else qrun)
    n_in,nds=next(ng)
    n128=[128>>i&1 for i in range(9)]
    qg=quant_gen(b,qq,[reset,begin]+n128+qn[492:512]+[nvalid,qready],shared=True)
    q_in,_=next(qg)
    qload=q_in[0]
    shared=[b.lor(n_in[0],qload)]+[b.mux(qload,u,v) for u,v in zip(n_in[1:],q_in[1:])]
    if fault=='norm_operands':shared=[shared[0]]+n_in[1:]
    dd,do=import_net(b,div_unit(),shared,nds)
    nxt_n,out_n=ng.send((dd,do));nxt_q,out_q=qg.send((None,do))
    _,s=import_net(b,fa.restart_shell(),nxt_n[488:492]+nph+[reset,begin,restart])
    nxt_n=nxt_n[:488]+s+nxt_n[492:]
    ndone=b.land(keep,b.mux(begin,b.lor(done,restart),0))
    outs=out_q[0:8]+out_q[17:37]+[out_q[38],out_n[51]]+out_n[20:27]+[out_q[40],b.lor(out_n[54],out_q[39])]
    assert len(outs)==NO
    comb=b.finish(nxt_n+nxt_q+[ndone]+outs);return with_state(comb,NS),comb


def cloud(net,cs):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    fa.OUT=OUT
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    rows,proto=fa.drive(net,cs)
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO;faults={}
    for name,g in [('output_flip',flip_output(net)),('no_divider_wait',connect('no_stall')[0]),('divider_ignores_quant',connect('norm_operands')[0])]:
        faults[name]=checks.check_nand(rows,g.encode());assert faults[name]>0,name;(OUT/('bad_'+name+'.nl')).write_bytes(g.encode())
    bad=flip_output(net);(OUT/'bad.v').write_text(rtl(bad,'final_a8s'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'final_a8s',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'final_a8s.v');checks.run([exe],600)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=600)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',protocol=proto,clocks=len(rows),rtl_clocks=len(rows),actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True)
    assert norm_alone().encode()==fa.norm10()[0].encode(),'norm restatement differs'
    assert norm_alone(1).encode()==norm.make()[0].encode(),'norm1 restatement differs'
    assert quant_alone().encode()==quant.make().encode(),'quant restatement differs'
    print('restatements byte-identical')
    t0=time.monotonic();net,comb=connect();old=fa.connect(fa.shell(),fa.norm10()[0],fa.quant.make())[0]
    assert metrics(old)['sha256']==json.loads((R/'integer_opt/x_head_units/manifest.json').read_text())['R115_final_a8_sha256']
    cs=fa.cases();(OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    for k,g in dict(final_a8s=net,transition=comb).items():(OUT/(k+'.nl')).write_bytes(g.encode())
    (OUT/'final_a8s.v').write_text(rtl(net,'final_a8s'))
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted({Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/final_a8_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/x_head_units/manifest.json',
              'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl'):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='unshared restatements byte-identical to accepted norm[1]/norm[10]/quant graphs; shared graph C drive and RTL await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),R115=metrics(old),saved=dict(nand=metrics(old)['nNand']-metrics(net)['nNand'],latch=metrics(old)['nLatch']-metrics(net)['nLatch']),
        restatement='norm_gen/quant_gen run alone rebuild norm_stream (norm[1], R39), the R115 norm[10] and quant_stream (R20) byte for byte',
        sharing='quant loads only while norm presents a finished result; norm phase 7 waits while quant runs (27 steps); DIV state lives in norm slot 192:307',
        C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),contract='same as R115 final_a8',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(net,cs);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='shared-DIV actual graph equals C on every byte/m; RTL and actual faults pass'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources',)},indent=1)[:1500])


if __name__=='__main__':main()
