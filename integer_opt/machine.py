#!/usr/bin/env python3
"""R131: the whole Bard machine as one self-running graph: tokens -> next token.

Children (imported unchanged): R129 five-layer model (model5, bank behind its host port) and
R130 x -> token head with the E8/escale lookup port. The parent shell (proved alone against an
independent behavioural RTL) holds a 16 x 8-bit token file and runs, after start:
 E  for each position p < n and element i: x0 = sat(RNE(E8[token][i] * escale[token], 4096))
    (int_model.c:121) from the head's own tables through the port, |E8|*escale by an 8-clock
    shift-add, written to bank rows 4p..4p+3 (32 values each, write echo consumed);
 M  model5 start with L = n, wait done (the bank then holds x after layer 4);
 H  head start; bank rows 4(n-1)..4(n-1)+3 streamed three times into the head; the token is
    C int_pick(int_run logits of position n-1, random, sample).
Interface: reset, start, tvalid, token8 (appends to the token file while idle), sample, random32
(held during a run) -> token8, done, busy, n5.
"""
from pathlib import Path
import argparse,hashlib,json,os,random,shutil,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_MACHINE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_MACHINE_OUT',str(R/'build/integer_opt/machine')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from export import import_net,rtl
NI,NO=44,15
CTL=dict(ph=4,n=5,tf=128,p=5,c=2,j=5,k=4,acc=26,pas=2,tok=8,done=1)
SCT=sum(CTL.values())
ORDER=['model','head']
sha=lambda b:hashlib.sha256(b).hexdigest()


def children(model=None):
    import model5,xhead_port
    ch={'model':model or model5.build()['net']}
    ch['head']=xhead_port.splice(xhead_port.r120())[0]
    return ch


def layout(ch):
    off={};o=SCT
    for k in ORDER:off[k]=(o,ch[k].n_state);o+=ch[k].n_state
    return off,o


