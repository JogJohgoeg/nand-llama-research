#!/usr/bin/env python3
"""R121: exact RoPE pair unit for C16 (positions 0..15), one shared 37-bit adder.

C (int_model.c rope): for pair i of a head, c=cos[p*16+i], s=sin[p*16+i] (|c|,|s| <= 32768)
  x' = sat(RNE(a*c - b*s, 32768)),  y' = sat(RNE(b*c + a*s, 32768)),  RNE sign-magnitude.
Signs of c and s are folded into the operands, so the products use 16 magnitude bits:
  X = sum_k 2^k (|c|_k * A1 + |s|_k * A2), A1 = sgn(c)*a, A2 = -sgn(s)*b   (Y: sgn(c)*b, sgn(s)*a).
Micro-steps per output (22): A1, A2, A3=A1+A2, 16 MSB-first accumulates acc=2acc+sel,
|acc|, RNE(|acc|/2^15), sign restore + sat20. Interface: reset,load,pos4,idx4,a20,b20,out_ready
-> x20,y20,valid,ready.
"""
from pathlib import Path
import argparse,hashlib,json,os,random,signal,struct,sys,time
R=Path(os.environ.get('H3_ROPE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_ROPE_OUT',str(R/'build/integer_opt/rope')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,flip_output
from bench import lookup
from export import import_net,MODEL_SHA
NI,NO=51,42
W=37
sha=lambda b:hashlib.sha256(b).hexdigest()
# state layout
F=dict(phase=(0,6),a=(6,20),b=(26,20),cm=(46,16),cs=(62,1),sm=(63,16),ss=(79,1),r1=(80,21),r2=(101,21),r3=(122,22),acc=(144,W),neg=(181,1),x=(182,20),y=(202,20))
NS=222


def tables():
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    off=268692+2*11*128
    cos=struct.unpack_from('<512i',blob,off);sin=struct.unpack_from('<512i',blob,off+2048)
    return list(cos[:256]),list(sin[:256])


def table_word(c,s):
    return (abs(c)&0xffff)|((c<0)<<16)|((abs(s)&0xffff)<<17)|((s<0)<<33)


def rne(n,d):
    a=abs(n);q,r=divmod(a,d);q+=int(2*r>d or (2*r==d and q&1));return -q if n<0 else q


def sat(v):return max(-524288,min(524287,v))


def golden(a,b,c,s):return sat(rne(a*c-b*s,32768)),sat(rne(b*c+a*s,32768))


# Step indices within a pair: X uses 0..21, Y 22..43, then 44 = present.
SETUP1,SETUP2,SETUP3,ACC0,ABS,RND,FIN=0,1,2,3,19,20,21
PRESENT=44;IDLE=63


def make(fault=None):
    cos,sin=tables();tab=lookup([table_word(c,s) for c,s in zip(cos,sin)],34,'shannon')
    b=Builder(NS+NI);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    f={k:q[o:o+n] for k,(o,n) in F.items()}
    reset,load=p[0],p[1];addr=p[2:10];ain=p[10:30];bin_=p[30:50];oready=p[50]
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0)
    eq=lambda bits,v:AND(*[w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)])
    keep=b.inv(reset);ph=f['phase']
    idle=eq(ph,IDLE);present=eq(ph,PRESENT);ready=AND(keep,idle)
    take=AND(ready,load);ack=AND(keep,present,oready)
    _,tw=import_net(b,tab,addr)
    # step within half (0..21) and which half (Y when phase>=22): phase = half*22 + step
    half=b.lor(b.land(ph[4],b.lor(ph[2],b.lor(ph[1],ph[3]))),ph[5])  # phase>=22 for phase<44 (22=010110)
    # exact decode by comparing against the 44 step values
    step_is=lambda s:OR(eq(ph,s),eq(ph,s+22))
    isY=OR(*[eq(ph,s) for s in range(22,44)])
    a,bb=f['a'],f['b']
    ext=lambda v,n=W:v+[v[-1]]*(n-len(v))
    # setup operands: X: A1=sgn(c)*a, A2=-sgn(s)*b ; Y: A1=sgn(c)*b, A2=sgn(s)*a
    s1=step_is(SETUP1);s2=step_is(SETUP2);s3=step_is(SETUP3)
    cs,ss=f['cs'][0],f['ss'][0]
    src1=[b.mux(isY,x,y) for x,y in zip(a,bb)]           # X: a, Y: b
    src2=[b.mux(isY,x,y) for x,y in zip(bb,a)]           # X: b, Y: a
    neg1=cs;neg2=b.mux(isY,b.inv(ss),ss)                 # X: -sgn(s)*b -> negate iff s>=0 ; Y: negate iff s<0
    acc=f['acc']
    # accumulate selector bit k: step ACC0+j uses magnitude bit 15-j
    accsteps=[step_is(ACC0+j) for j in range(16)]
    cbit=OR(*[b.land(accsteps[j],f['cm'][15-j]) for j in range(16)])
    sbit=OR(*[b.land(accsteps[j],f['sm'][15-j]) for j in range(16)])
    accum=OR(*accsteps)
    selv=[b.mux(cbit,b.mux(sbit,0,y),b.mux(sbit,x,z)) for x,y,z in zip(ext(f['r1']),ext(f['r2']),ext(f['r3']))]
    sab=step_is(ABS);srnd=step_is(RND);sfin=step_is(FIN)
    sign=acc[-1]
    q15=acc[15:]+[0]*15
    inc=AND(acc[14],b.lor(OR(*acc[:14]),acc[15]))
    # adder operands
    A=[0]*W;B=[0]*W;cin=0
    def pick(cond,va,vb,vc):
        nonlocal A,B,cin
        A=[b.mux(cond,x,y) for x,y in zip(A,va)];B=[b.mux(cond,x,y) for x,y in zip(B,vb)];cin=b.mux(cond,cin,vc)
    pick(s1,[0]*W,[b.xor(v,neg1) for v in ext(src1)],neg1)
    pick(s2,[0]*W,[b.xor(v,neg2) for v in ext(src2)],neg2 if fault!='bad_sign' else 0)
    pick(s3,ext(f['r1']),ext(f['r2']),0)
    pick(accum,[0]+acc[:-1],selv,0)
    pick(sab,[0]*W,[b.xor(v,sign) for v in acc],sign)
    pick(srnd,q15,[0]*W,inc if fault!='no_round' else 0)
    pick(sfin,[0]*W,[b.xor(v,f['neg'][0]) for v in acc],f['neg'][0])
    s,_=b.add(A,B,cin)
    # registers
    nxt={}
    nxt['a']=[b.mux(take,x,y) for x,y in zip(a,ain)];nxt['b']=[b.mux(take,x,y) for x,y in zip(bb,bin_)]
    nxt['cm']=[b.mux(take,x,y) for x,y in zip(f['cm'],tw[0:16])];nxt['cs']=[b.mux(take,cs,tw[16])]
    nxt['sm']=[b.mux(take,x,y) for x,y in zip(f['sm'],tw[17:33])];nxt['ss']=[b.mux(take,ss,tw[33])]
    nxt['r1']=[b.mux(s1,x,y) for x,y in zip(f['r1'],s[:21])];nxt['r2']=[b.mux(s2,x,y) for x,y in zip(f['r2'],s[:21])]
    nxt['r3']=[b.mux(s3,x,y) for x,y in zip(f['r3'],s[:22])]
    upd=OR(s3,accum,sab,srnd,sfin)
    nacc=[b.mux(upd,x,y) for x,y in zip(acc,s)]
    nacc=[b.land(b.inv(s3),v) for v in nacc]             # acc cleared when A3 is formed
    nxt['acc']=nacc
    nxt['neg']=[b.mux(sab,f['neg'][0],sign)]
    # sat20 of the signed result s (|value| <= 2^20+1)
    over=AND(b.inv(s[-1]),OR(*s[19:W-1]))                 # >= 2^19
    under=AND(s[-1],b.inv(AND(*s[19:W-1])))               # < -2^19
    satv=[b.mux(over,b.mux(under,v,0),1) for v in s[:19]]+[b.mux(over,b.mux(under,s[19],1),0)]
    xfin=AND(sfin,b.inv(isY));yfin=AND(sfin,isY)
    nxt['x']=[b.mux(xfin,x,y) for x,y in zip(f['x'],satv)];nxt['y']=[b.mux(yfin,x,y) for x,y in zip(f['y'],satv)]
    # phase: idle(63) -take-> 0 ... 43 -> 44(present) -ack-> idle ; reset -> idle
    inc_ph=b.add(ph,[0]*6,1)[0]
    busy=b.inv(OR(idle,present))
    np_=[b.mux(busy,x,y) for x,y in zip(ph,inc_ph)]
    np_=[b.mux(take,x,0) for x in np_]
    np_=[b.mux(ack,x,1) for x in np_]
    np_=[b.lor(reset,x) for x in np_]
    nxt['phase']=np_
    order=[nxt[k] for k in F]
    flat=[w for v in order for w in v];assert len(flat)==NS
    outs=f['x']+f['y']+[AND(keep,present),ready]
    comb=b.finish(flat+outs);return with_state(comb,NS),comb


