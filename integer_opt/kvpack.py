#!/usr/bin/env python3
"""R122: K/V packer for one attention head word (whole-layer piece 2).

Collects the 32 results of one head (rows 32j..32j+31 of the Q/K/V producer, in order)
into a rotating 32 x 20-bit ring. For K it applies RoPE to pairs (i,i+16) with the
accepted R121 unit (head of ring and entry 16, rotate one per pair, then 16 more to
realign). Then the accepted R20 quantizer (n=32) scans the ring once for the maximum and
converts on the replay; each q8 is written back over the entry that just rotated to the
tail. The head word is the low bytes of the ring plus m20 = exactly the R72 head's
276-bit LOAD word (lane i at bits 8i..8i+7, m at 256..275) = C rope (K only) + quant(.,32).
Interface: reset,start,kmode,pos4,in_valid,in20,word_ready -> word276,word_valid,in_ready,busy.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_KVPACK_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_KVPACK_OUT',str(R/'build/integer_opt/kvpack')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from export import import_net,rtl,MODEL_SHA
import rope,quant_stream
NI,NO=29,279
SRG,SCT=640,14;SRO,SQU=rope.NS,169
NS=SRG+SCT+SRO+SQU
RO,QO=rope.NO,41
SHELL_IN=NS+NI+(SRO+RO)+(SQU+QO);SHELL_OUT=NS+NO+rope.NI+quant_stream.NI
sha=lambda b:hashlib.sha256(b).hexdigest()


def shell(fault=None):
    """Glue only; child next-state/outputs are free inputs. ctl = phase3,cnt6,kmode1,pos4."""
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    o=NS+NI+2;rd=list(range(o,o+SRO));ro=list(range(o+SRO,o+SRO+RO));o+=SRO+RO
    qd=list(range(o,o+SQU));qo=list(range(o+SQU,o+SQU+QO))
    ring=[q[20*k:20*k+20] for k in range(32)];ctl=q[SRG:SRG+SCT];ph,cnt,km,pos=ctl[0:3],ctl[3:9],ctl[9],ctl[10:14]
    qst=q[SRG+SCT+SRO:NS];qph=qst[153:156]
    reset,start,kmode=p[0],p[1],p[2];pin=p[3:7];iv=p[7];x=p[8:28];wready=p[28]
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0)
    eq=lambda bits,v:AND(*[w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)])
    keep=b.inv(reset);P=[eq(ph,i) for i in range(7)]
    idle,collect,ropep,align,q1,q2,present=P
    rready,rvalid=ro[41],ro[40];qxr,qyv=qo[37],qo[38];qidle=eq(qph,0)
    begin=AND(keep,start,idle)
    take_in=AND(keep,collect,iv)
    rload=AND(keep,ropep,rready);rack=AND(keep,ropep,rvalid)
    rot_align=AND(keep,align)
    qstart=AND(keep,q1,eq(cnt,0),qidle)
    qtake1=AND(keep,q1,qxr);qtake2=AND(keep,q2,qxr);qack=AND(keep,q2,qyv)
    last=eq(cnt,31);last16=eq(cnt,15)
    rotate=OR(take_in,rack,rot_align,qtake1,qtake2)
    # ring next: rotate left one (r[k] <- r[k+1], r[31] <- r[0]) with writes
    head=ring[0];mid=ring[16]
    new_tail=[b.mux(take_in,b.mux(rack,h,xr),xi) for h,xr,xi in zip(head,ro[0:20],x)]
    yv=ro[20:40]
    nring=[]
    for k in range(32):
        src=ring[(k+1)%32]
        if k==31:src=new_tail
        if k==15:src=[b.mux(rack,s,y) for s,y in zip(ring[16],yv)]
        cur=ring[k]
        if k==31:
            # q2 ack writes q8 (sign-extended) over the tail entry (the element just converted)
            q8=qo[0:8]+[qo[7]]*12;cur=[b.mux(qack,c,v) for c,v in zip(cur,q8)] if fault!='no_writeback' else cur
        nring+= [b.mux(rotate,c,s) for c,s in zip(cur,src)]
    # control
    inc=b.add(cnt,[0]*6,1)[0]
    adv=OR(take_in,rack,rot_align,qtake1,qack)
    def clear_on(*conds):return OR(*conds)
    to_rope=AND(take_in,last,km);to_q1=OR(AND(take_in,last,b.inv(km)),AND(rot_align,last16))
    to_align=AND(rack,last16);to_q2=AND(qtake1,last);to_present=AND(qack,last);done_ack=AND(keep,present,wready)
    wrap=OR(to_rope,to_q1,to_align,to_q2,to_present,begin)
    ncnt=[AND(b.inv(wrap),b.mux(adv,c,i)) for c,i in zip(cnt,inc)]
    np_=ph[:]
    def setp(en,v):
        nonlocal np_
        np_=[b.mux(en,x_,v>>i&1) for i,x_ in enumerate(np_)]
    setp(begin,1);setp(to_rope,2);setp(to_align,3);setp(to_q1,4);setp(to_q2,5);setp(to_present,6);setp(done_ack,0)
    np_=[AND(keep,v) for v in np_]
    nkm=b.mux(begin,km,kmode);npos=[b.mux(begin,a,v) for a,v in zip(pos,pin)]
    nctl=np_+ncnt+[nkm]+npos
    # child inputs
    addr=cnt[0:4]+pos
    rin=[reset,rload]+addr+head+mid+[rack]
    n32=[32>>i&1 for i in range(9)]
    qin=[reset,qstart]+n32+head+[OR(AND(q1,keep),AND(q2,keep)),AND(keep,q2)]
    assert len(rin)==rope.NI and len(qin)==quant_stream.NI
    word=[w for k in range(32) for w in ring[k][0:8]]+qo[17:37]
    outs=word+[AND(keep,present),AND(keep,collect),AND(keep,b.inv(idle))]
    assert len(nring)==SRG
    return b.finish(nring+nctl+rd+qd+outs+rin+qin)


def reference():
    o=NS+NI;ro0=o+SRO;qd0=ro0+RO;qo0=qd0+SQU
    return f'''module top(input [{SHELL_IN-1}:0] din,output [{SHELL_OUT-1}:0] dout);
wire [{SRG-1}:0] ring=din[{SRG-1}:0];wire [2:0] ph=din[{SRG+2}:{SRG}];wire [5:0] cnt=din[{SRG+8}:{SRG+3}];wire km=din[{SRG+9}];wire [3:0] pos=din[{SRG+13}:{SRG+10}];
wire [2:0] qph=din[{SRG+SCT+SRO+155}:{SRG+SCT+SRO+153}];
wire [{NI-1}:0] p=din[{NS+NI-1}:{NS}];
wire [{SRO-1}:0] rd=din[{ro0-1}:{o}];wire [{RO-1}:0] ro=din[{qd0-1}:{ro0}];wire [{SQU-1}:0] qd=din[{qo0-1}:{qd0}];wire [{QO-1}:0] qo=din[{SHELL_IN-1}:{qo0}];
wire reset=p[0],start=p[1],kmode=p[2];wire [3:0] pin=p[6:3];wire iv=p[7];wire [19:0] x=p[27:8];wire wready=p[28];
wire keep=!reset;
wire idle=ph==0,collect=ph==1,ropep=ph==2,align=ph==3,q1=ph==4,q2=ph==5,present=ph==6;
wire rready=ro[41],rvalid=ro[40],qxr=qo[37],qyv=qo[38],qidle=qph==0;
wire begin_op=keep&&start&&idle,take_in=keep&&collect&&iv,rload=keep&&ropep&&rready,rack=keep&&ropep&&rvalid;
wire rot_align=keep&&align,qstart=keep&&q1&&cnt==0&&qidle,qtake1=keep&&q1&&qxr,qtake2=keep&&q2&&qxr,qack=keep&&q2&&qyv;
wire last=cnt==31,last16=cnt==15;
wire rotate=take_in||rack||rot_align||qtake1||qtake2;
wire [19:0] head=ring[19:0],mid=ring[339:320];
wire [19:0] q8x={{{{12{{qo[7]}}}},qo[7:0]}};
wire [19:0] ntail=take_in?x:(rack?ro[19:0]:head);
wire [{SRG-1}:0] rot={{ntail,ring[639:340],(rack?ro[39:20]:ring[339:320]),ring[319:20]}};
wire [{SRG-1}:0] nr=rotate?rot:(qack?{{q8x,ring[619:0]}}:ring);
wire to_rope=take_in&&last&&km,to_q1=(take_in&&last&&!km)||(rot_align&&last16),to_align=rack&&last16,to_q2=qtake1&&last,to_present=qack&&last,done_ack=keep&&present&&wready;
wire adv=take_in||rack||rot_align||qtake1||qack;
wire wrap=to_rope||to_q1||to_align||to_q2||to_present||begin_op;
wire [5:0] ncnt=wrap?6'd0:(adv?cnt+6'd1:cnt);
reg [2:0] np;
always @* begin np=ph;if(begin_op)np=1;if(to_rope)np=2;if(to_align)np=3;if(to_q1)np=4;if(to_q2)np=5;if(to_present)np=6;if(done_ack)np=0;if(reset)np=0; end
wire nkm=begin_op?kmode:km;wire [3:0] npos=begin_op?pin:pos;
wire [{rope.NI-1}:0] rin={{rack,mid,head,pos,cnt[3:0],rload,reset}};
wire [{quant_stream.NI-1}:0] qin={{keep&&q2,(keep&&q1)||(keep&&q2),head,9'd32,qstart,reset}};
wire [255:0] wbytes;genvar g;generate for(g=0;g<32;g=g+1) begin:wb assign wbytes[g*8 +: 8]=ring[g*20 +: 8]; end endgenerate
wire [{NO-1}:0] outs={{keep&&!idle,keep&&collect,keep&&present,qo[36:17],wbytes}};
assign dout={{qin,rin,outs,qd,rd,npos,nkm,ncnt,np,nr}};
endmodule
'''


def connect(shell_net,ropenet,quantnet):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2));qr=pins[SRG+SCT:SRG+SCT+SRO];qq=pins[SRG+SCT+SRO:NS];reset=pins[NS]
    # state/reset-only child outputs first (inputs other than reset are placeholders)
    _,r0=import_net(b,ropenet,[reset]+[0]*(rope.NI-1),qr)
    _,q0=import_net(b,quantnet,[reset]+[0]*(quant_stream.NI-1),qq)
    _,s1=import_net(b,shell_net,pins+[0]*SRO+r0+[0]*SQU+q0);rin=s1[NS+NO:NS+NO+rope.NI];qin=s1[NS+NO+rope.NI:]
    rd,ro=import_net(b,ropenet,rin,qr);qd,qo=import_net(b,quantnet,qin,qq)
    for a_,b_ in ((ro[40],r0[40]),(ro[41],r0[41]),(qo[37],q0[37]),(qo[38],q0[38])):assert a_==b_
    assert ro[:40]==r0[:40] and qo[:37]==q0[:37]
    _,full=import_net(b,shell_net,pins+rd+ro+qd+qo);assert full[NS+NO:]==rin+qin
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def golden_lib(tmp):
    so=Path(tmp)/'g.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer'),
        str(Path(__file__).with_name('kvpack_golden.c')),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
    g.kv_word.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.c_int,ct.POINTER(ct.c_int8)];g.kv_word.restype=ct.c_int32
    return g


def cases():
    rng=random.Random(26100722);vs=[[0]*32,[524287]*32,[-524288]*32,[(-524288,524287)[i%2] for i in range(32)],list(range(-16,16)),[1]*32]
    vs+=[[rng.randrange(-524288,524288)>>sh for _ in range(32)] for sh in (0,0,2,6,10,15)]
    out=[]
    with tempfile.TemporaryDirectory() as t:
        g=golden_lib(t)
        for k,v in enumerate(vs):
            for kmode in (0,1):
                pos=(k*5+kmode*3)%16;q=(ct.c_int8*32)();m=g.kv_word((ct.c_int32*32)(*v),pos,kmode,q)
                word=sum((x&255)<<(8*i) for i,x in enumerate(q))|(m<<256)
                out.append(dict(a=v,pos=pos,kmode=kmode,word=hex(word)))
    return out


def drive(net,cs,sim_so,seed=26100723):
    """Reactively drive the actual graph (nl_sim); every word must equal C."""
    lib=ct.CDLL(str(sim_so));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    raw=net.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;buf=ct.create_string_buffer((NO+7)//8)
    rng=random.Random(seed);rows=[];stats=dict(words=0,aborts=0,ignored_starts=0,in_stalls=0,out_stalls=0)
    def tick(x):
        lib.nl_step(x.to_bytes(4,'little'),buf);y=int.from_bytes(buf.raw,'little');rows.append((x,y,(1<<NO)-1));return y
    tick(1);tick(0)
    def run(c,stall,abort_at=None):
        before=len(rows);sent=0
        tick(2|(c['kmode']<<2)|(c['pos']<<3))
        while True:
            el=len(rows)-before
            if abort_at is not None and el==abort_at:tick(1|2);tick(0);stats['aborts']+=1;return
            iv=int(sent<32 and (not stall or rng.randrange(3)!=0));wr=int(not stall or rng.randrange(3)!=0)
            junk=int(el>2 and rng.randrange(60)==0)
            y=tick((junk<<1)|(rng.getrandbits(1)<<2)|(rng.getrandbits(4)<<3)|(iv<<7)|((c['a'][sent]&0xfffff if sent<32 else rng.getrandbits(20))<<8)|(wr<<28))
            if iv and y>>277&1:sent+=1
            elif iv:stats['in_stalls']+=1
            if y>>276&1:
                if not wr:stats['out_stalls']+=1;continue
                assert (y&((1<<276)-1))==int(c['word'],16),(hex(y&((1<<276)-1)),c['word'])
                stats['words']+=1;break
            assert el<20000
        tick(0)
    for k,c in enumerate(cs):run(c,stall=k%3==2)
    for at in (5,40,400,900):run(cs[1],False,abort_at=at)
    run(cs[1],False)
    return rows,stats


def remote_check():
    """m149 only: actual graph vs C, plus actual faults."""
    rn,_=rope.make();qn=quant_stream.make();net,comb=connect(shell(),rn,qn);cs=cases()
    with tempfile.TemporaryDirectory() as t:
        so=Path(t)/'s.so';subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(so)],check=True)
        rows,stats=drive(net,cs,so)
        lib=ct.CDLL(str(so));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
        def replay(n):
            raw=n.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;out=ct.create_string_buffer((NO+7)//8);bad=0
            for x,y,m in rows:lib.nl_step(x.to_bytes(4,'little'),out);bad+=bool((int.from_bytes(out.raw,'little')^y)&m)
            return bad
        faults=dict(output_flip=replay(flip_output(net)),no_writeback=replay(connect(shell('no_writeback'),rn,qn)[0]))
    return dict(clocks=len(rows),stats=stats,faults=faults)


def cloud(net,cs):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks,sampler as smp
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'shell';prefix.with_suffix('.ref.v').write_text(reference())
    ref=smp.mapped_reference(prefix,SHELL_IN,SHELL_OUT);proofs={}
    for k,g,w in [('source',shell(),'equivalent'),('negative',flip_output(shell()),'different'),('no_writeback',shell('no_writeback'),'different')]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(g))
    prefix.with_suffix('.reference.blif').write_text(blif(ref))
    for k,w in [('source','equivalent'),('negative','different'),('no_writeback','different')]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    rows,stats=drive(net,cs,OUT/'sim.so');(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO;faults={}
    rn,_=rope.make();qn=quant_stream.make()
    for k,g in [('output_flip',flip_output(net)),('no_writeback',connect(shell('no_writeback'),rn,qn)[0])]:
        faults[k]=checks.check_nand(rows,g.encode());assert faults[k]>0,k
    bad=flip_output(net);(OUT/'bad.v').write_text(rtl(bad,'kvpack'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'kvpack',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'kvpack.v');checks.run([exe],600)
    exe=checks.compile_rtl('negative',OUT/'bad.v');res=subprocess.run([str(exe)],capture_output=True,text=True,timeout=600)
    (OUT/'rtl.negative.log').write_text(res.stdout+res.stderr);assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),protocol=stats,clocks=len(rows),rtl_clocks=len(rows),actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--remote-check',action='store_true');a=ap.parse_args()
    if a.remote_check:print(json.dumps(remote_check()));return
    if not a.cloud:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);t0=time.monotonic()
    rn,_=rope.make();qn=quant_stream.make()
    assert metrics(rn)['sha256']=='5addc5d76b2a487467443d2e818f1895aea8ae142af3a93e77967f815024a641'
    assert metrics(qn)['sha256']=='0caf2626931329b14df86526aa4e294d020d7b4004a408c897045ce916e3550b'
    net,comb=connect(shell(),rn,qn);cs=cases()
    (OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    for k,g in (('kvpack',net),('transition',comb),('shell',shell())):(OUT/(k+'.nl')).write_bytes(g.encode())
    (OUT/'kvpack.v').write_text(rtl(net,'kvpack'))
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/kvpack_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='children bound to accepted R121 RoPE / R20 quantizer; C cases prepared; shell CEC, drive and RTL await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),shell=metrics(shell()),children=dict(rope=metrics(rn),quant=metrics(qn)),
        C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        contract='reset,start,kmode,pos4,in_valid,in20,word_ready -> word276 (lane i bits 8i..8i+7, m20 bits 256..275),word_valid,in_ready,busy',
        scope='one head word: 32 consecutive head results -> (K: RoPE) -> KV8 quant -> R72 LOAD word; caller supplies rows and position',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(net,cs);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='shell CEC vs independent RTL, actual-graph words == C, RTL replay and actual faults pass'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('kvpack',metrics(net),'cases',len(cs),round(time.monotonic()-t0,1))


if __name__=='__main__':main()
