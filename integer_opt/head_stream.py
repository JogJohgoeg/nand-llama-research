#!/usr/bin/env python3
"""R134: streaming attention head (one head of int_model.c attention()), no K/V word storage.

R72 stores 32 K/V words of 276 bits (8,832 LATCH). C computes the head in two passes, so the words
can stream from the head feeder, which holds each word until it is taken:
 Q    32 s20 values shifted into a ring (the feeder's QLOAD order is 0..31);
 K    per word s<n: per lane i deq=sat(RNE(k8*m,127)), dot+=q_i*deq; then
      score=RNE(RNE(dot,4096)*46341,2^18) pushed into a 16-entry ring, max updated, word taken;
 align the score ring so s=0 is in front;
 V    per word s<n: w=exp_weight(max-score_s) (exptab, 0 above index 1024), den+=w;
      per lane num_i+=w*deq (32-entry ring of 42-bit sums, the first word starts from 0); word taken;
 out  per lane out_i=sat(RNE(num_i,den)), one handshake per result.
One serial multiplier (acc = (acc + bit*(A<<20))>>1, 20 clocks) and one restoring divider (42
steps + a finishing clock) are shared by every step. Signed values are rounded on magnitudes as
int_rne does.
Interface: reset,start,n5,qvalid,q20,wvalid,word276(lane i bits 8i..8i+7, m bits 256..275),otake ->
qready,wtake,ovalid,out20,olast,busy.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_HSTREAM_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_HSTREAM_OUT',str(R/'build/integer_opt/head_stream')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from bench import lookup
from export import import_net,rtl
NI,NO=306,25
CT=dict(ph=4,n=5,s=5,i=5,ms=2,cyc=6,vp=1,acc=65,dq=20,w=17,dot=46,den=22,mx=30,qr=640,sr=480,nr=1344)
NS=sum(CT.values())
IDLE,QLOAD,KWAIT,LANE,SCORE,ALIGN,VWAIT,OUTDIV,OUTV=range(9)
sha=lambda b:hashlib.sha256(b).hexdigest()


def exptab():
    blob=(R/'physical/model.bin').read_bytes()
    # int_model.c int_init: ... costab/sintab (32*16 each, 4 bytes), then exptab 1025 x 4 bytes
    off=8+243200+35*4+192*128+192*4+11*128*2+2*32*16*4
    t=[int.from_bytes(blob[off+4*k:off+4*k+4],'little') for k in range(1025)]
    assert t[0]==65536 and t[1024]==0 and max(t)==65536
    return t


def make(fault=None):
    tab=lookup(exptab()+[0]*1023,17,'shannon')
    b=Builder(NS+NI);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    f={};o=0
    for k,n in CT.items():f[k]=q[o:o+n];o+=n
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0);NOT=b.inv
    eq=lambda bits,v:AND(*[w if v>>t&1 else NOT(w) for t,w in enumerate(bits)])
    cst=lambda v,n:[v>>t&1 for t in range(n)]
    add=lambda x,y,c=0:b.add(x,y,c)[0]
    def sub(x,y):                                   # x - y, same width; returns (diff, no_borrow)
        d,c=b.add(x,[NOT(v) for v in y],1);return d,c
    neg=lambda x:add([NOT(v) for v in x],[0]*len(x),1)
    absv=lambda x:[b.mux(x[-1],a,c) for a,c in zip(x,neg(x))]
    mux=lambda s,x,y:[b.mux(s,a,c) for a,c in zip(x,y)]          # s=0 -> x, s=1 -> y
    def sel(idx,items):                                            # items[idx] (list of equal-width lists)
        cur=items
        for s_ in idx:
            cur=[mux(s_,cur[k],cur[k+1]) if k+1<len(cur) else mux(s_,cur[k],[0]*len(cur[k])) for k in range(0,len(cur),2)]
        return cur[0]
    def gt(x,y):                                                   # unsigned x > y
        _,c=b.add(y,[NOT(v) for v in x],1);return NOT(c)
    def rne_shift(mag,k):                                          # RNE(mag / 2^k), width len(mag)-k
        qv=mag[k:];r=mag[:k]
        half=AND(r[k-1],NOT(OR(*r[:k-1]))) if k>1 else r[0]
        above=AND(r[k-1],OR(*r[:k-1])) if k>1 else 0
        return add(qv,[0]*len(qv),OR(above,AND(half,qv[0])))
    reset,start=p[0],p[1];nin=p[2:7];qvalid=p[7];qin=p[8:28];wvalid=p[28];word=p[29:305];otake=p[305]
    keep=NOT(reset);ph=f['ph'];S=lambda v:eq(ph,v)
    n,s,i,ms,cyc,vp=f['n'],f['s'],f['i'],f['ms'],f['cyc'],f['vp'][0]
    acc,dq,w,dot,den,mx=f['acc'],f['dq'],f['w'],f['dot'],f['den'],f['mx']
    qr=[f['qr'][20*k:20*k+20] for k in range(32)];sr=[f['sr'][30*k:30*k+30] for k in range(16)];nr=[f['nr'][42*k:42*k+42] for k in range(32)]
    lanes=[word[8*k:8*k+8] for k in range(32)];m=word[256:276];e=sel(i,lanes);esg=e[7]
    i_last=eq(i,31);s_last=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(add(s,[0]*5,1),n)],b.land,1)   # s+1 == n
    MS=lambda v:AND(S(LANE),eq(ms,v))
    # ---------- serial multiplier ----------
    mul_on=OR(MS(0),MS(2),AND(S(SCORE),NOT(eq(cyc,20))))
    dmag=absv(dot);a_mag=rne_shift(dmag,12)                                  # 34 bits
    A_k=absv(qr[0])                                                          # 20
    A_v=dq                                                                   # 20
    A_l=[b.mux(vp,x,y) for x,y in zip(A_k,A_v)]+[0]*14
    A_e=absv(e)+[0]*26
    Aop=mux(MS(0),mux(S(SCORE),A_l,a_mag),A_e)
    Bsrc_l=[b.mux(vp,x,y) for x,y in zip(dq,w+[0,0,0])]
    Bsrc=mux(MS(0),mux(S(SCORE),Bsrc_l,cst(46341,20)),m)
    bbit=sel(cyc[:5],[[x] for x in Bsrc]+[[0]]*12)[0]
    if fault=='mul_bit_order':bbit=sel(cyc[:5],[[x] for x in reversed(Bsrc)]+[[0]]*12)[0]
    first=eq(cyc,0)
    mbase=[AND(NOT(first),v) for v in acc[:54]]
    msum,mc=b.add(mbase,[0]*20+[AND(bbit,v) for v in Aop])
    acc_mul=msum[1:]+[mc]+[0]*11
    # ---------- restoring divider ----------
    div_on=OR(AND(MS(1),NOT(eq(cyc,42))),AND(S(OUTDIV),NOT(eq(cyc,42))))
    D=mux(S(OUTDIV),cst(127,22),den)
    num0=nr[0]
    Xsrc=mux(S(OUTDIV),acc[:42],absv(num0))
    X=mux(first,acc[:42],Xsrc);Rr=[AND(NOT(first),v) for v in acc[42:65]]
    Rs=[X[41]]+Rr[:22]
    Rd,ge=sub(Rs,D+[0])
    acc_div=[ge]+X[:41]+mux(ge,Rs,Rd)
    # ---------- division finish: RNE + sat ----------
    Xq,Rq=acc[:42],acc[42:65]
    twoR=[0]+Rq                                                              # 24 bits
    Dz=D+[0,0]
    gtR=gt(twoR,Dz);eqR=AND(*[b.inv(b.xor(x,y)) for x,y in zip(twoR,Dz)])
    inc=OR(gtR,AND(eqR,Xq[0]))
    if fault=='round_half_up':inc=OR(gtR,eqR)
    qv=add(Xq,[0]*42,inc)
    dsign=mux(S(OUTDIV),[esg],[num0[41]])[0]
    lim=[b.mux(dsign,1,0) for _ in range(19)]+[dsign]                        # 524287 or 524288
    over=gt(qv,lim+[0]*22)
    mag=mux(over,qv[:20],lim)
    outv=mux(dsign,mag,neg(mag))
    dq_new=mux(S(OUTDIV),mag,outv)
    # ---------- accumulate ----------
    prod=acc[:54]
    sgn_k=b.xor(qr[0][19],esg)
    p46=prod[:46];dot_acc=add(dot,mux(sgn_k,p46,neg(p46)))
    p42=prod[:42];nbase=[AND(NOT(eq(s,0)),v) for v in num0]
    n_new=add(nbase,mux(esg,p42,neg(p42)))
    # ---------- score ----------
    sm=rne_shift(acc[:54],18)                                                # 36 bits
    score=mux(dot[45],sm[:30],neg(sm)[:30])
    sc_gt=AND(NOT(b.xor(score[29],mx[29])),gt(score[:29],mx[:29]))           # same sign: compare magnitude bits
    sc_gt=OR(sc_gt,AND(NOT(score[29]),mx[29]))
    # ---------- exp weight ----------
    delta,_=sub([*mx,mx[29]],[*sr[0],sr[0][29]])                             # 31 bits
    jj=rne_shift(delta,6)                                                    # 25 bits
    big=gt(jj,cst(1024,25))
    _,wt=import_net(b,tab,jj[:11])
    w_new=[AND(NOT(big),v) for v in wt]
    # ---------- events ----------
    begin=AND(keep,S(IDLE),start,NOT(eq(nin,0)))
    qtake=AND(keep,S(QLOAD),qvalid)
    kgo=AND(keep,S(KWAIT),wvalid)
    vgo=AND(keep,S(VWAIT),wvalid)
    c19=eq(cyc,19);c42=eq(cyc,42);c20=eq(cyc,20)
    m0end=AND(keep,MS(0),c19);dend=AND(keep,MS(1),c42);m2end=AND(keep,MS(2),c19);accs=AND(keep,MS(3))
    lane_last=AND(accs,i_last)
    k_done=AND(lane_last,NOT(vp));v_word=AND(lane_last,vp)
    sc_end=AND(keep,S(SCORE),c20)
    al_n=add(cst(16,5),[NOT(v) for v in n],1)                                # 16 - n
    al_done=AND(keep,S(ALIGN),AND(*[b.inv(b.xor(x,y)) for x,y in zip(cyc[:5],al_n)]),NOT(cyc[5]))
    al_rot=AND(keep,S(ALIGN),NOT(al_done))
    od_end=AND(keep,S(OUTDIV),c42)
    out_take=AND(keep,S(OUTV),otake)
    wtake=OR(sc_end,v_word)
    # ---------- next state ----------
    np_=ph[:]
    def setp(en,v):
        nonlocal np_
        np_=[b.mux(en,x,v>>t&1) for t,x in enumerate(np_)]
    setp(begin,QLOAD);setp(AND(qtake,i_last),KWAIT);setp(kgo,LANE);setp(k_done,SCORE)
    setp(AND(v_word,NOT(s_last)),VWAIT);setp(AND(v_word,s_last),OUTDIV)
    setp(AND(sc_end,NOT(s_last)),KWAIT);setp(AND(sc_end,s_last),ALIGN);setp(al_done,VWAIT);setp(vgo,LANE)
    setp(od_end,OUTV);setp(AND(out_take,NOT(i_last)),OUTDIV);setp(AND(out_take,i_last),IDLE)
    np_=[AND(keep,x) for x in np_]
    def cnt(cur,clear,inc_):return [AND(NOT(clear),b.mux(inc_,x,y)) for x,y in zip(cur,add(cur,[0]*len(cur),1))]
    nn=mux(begin,n,nin)
    ns_=cnt(s,OR(begin,AND(sc_end,s_last),AND(v_word,s_last)),OR(AND(sc_end,NOT(s_last)),AND(v_word,NOT(s_last))))
    ni_=cnt(i,OR(begin,AND(qtake,i_last),kgo,vgo,lane_last,AND(out_take,i_last)),OR(AND(qtake,NOT(i_last)),AND(accs,NOT(i_last)),AND(out_take,NOT(i_last))))
    lane_step=OR(m0end,dend,m2end)
    nms=[AND(NOT(OR(kgo,vgo,accs)),x) for x in add(ms,[0,0],lane_step)]
    cyc_clear=OR(kgo,vgo,m0end,dend,m2end,accs,k_done,sc_end,al_done,od_end,out_take)
    cyc_inc=OR(AND(S(LANE),NOT(MS(3))),AND(S(SCORE),NOT(c20)),al_rot,AND(S(OUTDIV),NOT(c42)))
    ncyc=cnt(cyc,cyc_clear,cyc_inc)
    nvp=[AND(NOT(OR(begin,kgo)),OR(vp,vgo))]
    nacc=mux(mul_on,mux(div_on,acc,acc_div),acc_mul)
    ndq=mux(OR(dend,od_end),dq,dq_new)
    nw=mux(vgo,w,w_new)
    ndot=mux(kgo,mux(AND(accs,NOT(vp)),dot,dot_acc),[0]*46)
    nden=[AND(NOT(begin),v) for v in mux(vgo,den,add(den,w_new+[0]*5))]
    nmx=mux(begin,mux(AND(sc_end,sc_gt),mx,score),cst(1<<29,30))
    # rings
    q_shift=OR(qtake,AND(accs,NOT(vp)))
    q_in=mux(qtake,qr[0],qin)
    nqr=[mux(q_shift,qr[k],qr[k+1] if k<31 else q_in) for k in range(32)]
    s_push=sc_end;s_pop=OR(al_rot,vgo)
    s_in=mux(s_push,sr[0],score)
    nsr=[mux(OR(s_push,s_pop),sr[k],sr[k+1] if k<15 else s_in) for k in range(16)]
    n_shift=OR(AND(accs,vp),out_take)
    n_in=mux(AND(accs,vp),num0,n_new)
    if fault=='no_first_clear':n_in=mux(AND(accs,vp),num0,add(num0,mux(esg,p42,neg(p42))))
    nnr=[mux(n_shift,nr[k],nr[k+1] if k<31 else n_in) for k in range(32)]
    nxt=np_+nn+ns_+ni_+nms+ncyc+nvp+nacc+ndq+nw+ndot+nden+nmx+[x for r in nqr for x in r]+[x for r in nsr for x in r]+[x for r in nnr for x in r]
    assert len(nxt)==NS,(len(nxt),NS)
    outs=[S(QLOAD),wtake,S(OUTV)]+dq+[AND(S(OUTV),i_last),NOT(S(IDLE))]
    outs=[AND(keep,x) for x in outs[:3]]+outs[3:23]+[AND(keep,outs[23]),AND(keep,outs[24])]
    comb=b.finish(nxt+outs);return with_state(comb,NS),comb,tab


def reference():
    """Independent behavioural RTL of the whole head (same state layout)."""
    t=exptab();cases='\n'.join(f"  11'd{k}: et=17'd{v};" for k,v in enumerate(t))
    o=0;decl=[]
    for k,n in CT.items():decl.append(f'wire [{n-1}:0] {k}=din[{o+n-1}:{o}];');o+=n
    return f"""module top(input [{NS+NI-1}:0] din,output [{NS+NO-1}:0] dout);
{chr(10).join(decl)}
wire [{NI-1}:0] p=din[{NS+NI-1}:{NS}];
wire reset=p[0],start=p[1];wire [4:0] nin=p[6:2];wire qvalid=p[7];wire [19:0] qin=p[27:8];wire wvalid=p[28];wire [275:0] word=p[304:29];wire otake=p[305];
wire keep=!reset;
function [19:0] abs20(input [19:0] x); abs20=x[19] ? -x:x; endfunction
wire [19:0] q0=qr[19:0];wire [29:0] s0=sr[29:0];wire [41:0] num0=nr[41:0];
wire [7:0] e=word[8*i+:8];wire esg=e[7];wire [19:0] m=word[275:256];
wire i_last=i==5'd31;wire [4:0] s1=s+5'd1;wire s_last=s1==n;
wire lane=ph==4'd3;wire ms0=lane&&ms==2'd0,ms1=lane&&ms==2'd1,ms2=lane&&ms==2'd2,ms3=lane&&ms==2'd3;
wire isScore=ph==4'd4,isOut=ph==4'd7;
// multiplier
wire mul_on=ms0||ms2||(isScore&&cyc!=6'd20);
wire [45:0] dmag=dot[45] ? -dot:dot;
wire [33:0] aq=dmag[45:12];wire [11:0] ar=dmag[11:0];
wire [33:0] a_mag=aq+((ar>12'd2048)||(ar==12'd2048&&aq[0]));
wire [7:0] emag=esg ? -e:e;
wire [33:0] Aop=ms0 ? {{26'd0,emag}}:(isScore ? a_mag:{{14'd0,(vp ? dq:abs20(q0))}});
wire [19:0] Bsrc=ms0 ? m:(isScore ? 20'd46341:(vp ? {{3'd0,w}}:dq));
wire bbit=cyc[4:0]<5'd20 ? Bsrc[cyc[4:0]]:1'b0;
wire first=cyc==6'd0;
wire [53:0] mbase=first ? 54'd0:acc[53:0];
wire [54:0] msum={{1'b0,mbase}}+(bbit ? {{1'b0,Aop,20'd0}}:55'd0);
wire [64:0] acc_mul={{11'd0,msum[54:1]}};
// divider
wire div_on=(ms1&&cyc!=6'd42)||(isOut&&cyc!=6'd42);
wire [21:0] D=isOut ? den:22'd127;
wire [41:0] numabs=num0[41] ? -num0:num0;
wire [41:0] X=first ? (isOut ? numabs:acc[41:0]):acc[41:0];
wire [22:0] Rr=first ? 23'd0:acc[64:42];
wire [22:0] Rs={{Rr[21:0],X[41]}};
wire ge=Rs>={{1'b0,D}};
wire [22:0] Rn=ge ? Rs-{{1'b0,D}}:Rs;
wire [64:0] acc_div={{Rn,X[40:0],ge}};
// finish
wire [41:0] Xq=acc[41:0];wire [22:0] Rq=acc[64:42];wire [23:0] twoR={{Rq,1'b0}};
wire inc=(twoR>{{2'd0,D}})||(twoR=={{2'd0,D}}&&Xq[0]);
wire [41:0] qv=Xq+inc;
wire dsign=isOut ? num0[41]:esg;
wire [19:0] lim=dsign ? 20'd524288:20'd524287;
wire [19:0] mag=(qv>{{22'd0,lim}}) ? lim:qv[19:0];
wire [19:0] dq_new=isOut ? (dsign ? -mag:mag):mag;
// accumulate
wire [53:0] prod=acc[53:0];wire sgn_k=q0[19]^esg;
wire [45:0] p46=prod[45:0];wire [45:0] dot_acc=dot+(sgn_k ? -p46:p46);
wire [41:0] p42=prod[41:0];wire [41:0] nbase=(s==5'd0) ? 42'd0:num0;
wire [41:0] n_new=nbase+(esg ? -p42:p42);
// score
wire [35:0] sq=prod[53:18];wire [17:0] sr_=prod[17:0];
wire [35:0] sm=sq+((sr_>18'd131072)||(sr_==18'd131072&&sq[0]));
wire [35:0] smn=-sm;
wire [29:0] score=dot[45] ? smn[29:0]:sm[29:0];
wire sc_gt=$signed(score)>$signed(mx);
// exp weight
wire [30:0] delta={{mx[29],mx}}-{{s0[29],s0}};
wire [24:0] dq_=delta[30:6];wire [5:0] dr=delta[5:0];
wire [24:0] jj=dq_+((dr>6'd32)||(dr==6'd32&&dq_[0]));
reg [16:0] et;always @* begin case(jj[10:0])
{cases}
  default: et=17'd0; endcase end
wire [16:0] w_new=(jj>25'd1024) ? 17'd0:et;
// events
wire begin_op=keep&&ph==4'd0&&start&&nin!=5'd0;
wire qtake=keep&&ph==4'd1&&qvalid;
wire kgo=keep&&ph==4'd2&&wvalid,vgo=keep&&ph==4'd6&&wvalid;
wire c19=cyc==6'd19,c42=cyc==6'd42,c20=cyc==6'd20;
wire m0end=keep&&ms0&&c19,dend=keep&&ms1&&c42,m2end=keep&&ms2&&c19,accs=keep&&ms3;
wire lane_last=accs&&i_last;wire k_done=lane_last&&!vp,v_word=lane_last&&vp;
wire sc_end=keep&&isScore&&c20;
wire [4:0] al_n=5'd16-n;
wire al_done=keep&&ph==4'd5&&cyc[4:0]==al_n&&!cyc[5];wire al_rot=keep&&ph==4'd5&&!al_done;
wire od_end=keep&&isOut&&c42;wire out_take=keep&&ph==4'd8&&otake;
wire wtake=sc_end||v_word;
reg [3:0] np;always @* begin np=ph;
 if(begin_op)np=1; if(qtake&&i_last)np=2; if(kgo)np=3; if(k_done)np=4;
 if(v_word&&!s_last)np=6; if(v_word&&s_last)np=7;
 if(sc_end&&!s_last)np=2; if(sc_end&&s_last)np=5; if(al_done)np=6; if(vgo)np=3;
 if(od_end)np=8; if(out_take&&!i_last)np=7; if(out_take&&i_last)np=0;
 if(reset)np=0; end
wire [4:0] nn=begin_op ? nin:n;
wire s_clr=begin_op||(sc_end&&s_last)||(v_word&&s_last),s_inc=(sc_end&&!s_last)||(v_word&&!s_last);
wire [4:0] ns=s_clr ? 5'd0:(s_inc ? s+5'd1:s);
wire i_clr=begin_op||(qtake&&i_last)||kgo||vgo||lane_last||(out_take&&i_last);
wire i_inc=(qtake&&!i_last)||(accs&&!i_last)||(out_take&&!i_last);
wire [4:0] ni=i_clr ? 5'd0:(i_inc ? i+5'd1:i);
wire lane_step=m0end||dend||m2end;
wire [1:0] nms=(kgo||vgo||accs) ? 2'd0:ms+lane_step;
wire cyc_clr=kgo||vgo||m0end||dend||m2end||accs||k_done||sc_end||al_done||od_end||out_take;
wire cyc_inc=(lane&&!ms3)||(isScore&&!c20)||al_rot||(isOut&&!c42);
wire [5:0] ncyc=cyc_clr ? 6'd0:(cyc_inc ? cyc+6'd1:cyc);
wire nvp=!(begin_op||kgo)&&(vp||vgo);
wire [64:0] nacc=mul_on ? acc_mul:(div_on ? acc_div:acc);
wire [19:0] ndq=(dend||od_end) ? dq_new:dq;
wire [16:0] nw=vgo ? w_new:w;
wire [45:0] ndot=kgo ? 46'd0:((accs&&!vp) ? dot_acc:dot);
wire [21:0] nden=begin_op ? 22'd0:(vgo ? den+{{5'd0,w_new}}:den);
wire [29:0] nmx=begin_op ? 30'h20000000:((sc_end&&sc_gt) ? score:mx);
wire q_shift=qtake||(accs&&!vp);wire [19:0] q_in=qtake ? qin:q0;
wire [639:0] nqr=q_shift ? {{q_in,qr[639:20]}}:qr;
wire s_push=sc_end,s_pop=al_rot||vgo;wire [29:0] s_in=s_push ? score:s0;
wire [479:0] nsr=(s_push||s_pop) ? {{s_in,sr[479:30]}}:sr;
wire n_shift=(accs&&vp)||out_take;wire [41:0] n_in=(accs&&vp) ? n_new:num0;
wire [1343:0] nnr=n_shift ? {{n_in,nr[1343:42]}}:nr;
wire [24:0] outs={{keep&&ph!=4'd0,keep&&ph==4'd8&&i_last,dq,keep&&ph==4'd8,wtake,keep&&ph==4'd1}};
assign dout={{outs,nnr,nsr,nqr,nmx,nden,ndot,nw,ndq,nacc,nvp,ncyc,nms,ni,ns,nn,np}};
endmodule
"""


GOLDEN=r"""
#include "int_model.c"
void head1(const int32_t *q,int n,const int8_t *kq,const int32_t *km,const int8_t *vq,const int32_t *vm,int32_t *out){
    int32_t scores[32];uint32_t w[32];int32_t maximum=INT32_MIN;
    for(int s=0;s<n;s++){int64_t dot=0;for(int i=0;i<32;i++)dot+=(int64_t)q[i]*kv_dequant(kq[s*32+i],km[s]);
        scores[s]=(int32_t)int_rne(int_rne(dot,4096)*46341,262144);if(scores[s]>maximum)maximum=scores[s];}
    uint32_t den=0;for(int s=0;s<n;s++){w[s]=exp_weight((int64_t)maximum-scores[s]);den+=w[s];}
    for(int i=0;i<32;i++){int64_t num=0;for(int s=0;s<n;s++)num+=(int64_t)w[s]*kv_dequant(vq[s*32+i],vm[s]);out[i]=sat(int_rne(num,den));}
}
"""

TB=r"""
#include "Vdut.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
static Vdut*t;static uint64_t clk=0;static uint32_t din[10];
static void setb(int k,int v){if(v)din[k>>5]|=1u<<(k&31);else din[k>>5]&=~(1u<<(k&31));}
static void setv(int k,int w,uint32_t v){for(int j=0;j<w;j++)setb(k+j,v>>j&1);}
static void apply(){for(int j=0;j<10;j++)t->din[j]=din[j];}
static uint32_t peek(){apply();t->clk=0;t->eval();return t->dout;}
static uint32_t step(){apply();t->clk=0;t->eval();uint32_t o=t->dout;t->clk=1;t->eval();t->clk=0;t->eval();clk++;return o;}
int main(int argc,char**argv){
  FILE*f=fopen(argv[1],"r");if(!f)return 2;t=new Vdut;memset(din,0,sizeof din);setb(0,1);step();setb(0,0);step();
  int n,cases=0,bad=0;long guard;
  while(fscanf(f,"%d",&n)==1){
    int32_t q[32],km[16],vm[16],want[32];int kq[512],vq[512];
    for(int i=0;i<32;i++)fscanf(f,"%d",&q[i]);
    for(int s=0;s<n;s++){fscanf(f,"%d",&km[s]);for(int i=0;i<32;i++)fscanf(f,"%d",&kq[s*32+i]);}
    for(int s=0;s<n;s++){fscanf(f,"%d",&vm[s]);for(int i=0;i<32;i++)fscanf(f,"%d",&vq[s*32+i]);}
    for(int i=0;i<32;i++)fscanf(f,"%d",&want[i]);
    while(peek()>>24&1)step();
    setv(2,5,n);setb(1,1);step();setb(1,0);
    for(int i=0;i<32;i++){setb(7,1);setv(8,20,q[i]&0xfffff);guard=0;while(!(peek()&1)){step();if(++guard>100000){printf("{\"status\":\"stall q\"}\n");return 4;}}step();}
    setb(7,0);
    for(int pass=0;pass<2;pass++)for(int s=0;s<n;s++){
      const int*src=pass?vq:kq;int mm=pass?vm[s]:km[s];
      for(int i=0;i<32;i++)setv(29+8*i,8,src[s*32+i]&255);setv(285,20,mm&0xfffff);setb(28,1);
      guard=0;while(!(peek()>>1&1)){step();if(++guard>2000000){printf("{\"status\":\"stall w\"}\n");return 4;}}
      step();setb(28,0);}
    for(int i=0;i<32;i++){guard=0;setb(305,1);uint32_t o;while(!((o=peek())>>2&1)){step();if(++guard>2000000){printf("{\"status\":\"stall o\"}\n");return 4;}}
      int32_t r=(int32_t)((o>>3&0xfffff)<<12)>>12;if(r!=want[i]){if(bad<5)printf("case %d lane %d got %d want %d\n",cases,i,r,want[i]);bad++;}step();}
    setb(305,0);cases++;
  }
  printf("{\"status\":\"%s\",\"cases\":%d,\"clocks\":%llu,\"mismatches\":%d}\n",bad?"fail":"pass",cases,(unsigned long long)clk,bad);
  delete t;return bad?1:0;
}
"""


def golden_lib(tmp):
    src=Path(tmp)/'g.c';src.write_text(GOLDEN);so=Path(tmp)/'g.so'
    subprocess.run(['cc','-O2','-std=c99','-shared','-fPIC','-I',str(R/'integer'),str(src),'-o',str(so)],check=True,timeout=60)
    g=ct.CDLL(str(so));raw=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(raw,len(raw))==0
    I32=ct.POINTER(ct.c_int32);I8=ct.POINTER(ct.c_int8);g.head1.argtypes=[I32,ct.c_int,I8,I32,I8,I32,I32];return g


def case_text(seed=26100734,count=24):
    rng=random.Random(seed);lines=[];meta=[]
    with tempfile.TemporaryDirectory() as t:
        g=golden_lib(t)
        for c in range(count):
            n=[1,16,2,3][c] if c<4 else rng.randrange(1,17)
            ext=c%6==5
            q=[(524287 if k%2 else -524288) if ext else rng.randrange(-524288,524288)>>rng.randrange(0,12) for k in range(32)]
            km=[524287 if ext else rng.randrange(0,1<<rng.randrange(1,20)) for _ in range(n)];vm=[524287 if ext else rng.randrange(0,1<<rng.randrange(1,20)) for _ in range(n)]
            kq=[(127 if k%3 else -128) if ext else rng.randrange(-128,128) for k in range(32*n)];vq=[(-128 if k%2 else 127) if ext else rng.randrange(-128,128) for k in range(32*n)]
            out=(ct.c_int32*32)()
            g.head1((ct.c_int32*32)(*q),n,(ct.c_int8*(32*n))(*kq),(ct.c_int32*n)(*km),(ct.c_int8*(32*n))(*vq),(ct.c_int32*n)(*vm),out)
            parts=[str(n)]+[str(x) for x in q]
            for s_ in range(n):parts+=[str(km[s_])]+[str(x) for x in kq[32*s_:32*s_+32]]
            for s_ in range(n):parts+=[str(vm[s_])]+[str(x) for x in vq[32*s_:32*s_+32]]
            parts+=[str(x) for x in out];lines.append(' '.join(parts));meta.append(n)
    return '\n'.join(lines)+'\n',dict(cases=count,n=meta)


VFLAGS=['--cc','--exe','--build','-O2','-Wno-fatal','--x-assign','fast','--x-initial','fast','--prefix','Vdut','--top-module','head','-j','4','head.v','tb.cpp','-o','vsim']


def vrun(d,net,case):
    d.mkdir(parents=True,exist_ok=True);(d/'head.v').write_text(rtl(net,'head'));(d/'tb.cpp').write_text(TB)
    r=subprocess.run(['verilator']+VFLAGS,cwd=d,capture_output=True,text=True,timeout=3600);(d/'build.log').write_text(r.stdout[-20000:]+r.stderr[-20000:]);assert r.returncode==0,d
    res=subprocess.run([str(d/'obj_dir/vsim'),str(case)],capture_output=True,text=True,timeout=3600)
    lines=[l for l in res.stdout.splitlines() if l.startswith('{')]
    return dict(rc=res.returncode,result=json.loads(lines[-1]) if lines else None,rtl_sha256=sha(rtl(net,'head').encode()))


FAULTS=('mul_bit_order','round_half_up','no_first_clear')


def cloud(net,comb):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import sampler as smp
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'head';prefix.with_suffix('.ref.v').write_text(reference())
    ref=smp.mapped_reference(prefix,NS+NI,NS+NO);proofs={}
    for k,n in [('source',comb),('negative',flip_output(comb)),('reference',ref)]+[(f,make(f)[1]) for f in FAULTS]:prefix.with_suffix('.'+k+'.blif').write_text(blif(n))
    for k,w in [('source','equivalent'),('negative','different')]+[(f,'different') for f in FAULTS]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    case=OUT/'case.txt';runs=dict(source=vrun(OUT/'vlt_source',net,case))
    assert runs['source']['rc']==0 and runs['source']['result']['mismatches']==0 and runs['source']['result']['cases']==24
    for f in ('mul_bit_order','no_first_clear','output_flip'):   # round_half_up needs an exact .5 tie: rejected by CEC only
        runs[f]=vrun(OUT/('vlt_'+f),flip_output(net) if f=='output_flip' else make(f)[0],case);assert runs[f]['rc']!=0,f
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),runs=runs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--tb');ap.add_argument('--fault');ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    t0=time.monotonic();net,comb,tab=make(a.fault);print('head_stream',metrics(net),'exptab',metrics(tab)['nNand'],flush=True)
    if a.tb:
        d=Path(a.tb);d.mkdir(parents=True,exist_ok=True);(d/'head.v').write_text(rtl(net,'head'));(d/'tb.cpp').write_text(TB)
        txt,meta=case_text();(d/'case.txt').write_text(txt);(d/'ref.v').write_text(reference());print(meta);return
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'head_stream.nl').write_bytes(net.encode());(OUT/'exptab.nl').write_bytes(tab.encode())
    txt,meta=case_text();(OUT/'case.txt').write_text(txt)
    import sampler,ci  # bound sources
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','physical/model.bin'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='streaming head built; CEC vs RTL and C cases await Actions',metrics=metrics(net),exptab=metrics(tab),
        r72_metrics_for_comparison=dict(nNand=17823,nLatch=12060),cases=meta,case_sha256=sha(txt.encode()),golden_sha256=sha(GOLDEN.encode()),
        contract='reset,start,n5,qvalid,q20,wvalid,word276,otake -> qready,wtake,ovalid,out20,olast,busy; out_i = int_model.c attention() for one head',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(net,comb);report['status']='whole head CEC vs independent RTL; actual graph (Verilator) == C attention for 24 cases (n=1..16, extremes); faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
