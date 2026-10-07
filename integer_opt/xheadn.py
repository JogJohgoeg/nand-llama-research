#!/usr/bin/env python3
"""R120: R118 x -> token head with the stored top40 scores narrowed to SW bits.

The three glue levels (pick40, vocab_pick parent shell, x_head parent shell) are
restated with the sorter's state size/offsets as parameters; with SW=32 they rebuild
the accepted R113, R114 and R118 graphs byte for byte. Correctness of SW=27 rests on
(1) a universal embedding CEC of the sorter (top40n.embed_pair) and of the whole pick40
level (pick_embed_pair), (2) the model-constant bound |logit| < 2^26 over the scanner's
whole input domain, and (3) the actual graph driven against C int_pick on int_run cases.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_XHEADN_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_XHEADN_OUT',str(R/'build/integer_opt/xheadn')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from export import import_net,rtl
import top40,top40n,pick40,sampler,vocab_pick as vp,x_head_s as xh,final_a8s as fs,final_a8 as fa
sha=lambda b:hashlib.sha256(b).hexdigest()
SM=sampler.NS


def sizes(SW):
    L=top40n.layout(SW);SS=L['NS'];SP=SS+SM;SA=vp.SA;SV=SA+SP;SF=fs.NS
    return dict(SW=SW,L=L,SS=SS,SP=SP,SV=SV,SF=SF,
        VP_IN=SV+vp.NI+(SA+44)+(SP+11),VP_OUT=SV+vp.NO+32+68,
        XH_NS=SF+SV,XH_IN=SF+SV+xh.NI+(SF+xh.FO)+(SV+vp.NO),XH_OUT=SF+SV+xh.NO+fa.NI+vp.NI)


def sorter_comb(SW,bad_tie=False):
    return top40.make(bad_tie)[1] if SW==32 else top40n.make(SW,bad_tie)[1]


# ---- level 1: pick40 (sorter + replay shell + sampler) ----
def sorter_r(SW,bad=False,bad_tie=False):
    z=sizes(SW);L=z['L'];SS=z['SS'];comb=sorter_comb(SW,bad_tie)
    b=Builder(SS+37);q=list(range(2,SS+2));p=list(range(SS+2,SS+39))
    _,o=import_net(b,comb,q+p[:36]);ph=L['phase'];ix=L['index']
    _,s=import_net(b,pick40.replay_shell(bad),o[ph:ph+3]+o[ix:ix+8]+q[ph:ph+3]+[p[0],p[1],p[36]])
    nxt=o[:ix]+s[3:11]+o[ix+8:ph]+s[:3]
    return b.finish(nxt+o[SS:]),comb


def pick_connect(SW,scomb,mcomb,ungated=False):
    z=sizes(SW);SS=z['SS'];NS=z['SP'];ph=z['L']['phase'];NI=pick40.NI
    b=Builder(NS+NI);qs=list(range(2,SS+2));qm=list(range(SS+2,NS+2));p=list(range(NS+2,NS+NI+2))
    reset,start,smp=p[0],p[1],p[2];rnd=p[3:35];iv=p[35];score=p[36:68]
    eq=lambda bits,v:b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    sp=qs[ph:ph+3];mp=qm[0:3]
    begin=b.reduce([b.inv(reset),start,b.lor(eq(sp,0),eq(sp,6))]+([] if ungated else [b.lor(eq(mp,0),eq(mp,4))]),b.land,1)
    _,m0=import_net(b,mcomb,qm+[reset,begin,smp]+rnd+[0]*41)
    ready,rep=m0[SM+8],m0[SM+9]
    _,so=import_net(b,scomb,qs+[reset,begin,iv]+score+[ready,rep])
    carry=so[SS:SS+40];valid=so[SS+40]
    _,mo=import_net(b,mcomb,qm+[reset,begin,smp]+rnd+[valid]+carry)
    assert mo[SM+8]==ready and mo[SM+9]==rep
    outs=mo[SM:SM+8]+[so[SS+42],b.lor(so[SS+41],mo[SM+10]),b.land(so[SS+43],mo[SM+11])]
    comb=b.finish(so[:SS]+mo[:SM]+outs);return with_state(comb,NS),comb


def pick(SW,**kw):
    return pick_connect(SW,sorter_r(SW,kw.get('bad_replay',False),kw.get('bad_tie',False))[0],sampler.make(kw.get('sampler_fault'))[1],kw.get('ungated',False))


def pick_embed_pair(SW):
    """A = sign-extend(narrow pick40 transition); B = accepted R113 transition on the
    sign-extended state; inputs = narrow state + pick inputs with score sign-extended from SW."""
    z=sizes(SW);L=z['L'];E=L['E'];SS=z['SS'];NSn=z['SP'];NI=pick40.NI
    _,narrow=pick(SW);_,wide=pick(32)
    def ext_entry(bits):return bits[:SW]+[bits[SW-1]]*(32-SW)+bits[SW:E]
    def ext_sorter(s):
        out=[]
        for k in range(40):out+=ext_entry(s[k*E:(k+1)*E])
        out+=s[40*E:40*E+20]+ext_entry(s[L['carry']:L['carry']+E])+s[L['inserted']:L['inserted']+4]
        assert len(out)==1664;return out
    def ext_state(s):return ext_sorter(s[:SS])+s[SS:]
    graphs=[]
    for which in 'AB':
        b=Builder(NSn+NI);s=list(range(2,NSn+2));p=list(range(NSn+2,NSn+NI+2))
        pe=p[:36+SW]+[p[36+SW-1]]*(32-SW)
        if which=='A':_,o=import_net(b,narrow,s+pe);outs=ext_state(o[:NSn])+o[NSn:]
        else:_,o=import_net(b,wide,ext_state(s)+pe);outs=o
        graphs.append(b.finish(outs))
    return graphs


# ---- level 2: vocab_pick (scanner + pick40) ----
def vp_shell(SW,ungated=False):
    z=sizes(SW);SA=vp.SA;SP=z['SP'];NS=z['SV'];NI,NO=vp.NI,vp.NO;ph=z['L']['phase'];SS=z['SS']
    b=Builder(z['VP_IN']);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    s=list(range(NS+NI+2,NS+NI+2+SA+44));t=list(range(NS+NI+2+SA+44,z['VP_IN']+2))
    assert len(t)==SP+11
    sy=s[SA:];ty=t[SP:];qp=q[SA:]
    _,ctl=import_net(b,vp.control(),q[1417:1420]+qp[ph:ph+3]+qp[SS:SS+3]+p[:2]);begin=p[1] if ungated else ctl[0]
    sc=[p[0],begin]+p[2:31]+[ctl[1]];tc=[p[0],begin,p[31]]+p[32:64]+[sy[32]]+sy[:32]
    outputs=ty[:8]+[b.lor(sy[33],ty[9]),sy[34],ty[10]]
    return b.finish(s[:SA]+t[:SP]+outputs+sc+tc)


def vp_connect(SW,shell_net,scanner,pk):
    z=sizes(SW);SA=vp.SA;SP=z['SP'];NS=z['SV'];NI,NO=vp.NI,vp.NO
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2))
    _,first=import_net(b,shell_net,pins+[0]*(SA+44+SP+11));sc=first[NS+NO:NS+NO+32]
    sd,sy=import_net(b,scanner,sc,pins[:SA])
    _,second=import_net(b,shell_net,pins+sd+sy+[0]*(SP+11));tc=second[NS+NO+32:]
    assert second[NS+NO:NS+NO+32]==sc
    td,ty=import_net(b,pk,tc,pins[SA:NS]);assert ty[8]==sc[-1]
    _,full=import_net(b,shell_net,pins+sd+sy+td+ty);assert full[NS+NO:]==sc+tc
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def vp_reference(SW):
    z=sizes(SW);SA=vp.SA;SP=z['SP'];NS=z['SV'];I=z['VP_IN'];O=z['VP_OUT'];ph=z['L']['phase'];SS=z['SS']
    return f'''module top(input [{I-1}:0] din,output [{O-1}:0] dout);
wire [{NS-1}:0] q=din[{NS-1}:0];wire [63:0] p=din[{NS+63}:{NS}];
wire [{SA+43}:0] scanner=din[{NS+64+SA+43}:{NS+64}];wire [{SP+10}:0] pick=din[{I-1}:{NS+64+SA+44}];
wire [43:0] sy=scanner[{SA+43}:{SA}];wire [10:0] ty=pick[{SP+10}:{SP}];
wire [2:0] ap=q[1419:1417],tp=q[{SA+ph+2}:{SA+ph}],mp=q[{SA+SS+2}:{SA+SS}];
wire begin_op=!p[0] && p[1] && (ap==0 || ap==4) && (tp==0 || tp==6) && (mp==0 || mp==4);
wire ready=!p[0] && tp==1;
wire [31:0] sc={{ready,p[30:2],begin_op,p[0]}};
wire [67:0] tc={{sy[31:0],sy[32],p[63:31],begin_op,p[0]}};
wire [10:0] result={{ty[10],sy[34],(sy[33] || ty[9]),ty[7:0]}};
assign dout={{tc,sc,result,pick[{SP-1}:0],scanner[{SA-1}:0]}};
endmodule
'''


# ---- level 3: x_head (R117 front end + vocab_pick) ----
def xh_shell(SW,ungated=False,early=False):
    z=sizes(SW);SF=z['SF'];SV=z['SV'];NS=z['XH_NS'];NI,NO=xh.NI,xh.NO;FO,VO=xh.FO,vp.NO
    b=Builder(z['XH_IN']);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    f=list(range(NS+NI+2,NS+NI+2+SF+FO));v=list(range(NS+NI+2+SF+FO,z['XH_IN']+2))
    qf=q[:SF];fd,fo=f[:SF],f[SF:];vd,vo=v[:SV],v[SV:]
    reset,start=p[0],p[1];x=p[2:22];xvalid,smp=p[22],p[23];rnd=p[24:56]
    eq=lambda bits,k:b.reduce([w if k>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    keep=b.inv(reset);nph=qf[488:492];qph=qf[xh.QPH:xh.QPH+3];restarted=qf[xh.DONE]
    fidle=b.land(eq(nph,0),eq(qph,0));vdone=b.lor(b.inv(vo[8]),vo[10])
    begin=b.reduce([keep,start,fidle]+([] if ungated else [vdone]),b.land,1)
    mready=begin if early else b.reduce([keep,eq(nph,0),eq(qph,2),b.inv(restarted)],b.land,1)
    fi=[reset,begin]+x+[xvalid,vo[9]]
    vi=[reset,mready]+fo[8:28]+[fo[28]]+fo[0:8]+[smp]+rnd
    outs=vo[0:8]+[b.lor(fo[38],vo[8]),b.land(vo[10],fidle),fo[29]]+fo[30:37]+[fo[37]]
    return b.finish(fd+vd+outs+fi+vi)


def xh_connect(SW,shell_net,front,head):
    z=sizes(SW);SF=z['SF'];SV=z['SV'];NS=z['XH_NS'];NI,NO=xh.NI,xh.NO;FO,VO=xh.FO,vp.NO
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2));qf,qv=pins[:SF],pins[SF:NS]
    _,s1=import_net(b,shell_net,pins+[0]*(SF+FO+SV+VO));vi0=s1[NS+NO+fa.NI:]
    _,v0=import_net(b,head,vi0,qv);ready=v0[9]
    _,s2=import_net(b,shell_net,pins+[0]*(SF+FO)+[0]*SV+v0);fi=s2[NS+NO:NS+NO+fa.NI]
    fd,fo=import_net(b,front,fi,qf)
    _,s3=import_net(b,shell_net,pins+fd+fo+[0]*SV+v0);vi=s3[NS+NO+fa.NI:];assert s3[NS+NO:NS+NO+fa.NI]==fi
    vd,vo=import_net(b,head,vi,qv);assert vo[9]==ready
    _,full=import_net(b,shell_net,pins+fd+fo+vd+vo);assert full[NS+NO:]==fi+vi
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def xh_reference(SW):
    z=sizes(SW);SF=z['SF'];SV=z['SV'];NS=z['XH_NS'];FO,VO=xh.FO,vp.NO;I=z['XH_IN'];O=z['XH_OUT']
    o=NS+xh.NI;f0=o;v0=o+SF+FO;QPH,DONE=xh.QPH,xh.DONE
    return f'''module top(input [{I-1}:0] din,output [{O-1}:0] dout);
wire [{SF-1}:0] qf=din[{SF-1}:0];wire [55:0] p=din[{NS+55}:{NS}];
wire [{SF-1}:0] fd=din[{f0+SF-1}:{f0}];wire [{FO-1}:0] fo=din[{f0+SF+FO-1}:{f0+SF}];
wire [{SV-1}:0] vd=din[{v0+SV-1}:{v0}];wire [{VO-1}:0] vo=din[{v0+SV+VO-1}:{v0+SV}];
wire reset=p[0],start=p[1],xvalid=p[22],smp=p[23];wire [19:0] x=p[21:2];wire [31:0] rnd=p[55:24];
wire [3:0] nph=qf[491:488];wire [2:0] qph=qf[{QPH+2}:{QPH}];wire restarted=qf[{DONE}];
wire begin_op=!reset && start && nph==0 && qph==0 && (!vo[8] || vo[10]);
wire mready=!reset && nph==0 && qph==2 && !restarted;
wire [23:0] fi={{vo[9],xvalid,x,begin_op,reset}};
wire [63:0] vi={{rnd,smp,fo[7:0],fo[28],fo[27:8],mready,reset}};
wire [18:0] outs={{fo[37],fo[36:30],fo[29],(vo[10] && nph==0 && qph==0),(fo[38] || vo[8]),vo[7:0]}};
assign dout={{vi,fi,outs,vd,fd}};
endmodule
'''


def build(SW,scanner=None,front=None):
    scanner=scanner or vp.scanner_net()[0];front=front or fs.connect()[0]
    pk=pick(SW)[0];head=vp_connect(SW,vp_shell(SW),scanner,pk)[0]
    net,comb=xh_connect(SW,xh_shell(SW),front,head)
    return dict(pick=pk,head=head,joint=net,transition=comb,vp_shell=vp_shell(SW),xh_shell=xh_shell(SW),scanner=scanner,front=front)


def logit_bound():
    """|logit| upper bound over the scanner's whole input domain (any q8, any 20-bit max)."""
    blob=(R/'physical/model.bin').read_bytes()
    e=blob[243348:267924];esc=[int.from_bytes(blob[i:i+4],'little') for i in range(267924,268692,4)]
    def rne(n,d):
        q,r=divmod(n,d);return q+int(2*r>d or (2*r==d and q&1))
    rows=[sum(abs(v if v<128 else v-256) for v in e[r*128:(r+1)*128]) for r in range(192)]
    b=max(rne(rne(rows[r]*128*((1<<20)-1),127)*esc[r],1<<24) for r in range(192))
    return dict(bound=b,signed_bits_needed=b.bit_length()+1,escale_max=max(esc),abs_row_sum_max=max(rows))