def shell(ch,fault=None):
    off,NS=layout(ch)
    nin=NS+NI+sum(ch[k].n_state+ch[k].n_out for k in ORDER)
    b=Builder(nin);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    o=NS+NI+2;cd={};co={}
    for k in ORDER:
        cd[k]=list(range(o,o+ch[k].n_state));o+=ch[k].n_state;co[k]=list(range(o,o+ch[k].n_out));o+=ch[k].n_out
    f={};i=0
    for k,n in CTL.items():f[k]=q[i:i+n];i+=n
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0)
    eq=lambda bits,v:AND(*[w if v>>t&1 else b.inv(w) for t,w in enumerate(bits)])
    same=lambda xs,ys:AND(*[b.inv(b.xor(x,y)) for x,y in zip(xs,ys)])
    add1=lambda bits:b.add(bits,[0]*len(bits),1)[0]
    reset,start,tvalid=p[0],p[1],p[2];token=p[3:11];sample=p[11];rnd=p[12:44]
    keep=b.inv(reset);ph=f['ph'];S=lambda v:eq(ph,v)
    M,H=co['model'],co['head']
    bk_data=M[0:20];bk_inready=M[20];bk_valid=M[21];bk_busy=M[23];m_done=M[26]
    h_done=H[9];h_xready=H[10];e8=H[19:27];g18=H[27:45]
    n,tf,pp,cc,jj,kk,acc,pas,tok,done=f['n'],f['tf'],f['p'],f['c'],f['j'],f['k'],f['acc'],f['pas'],f['tok'],f['done'][0]
    idle=S(0)
    # --- token file ---
    tput=AND(keep,idle,tvalid,b.inv(n[4]))                       # append while n < 16
    ntf=[]
    for s in range(16):
        w=AND(tput,eq(n[:4],s))
        ntf+=[b.mux(w,x,y) for x,y in zip(tf[8*s:8*s+8],token)]
    cur=[b.reduce([AND(eq(pp[:4],s),tf[8*s+t]) for s in range(16)],b.lor,0) for t in range(8)]   # token at position p
    # --- events ---
    begin=AND(keep,start,idle,b.inv(eq(n,0)))
    p_end=same(pp,n)                                             # p == n (5 bits: n may be 16)
    e1go=AND(keep,S(1),b.inv(p_end),b.inv(bk_busy));e1model=AND(keep,S(1),p_end)
    mac=AND(keep,S(2));k_last=eq(kk,7)
    put=AND(keep,S(3),bk_inready);put_last=AND(put,eq(jj,31))
    echo=AND(keep,S(4),bk_valid);echo_last=AND(echo,eq(jj,31));row_last=eq(cc,3)
    m_start=AND(keep,S(5))
    m_end=AND(keep,S(6),m_done)
    h_start=AND(keep,S(7))
    h_row=AND(keep,S(8),b.inv(bk_busy))
    xfer=AND(keep,S(9),bk_valid,h_xready);xfer_last=AND(xfer,eq(jj,31));all_rows=AND(row_last,eq(pas,2))
    h_end=AND(keep,S(10),h_done)
    # --- next phase ---
    np_=ph[:]
    def setp(en,v):
        nonlocal np_
        np_=[b.mux(en,x,v>>t&1) for t,x in enumerate(np_)]
    setp(begin,1);setp(e1go,2);setp(e1model,5);setp(AND(mac,k_last),3)
    setp(AND(put,b.inv(put_last)),2);setp(put_last,4);setp(echo_last,1)
    setp(m_start,6);setp(m_end,7);setp(h_start,8);setp(h_row,9)
    setp(AND(xfer_last,b.inv(all_rows)),8);setp(AND(xfer_last,all_rows),10);setp(h_end,0)
    np_=[AND(keep,x) for x in np_]
    # --- counters ---
    def cnt(cur_,inc,clear,en):return [AND(b.inv(clear),b.mux(en,x,y)) for x,y in zip(cur_,inc)]
    nn=[AND(keep,x) for x in cnt(n,add1(n),0,tput)]
    npp=cnt(pp,add1(pp),begin,AND(echo_last,row_last))
    ncc=cnt(cc,add1(cc),OR(begin,m_end),OR(echo_last,xfer_last))
    njj=cnt(jj,add1(jj),OR(begin,put_last,echo_last,m_end,xfer_last),OR(put,echo,xfer))
    nkk=cnt(kk,add1(kk),OR(begin,AND(mac,k_last),put),mac)
    npas=cnt(pas,add1(pas),OR(begin,m_end),AND(xfer_last,row_last))
    # MAC: acc = (acc + bit_k(|e|) * (escale<<8)) >> 1, eight times -> |e|*escale
    neg=e8[7];mag=b.add([b.xor(v,neg) for v in e8],[0]*8,neg)[0]           # |e| (8 bits, 128 fits)
    bit=b.reduce([AND(eq(kk,t),mag[t]) for t in range(8)],b.lor,0)
    if fault=='mac_msb_first':bit=b.reduce([AND(eq(kk,t),mag[7-t]) for t in range(8)],b.lor,0)
    addend=[0]*8+[AND(bit,v) for v in g18]
    s,co_=b.add(acc,addend)
    shifted=s[1:]+[co_]
    nacc=[AND(keep,b.inv(OR(begin,put)),b.mux(mac,a,v)) for a,v in zip(acc,shifted)]
    # x0 = sign * RNE(|e|*escale, 4096); |x0| <= 8192
    qq=acc[12:26];rr=acc[0:12]
    half=AND(acc[11],b.inv(b.reduce(acc[0:11],b.lor,0)))                 # r == 2048
    above=AND(acc[11],b.reduce(acc[0:11],b.lor,0))                       # r > 2048
    inc=OR(above,AND(half,qq[0])) if fault!='round_half_up' else acc[11]
    qr=b.add(qq+[0],[0]*15,inc)[0]                                       # keep the carry: CEC is over every acc value
    x0=[b.xor(v,neg) for v in qr+[0]*5]
    x0=b.add(x0,[0]*20,neg)[0]
    ntok=[AND(keep,b.mux(h_end,a,v)) for a,v in zip(tok,H[0:8])]
    ndone=[AND(keep,b.inv(begin),OR(done,h_end))]
    nctl=np_+nn+ntf+npp+ncc+njj+nkk+nacc+npas+ntok+ndone;assert len(nctl)==SCT
    # --- child inputs ---
    # model5: reset,start,L5,host bank port (start,row6,mode2,data20,valid,read)
    wrow=cc+pp[:4];hrow=cc+b.add(pp[:4],[1]*4,0)[0]                      # 4p+c ; 4(n-1)+c uses p=n here
    bstart=OR(e1go,h_row);brow=[b.mux(S(1),y,x) for x,y in zip(wrow,hrow)]
    bmode=[h_row,0]
    m_in=[reset,m_start]+n+[bstart]+brow+bmode+x0+[put,OR(echo,xfer)]
    # head: reset,start,x20,xvalid,sample,random32 + port_enable,row8,col7
    col=jj+cc
    h_in=[reset,h_start]+bk_data+[AND(keep,S(9),bk_valid)]+[sample]+rnd+[b.inv(OR(S(0),S(7),S(8),S(9),S(10)))]+cur+col
    assert len(m_in)==ch['model'].n_in and len(h_in)==ch['head'].n_in,(len(m_in),len(h_in))
    outs=tok+[AND(keep,done),AND(keep,b.inv(idle))]+n
    nd=[w for k in ORDER for w in cd[k]]
    return b.finish(nctl+nd+outs+m_in+h_in)