def transition(state,x):
    """Independent behavioural model of the same micro-steps."""
    g=lambda k:state>>F[k][0]&((1<<F[k][1])-1)
    sx=lambda v,n:v-((v>>(n-1)&1)<<n)
    M=(1<<W)-1
    ph=g('phase');reset=x&1;load=x>>1&1;addr=x>>2&255;ain=x>>10&0xfffff;bin_=x>>30&0xfffff;oready=x>>50&1
    st={k:g(k) for k in F};keep=not reset
    ready=keep and ph==IDLE;take=ready and load;ack=keep and ph==PRESENT and oready
    out=st['x']|(st['y']<<20)|(int(keep and ph==PRESENT)<<40)|(int(ready)<<41)
    nst=dict(st)
    if take:
        cos,sin=TAB;w=table_word(cos[addr],sin[addr])
        nst.update(a=ain,b=bin_,cm=w&0xffff,cs=w>>16&1,sm=w>>17&0xffff,ss=w>>33&1)
    if ph<44:
        isY=ph>=22;stp=ph-22 if isY else ph
        a,bb=sx(st['a'],20),sx(st['b'],20)
        r1,r2,r3=sx(st['r1'],21),sx(st['r2'],21),sx(st['r3'],22)
        if stp==SETUP1:v=(bb if isY else a);nst['r1']=(-v if st['cs'] else v)&0x1fffff
        elif stp==SETUP2:
            v=(a if isY else bb);neg=st['ss'] if isY else not st['ss'];nst['r2']=(-v if neg else v)&0x1fffff
        elif stp==SETUP3:nst['r3']=(r1+r2)&0x3fffff;nst['acc']=0
        elif ACC0<=stp<ACC0+16:
            j=stp-ACC0;cb=st['cm']>>(15-j)&1;sb=st['sm']>>(15-j)&1
            sel={(0,0):0,(1,0):r1,(0,1):r2,(1,1):r3}[(cb,sb)]
            nst['acc']=(2*st['acc']+sel)&M
        elif stp==ABS:
            v=sx(st['acc'],W);nst['neg']=int(v<0);nst['acc']=abs(v)&M
        elif stp==RND:
            v=st['acc'];qq,r=v>>15,v&0x7fff;nst['acc']=(qq+int(r>0x4000 or (r==0x4000 and qq&1)))&M
        elif stp==FIN:
            # Exactly the gates: 37-bit two's complement sign restore, written back to acc, then sat20.
            v=((st['acc']^(M if st['neg'] else 0))+st['neg'])&M;nst['acc']=v
            nst['y' if isY else 'x']=sat(sx(v,W))&0xfffff
    nph=ph
    if ph<44 or 44<ph<63:nph=(ph+1)&63   # unused codes 45..62 count on, as the gates do
    if take:nph=0
    if ack:nph=IDLE
    if reset:nph=IDLE
    nst['phase']=nph
    n=0
    for k,(o,_) in F.items():n|=nst[k]<<o
    return n,out


