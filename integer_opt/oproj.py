#!/usr/bin/env python3
"""R124: attention output -> A8 -> O projection -> residual (whole-layer piece 4), layer 0.

C: a = linear(h,0,3) = sat(RNE(dot*m*alpha[3],33292288)), dot = sum_c q[c]*w[j][c], q,m = quant(h,128);
   x'[j] = sat(x[j] + a[j]).
The 128 head outputs h enter a rotating 128 x 20-bit ring; the accepted R20 quantizer (n=128)
scans it and converts on the replay, writing each q8 over the entry just rotated to the tail.
Row j: 128 clocks of serial MAC, the ring rotating once per column (no address mux), ternary
w[j][c] from a constant table addressed by (row,col); then the accepted R14 scale unit
(dot17, m20, alpha18 -> S(R(dot*m*alpha,33292288))); then the old x[j] is accepted and
y = sat(x + a) leaves in the same handshake.
Interface: reset,start,h_valid,h20,x_valid,x20,y_ready -> h_ready,y20,y_valid(=x_ready),busy.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_OPROJ_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_OPROJ_OUT',str(R/'build/integer_opt/oproj')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from bench import lookup
from export import import_net,rtl,MODEL_SHA
import quant_stream,scale_pipeline
NI,NO=45,23
SRG,SCT=2560,3+7+7+17
SQU,SSC=169,419
NS=SRG+SCT+SQU+SSC
QO,CO=41,22
SHELL_IN=NS+NI+(SQU+QO)+(SSC+CO)+2;SHELL_OUT=NS+NO+quant_stream.NI+scale_pipeline.NI+14
sha=lambda b:hashlib.sha256(b).hexdigest()


def golden_lib(tmp):
    so=Path(tmp)/'g.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer'),
        str(Path(__file__).with_name('oproj_golden.c')),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.oproj.argtypes=[ct.POINTER(ct.c_int32)]*3+[ct.c_int];g.weight_at.argtypes=[ct.c_int]*4;g.alpha_at.argtypes=[ct.c_int]*2
    return g


def weights(layer=0):
    with tempfile.TemporaryDirectory() as t:
        g=golden_lib(t);w=[g.weight_at(layer,3,a>>7,a&127) for a in range(16384)];al=g.alpha_at(layer,3)
    return [{0:0,1:1,-1:2}[v] for v in w],al     # address = row<<7 | col ; code 1=+1, 2=-1


def table(layer=0):
    codes,al=weights(layer);return lookup(codes,2,'shannon'),codes,al


def shell(alpha,fault=None):
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    o=NS+NI+2;qd=list(range(o,o+SQU));qo=list(range(o+SQU,o+SQU+QO));o+=SQU+QO
    sd=list(range(o,o+SSC));so=list(range(o+SSC,o+SSC+CO));o+=SSC+CO;wc=[o,o+1]
    ring=[q[20*k:20*k+20] for k in range(128)];c=q[SRG:SRG+SCT];ph,cnt,row,acc=c[0:3],c[3:10],c[10:17],c[17:34]
    qph=q[SRG+SCT:SRG+SCT+SQU][153:156]
    reset,start,hv=p[0],p[1],p[2];h=p[3:23];xv=p[23];x=p[24:44];yr=p[44]
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0)
    eq=lambda bits,v:AND(*[w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)])
    keep=b.inv(reset);P=[eq(ph,i) for i in range(7)];idle,collect,q1,q2,mac,scale,res=P
    qxr,qyv=qo[37],qo[38];qidle=eq(qph,0);sres,svalid,sbusy=so[0:20],so[20],so[21]
    begin=AND(keep,start,idle);take=AND(keep,collect,hv)
    qstart=AND(keep,q1,eq(cnt,0),qidle);qt1=AND(keep,q1,qxr);qt2=AND(keep,q2,qxr);qack=AND(keep,q2,qyv)
    macs=AND(keep,mac);sstart=AND(keep,scale,eq(cnt,0));sdone=AND(keep,scale,b.inv(eq(cnt,0)),svalid,b.inv(sbusy))
    xfer=AND(keep,res,xv,yr)
    last=eq(cnt,127);lastrow=eq(row,127)
    rotate=OR(take,qt1,qt2,macs)
    head=ring[0]
    tail_new=[b.mux(take,hh,xx) for hh,xx in zip(head,h)]
    q8x=qo[0:8]+[qo[7]]*12
    nring=[]
    for k in range(128):
        src=tail_new if k==127 else ring[k+1];cur=ring[k]
        if k==127 and fault!='no_writeback':cur=[b.mux(qack,cc,v) for cc,v in zip(cur,q8x)]
        nring+=[b.mux(rotate,cc,s) for cc,s in zip(cur,src)]
    # MAC: acc += w*q, q = sign-extended low byte of the ring head
    qv=head[0:8]+[head[7]]*9;plus,minus=wc[0],wc[1]
    neg=minus if fault!='bad_weight' else plus
    opnd=[b.land(b.lor(plus,minus),b.xor(v,neg)) for v in qv]
    s,_=b.add(acc,opnd,b.land(neg,b.lor(plus,minus)))
    nacc=[b.land(b.inv(OR(begin,AND(scale,svalid,b.inv(sbusy)),xfer)),b.mux(macs,a_,v)) for a_,v in zip(acc,s)]
    # control
    inc=b.add(cnt,[0]*7,1)[0];rinc=b.add(row,[0]*7,1)[0]
    to_q1=AND(take,last);to_q2=AND(qt1,last);to_mac=OR(AND(qack,last),AND(xfer,b.inv(lastrow)));to_scale=AND(macs,last)
    to_res=sdone;to_idle=AND(xfer,lastrow)
    adv=OR(take,qt1,qack,macs,sstart)
    wrap=OR(begin,to_q1,to_q2,to_mac,to_scale,to_res,to_idle)
    ncnt=[AND(b.inv(wrap),b.mux(adv,a_,v)) for a_,v in zip(cnt,inc)]
    nrow=[AND(b.inv(OR(begin,to_idle)),b.mux(xfer,a_,v)) for a_,v in zip(row,rinc)]
    np_=ph[:]
    def setp(en,v):
        nonlocal np_
        np_=[b.mux(en,x_,v>>i&1) for i,x_ in enumerate(np_)]
    setp(begin,1);setp(to_q1,2);setp(to_q2,3);setp(to_mac,4);setp(to_scale,5);setp(to_res,6);setp(to_idle,0)
    np_=[AND(keep,v) for v in np_]
    # residual y = sat20(x + a)
    sm,_=b.add(x+[x[-1]],sres+[sres[-1]],0)
    over=AND(b.inv(sm[20]),sm[19]);under=AND(sm[20],b.inv(sm[19]))
    y=[b.mux(over,b.mux(under,v,0),1) for v in sm[:19]]+[b.mux(over,b.mux(under,sm[19],1),0)]
    n128=[128>>i&1 for i in range(9)]
    qin=[reset,qstart]+n128+head+[OR(AND(keep,q1),AND(keep,q2)),AND(keep,q2)]
    sin=[reset,sstart]+acc+qo[17:37]+[alpha>>i&1 for i in range(18)]
    addr=cnt+row
    assert len(qin)==quant_stream.NI and len(sin)==scale_pipeline.NI
    outs=[AND(keep,collect)]+y+[AND(keep,res,xv)]+[AND(keep,b.inv(idle))]
    nctl=np_+ncnt+nrow+nacc
    return b.finish(nring+nctl+qd+sd+outs+qin+sin+addr)


def reference(alpha):
    o=NS+NI;qd0=o;qo0=o+SQU;sd0=qo0+QO;so0=sd0+SSC;w0=so0+CO
    return f'''module top(input [{SHELL_IN-1}:0] din,output [{SHELL_OUT-1}:0] dout);
wire [{SRG-1}:0] ring=din[{SRG-1}:0];wire [2:0] ph=din[{SRG+2}:{SRG}];wire [6:0] cnt=din[{SRG+9}:{SRG+3}],row=din[{SRG+16}:{SRG+10}];
wire signed [16:0] acc=din[{SRG+33}:{SRG+17}];wire [2:0] qph=din[{SRG+SCT+155}:{SRG+SCT+153}];
wire [{NI-1}:0] p=din[{NS+NI-1}:{NS}];
wire [{SQU-1}:0] qd=din[{qo0-1}:{qd0}];wire [{QO-1}:0] qo=din[{sd0-1}:{qo0}];wire [{SSC-1}:0] sd=din[{so0-1}:{sd0}];wire [{CO-1}:0] so=din[{w0-1}:{so0}];
wire plus=din[{w0}],minus=din[{w0+1}];
wire reset=p[0],start=p[1],hv=p[2];wire signed [19:0] h=p[22:3];wire xv=p[23];wire signed [19:0] x=p[43:24];wire yr=p[44];
wire keep=!reset;
wire idle=ph==0,collect=ph==1,q1=ph==2,q2=ph==3,mac=ph==4,scale=ph==5,res=ph==6;
wire qxr=qo[37],qyv=qo[38],qidle=qph==0;wire signed [19:0] sres=so[19:0];wire svalid=so[20],sbusy=so[21];
wire begin_op=keep&&start&&idle,take=keep&&collect&&hv;
wire qstart=keep&&q1&&cnt==0&&qidle,qt1=keep&&q1&&qxr,qt2=keep&&q2&&qxr,qack=keep&&q2&&qyv;
wire macs=keep&&mac,sstart=keep&&scale&&cnt==0,sdone=keep&&scale&&cnt!=0&&svalid&&!sbusy,xfer=keep&&res&&xv&&yr;
wire last=cnt==127,lastrow=row==127;
wire rotate=take||qt1||qt2||macs;
wire [19:0] head=ring[19:0];
wire [{SRG-1}:0] rot={{(take?h:head),ring[{SRG-1}:20]}};
wire [{SRG-1}:0] nr=rotate?rot:(qack?{{{{12{{qo[7]}}}},qo[7:0],ring[{SRG-21}:0]}}:ring);
wire signed [16:0] qv=$signed(head[7:0]);
wire signed [16:0] term=minus?-qv:(plus?qv:17'sd0);   // code 11 never occurs in the table; same convention as the gates
wire signed [16:0] nacc=(begin_op||(scale&&svalid&&!sbusy)||xfer)?17'sd0:(macs?acc+term:acc);
wire to_q1=take&&last,to_q2=qt1&&last,to_mac=(qack&&last)||(xfer&&!lastrow),to_scale=macs&&last,to_res=sdone,to_idle=xfer&&lastrow;
wire adv=take||qt1||qack||macs||sstart;
wire wrap=begin_op||to_q1||to_q2||to_mac||to_scale||to_res||to_idle;
wire [6:0] ncnt=wrap?7'd0:(adv?cnt+7'd1:cnt);
wire [6:0] nrow=(begin_op||to_idle)?7'd0:(xfer?row+7'd1:row);
reg [2:0] np;always @* begin np=ph;if(begin_op)np=1;if(to_q1)np=2;if(to_q2)np=3;if(to_mac)np=4;if(to_scale)np=5;if(to_res)np=6;if(to_idle)np=0;if(reset)np=0; end
wire signed [20:0] sm=x+sres;
wire [19:0] y=(sm>21'sd524287)?20'h7ffff:(sm< -21'sd524288)?20'h80000:sm[19:0];
wire [{quant_stream.NI-1}:0] qin={{keep&&q2,(keep&&q1)||(keep&&q2),head,9'd128,qstart,reset}};
wire [{scale_pipeline.NI-1}:0] sin={{18'd{alpha},qo[36:17],acc,sstart,reset}};
wire [{NO-1}:0] outs={{keep&&!idle,keep&&res&&xv,y,keep&&collect}};
assign dout={{row,cnt,sin,qin,outs,sd,qd,nacc,nrow,ncnt,np,nr}};
endmodule
'''


def connect(shell_net,tab,qn,sn):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2));qq=pins[SRG+SCT:SRG+SCT+SQU];qs=pins[SRG+SCT+SQU:NS];reset=pins[NS]
    c=pins[SRG:SRG+SCT];addr=c[3:10]+c[10:17]
    _,w=import_net(b,tab,addr)
    _,q0=import_net(b,qn,[reset]+[0]*(quant_stream.NI-1),qq)
    _,s0=import_net(b,sn,[reset]+[0]*(scale_pipeline.NI-1),qs)
    _,s1=import_net(b,shell_net,pins+[0]*SQU+q0+[0]*SSC+s0+w);o=NS+NO
    qin=s1[o:o+quant_stream.NI];sin=s1[o+quant_stream.NI:o+quant_stream.NI+scale_pipeline.NI];assert s1[o+quant_stream.NI+scale_pipeline.NI:]==addr
    qd,qo=import_net(b,qn,qin,qq);sd,so=import_net(b,sn,sin,qs)
    assert qo[:39]==q0[:39] and so==s0
    _,full=import_net(b,shell_net,pins+qd+qo+sd+so+w);assert full[o:]==qin+sin+addr
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def build(fault=None):
    tab,codes,al=table();qn=quant_stream.make();sn=scale_pipeline.make()
    sh=shell(al,fault);net,comb=connect(sh,tab,qn,sn);return dict(net=net,comb=comb,shell=sh,table=tab,codes=codes,alpha=al,quant=qn,scale=sn)


def cases():
    rng=random.Random(26100724);hs=[[0]*128,[524287]*128,[-524288]*128,[(-524288,524287)[i%2] for i in range(128)],list(range(-64,64))]
    hs+=[[rng.randrange(-524288,524288)>>sh for _ in range(128)] for sh in (0,0,3,8,13)]
    out=[]
    with tempfile.TemporaryDirectory() as t:
        g=golden_lib(t)
        for k,hv in enumerate(hs):
            x=[rng.randrange(-524288,524288)>>(k%4*5) for _ in range(128)] if k%3 else [(524287 if i%2 else -524288) for i in range(128)]
            y=(ct.c_int32*128)();g.oproj((ct.c_int32*128)(*hv),(ct.c_int32*128)(*x),y,0)
            out.append(dict(h=hv,x=x,y=list(y)))
    return out


def drive(net,cs,sim_so,seed=26100725):
    lib=ct.CDLL(str(sim_so));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    raw=net.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;buf=ct.create_string_buffer(3)
    rng=random.Random(seed);rows=[];stats=dict(vectors=0,aborts=0,stalls=0)
    def tick(xx):
        lib.nl_step(xx.to_bytes(6,'little'),buf);y=int.from_bytes(buf.raw,'little');rows.append((xx,y,(1<<NO)-1));return y
    tick(1);tick(0)
    def run(c,stall,abort_at=None):
        before=len(rows);sent=0;got=[]
        tick(2)
        while True:
            el=len(rows)-before
            if abort_at is not None and el==abort_at:tick(1|2);tick(0);stats['aborts']+=1;return
            hv=int(sent<128 and (not stall or rng.randrange(3)!=0));xv=int(not stall or rng.randrange(3)!=0);yr=int(not stall or rng.randrange(4)!=0)
            hval=c['h'][sent]&0xfffff if sent<128 else rng.getrandbits(20);xval=c['x'][len(got)]&0xfffff if len(got)<128 else 0
            junk=int(el>2 and rng.randrange(80)==0)
            y=tick((junk<<1)|(hv<<2)|(hval<<3)|(xv<<23)|(xval<<24)|(yr<<44))
            if hv and y&1:sent+=1
            if y>>21&1 and yr:
                got.append(y>>1&0xfffff)
                if len(got)==128:break
            elif y>>21&1:stats['stalls']+=1
            assert el<200000
        assert got==[v&0xfffff for v in c['y']],(got[:4],[v&0xfffff for v in c['y'][:4]])
        stats['vectors']+=1;tick(0)
    for k,c in enumerate(cs):run(c,stall=k%3==2)
    for at in (10,200,600,20000):run(cs[1],False,abort_at=at)
    run(cs[1],False)
    return rows,stats


def remote_check():
    g=build();cs=cases()
    with tempfile.TemporaryDirectory() as t:
        so=Path(t)/'s.so';subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(so)],check=True)
        rows,stats=drive(g['net'],cs,so)
        from gate_check import simulate
        tab_ok=simulate(g['table'],list(range(16384)))==g['codes']
        lib=ct.CDLL(str(so));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
        def replay(n):
            raw=n.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;out=ct.create_string_buffer(3);bad=0
            for xx,y,m in rows:lib.nl_step(xx.to_bytes(6,'little'),out);bad+=bool((int.from_bytes(out.raw,'little')^y)&m)
            return bad
        faults={k:replay(build(k)['net']) for k in ('no_writeback','bad_weight')};faults['output_flip']=replay(flip_output(g['net']))
    return dict(metrics=metrics(g['net']),table=metrics(g['table']),table_all_16384_equal_C=tab_ok,clocks=len(rows),stats=stats,faults=faults)


def cloud(g,cs):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks,sampler as smp
    from ci import cec
    from gate_check import verify as vtab
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'shell';prefix.with_suffix('.ref.v').write_text(reference(g['alpha']))
    ref=smp.mapped_reference(prefix,SHELL_IN,SHELL_OUT);proofs={}
    for k,n in [('source',g['shell']),('negative',flip_output(g['shell'])),('no_writeback',shell(g['alpha'],'no_writeback')),('bad_weight',shell(g['alpha'],'bad_weight')),('reference',ref)]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(n))
    for k,w in [('source','equivalent'),('negative','different'),('no_writeback','different'),('bad_weight','different')]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    table=vtab(g['table'],list(range(16384)),g['codes']);assert table['status']=='pass'
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    rows,stats=drive(g['net'],cs,OUT/'sim.so');(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO;faults={}
    for k,n in [('output_flip',flip_output(g['net'])),('no_writeback',build('no_writeback')['net']),('bad_weight',build('bad_weight')['net'])]:
        faults[k]=checks.check_nand(rows,n.encode());assert faults[k]>0,k
    bad=flip_output(g['net']);(OUT/'bad.v').write_text(rtl(bad,'oproj'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'oproj',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'oproj.v');checks.run([exe],1200)
    exe=checks.compile_rtl('negative',OUT/'bad.v');res=subprocess.run([str(exe)],capture_output=True,text=True,timeout=1200)
    (OUT/'rtl.negative.log').write_text(res.stdout+res.stderr);assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),weight_table_all_addresses=table,protocol=stats,clocks=len(rows),rtl_clocks=len(rows),actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--remote-check',action='store_true');a=ap.parse_args()
    if a.remote_check:print(json.dumps(remote_check()));return
    if not a.cloud:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);t0=time.monotonic();g=build()
    assert metrics(g['quant'])['sha256']=='0caf2626931329b14df86526aa4e294d020d7b4004a408c897045ce916e3550b'
    assert metrics(g['scale'])['sha256']=='444ddb4f2929b26cf03ef62b4670254d2b538d139490fed94cfaaa9c4b1d8b8b'
    cs=cases();(OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    for k in ('net','comb','shell','table'):(OUT/(k+'.nl')).write_bytes(g[k].encode())
    (OUT/'oproj.v').write_text(rtl(g['net'],'oproj'))
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/oproj_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='children bound to accepted R20 quantizer / R14 scale; C cases prepared; shell CEC, table, drive and RTL await Actions',
        metrics=metrics(g['net']),comb_metrics=metrics(g['comb']),shell=metrics(g['shell']),table=metrics(g['table']),alpha=g['alpha'],
        children=dict(quant=metrics(g['quant']),scale=metrics(g['scale'])),C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        contract='reset,start,h_valid,h20,x_valid,x20,y_ready -> h_ready,y20,y_valid(x_ready),busy; layer-0 O = linear(h,0,3), y = sat(x+o)',
        scope='one position: 128 attention outputs -> A8 -> layer-0 O projection -> residual with the caller-supplied old x',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(g,cs);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='shell CEC vs independent RTL, all 16,384 weights, actual-graph outputs == C, RTL replay and actual faults pass'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('oproj',metrics(g['net']),'cases',len(cs),round(time.monotonic()-t0,1))


if __name__=='__main__':main()