def reference(ch):
    """Independent behavioural RTL of the machine shell (child next-states pass through)."""
    off,NS=layout(ch);sts={k:ch[k].n_state for k in ORDER};outn={k:ch[k].n_out for k in ORDER};inn={k:ch[k].n_in for k in ORDER}
    nin=NS+NI+sum(sts[k]+outn[k] for k in ORDER);nout=SCT+sum(sts.values())+NO+sum(inn.values())
    lines=[f'module top(input [{nin-1}:0] din,output [{nout-1}:0] dout);',f'wire [{NI-1}:0] p=din[{NS+NI-1}:{NS}];']
    o=0
    for k,n in CTL.items():lines.append(f'wire [{n-1}:0] c_{k}=din[{o+n-1}:{o}];');o+=n
    o=NS+NI
    for k in ORDER:
        lines.append(f'wire [{sts[k]-1}:0] d_{k}=din[{o+sts[k]-1}:{o}];');o+=sts[k]
        lines.append(f'wire [{outn[k]-1}:0] o_{k}=din[{o+outn[k]-1}:{o}];');o+=outn[k]
    lines.append('''wire reset=p[0],start=p[1],tvalid=p[2];wire [7:0] token=p[10:3];wire sample=p[11];wire [31:0] rnd=p[43:12];
wire keep=!reset;wire [3:0] ph=c_ph;wire [4:0] n=c_n;wire [127:0] tf=c_tf;wire [4:0] pp=c_p;wire [1:0] cc=c_c;wire [4:0] jj=c_j;wire [3:0] kk=c_k;
wire [25:0] acc=c_acc;wire [1:0] pas=c_pas;wire [7:0] tok=c_tok;wire done=c_done[0];
wire [19:0] bk_data=o_model[19:0];wire bk_inready=o_model[20],bk_valid=o_model[21],bk_busy=o_model[23],m_done=o_model[26];
wire h_done=o_head[9],h_xready=o_head[10];wire signed [7:0] e8=o_head[26:19];wire [17:0] g18=o_head[44:27];
wire idle=ph==0;
wire tput=keep&&idle&&tvalid&&n<16;
reg [127:0] ntf;integer s;always @* begin ntf=tf;if(tput)ntf[8*n[3:0]+:8]=token; end
wire [7:0] cur=tf[8*pp[3:0]+:8];
wire begin_op=keep&&start&&idle&&n!=0;
wire p_end=pp==n;
wire e1go=keep&&ph==1&&!p_end&&!bk_busy,e1model=keep&&ph==1&&p_end;
wire mac=keep&&ph==2,k_last=kk==7;
wire put=keep&&ph==3&&bk_inready,put_last=put&&jj==31;
wire echo=keep&&ph==4&&bk_valid,echo_last=echo&&jj==31,row_last=cc==3;
wire m_start=keep&&ph==5,m_end=keep&&ph==6&&m_done,h_start=keep&&ph==7,h_row=keep&&ph==8&&!bk_busy;
wire xfer=keep&&ph==9&&bk_valid&&h_xready,xfer_last=xfer&&jj==31,all_rows=row_last&&pas==2;
wire h_end=keep&&ph==10&&h_done;
reg [3:0] np;always @* begin np=ph;
 if(begin_op)np=1; if(e1go)np=2; if(e1model)np=5; if(mac&&k_last)np=3; if(put&&!put_last)np=2; if(put_last)np=4; if(echo_last)np=1;
 if(m_start)np=6; if(m_end)np=7; if(h_start)np=8; if(h_row)np=9; if(xfer_last&&!all_rows)np=8; if(xfer_last&&all_rows)np=10; if(h_end)np=0;
 if(reset)np=0; end
wire [4:0] nn=reset?5'd0:(tput?n+5'd1:n);
wire [4:0] npp=begin_op?5'd0:((echo_last&&row_last)?pp+5'd1:pp);
wire [1:0] ncc=(begin_op||m_end)?2'd0:((echo_last||xfer_last)?cc+2'd1:cc);
wire [4:0] njj=(begin_op||put_last||echo_last||m_end||xfer_last)?5'd0:((put||echo||xfer)?jj+5'd1:jj);
wire [3:0] nkk=(begin_op||(mac&&k_last)||put)?4'd0:(mac?kk+4'd1:kk);
wire [1:0] npas=(begin_op||m_end)?2'd0:((xfer_last&&row_last)?pas+2'd1:pas);
wire [7:0] mag=e8[7]?-e8:e8;
wire [26:0] sum={1'b0,acc}+(mag[kk[2:0]]&&kk<8?{1'b0,g18,8'd0}:27'd0);
wire [25:0] nacc=(reset||begin_op||put)?26'd0:(mac?sum[26:1]:acc);
wire [13:0] qq=acc[25:12];wire [11:0] rr=acc[11:0];
wire inc=(rr>12'd2048)||(rr==12'd2048&&qq[0]);
wire [19:0] qr={6'd0,qq}+{19'd0,inc};
wire [19:0] x0=e8[7]?-qr:qr;
wire [7:0] ntok=reset?8'd0:(h_end?o_head[7:0]:tok);
wire ndone=keep&&!begin_op&&(done||h_end);
wire [5:0] wrow={pp[3:0],cc},hrow={pp[3:0]-4'd1,cc};
wire bstart=e1go||h_row;wire [5:0] brow=(ph==1)?wrow:hrow;
wire [37:0] m_in={echo||xfer,put,x0,1'b0,h_row,brow,bstart,n,m_start,reset};
wire pen=!(ph==0||ph==7||ph==8||ph==9||ph==10);
wire [71:0] h_in={cc,jj,cur,pen,rnd,sample,keep&&ph==9&&bk_valid,bk_data,h_start,reset};
wire [14:0] outs={n,keep&&!idle,keep&&done,tok};''')
    nd='{'+','.join(f'd_{k}' for k in reversed(ORDER))+'}'
    lines.append(f'assign dout={{h_in,m_in,outs,{nd},ndone,ntok,npas,nacc,nkk,njj,ncc,npp,ntf,nn,np}};')
    lines.append('endmodule')
    return '\n'.join(lines)+'\n'