TAB=tables()


def vectors():
    """Protocol rows (x,y,mask) from the behavioural model: every (pos,i), edge/random pairs,
    output stalls, spurious loads while busy and resets mid-pair."""
    rng=random.Random(26100721);cos,sin=TAB;rows=[];pairs=0
    edges=[0,1,-1,524287,-524288,262144,-262144,12345,-54321]
    state=IDLE<<F['phase'][0]
    def tick(x):
        nonlocal state
        state,y=transition(state,x);rows.append((x,y,(1<<NO)-1));return y
    tick(1)
    for addr in range(256):
        for k in range(6):
            av=rng.choice(edges) if rng.random()<0.4 else rng.randrange(-524288,524288)
            bv=rng.choice(edges) if rng.random()<0.4 else rng.randrange(-524288,524288)
            word=(addr<<2)|((av&0xfffff)<<10)|((bv&0xfffff)<<30)
            tick(2|word)
            if k==5 and addr%32==7:                           # abort mid-pair, then redo
                for _ in range(rng.randrange(1,40)):tick(rng.getrandbits(50)&~1|2)
                tick(1);tick(2|word)
            while True:
                y=tick((rng.getrandbits(50)&~3|2) if rng.random()<0.2 else 0|(int(rng.random()<0.7)<<50))
                if y>>40&1 and rows[-1][0]>>50&1:break
            gx,gy=golden(av,bv,cos[addr],sin[addr])
            assert (y&0xfffff)==gx&0xfffff and (y>>20&0xfffff)==gy&0xfffff;pairs+=1
    return rows,pairs


