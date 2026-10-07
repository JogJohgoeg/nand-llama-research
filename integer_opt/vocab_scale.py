#!/usr/bin/env python3
"""Exact tied-head scaling with one serial MUL and one serial DIV.

R(R(dot*m,127)*g,2^24), not a merged rounding. Local work constructs the
full graph and checks only small components; Actions proves every D/output
against independent transition RTL and replays all C protocol vectors.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_VOCAB_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output,from_yosys
from golden import Netlist
from export import import_net,load_unit,rtl,MODEL_SHA
from gate_check import verify
from scale_bounded_check import check as check_unit
from weight_order import C_SOURCES
from ci import cec
OUT=Path(os.environ.get('H3_VOCAB_OUT',str(R/'build/integer_opt/vocab_scale')))
NI,NO,NS,LATENCY=62,34,430,86
MASK64=(1<<64)-1
sha=lambda b:hashlib.sha256(b).hexdigest()


def rne(n,d):
    q,r=divmod(abs(n),d);q+=2*r>d or (2*r==d and q&1)
    return -q if n<0 else q


def tail():
    b=Builder(64);n=list(range(2,66))
    sticky=b.reduce(n[:23],b.lor,0)
    increment=b.land(n[23],b.lor(sticky,n[24]))
    return b.finish(b.add(n[24:56],[0]*32,increment)[0])


def control():
    b=Builder(11);p=list(range(2,13));count=p[:6];phase=p[6:8];valid,reset,start=p[8:]
    def eq(bits,value):return b.reduce([x if value>>i&1 else b.inv(x) for i,x in enumerate(bits)],b.land,1)
    def AND(*xs):return b.reduce(xs,b.land,1)
    keep=b.inv(reset);busy=b.lor(*phase);begin=AND(keep,start,b.inv(busy))
    first=AND(eq(phase,1),eq(count,20));load_div=AND(eq(phase,2),eq(count,0))
    second=AND(eq(phase,2),eq(count,43));load_mul=AND(eq(phase,3),eq(count,0))
    finish=AND(eq(phase,3),eq(count,19))
    clear=b.reduce([reset,begin,first,second,finish],b.lor,0)
    cn=[AND(b.inv(clear),v) for v in b.add(count,[0]*6,busy)[0]]
    pn=phase[:]
    for enable,value in ((begin,1),(first,2),(second,3),(finish,0)):
        pn=[b.mux(enable,p,value>>i&1) for i,p in enumerate(pn)]
    pn=[AND(keep,p) for p in pn]
    vn=AND(keep,b.inv(begin),b.lor(valid,finish))
    return b.finish(cn+pn+[vn,begin,first,load_div,second,load_mul,finish,busy])


def control_ref(x):
    count=x&63;phase=x>>6&3;valid=x>>8&1;reset=x>>9&1;start=x>>10&1
    busy=int(phase!=0);begin=int(not reset and start and not busy)
    first=int(phase==1 and count==20);ld=int(phase==2 and count==0)
    second=int(phase==2 and count==43);lm=int(phase==3 and count==0);finish=int(phase==3 and count==19)
    c=0 if reset or begin or first or second or finish else (count+busy)&63
    p=0 if reset or finish else 3 if second else 2 if first else 1 if begin else phase
    v=int(not reset and not begin and (valid or finish))
    return c+(p<<6)+(v<<8)+(begin<<9)+(first<<10)+(ld<<11)+(second<<12)+(lm<<13)+(finish<<14)+(busy<<15)


def divider():
    d=R/'integer_opt/pilot_units';meta=json.loads((d/'manifest.json').read_text())['serial_div'];raw=(d/'serial_div.nl').read_bytes()
    assert sha(raw)==meta['sha256'];return Netlist.decode(raw,meta['nIn'],meta['nOut'])


def baseline():
    directory=Path(__file__).with_name('vocab_units')
    meta=json.loads((directory/'manifest.json').read_text());raw=(directory/'head_scale.nl').read_bytes()
    assert sha(raw)==meta['metrics']['sha256'] and meta['golden_sha256']['int_model.c']==C_SOURCES['integer/int_model.c']
    net=Netlist.decode(raw,60,32);assert metrics(net)==meta['metrics'];return net


def make():
    mul=load_unit('serial_mul');div=divider();assert mul.n_state==192 and div.n_state==115
    b=Builder(NS+NI);q=list(range(2,2+NS));p=list(range(2+NS,2+NS+NI))
    ms=q[:192];ds=q[192:307];temp=q[307:371];factor=q[371:389]
    count=q[389:395];phase=q[395:397];result=q[397:429];valid=q[429]
    reset,start=p[:2];dot=p[2:24];maximum=p[24:44];scale=p[44:62];keep=b.inv(reset)
    _,ctl=import_net(b,control(),count+phase+[valid,reset,start])
    begin,first,ld,second,lm,finish,busy=ctl[9:]
    mx=[b.mux(begin,t,dot[i] if i<22 else dot[-1]) for i,t in enumerate(temp)]
    my=[b.mux(begin,factor[i] if i<18 else 0,maximum[i] if i<20 else 0) for i in range(64)]
    md,mo=import_net(b,mul,[b.lor(begin,lm)]+mx+my,ms)
    dd,do=import_net(b,div,[ld]+[0]*22+temp[:42]+[int(i<7) for i in range(25)],ds)
    product=mo[192:];quotient=do[115:179];assert len(product)==len(quotient)==64
    _,rounded=import_net(b,tail(),product)
    td=[b.mux(first,t,v) for t,v in zip(temp,product)]
    td=[b.land(keep,b.mux(second,t,v)) for t,v in zip(td,quotient)]
    fd=[b.land(keep,b.mux(begin,f,g)) for f,g in zip(factor,scale)]
    rd=[b.land(keep,b.mux(finish,r,v)) for r,v in zip(result,rounded)]
    nxt=md+dd+td+fd+ctl[:8]+rd+[ctl[8]];assert len(nxt)==NS
    comb=b.finish(nxt+result+[valid,busy]);return with_state(comb,NS),comb


def reference():
    return '''module top(input [491:0] din,output [463:0] dout);
wire [429:0] q=din[429:0];wire [61:0] p=din[491:430];
wire [63:0] acc=q[63:0],a=q[127:64],y=q[191:128];
wire [63:0] A=q[255:192];wire [24:0] rem=q[280:256],den=q[305:281];wire signbit=q[306];
wire [63:0] temp=q[370:307];wire [17:0] factor=q[388:371];
wire [5:0] count=q[394:389];wire [1:0] phase=q[396:395];wire [31:0] result=q[428:397];wire valid=q[429];
wire reset=p[0],start=p[1];wire signed [21:0] dot=p[23:2];wire [19:0] maximum=p[43:24];wire [17:0] scale=p[61:44];
wire busy=phase!=0,begin_op=!reset && start && !busy;
wire first=phase==1 && count==20,ld=phase==2 && count==0;
wire second=phase==2 && count==43,lm=phase==3 && count==0,finish_op=phase==3 && count==19;
wire mul_load=begin_op || lm;
wire [63:0] mx=begin_op?{{42{dot[21]}},dot}:temp,my=begin_op?{44'b0,maximum}:{46'b0,factor};
wire [63:0] next_acc=mul_load?64'b0:acc+(y[0]?a:64'b0);
wire [63:0] next_a=mul_load?mx:(a<<1),next_y=mul_load?my:(y>>1);
wire [63:0] numerator={temp[41:0],22'b0};wire [63:0] magnitude=numerator[63]?-numerator:numerator;
wire [25:0] shifted_rem={rem,A[63]};wire ge=shifted_rem>={1'b0,den};
wire [25:0] subtracted=shifted_rem-{1'b0,den};
wire [24:0] next_rem=ld?25'b0:ge?subtracted[24:0]:shifted_rem[24:0];
wire [63:0] next_A=ld?magnitude:{A[62:0],ge};
wire [24:0] next_den=ld?25'd127:den;wire next_sign=ld?numerator[63]:signbit;
wire inc={rem,A[0]}>{1'b0,den};
wire [63:0] quotient=(A^{64{signbit}})+{63'b0,(inc^signbit)};
wire signed [63:0] signed_product=acc;
wire signed [63:0] rounded=(signed_product>>>24)+((acc[23] && ((|acc[22:0]) || acc[24]))?64'sd1:64'sd0);
reg [5:0] nc;reg [1:0] np;reg nv;
always @* begin
 nc=count+(busy?6'd1:6'd0);np=phase;nv=valid;
 if(begin_op)begin nc=0;np=1;nv=0;end
 if(first)begin nc=0;np=2;end
 if(second)begin nc=0;np=3;end
 if(finish_op)begin nc=0;np=0;nv=1;end
 if(reset)begin nc=0;np=0;nv=0;end
end
wire [63:0] nt=reset?64'b0:second?quotient:first?acc:temp;
wire [17:0] nf=reset?18'b0:begin_op?scale:factor;
wire [31:0] nr=reset?32'b0:finish_op?rounded[31:0]:result;
assign dout={busy,valid,result,nv,nr,np,nc,nf,nt,next_sign,next_den,next_rem,next_A,next_y,next_a,next_acc};
endmodule
'''


def cases():
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    for name,h in C_SOURCES.items():assert sha((R/name).read_bytes())==h
    so=OUT/'golden.so';source=Path(__file__).with_name('vocab_scale_golden.c')
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer_opt'),str(source),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.vocab_scale_expected.argtypes=[ct.c_int32,ct.c_uint32,ct.c_uint32];g.vocab_scale_expected.restype=ct.c_int64
    g.vocab_rows.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int64)]
    rng=random.Random(261002);tests=[]
    for a in (-(1<<21),-1,0,1,(1<<21)-1):
        for m in (0,1,(1<<20)-1):
            for s in (0,1,(1<<18)-1):tests.append((a,m,s))
    tests += [(127*64*k,1,1<<17) for k in range(-31,32,2)]
    tests += [(sign*(127*q+delta),1,1<<17) for sign in (-1,1) for q,delta in ((64,1),(192,-1))]
    tests += [(k,1,1<<17) for k in (-191,-190,-64,-63,63,64,190,191)]
    tests += [(rng.randrange(-(1<<21),1<<21),rng.randrange(1<<20),rng.randrange(1<<18)) for _ in range(256)]
    real=[]
    inputs=[[0]*128,[524287 if i&1 else -524288 for i in range(128)],
            [rng.randrange(-4096,4097) for _ in range(128)],[rng.randrange(-524288,524288) for _ in range(128)]]
    for index,x in enumerate(inputs):
        out=(ct.c_int64*(192*4))();assert g.vocab_rows((ct.c_int32*128)(*x),out)==0
        rows=[tuple(int(v) for v in out[4*j:4*j+4]) for j in range(192)]
        real.append(dict(input=x,rows=rows));tests += [r[:3] for r in rows]
    expected=[int(g.vocab_scale_expected(*t)) for t in tests]
    assert expected==[rne(rne(a*m,127)*s,1<<24) for a,m,s in tests]
    for block in real:
        assert [row[3] for row in block['rows']]==[rne(rne(a*m,127)*s,1<<24) for a,m,s,_ in block['rows']]
    fused=sum(y!=rne(a*m*s,127*(1<<24)) for (a,m,s),y in zip(tests,expected));assert fused>0
    result=dict(tests=tests,expected=expected,true_weight_rows=real,merged_rounding_mismatches=fused,
                scope='full input endpoints, RNE half ties, random operands; all192 frozen E8 rows on four deterministic norm10 inputs, not full prompt inference')
    (OUT/'cases.json').write_text(json.dumps(result,indent=2)+'\n');return result


def vectors(c):
    rows=[];remaining=0;last=valid=pending=0;completed=aborts=ignored=0;rng=random.Random(261003)
    def tick(a=0,m=0,s=0,start=0,reset=0,mask=(1<<NO)-1):
        nonlocal remaining,last,valid,pending,completed,aborts,ignored
        x=reset+(start<<1)+((a&((1<<22)-1))<<2)+(m<<24)+(s<<44)
        y=(last&0xffffffff)+(valid<<32)+(int(remaining>0)<<33);rows.append((x,y,mask))
        if reset:aborts+=int(remaining>0);remaining=0;last=valid=0
        elif start and not remaining:remaining=LATENCY-1;valid=0;pending=rne(rne(a*m,127)*s,1<<24)
        elif remaining:
            ignored+=int(bool(start));remaining-=1
            if not remaining:last=pending;valid=1;completed+=1
    def noise():tick(rng.randrange(-(1<<21),1<<21),rng.randrange(1<<20),rng.randrange(1<<18),start=int(rng.randrange(5)==0))
    tick(reset=1,mask=0)
    for t,want in zip(c['tests'],c['expected']):
        tick(*t,start=1)
        for _ in range(LATENCY-1):noise()
        assert not remaining and valid and last==want
        for _ in range(2):tick()
    for elapsed in (1,20,21,22,63,64,65,66,84):
        tick(-123456,12345,67890,start=1)
        for _ in range(elapsed-1):noise()
        tick(reset=1,start=1);tick()
    tick(-8128,1,131072,start=1)
    for _ in range(LATENCY-1):noise()
    tick();assert valid and last==0
    return rows,dict(clocks=len(rows),latency_clocks=LATENCY,completed=completed,aborts=aborts,busy_starts_ignored=ignored)


def small(c):
    ctl=control();t=tail();assert len(ctl.records)<=4000 and len(t.records)<=4000
    cv=verify(ctl,list(range(2048)),[control_ref(x) for x in range(2048)]);assert cv['status']=='pass'
    rng=random.Random(261004);values=[0,-1,1,-(1<<63)+1,(1<<63)-1]
    values += [(k<<24)+delta for k in range(-96,97) for delta in (-(1<<23)-1,-(1<<23),-(1<<23)+1,(1<<23)-1,1<<23,(1<<23)+1)]
    values += [rng.randrange(-(1<<63)+1,1<<63) for _ in range(256)]
    tv=verify(t,[v&MASK64 for v in values],[rne(v,1<<24)&0xffffffff for v in values]);assert tv['status']=='pass'
    tests=c['tests'][:149];mul=load_unit('serial_mul');div=divider();assert len(mul.records)<=4000 and len(div.records)<=4000
    parts={}
    for name,xy,steps in [('dot_max',[(a,m) for a,m,s in tests],20),('scale',[(rne(a*m,127),s) for a,m,s in tests],18)]:
        parts[name]=check_unit(mul,[1+((x&MASK64)<<1)+(y<<65) for x,y in xy],steps,[(x*y)&MASK64 for x,y in xy],mul.n_state,64)
    nums=[a*m for a,m,s in tests];assert all(-(1<<63)<n*(1<<22)<1<<63 for n in nums)
    parts['divide127']=check_unit(div,[1+(((n<<22)&MASK64)<<1)+(127<<65) for n in nums],42,[rne(n,127)&MASK64 for n in nums],div.n_state,64)
    return dict(control=cv,round_tail=tv,arithmetic=parts,mode='actual <=4k records only; complete pipeline awaits Actions')


def cloud(net,comb,rows,c):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    old=baseline();inputs=[(a&((1<<22)-1))+(m<<22)+(s<<42) for a,m,s in c['tests']]
    before=verify(old,inputs,[y&0xffffffff for y in c['expected']]);assert before['status']=='pass'
    ys=OUT/'transition.ys';ys.write_text(f'read_verilog {OUT}/transition.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/transition.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'transition.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    ref=from_yosys(json.loads((OUT/'transition.ref.json').read_text()),NS+NI,NS+NO)
    for name,g in [('source',comb),('reference',ref),('negative',flip_output(comb))]:(OUT/(name+'.blif')).write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    good=cec(abc,OUT/'source.blif',OUT/'reference.blif',OUT/'transition.cec.log');assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'transition.negative.log');assert bad['verdict']=='different'
    proof=dict(status='pass',all_state_bits=NS,all_outputs=NO,arbitrary_transition_cec=good,actual_D_fault=bad)
    (OUT/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    mutant=flip_output(net);wrong=checks.check_nand(rows,mutant.encode());assert wrong>0
    valid_rows=[(x,y,(m&0xffffffff) if y>>32&1 else 0) for x,y,m in rows]
    valid_wrong=checks.check_nand(valid_rows,mutant.encode());assert valid_wrong>0
    (OUT/'bad.nl').write_bytes(mutant.encode())
    (OUT/'bad.v').write_text(rtl(mutant,'vocab_scale'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'vocab_scale',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'scale.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',proof=proof,baseline_C=before,clocks=len(rows),nand_mismatches=0,actual_output_fault_mismatches=wrong,
                actual_valid_result_fault_mismatches=valid_wrong,rtl_clocks=len(rows),actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    begin=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    old=baseline();c=cases();checked=small(c);rows,expected=vectors(c);net,comb=make()
    assert net.n_in==NI and net.n_out==NO and net.n_state==NS
    for name,g in [('scale',net),('transition',comb)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'scale.v').write_text(rtl(net,'vocab_scale'));(OUT/'transition.ref.v').write_text(reference())
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    paths=[Path(__file__).resolve(),Path(__file__).with_name('vocab_scale_golden.c').resolve()]
    paths += [Path(__file__).with_name('vocab_units')/n for n in ('manifest.json','head_scale.nl')]
    paths += [R/n for n in ('integer_opt/scale_bounded_check.py','integer_opt/weight_order.py','integer_opt/weights_golden.c','integer_opt/gate_check.py','integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py','physical/nl_sim.c','physical/units/manifest.json','physical/units/serial_mul.nl','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','nand.py','golden.py','bench.py','ci.py')]
    report=dict(status='bounded local C/components pass; full pipeline NAND/RTL/CEC await Actions',before=metrics(old),metrics=metrics(net),comb_metrics=metrics(comb),small=checked,expected=expected,
        cases_sha256=sha((OUT/'cases.json').read_bytes()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),model_sha256=MODEL_SHA,
        merged_rounding_mismatches=c['merged_rounding_mismatches'],numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        contract='reset,start,dot s22,m u20,g u18 -> held logit s32,valid,busy; idle start captures, busy starts ignored, reset aborts',
        arithmetic='20 MUL steps,42 DIV steps on signed42 numerator shifted22,18 MUL steps; exact signed RNE24 tail;86 total clocks',
        scope='standalone output-head scaling, not E8 MAC, final norm, top40 or complete model; no whole-budget saving',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+str(p.relative_to(Path(__file__).resolve().parent)):sha(p.read_bytes()) for p in paths})
    receipt=OUT/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
    if args.cloud:report['verification']=cloud(net,comb,rows,c);report['status']='all transition CEC and complete NAND/RTL/C protocol pass; real faults rejected'
    report['seconds']=time.monotonic()-begin;save()
    print(json.dumps({k:v for k,v in report.items() if k in ('status','metrics','expected','seconds','merged_rounding_mismatches')},indent=2))


if __name__=='__main__':main()