def connect(shell_net,ch):
    off,NS=layout(ch);b=Builder(NS+NI);pins=list(range(2,NS+NI+2));reset=pins[NS]
    st={k:pins[off[k][0]:off[k][0]+off[k][1]] for k in ORDER}
    outs={k:import_net(b,ch[k],[reset]+[0]*(ch[k].n_in-1),st[k])[1] for k in ORDER}
    dsts={k:[0]*ch[k].n_state for k in ORDER}
    for rnd in range(6):
        flat=pins+[w for k in ORDER for w in dsts[k]+outs[k]]
        _,y=import_net(b,shell_net,flat);o=NS+NO;ins={}
        for k in ORDER:ins[k]=y[o:o+ch[k].n_in];o+=ch[k].n_in
        new={};nds={}
        for k in ORDER:nds[k],new[k]=import_net(b,ch[k],ins[k],st[k])
        if all(new[k]==outs[k] for k in ORDER) and all(nds[k]==dsts[k] for k in ORDER):break
        outs,dsts=new,nds
    else:raise AssertionError('child/shell wiring did not converge')
    comb=b.finish(y[:NS+NO]);return with_state(comb,NS),comb


TB=r"""
#include "Vdut.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
static Vdut*t;static uint64_t clk=0;
static uint32_t step(uint64_t d){t->din=d;t->clk=0;t->eval();uint32_t o=t->dout;t->clk=1;t->eval();t->clk=0;t->eval();clk++;return o;}
static uint32_t peek(uint64_t d){t->din=d;t->clk=0;t->eval();return t->dout;}
int main(int argc,char**argv){
  if(argc<2){printf("usage: vsim case.txt [max]\n");return 2;}
  FILE*f=fopen(argv[1],"r");int L,sample;unsigned rnd;int want;if(!f||fscanf(f,"%d %d %u %d",&L,&sample,&rnd,&want)!=4)return 2;
  int toks[16];for(int i=0;i<L;i++)if(fscanf(f,"%d",&toks[i])!=1)return 2;
  long max=argc>2?atol(argv[2]):200000000L*L;
  t=new Vdut;step(1);step(0);
  for(int i=0;i<L;i++)step((1ull<<2)|((uint64_t)toks[i]<<3));
  uint64_t hold=((uint64_t)sample<<11)|((uint64_t)rnd<<12);
  if(((peek(hold)>>10)&31)!=(unsigned)L){printf("{\"status\":\"fail\",\"why\":\"token count\"}\n");return 1;}
  step(hold|2);uint64_t t0=clk;
  while(!(peek(hold)>>8&1)){step(hold);if(clk-t0>(uint64_t)max){printf("{\"status\":\"timeout\",\"clocks\":%llu}\n",(unsigned long long)(clk-t0));return 4;}}
  int got=peek(hold)&255;
  printf("{\"status\":\"%s\",\"L\":%d,\"sample\":%d,\"token\":%d,\"want\":%d,\"clocks\":%llu}\n",got==want?"pass":"fail",L,sample,got,want,(unsigned long long)(clk-t0));
  delete t;return got==want?0:1;
}
"""