def reference():
    """Independent behavioural RTL of the same machine (state layout F, 37-bit two's complement)."""
    cos,sin=TAB
    cases='\n'.join(f"  8'd{i}: tw=34'd{table_word(c,s_)};" for i,(c,s_) in enumerate(zip(cos,sin)))
    return f'''module top(input [{NS+NI-1}:0] din,output [{NS+NO-1}:0] dout);
wire [{NS-1}:0] q=din[{NS-1}:0];wire [{NI-1}:0] p=din[{NS+NI-1}:{NS}];
wire [5:0] ph=q[5:0];wire signed [19:0] a=q[25:6],b=q[45:26];wire [15:0] cm=q[61:46],sm=q[78:63];wire cs=q[62],ss=q[79];
wire signed [20:0] r1=q[100:80],r2=q[121:101];wire signed [21:0] r3=q[143:122];wire signed [36:0] acc=q[180:144];wire neg=q[181];
wire [19:0] xr=q[201:182],yr=q[221:202];
wire reset=p[0],load=p[1];wire [7:0] addr=p[9:2];wire [19:0] ain=p[29:10],bin=p[49:30];wire oready=p[50];
reg [33:0] tw;always @* case(addr)
{cases}
  default: tw=34'd0;
endcase
wire idle=ph==63,present=ph==44,ready=!reset&&idle,take=ready&&load,ack=!reset&&present&&oready;
wire isY=ph>=22&&ph<44;wire [5:0] stp=isY?ph-6'd22:ph;wire act=ph<44;
wire signed [36:0] ae=a,be=b,r1e=r1,r2e=r2,r3e=r3;
wire signed [36:0] src1=isY?be:ae,src2=isY?ae:be;
wire neg2=isY?ss:!ss;
wire [3:0] j=stp-6'd3;wire cb=cm[15-j],sb=sm[15-j];
wire signed [36:0] sel=cb?(sb?r3e:r1e):(sb?r2e:37'sd0);
wire [36:0] absv=acc[36]?-acc:acc;
wire [36:0] qq=acc>>15;wire inc=acc[14]&&((|acc[13:0])||acc[15]);
wire [36:0] fin=neg?-acc:acc;wire signed [36:0] fins=fin;
wire [19:0] satv=(fins>37'sd524287)?20'h7ffff:(fins< -37'sd524288)?20'h80000:fin[19:0];
reg [5:0] np;reg [19:0] na,nb,nx,ny;reg [15:0] ncm,nsm;reg ncs,nss,nneg;reg [20:0] nr1,nr2;reg [21:0] nr3;reg [36:0] nacc;
always @* begin
 na=a;nb=b;ncm=cm;nsm=sm;ncs=cs;nss=ss;nr1=r1;nr2=r2;nr3=r3;nacc=acc;nneg=neg;nx=xr;ny=yr;
 if(take) begin na=ain;nb=bin;ncm=tw[15:0];ncs=tw[16];nsm=tw[32:17];nss=tw[33]; end
 if(act) begin
  if(stp==0) nr1=cs?-src1:src1;
  if(stp==1) nr2=neg2?-src2:src2;
  if(stp==2) begin nr3=r1e+r2e;nacc=0; end
  if(stp>=3&&stp<19) nacc=(acc<<<1)+sel;
  if(stp==19) begin nneg=acc[36];nacc=absv; end
  if(stp==20) nacc=qq+inc;
  if(stp==21) begin nacc=fin; if(isY) ny=satv; else nx=satv; end
 end
 np=ph;
 if(ph<44||(ph>44&&ph<63)) np=ph+6'd1;
 if(take) np=0;
 if(ack) np=63;
 if(reset) np=63;
end
assign dout={{ready,!reset&&present,yr,xr,ny,nx,nneg,nacc,nr3,nr2,nr1,nss,nsm,ncs,ncm,nb,na,np}};
endmodule
'''