def cloud(g,SW):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks,sampler as smp
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc;proofs={}
    d=OUT/'proofs';d.mkdir(exist_ok=True)
    def pair(name,x,y,want):
        (d/(name+'.a.blif')).write_text(blif(x));(d/(name+'.b.blif')).write_text(blif(y))
        proofs[name]=cec(abc,d/(name+'.a.blif'),d/(name+'.b.blif'),d/(name+'.log'));assert proofs[name]['verdict']==want,name
    A,B=top40n.embed_pair(SW);pair('sorter_embed',A,B,'equivalent')
    A2,_=top40n.embed_pair(SW,narrow_comb=top40n.make(SW,bad_tie=True)[1]);pair('sorter_embed_bad_tie',A2,B,'different')
    A,B=pick_embed_pair(SW);pair('pick_embed',A,B,'equivalent')
    for name,src,ref,ung in [('vp_shell',vp_shell(SW),vp_reference(SW),vp_shell(SW,True)),('xh_shell',xh_shell(SW),xh_reference(SW),xh_shell(SW,True))]:
        prefix=d/name;prefix.with_suffix('.ref.v').write_text(ref)
        r=smp.mapped_reference(prefix,src.n_in,src.n_out)
        pair(name,src,r,'equivalent');pair(name+'_negative',flip_output(src),r,'different');pair(name+'_ungated',ung,r,'different')
    xh.OUT=OUT;fa.OUT=OUT
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    cs=json.loads((OUT/'cases.json').read_text())
    rows,proto=xh.drive(g['joint'],cs);(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    checks.OUT=OUT;checks.NI=xh.NI;checks.NO=xh.NO;faults={}
    tie=xh_connect(SW,xh_shell(SW),g['front'],vp_connect(SW,vp_shell(SW),g['scanner'],pick(SW,bad_tie=True)[0])[0])[0]
    early=xh_connect(SW,xh_shell(SW,early=True),g['front'],g['head'])[0]
    for name,n in [('output_flip',flip_output(g['joint'])),('narrow_sorter_tie',tie),('early_head_start',early)]:
        faults[name]=checks.check_nand(rows,n.encode());assert faults[name]>0,name;(OUT/('bad_'+name+'.nl')).write_bytes(n.encode())
    bad=flip_output(g['joint']);(OUT/'bad.v').write_text(rtl(bad,'x_head_n'));(OUT/'tb.v').write_text(checks.testbench(xh.NI,xh.NO,'x_head_n',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'x_head_n.v');checks.run([exe],1800)
    exe=checks.compile_rtl('negative',OUT/'bad.v');res=subprocess.run([str(exe)],capture_output=True,text=True,timeout=1800)
    (OUT/'rtl.negative.log').write_text(res.stdout+res.stderr)
    assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    return dict(status='pass',proofs=proofs,protocol=proto,clocks=len(rows),rtl_clocks=len(rows),actual_fault_mismatches=faults,actual_rtl_fault_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--sw',type=int,default=27);a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    scanner=vp.scanner_net()[0];front=fs.connect()[0]
    wide=build(32,scanner,front)
    # Restatement: SW=32 rebuilds the accepted R113 / R114 / R118 graphs byte for byte.
    assert metrics(wide['pick'])['sha256']=='d4804af25de6c50aecc74a17a0db8f96c38a2d6251c87ce63ec116015a710d67'
    assert metrics(wide['head'])['sha256']=='8eb6fcb5a86d0a6d35a7f98386c033fb007ffbe5c270d63dc77077f23d127d48'
    assert metrics(wide['joint'])['sha256']=='b533750daaf41c86409a0f00dd1040b39c5bc643a83798fa768ce3d68b883887'
    nar=build(a.sw,scanner,front);bound=logit_bound();assert bound['signed_bits_needed']<=a.sw
    cs=xh.cases();(OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    for k in ('pick','head','joint','transition','vp_shell','xh_shell'):(OUT/(k+'.nl')).write_bytes(nar[k].encode())
    (OUT/'x_head_n.v').write_text(rtl(nar['joint'],'x_head_n'))
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted({Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/final_a8_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/sample_stream.c','integer_opt/top40_cases.json',
              'integer_opt/x_head_s_units/manifest.json','integer_opt/vocab_pick_units/manifest.json','integer_opt/vocab_pick_units/cases.json','integer_opt/pick40_units/manifest.json',
              'integer_opt/vocab_row_units/manifest.json','integer_opt/vocab_row_units/head_mac.nl','integer_opt/sample_weight_units/baseline.nl','integer_opt/sample_weight_units/manifest.json',
              'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl'):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='SW=32 restatements byte-identical to R113/R114/R118; narrow graphs, bound and int_run cases prepared; proofs/drive/RTL await Actions',
        SW=a.sw,logit_bound=bound,metrics=metrics(nar['joint']),R118=metrics(wide['joint']),nets={k:metrics(nar[k]) for k in ('pick','head','joint','vp_shell','xh_shell')},
        sorter=dict(narrow=metrics(top40n.make(a.sw)[0]),R109=metrics(top40.make()[0])),C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        claim='sorter stores SW-bit scores; equal to R109/R113 for every narrow state and sign-extended input (embedding CEC); the scanner never emits a logit outside SW bits (bound)',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(nar,a.sw);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='embedding/shell proofs, actual int_run tokens, RTL and actual faults pass'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','nets')},indent=1)[:2000])


if __name__=='__main__':main()