def write_case(path,L,seed,sample):
    import ctypes as ct,final_a8 as fa
    rng=random.Random(seed);toks=[rng.randrange(192) for _ in range(L)];r=rng.getrandbits(32)
    with tempfile.TemporaryDirectory() as t:
        g=fa.golden_lib(t);lg=(ct.c_int32*(L*192))();assert g.int_run((ct.c_int32*L)(*toks),L,lg,None)==0
        g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
        want=g.int_pick((ct.c_int32*192)(*lg[(L-1)*192:L*192]),r,sample)
    text=f'{L} {sample} {r} {want}\n'+' '.join(map(str,toks))+'\n';Path(path).write_text(text)
    return dict(L=L,seed=seed,sample=sample,tokens=toks,random=r,token=want,case_sha256=sha(text.encode()))


CASES=[(1,7,0),(2,11,1),(3,13,0)]
VFLAGS=['--cc','--exe','--build','-O2','-Wno-fatal','--x-assign','fast','--x-initial','fast','--prefix','Vdut','--top-module','machine',
        '-j','4','--output-split','20000','--output-split-cfuncs','2000','machine.v','tb.cpp','-o','vsim']


def vbuild(d,net):
    d.mkdir(parents=True,exist_ok=True);(d/'machine.v').write_text(rtl(net,'machine'));(d/'tb.cpp').write_text(TB);t=time.monotonic()
    r=subprocess.run(['verilator']+VFLAGS,cwd=d,capture_output=True,text=True,timeout=7200);(d/'build.log').write_text(r.stdout[-20000:]+r.stderr[-20000:]);assert r.returncode==0,d
    return dict(seconds=round(time.monotonic()-t,1),rtl_sha256=sha((d/'machine.v').read_bytes()))