def cloud(net,comb,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import shutil,subprocess,verify as checks,sampler as smp
    from ci import cec
    from nand import blif
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    prefix=OUT/'body';prefix.with_suffix('.ref.v').write_text(reference())
    ref=smp.mapped_reference(prefix,NS+NI,NS+NO);proofs={}
    for k,g in [('source',comb),('negative',flip_output(comb)),('bad_sign',make('bad_sign')[1]),('no_round',make('no_round')[1])]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(g))
    prefix.with_suffix('.reference.blif').write_text(blif(ref))
    for k,w in [('source','equivalent'),('negative','different'),('bad_sign','different'),('no_round','different')]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0;faults={}
    for k,g in [('output_flip',flip_output(net)),('bad_sign',make('bad_sign')[0]),('no_round',make('no_round')[0])]:
        faults[k]=checks.check_nand(rows,g.encode());assert faults[k]>0,k
    from export import rtl
    bad=flip_output(net);(OUT/'bad.v').write_text(rtl(bad,'rope'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'rope',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'rope.v');checks.run([exe],600)
    exe=checks.compile_rtl('negative',OUT/'bad.v');res=subprocess.run([str(exe)],capture_output=True,text=True,timeout=600)
    (OUT/'rtl.negative.log').write_text(res.stdout+res.stderr);assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def remote_check():
    """m149 only: actual gates == model on arbitrary states and on the whole protocol."""
    import ctypes as ct,subprocess,tempfile
    from golden import Netlist
    net,comb=make();g=Netlist.decode(net.encode(),NI,NO)
    rng=random.Random(5);states=[rng.getrandbits(NS) for _ in range(256)];xs=[rng.getrandbits(NI) for _ in states]
    got=g.step_simd([bytes(s>>i&1 for i in range(NS)) for s in states],[bytes(x>>i&1 for i in range(NI)) for x in xs])
    arb=sum((sum(v<<i for i,v in enumerate(a)),sum(v<<i for i,v in enumerate(b)))==transition(s,x) for (a,b),s,x in zip(got,states,xs))
    rows,pairs=vectors()
    with tempfile.TemporaryDirectory() as t:
        so=Path(t)/'s.so';subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(so)],check=True)
        lib=ct.CDLL(str(so));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
        def run(n):
            raw=n.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;out=ct.create_string_buffer(6);bad=0
            for x,y,m in rows:
                lib.nl_step(x.to_bytes(7,'little'),out);bad+=bool((int.from_bytes(out.raw,'little')^y)&m)
            return bad
        good=run(net);faults={k:run(make(k)[0]) for k in ('bad_sign','no_round')};faults['output_flip']=run(flip_output(net))
    return dict(arbitrary_state_transitions=len(states),arbitrary_equal=arb,protocol_clocks=len(rows),pairs=pairs,protocol_mismatches=good,fault_mismatches=faults)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--remote-check',action='store_true');a=ap.parse_args()
    if a.remote_check:print(json.dumps(remote_check()));return
    if not a.cloud:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);t0=time.monotonic()
    net,comb=make();print('rope',metrics(net),round(time.monotonic()-t0,1))
    # behavioural model vs C golden over all 256 (pos,i) and edge/random a,b
    rng=random.Random(26100721);cos,sin=TAB;checked=0
    edges=[0,1,-1,524287,-524288,262144,-262144,12345,-54321]
    state=1<<F['phase'][0]|0;state=IDLE<<F['phase'][0]
    for addr in range(256):
        for _ in range(6):
            av=rng.choice(edges) if rng.random()<0.4 else rng.randrange(-524288,524288)
            bv=rng.choice(edges) if rng.random()<0.4 else rng.randrange(-524288,524288)
            x=(1<<1)|(addr<<2)|((av&0xfffff)<<10)|((bv&0xfffff)<<30)
            state,_=transition(state,x)
            for _ in range(44):state,_=transition(state,0)
            state,o=transition(state,1<<50)
            assert o>>40&1
            gx,gy=golden(av,bv,cos[addr],sin[addr])
            assert (o&0xfffff)==gx&0xfffff and (o>>20&0xfffff)==gy&0xfffff,(addr,av,bv,o&0xfffff,gx&0xfffff)
            checked+=1
    print('model == C on',checked,'pairs',round(time.monotonic()-t0,1))
    rows,pairs=vectors();(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    from export import rtl
    for k,g in (('rope',net),('transition',comb)):(OUT/(k+'.nl')).write_bytes(g.encode())
    (OUT/'rope.v').write_text(rtl(net,'rope'));(OUT/'body.ref.v').write_text(reference())
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='model==C on every table entry; protocol prepared; universal CEC, NAND/RTL replay await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),table=dict(entries=256,width=34),C_pairs=checked,protocol_pairs=pairs,
        clocks=len(rows),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cycles_per_pair=45,
        contract='reset,load,pos4/idx4 (addr8=pos*16+idx),a20,b20,out_ready -> x20,y20,valid,ready; exact int_model rope pair for C16',
        scope='one RoPE pair unit; pairing (i,i+16) within a head and position sequencing are the caller',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(net,comb,rows);report['status']='universal transition CEC vs independent RTL, NAND/RTL/C protocol and actual faults pass'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
