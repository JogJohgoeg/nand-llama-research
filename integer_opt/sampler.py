#!/usr/bin/env python3
"""Two-pass exact sampler behind the stable top40 stream (int_pick, sample!=0).

Pass 1 consumes the 40 sorted (score,id) entries, keeps the first score and
accumulates total=sum exp_weight(RNE(5*(top-score),4)). A 32-step shift-add
multiplies the latched random word by total in place, leaving
at=(random*total)>>32 in the high register. The source is then asked to replay
the same list; pass 2 subtracts weights until at<w and keeps that id.
Greedy (sample=0) keeps the first id of pass 1 and never requests a replay.
One shared 22-bit adder serves pass 1 (T+w), multiply (hi+T) and pass 2 (hi-w).
The weight unit is the exact R111 sample_weight graph, imported unchanged.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_SAMPLER_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_SAMPLER_OUT',str(R/'build/integer_opt/sampler')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output,from_yosys
from golden import Netlist
from gate_check import Snapshot
from export import import_net,rtl,MODEL_SHA
import sample_weight
NI,NO=76,12
# state: phase3 cnt6 top32 T22 hi22 rnd32 res8 found1 smp1
F=dict(phase=(0,3),cnt=(3,6),top=(9,32),T=(41,22),hi=(63,22),rnd=(85,32),res=(117,8),found=(125,1),smp=(126,1))
NS=127
sha=lambda b:hashlib.sha256(b).hexdigest()
C_SHA='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'


def make(fault=None):
    b=Builder(NS+NI);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    f={k:q[o:o+n] for k,(o,n) in F.items()}
    reset,start,sample=p[0],p[1],p[2];random_in=p[3:35];sv=p[35];score=p[36:68];ident=p[68:76]
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0)
    eq=lambda bits,v:AND(*[w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)])
    keep=b.inv(reset);ph=f['phase'];idle,p1,mul,p2,done=[eq(ph,i) for i in range(5)]
    cnt=f['cnt'];first=eq(cnt,0);last=eq(cnt,39);mlast=eq(cnt,31)
    begin=AND(keep,start,b.lor(idle,done));ready=AND(keep,b.lor(p1,p2));take=AND(ready,sv)
    topin=[b.mux(first,t,s) for t,s in zip(f['top'],score)]
    _,w=import_net(b,sample_weight.make(),topin+score);w=w+[0]*5
    T,hi=f['T'],f['hi']
    # Shared adder: pass1 T+w, multiply hi+(rnd[0]?T:0), pass2 hi+~w+1.
    A=[b.mux(p1,h,t) for t,h in zip(T,hi)]
    mb=[b.land(f['rnd'][0],t) for t in T]
    B=[b.mux(mul,b.mux(p2,x,b.inv(x)),m) for x,m in zip(w,mb)]
    if fault=='no_borrow':B=[b.mux(mul,x,m) for x,m in zip(w,mb)]
    s,c=b.add(A,B,p2)
    borrow=b.inv(c);found=f['found'][0];hit=AND(take,p2,b.inv(found),borrow)
    step=AND(keep,mul)
    # T: cleared on begin, +w on pass1 take.
    nT=[AND(b.inv(begin),b.mux(AND(take,p1),t,x)) for t,x in zip(T,s)]
    # hi: cleared on begin; multiply step {c,s}>>1; pass2 take without hit and not found -> s.
    sub=AND(take,p2,b.inv(found),c)
    shifted=s[1:]+[c]
    nhi=[AND(b.inv(begin),b.mux(step,b.mux(sub,h,x),y)) for h,x,y in zip(hi,s,shifted)]
    # rnd: loaded on begin, shifts right during multiply with s[0] entering the top.
    rnd=f['rnd'];rs=rnd[1:]+[s[0]]
    nrnd=[b.mux(begin,b.mux(step,r,x),y) for r,x,y in zip(rnd,rs,random_in)]
    ntop=[b.mux(AND(take,p1,first),t,x) for t,x in zip(f['top'],score)]
    rec=b.lor(AND(take,p1,first),hit)
    nres=[b.mux(rec,r,x) for r,x in zip(f['res'],ident)]
    nfound=AND(b.inv(begin),b.lor(found,hit))
    nsmp=[b.mux(begin,f['smp'][0],sample)]
    adv=b.lor(take,step);wrap=b.lor(AND(take,last),AND(step,mlast))
    inc=b.add(cnt,[0]*6,1)[0]
    ncnt=[AND(b.inv(b.lor(begin,wrap)),b.mux(adv,x,y)) for x,y in zip(cnt,inc)]
    np=ph[:]
    def setp(en,v):
        nonlocal np
        np=[b.mux(en,x,v>>i&1) for i,x in enumerate(np)]
    setp(begin,1);setp(AND(take,p1,last),2);setp(AND(take,p1,last,b.inv(f['smp'][0])),4)
    setp(AND(step,mlast),3);setp(AND(take,p2,last),4)
    np=[AND(keep,x) for x in np]
    nxt=np+ncnt+ntop+nT+nhi+nrnd+nres+[nfound]+nsmp;assert len(nxt)==NS
    replay=AND(keep,p2,first)
    outs=f['res']+[ready,replay,AND(keep,OR(p1,mul,p2)),AND(keep,done)]
    comb=b.finish(nxt+outs);return with_state(comb,NS),comb


def weight(top,score):
    d=top-score
    if d<0:return 0
    j=sample_weight.rne(sample_weight.rne(5*d,4),64)
    return TABLE[j] if j<=1024 else 0


def transition(state,x):
    """Independent behavioural model of the same state machine."""
    g=lambda k:state>>F[k][0]&((1<<F[k][1])-1)
    s32=lambda v:v-((v>>31&1)<<32)
    ph,cnt,top,T,hi,rnd,res,found,smp=[g(k) for k in ('phase','cnt','top','T','hi','rnd','res','found','smp')]
    reset=x&1;start=x>>1&1;sample=x>>2&1;rin=x>>3&0xffffffff;sv=x>>35&1;score=x>>36&0xffffffff;ident=x>>68&255
    keep=not reset;begin=keep and start and ph in (0,4);ready=keep and ph in (1,3);take=ready and sv
    first=cnt==0;topin=score if first else top
    w=weight(s32(topin),s32(score))
    M=(1<<22)-1
    ntop,nT,nhi,nrnd,nres,nfound,nsmp,ncnt,np=top,T,hi,rnd,res,found,smp,cnt,ph
    if take and ph==1:
        nT=(T+w)&M
        if first:ntop=score;nres=ident
    step=keep and ph==2
    if step:
        tot=hi+(T if rnd&1 else 0);nrnd=(rnd>>1)|((tot&1)<<31);nhi=tot>>1
    if take and ph==3 and not found:
        if hi<w:nfound=1;nres=ident
        else:nhi=hi-w
    if begin:nT=0;nhi=0;nrnd=rin;nfound=0;nsmp=sample
    if (take and cnt==39) or (step and cnt==31) or begin:ncnt=0
    elif take or step:ncnt=cnt+1
    if begin:np=1
    if take and ph==1 and cnt==39:np=2 if smp else 4
    if step and cnt==31:np=3
    if take and ph==3 and cnt==39:np=4
    if reset:np=0
    nxt=0
    for k,v in zip(('phase','cnt','top','T','hi','rnd','res','found','smp'),(np,ncnt,ntop,nT,nhi,nrnd,nres,nfound,nsmp)):nxt|=v<<F[k][0]
    out=res|(int(ready)<<8)|(int(keep and ph==3 and first)<<9)|(int(keep and ph in (1,2,3))<<10)|(int(keep and ph==4)<<11)
    return nxt,out


TABLE=sample_weight.table()


def golden(cases):
    src=R/'integer_opt/sample_stream.c';assert sha((R/'integer/int_model.c').read_bytes())==C_SHA
    with tempfile.TemporaryDirectory() as tmp:
        so=Path(tmp)/'s.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(src),'-o',str(so)],check=True,timeout=30)
        g=ct.CDLL(str(so));g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
        g.stream_logit.argtypes=[ct.c_uint,ct.c_int32];g.stream_pick.argtypes=[ct.c_uint32,ct.c_int]
        blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
        out=[]
        for logits,rv,smp in cases:
            arr=(ct.c_int32*192)(*logits);a=g.int_pick(arr,rv,smp)
            g.stream_begin()
            for i,v in enumerate(logits):g.stream_logit(i,v)
            assert g.stream_pick(rv,smp)==a;out.append(a)
    return out


def cases():
    import top40
    lists,_=top40.cases();rng=random.Random(26100711);result=[]
    for c in lists:
        lg=c['logits'];order=c['order'];ws=[weight(lg[order[0]],lg[i]) for i in order];T=sum(ws)
        rs={0,0xffffffff,rng.getrandbits(32),rng.getrandbits(32)}
        cum=0
        for wv in ws[:-1]:
            cum+=wv;r=-(-(cum<<32)//T)  # smallest r with at==cum
            for d in (-1,0):
                if 0<=r+d<1<<32:rs.add(r+d)
        pick=sorted(rs);pick=pick if len(pick)<=12 else sorted(rng.sample(pick,12))+[0,0xffffffff]
        for r in sorted(set(pick)):result.append((lg,r,1))
        result.append((lg,rng.getrandbits(32),0))
    return result


def protocol(cases,want,stall_seed=2610071):
    """Per-case input sequences: a source that presents the sorted list, then
    replays it after a random delay once the sampler asks; random valid gaps."""
    rng=random.Random(stall_seed);seqs=[]
    for k,((lg,r,smp),tok) in enumerate(zip(cases,want)):
        order=sorted(range(192),key=lambda i:(-lg[i],i))[:40];stall=k%3==1
        seqs.append(dict(order=[(lg[i]&0xffffffff,i) for i in order],random=r,sample=smp,token=tok,stall=stall))
    return seqs


def run_lanes(step,seqs,rng):
    """Drive every case concurrently; step(states,inputs)->(states,outputs)."""
    n=len(seqs);state=[0]*n;rows=[[] for _ in range(n)];ends=[None]*n
    phase=['reset']*n;pos=[0]*n;delay=[0]*n;passes=[0]*n;result=[None]*n
    for clock in range(400):
        xs=[]
        for i,s in enumerate(seqs):
            x=0
            if phase[i]=='reset':x=1
            elif phase[i]=='start':x=2|(s['sample']<<2)|(s['random']<<3)
            elif phase[i]=='stream':
                send=pos[i]<40 and delay[i]==0 and (not s['stall'] or rng.randrange(4)!=0)
                sc,idn=s['order'][pos[i]] if pos[i]<40 else (rng.getrandbits(32),rng.getrandbits(8))
                x=(int(send)<<35)|(sc<<36)|(idn<<68)|(rng.getrandbits(1)<<1)
            xs.append(x)
        state,ys=step(state,xs)
        for i,(x,y) in enumerate(zip(xs,ys)):
            rows[i].append((x,y))
            if phase[i]=='reset':phase[i]='start'
            elif phase[i]=='start':phase[i]='stream'
            elif phase[i]=='stream':
                if x>>35&1 and y>>8&1:pos[i]+=1
                if delay[i]:delay[i]-=1
                if pos[i]==40 and y>>9&1 and passes[i]==0:passes[i]=1;pos[i]=0;delay[i]=rng.randrange(1,6)
                if y>>11&1:result[i]=y&255;phase[i]='end';ends[i]=clock+1
        if all(e is not None for e in ends):break
    assert all(e is not None for e in ends)
    return [r[:e] for r,e in zip(rows,ends)],result


def reference():
    weight=sample_weight.reference().replace('module top(','module wref(')
    return weight+'''module top(input [202:0] din,output [138:0] dout);
wire [126:0] q=din[126:0];wire [75:0] p=din[202:127];
wire [2:0] ph=q[2:0];wire [5:0] cnt=q[8:3];wire [31:0] top=q[40:9];wire [21:0] T=q[62:41],hi=q[84:63];
wire [31:0] rnd=q[116:85];wire [7:0] res=q[124:117];wire found=q[125],smp=q[126];
wire reset=p[0],start=p[1],sample=p[2],sv=p[35];wire [31:0] rin=p[34:3],score=p[67:36];wire [7:0] id=p[75:68];
wire keep=!reset,begin_op=keep && start && (ph==0 || ph==4),ready=keep && (ph==1 || ph==3),take=ready && sv;
wire first=cnt==0,step=keep && ph==2;
wire [31:0] topin=first?score:top;wire [16:0] w;
wref u(.din({score,topin}),.dout(w));
wire [22:0] mulsum={1'b0,hi}+(rnd[0]?{1'b0,T}:23'd0);
wire hit=take && ph==3 && !found && hi<{5'd0,w};
reg [2:0] np;reg [5:0] nc;reg [31:0] ntop,nr;reg [21:0] nT,nh;reg [7:0] nres;reg nf,ns;
always @* begin
 np=ph;nc=cnt;ntop=top;nT=T;nh=hi;nr=rnd;nres=res;nf=found;ns=smp;
 if(take && ph==1) begin nT=T+{5'd0,w};if(first) begin ntop=score;nres=id;end end
 if(step) begin nh=mulsum[22:1];nr={mulsum[0],rnd[31:1]};end
 if(take && ph==3 && !found) begin if(hit) begin nf=1;nres=id;end else nh=hi-{5'd0,w};end
 if(begin_op) begin nT=0;nh=0;nr=rin;nf=0;ns=sample;end
 if((take && cnt==39) || (step && cnt==31) || begin_op) nc=0; else if(take || step) nc=cnt+6'd1;
 if(begin_op) np=1;
 if(take && ph==1 && cnt==39) np=smp?3'd2:3'd4;
 if(step && cnt==31) np=3;
 if(take && ph==3 && cnt==39) np=4;
 if(reset) np=0;
end
wire [3:0] flags={keep && ph==4,keep && ph>=1 && ph<=3,keep && ph==3 && first,ready};
assign dout={flags,res,ns,nf,nres,nr,nh,nT,ntop,nc,np};
endmodule
'''


def vectors(lanes):
    rows=[];state=0;tokens=[]
    for seq in lanes:
        for x,_ in seq:
            state,y=transition(state,x);rows.append((x,y,(1<<NO)-1))
        tokens.append(rows[-1][1]&255)
    return rows,tokens


def mapped_reference(prefix,ni,no):
    # The EXP case table becomes a ROM ($memrd); memory_map lowers it (as in R111).
    ys=Path(str(prefix)+'.ys')
    ys.write_text(f'read_verilog {prefix}.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\nopt\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {prefix}.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(prefix)+'.yosys.log','-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=600)
    return from_yosys(json.loads(Path(str(prefix)+'.ref.json').read_text()),ni,no)


def cloud(net,comb,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    prefix=OUT/'body';prefix.with_suffix('.ref.v').write_text(reference())
    ref=mapped_reference(prefix,NS+NI,NS+NO)
    bad_net,bad_comb=make('no_borrow')
    for kind,g in [('source',comb),('reference',ref),('negative',flip_output(comb)),('no_borrow',bad_comb)]:prefix.with_suffix('.'+kind+'.blif').write_text(blif(g))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    proofs={}
    for kind,want in [('source','equivalent'),('negative','different'),('no_borrow','different')]:
        proofs[kind]=cec(abc,prefix.with_suffix('.'+kind+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+kind+'.log'));assert proofs[kind]['verdict']==want,kind
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    changed=flip_output(net);wrong=checks.check_nand(rows,changed.encode());assert wrong>0
    borrow_wrong=checks.check_nand(rows,bad_net.encode());assert borrow_wrong>0
    for name,g in [('bad',changed),('bad_borrow',bad_net)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'bad.v').write_text(rtl(changed,'sampler'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'sampler',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'sampler.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'bad.v');result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'rtl.negative.log').write_text(result.stdout+result.stderr)
    assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
    return dict(status='pass',all_state_bits=NS,all_input_bits=NI,all_output_bits=NO,proofs=proofs,reference_metrics=metrics(ref),
        clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,actual_output_fault_mismatches=wrong,actual_no_borrow_fault_mismatches=borrow_wrong,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    net,comb=make();print('sampler',metrics(net))
    cs=cases();want=golden(cs);seqs=protocol(cs,want)
    # Behavioural model vs C first.
    beh=lambda st,xs:tuple(zip(*[transition(s,x) for s,x in zip(st,xs)]))
    _,res=run_lanes(beh,seqs,random.Random(7));assert res==want,'model'
    g=Snapshot.decode(net.encode(),NI,NO)
    def gate(st,xs):
        o=g.step_simd([bytes(s>>i&1 for i in range(NS)) for s in st],[bytes(x>>i&1 for i in range(NI)) for x in xs])
        return [sum(v<<i for i,v in enumerate(s)) for s,_ in o],[sum(v<<i for i,v in enumerate(y)) for _,y in o]
    lanes,res=run_lanes(gate,seqs,random.Random(7));assert res==want,'gates'
    bad_net,_=make('no_borrow');gb=Snapshot.decode(bad_net.encode(),NI,NO)
    def gate_bad(st,xs):
        o=gb.step_simd([bytes(s>>i&1 for i in range(NS)) for s in st],[bytes(x>>i&1 for i in range(NI)) for x in xs])
        return [sum(v<<i for i,v in enumerate(s)) for s,_ in o],[sum(v<<i for i,v in enumerate(y)) for _,y in o]
    _,bres=run_lanes(gate_bad,seqs,random.Random(7));bad_picks=sum(x!=y for x,y in zip(bres,want));assert bad_picks>0
    # Lane outputs from zero state equal the behavioural model per lane.
    for seq in lanes[:64]:
        st=0
        for x,y in seq:st,yy=transition(st,x);assert yy==y
    rows,tokens=vectors(lanes);assert tokens==want
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    for name,g in [('sampler',net),('transition',comb)]:(OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'sampler.v').write_text(rtl(net,'sampler'));(OUT/'body.ref.v').write_text(reference())
    clocks=[len(l) for l in lanes]
    nostall=sorted({len(l) for l,s in zip(lanes,seqs) if not s['stall']})
    fixture=[dict(logits_sha256=sha(b''.join((v&0xffffffff).to_bytes(4,'little') for v in c[0])),random=c[1],sample=c[2],token=t) for c,t in zip(cs,want)]
    (OUT/'cases.json').write_text(json.dumps(fixture,separators=(',',':'))+'\n')
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted({Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)} ) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/sample_stream.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/top40_cases.json','integer_opt/sample_weight_units/baseline.nl','integer_opt/sample_weight_units/manifest.json'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='local gate-level C protocol pass; universal CEC and RTL pending Actions',metrics=metrics(net),comb_metrics=metrics(comb),
        weight_unit=metrics(sample_weight.make()),state_bits={k:v[1] for k,v in F.items()},
        contract='reset,start,sample,random32,valid,score32,id8 -> token8,ready,replay,busy,done; two passes over the same sorted top40 list',
        C_cases=len(cs),samples=sum(c[2] for c in cs),greedy=len(cs)-sum(c[2] for c in cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        lane_clocks=sum(clocks),sequential_clocks=len(rows),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        no_stall_clocks=nostall,max_clocks=max(clocks),actual_no_borrow_wrong_picks=bad_picks,
        scope='standalone sampler after top40; needs the sorter to replay its list once (not yet integrated); RNG is an external 32-bit input',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(net,comb,rows);report['status']='universal transition CEC, full NAND/RTL/C protocol pass; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=1))

if __name__=='__main__':main()