def vrun(d,case,limit):
    res=subprocess.run([str(d/'obj_dir/vsim'),str(case),str(limit)],capture_output=True,text=True,timeout=5*3600)
    lines=[l for l in res.stdout.splitlines() if l.startswith('{')]
    return dict(rc=res.returncode,result=json.loads(lines[-1]) if lines else None)


def cloud(ch,sh,net):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import sampler as smp
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'shell';prefix.with_suffix('.ref.v').write_text(reference(ch))
    ref=smp.mapped_reference(prefix,sh.n_in,sh.n_out);proofs={}
    for k,n in [('source',sh),('negative',flip_output(sh)),('mac_msb_first',shell(ch,'mac_msb_first')),('round_half_up',shell(ch,'round_half_up')),('reference',ref)]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(n))
    for k,w in [('source','equivalent'),('negative','different'),('mac_msb_first','different'),('round_half_up','different')]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    cases={f'L{L}':write_case(OUT/f'case_L{L}.txt',L,seed,smp_) for L,seed,smp_ in CASES}
    builds=dict(source=vbuild(OUT/'vlt_source',net),mac_msb_first=vbuild(OUT/'vlt_mac',connect(shell(ch,'mac_msb_first'),ch)[0]))
    runs={}
    for L,_,_ in CASES:
        r=vrun(OUT/'vlt_source',OUT/f'case_L{L}.txt',200_000_000*L);runs[f'L{L}']=r
        assert r['rc']==0 and r['result']['status']=='pass' and r['result']['token']==cases[f'L{L}']['token'],(L,r)
    neg=vrun(OUT/'vlt_mac',OUT/'case_L1.txt',2*runs['L1']['result']['clocks']);assert neg['rc']!=0,neg
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),cases=cases,verilator_builds=builds,machine_runs=runs,actual_rtl_fault_rejected=dict(mac_msb_first=neg))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--tb');ap.add_argument('--L',type=int,default=1);ap.add_argument('--seed',type=int,default=7)
    ap.add_argument('--sample',type=int,default=0);ap.add_argument('--fault');ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    t0=time.monotonic();ch=children();sh=shell(ch,a.fault);net,comb=connect(sh,ch)
    print('machine',metrics(net),'shell',metrics(sh)['nNand'],{k:metrics(ch[k])['nNand'] for k in ORDER},round(time.monotonic()-t0,1),flush=True)
    if a.tb:
        d=Path(a.tb);d.mkdir(parents=True,exist_ok=True);(d/'machine.v').write_text(rtl(net if a.fault!='output_flip' else flip_output(net),'machine'));(d/'tb.cpp').write_text(TB)
        print(write_case(d/'case.txt',a.L,a.seed,a.sample));return
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'shell.nl').write_bytes(sh.encode())
    import sampler,ci,final_a8,layer0_tb  # bound sources
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','integer_opt/final_a8_golden.c','integer_opt/oproj_golden.c','physical/model.bin','integer_opt/layer0_units/manifest.json')+tuple('integer_opt/layer0_units/'+f for f in ('r95_norm_qkv.nl','r72_head.nl','r98_ffn.nl','r52_xbank.nl')):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='R129 model + R130 head with the machine shell; shell CEC and machine runs await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),shell=metrics(sh),children={k:metrics(ch[k]) for k in ORDER},controller_state_bits=SCT,
        contract='reset,start,tvalid,token8,sample,random32 -> token8,done,busy,n5; token = C int_pick(int_run(tokens) logits of the last position, random, sample)',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(ch,sh,net);report['status']='machine shell CEC vs independent RTL; actual whole-machine graph (Verilator) tokens -> next token == C int_run+int_pick for L=1..3 (greedy and sampled); front-end fault rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
