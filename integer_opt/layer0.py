#!/usr/bin/env python3
"""R125: autonomous transformer layer 0 (C16) from accepted blocks + a layer controller.

Children (imported unchanged, SHA-bound): R95 norm0/A8 cache + Q/K/V producer, R123 head feeder,
R72 attention head, R124 O projection + residual, R98 norm1/FFN/residual, R52 X bank (64 rows x 32 s20).
X[p] lives in bank rows 4p..4p+3. While idle the bank is passed through to the host. start with L
runs the layer over positions 0..L-1 exactly as int_model.c:124-160 (see h3/LAYER.md):
 A  for p: producer(pos p) <- X[p] streamed from the bank three times
 B  for p, head j: Q call -> feeder(Q) -> 32 QLOADs; for s<=p: K call -> feeder(K) -> LOAD 16+s,
    V call -> feeder(V) -> LOAD s; head n=p+1 -> 32 results -> O projection h input
 C  O projection residual with old X[p] from the bank, its output scanned straight into the FFN
 D  FFN results written back to bank rows 4p..4p+3
Interface: reset,start,L5,host bank port (start,row6,mode2,data20,valid,read) -> bank outputs25,busy,done.
"""
from pathlib import Path
import argparse,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_LAYER0_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_LAYER0_OUT',str(R/'build/integer_opt/layer0')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from export import import_net,rtl,MODEL_SHA
import headfeed,oproj
U=Path(__file__).with_name('layer0_units')
sha=lambda b:hashlib.sha256(b).hexdigest()
NI,NO=38,27
CTL=dict(ph=5,L=5,p=4,s=4,j=2,c=4,k=5,kv=1,done=1)
SCT=sum(CTL.values())
ORDER=['r95','feed','head','oproj','ffn','bank']


def ctl(layers=1):
    return CTL if layers==1 else dict(CTL,ly=3)


WORDED=('r95','oproj','ffn')      # R132: children reading the shared weight table (64-bit word input last)
n_in=lambda ch,k:ch[k].n_in-(64 if 'wt' in ch and k in WORDED else 0)


def shared_words():
    from export import weight_words
    blob=(R/'physical/model.bin').read_bytes();words=[w for l in range(5) for w in weight_words(blob,l)]
    return words+[0]*(32768-len(words))


def shared_table():
    """R97's full-model table: linear address layer*6144 + word, every address bit expanded in reverse."""
    import r95_layers as r95l
    return r95l.ordered(shared_words(),64,list(range(14,-1,-1)))


def children(layers=1,shared=False):
    man=json.loads((U/'manifest.json').read_text());ch={}
    for key,f in (('r95','r95_norm_qkv.nl'),('head','r72_head.nl'),('ffn','r98_ffn.nl'),('bank','r52_xbank.nl')):
        raw=(U/f).read_bytes();m=man[f];assert sha(raw)==m['sha256'],f
        ch[key]=Netlist.decode(raw,m['nIn'],m['nOut'])
    import rope,quant_stream
    ch['feed']=headfeed.connect(headfeed.shell(),rope.make()[0],quant_stream.make())[0]
    ch['oproj']=oproj.build()['net']
    assert metrics(ch['feed'])['sha256']=='052c64c28dc805a34e0237c2fdef146ded0ba0de0715393dd7af430c32bcc757'
    assert metrics(ch['oproj'])['sha256']=='e7e592dd93337c988351d3a7fa91d1950225c9901010fe7e4fc56099cac1d20f'
    if layers>1:   # R126/R127/R128: the three children holding layer constants, with a 3-bit layer input
        import oproj_layers,r95_layers as r95l,r98_layers as r98l
        assert ch['r95'].encode()==r95l.r95().encode() and ch['ffn'].encode()==r98l.r98().encode()
        ch['r95']=r95l.splice(ch['r95'],r95l.selector5(r95l.SEL5_ORDER),r95l.gamma5(r95l.GAM5_ORDER))[0]
        ch['oproj']=oproj_layers.build()['net']
        ch['ffn']=r98l.splice(ch['ffn'],r98l.tables5(r98l.GU5_ORDER,r98l.DOWN5_ORDER,r98l.GAM5_ORDER))[0]
        if shared:   # R132: the four weight tables leave the children; one shared table in the composition
            r95n,ffn=r95l.r95(),r98l.r98()
            ch['r95']=r95l.splice(r95n,None,r95l.gamma5(r95l.GAM5_ORDER),port=True)[0]
            g=oproj_layers.build();ch['oproj']=oproj_layers.connect(g['shell'],None,g['quant'],g['scale'],g['alphas'],word=True)[0]
            ch['ffn']=r98l.splice(ffn,(None,None,r98l.tables5(r98l.GU5_ORDER,r98l.DOWN5_ORDER,r98l.GAM5_ORDER)[2]),port=True)[0]
            ch['wt']=shared_table()
    return ch


def layout(ch,layers=1):
    off={};o=sum(ctl(layers).values())
    for k in ORDER:off[k]=(o,ch[k].n_state);o+=ch[k].n_state
    return off,o


def shell(ch,fault=None,layers=1):
    off,NS=layout(ch,layers)
    nin=NS+NI+sum(ch[k].n_state+ch[k].n_out for k in ORDER);shared='wt' in ch
    b=Builder(nin);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    o=NS+NI+2;cd={};co={}
    for k in ORDER:
        cd[k]=list(range(o,o+ch[k].n_state));o+=ch[k].n_state;co[k]=list(range(o,o+ch[k].n_out));o+=ch[k].n_out
    f={};i=0
    for k,n in ctl(layers).items():f[k]=q[i:i+n];i+=n
    AND=lambda *xs:b.reduce(xs,b.land,1);OR=lambda *xs:b.reduce(xs,b.lor,0)
    eq=lambda bits,v:AND(*[w if v>>t&1 else b.inv(w) for t,w in enumerate(bits)])
    add1=lambda bits:b.add(bits,[0]*len(bits),1)[0]
    reset,start=p[0],p[1];Lin=p[2:7];hstart=p[7];hrow=p[8:14];hmode=p[14:16];hdata=p[16:36];hvalid=p[36];hread=p[37]
    keep=b.inv(reset);ph=f['ph'];S=lambda v:eq(ph,v)
    R5,FD,HD,OP,FF,BK=co['r95'],co['feed'],co['head'],co['oproj'],co['ffn'],co['bank']
    # child status (all state/reset-only outputs)
    r95_ready=R5[74];r95_xready=R5[7];r95_pdone=R5[10];r95_valid=R5[69];r95_row=R5[60:67];r95_res=R5[40:60]
    fd_idle=b.inv(FD[278]);fd_inready=FD[277];fd_wvalid=FD[276];fd_qvalid=FD[304]
    hd_busy=HD[28];hd_done=HD[29];hd_qready=HD[39];hd_valid=HD[25];hd_last=HD[26]
    op_idle=b.inv(OP[22]);op_hready=OP[0];op_yvalid=OP[21];op_y=OP[1:21]
    ff_busy=FF[29];ff_xready=FF[27];ff_avail=FF[30]
    bk_busy=BK[23];bk_inready=BK[20];bk_valid=BK[21];bk_last=BK[22];bk_data=BK[0:20]
    L,pp,ss,jj,cc,kk,kv,done=f['L'],f['p'],f['s'],f['j'],f['c'],f['k'],f['kv'][0],f['done'][0]
    idle=S(0)
    begin=AND(keep,start,idle,b.inv(eq(Lin,0)))
    # --- events ---
    # A fill
    a1=AND(keep,S(1),r95_ready)                                  # producer start
    a2=AND(keep,S(2),b.inv(bk_busy))                             # bank read start row 4p+(c&3)
    a3x=AND(keep,S(3),bk_valid,r95_xready)                       # X transfer
    a3end=AND(a3x,bk_last);a3fin=AND(a3end,eq(cc,11))
    a4=AND(keep,S(4),r95_pdone)
    pinc=add1(pp)
    pinc5=add1(pp+[0])                                          # 5-bit: L may be 16
    p_is_last=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(pinc5,L)],b.land,1)   # p+1 == L
    # B matrix calls (Q at state 5/6, K/V at 8/9)
    qkstart=lambda st:AND(keep,S(st),r95_ready,fd_idle,b.lor(b.inv(eq(jj,0)),op_idle) if st==5 else 1)
    b5=qkstart(5);b8=AND(keep,S(8),r95_ready,fd_idle)
    rows_state=b.lor(S(6),S(9))
    seg=AND(b.inv(b.xor(r95_row[5],jj[0])),b.inv(b.xor(r95_row[6],jj[1])))
    racc=AND(keep,rows_state,r95_valid,b.lor(b.inv(seg),fd_inready))
    rlast=AND(racc,AND(*r95_row[:7]))
    b7take=AND(keep,S(7),fd_qvalid,hd_qready)
    b10=AND(keep,S(10),fd_wvalid,b.inv(hd_busy))
    b11=AND(keep,S(11),hd_done)
    sinc=add1(ss);s_past=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(ss,pp)],b.land,1)  # s == p
    b12=AND(keep,S(12),b.inv(hd_busy))
    b13take=AND(keep,S(13),hd_valid,op_hready);b13end=AND(b13take,hd_last)
    # C residual + FFN scan
    c14=AND(keep,S(14),b.inv(ff_busy))
    c15=AND(keep,S(15),b.inv(bk_busy))
    xfer=AND(keep,S(16),op_yvalid,ff_xready);c16end=AND(xfer,bk_last)
    d17=AND(keep,S(17),ff_avail)
    d18=AND(keep,S(18),b.inv(bk_busy))
    d19=AND(keep,S(19),bk_inready,ff_avail);d19end=AND(d19,eq(kk,31))
    d20=AND(keep,S(20),bk_valid);d20end=AND(d20,bk_last)
    # --- next phase ---
    np_=ph[:]
    def setp(en,v):
        nonlocal np_
        np_=[b.mux(en,x,v>>t&1) for t,x in enumerate(np_)]
    c_is=lambda v:eq(cc,v)
    setp(begin,1);setp(a1,2);setp(a2,3);setp(AND(a3end,b.inv(c_is(11))),2);setp(a3fin,4)
    setp(AND(a4,b.inv(p_is_last)),1);setp(AND(a4,p_is_last),5)
    setp(b5,6);setp(AND(rlast,S(6)),7)
    q7done=AND(keep,S(7),fd_idle)
    setp(q7done,8);setp(b8,9);setp(AND(rlast,S(9)),10);setp(b10,11)
    setp(AND(b11,b.inv(kv)),8)                          # K loaded -> V call for the same s
    setp(AND(b11,kv,b.inv(s_past)),8);setp(AND(b11,kv,s_past),12)
    setp(b12,13);setp(AND(b13end,b.inv(eq(jj,3))),5);setp(AND(b13end,eq(jj,3)),14)
    setp(c14,15);setp(c15,16);setp(AND(c16end,b.inv(c_is(3))),15);setp(AND(c16end,c_is(3)),17)
    setp(d17,18);setp(d18,19);setp(d19end,20);setp(AND(d20end,b.inv(c_is(3))),18)
    if layers==1:
        setp(AND(d20end,c_is(3),b.inv(p_is_last)),5);setp(AND(d20end,c_is(3),p_is_last),0)
    else:
        E=AND(d20end,c_is(3),p_is_last)                 # last position of a layer written back
        ly=f['ly'];lastly=eq(ly,layers-1);more=AND(E,b.inv(lastly));E=AND(E,lastly)
        setp(AND(d20end,c_is(3),b.inv(p_is_last)),5);setp(E,0);setp(more,1)   # next layer: refill the cache from the bank
    np_=[AND(keep,x) for x in np_]
    # --- counters ---
    def cnt(cur,inc,clear_cond,inc_cond):
        return [AND(b.inv(clear_cond),b.mux(inc_cond,x,y)) for x,y in zip(cur,inc)]
    nL=[b.mux(begin,x,y) for x,y in zip(L,Lin)]
    p_clear=OR(begin,AND(a4,p_is_last),*([more] if layers>1 else []));p_inc=OR(AND(a4,b.inv(p_is_last)),AND(d20end,c_is(3),b.inv(p_is_last)))
    npp=cnt(pp,pinc,p_clear,p_inc)
    s_clear=OR(begin,q7done,AND(b11,kv,s_past));s_inc=AND(b11,kv,b.inv(s_past))
    nss=cnt(ss,sinc,s_clear,s_inc)
    j_clear=OR(begin,AND(b13end,eq(jj,3)));j_inc=AND(b13end,b.inv(eq(jj,3)))
    njj=cnt(jj,add1(jj),j_clear,j_inc)
    c_clear=OR(begin,a1,a3fin,AND(c16end,c_is(3)),c14,d17,AND(d20end,c_is(3)))
    c_inc=OR(AND(a3end,b.inv(c_is(11))),AND(c16end,b.inv(c_is(3))),AND(d20end,b.inv(c_is(3))))
    ncc=cnt(cc,add1(cc),c_clear,c_inc)
    k_clear=OR(begin,d18,d19end);k_inc=d19
    nkk=cnt(kk,add1(kk),k_clear,k_inc)
    nkv=[AND(b.inv(OR(begin,q7done,AND(b11,kv))),OR(kv,AND(b11,b.inv(kv))))]
    ndone=[AND(keep,b.inv(begin),OR(done,AND(d20end,c_is(3),p_is_last) if layers==1 else E))]
    nctl=np_+nL+npp+nss+njj+ncc+nkk+nkv+ndone
    if layers>1:nctl+=[AND(keep,b.inv(begin),x) for x in [b.mux(more,a,v) for a,v in zip(ly,add1(ly))]]
    assert len(nctl)==sum(ctl(layers).values())
    # --- child inputs ---
    zero=lambda n:[0]*n
    row4=lambda: cc[0:2]+pp                                      # bank row 4p + (c & 3)
    # R95: reset,pstart,X20,Xvalid,enable,pos4,mstart,mat2,yready,addr12
    pos_m=[b.mux(S(5),x,y) for x,y in zip(ss,pp)]   # Q call uses p, K/V calls use s
    mat=[AND(S(8),b.inv(kv)),AND(S(8),kv)]            # Q=0, K=1, V=2
    mstart=OR(b5,b8)
    r95_in=[reset,a1]+bk_data+[a3x,1]+[b.mux(S(1),x,y) for x,y in zip(pos_m,pp)]+[mstart]+mat+[racc]+zero(12)   # producer start uses p
    # feeder: reset,start,mode2,pos4,in_valid,in20,word_ready,qtake
    fmode=[OR(S(5),AND(S(8),b.inv(kv))),S(5)]                    # Q=3, K=1, V=0
    f_in=[reset,mstart]+fmode+pos_m+[AND(keep,rows_state,r95_valid,seg)]+r95_res+[b10,b7take]
    # head: reset,start,load,n5,qload,addr5,word276,take
    n5=add1(pp+[0])
    haddr=[b.mux(S(7),h,z) for h,z in zip(ss+[b.inv(kv)],FD[299:304])]
    hword=[b.mux(S(7),w,(FD[279+t] if t<20 else 0)) for t,w in enumerate(FD[0:276])]
    hstart_=OR(b10,b12,AND(keep,S(7),fd_qvalid));hload=OR(b10,AND(keep,S(7),fd_qvalid))
    h_in=[reset,hstart_,hload]+n5+[AND(keep,S(7),fd_qvalid)]+haddr+hword+[b13take]
    # O projection: reset,start,h_valid,h20,x_valid,x20,y_ready
    op_start=AND(b5,eq(jj,0))
    o_in=[reset,op_start,AND(keep,S(13),hd_valid)]+HD[0:20]+[AND(keep,S(16),bk_valid)]+bk_data+[AND(keep,S(16),ff_xready)]
    # FFN: reset,start,X20,xvalid,enable,we,read
    ff_in=[reset,c14]+op_y+[AND(keep,S(16),op_yvalid),1,1,d19]
    # bank: reset,start,row6,mode2,data20,valid,read  (host passthrough while idle)
    bstart=OR(a2,c15,d18);bmode=[OR(a2,c15),0]                  # mode 1 = read (bit0), 0 = write
    bdata=FF[0:20];bvalid=d19;bread=OR(a3x,xfer,d20)
    bk_in=[reset]+[b.mux(idle,x,y) for x,y in zip([bstart]+row4()+bmode+bdata+[bvalid,bread],[hstart]+hrow+hmode+hdata+[hvalid,hread])]
    if fault=='no_rows_skip':r95_in[31]=AND(keep,rows_state,r95_valid,fd_inready)
    if layers>1:
        lyw=ly if fault!='layer_stuck0' else [0,0,0]
        r95_in+=lyw;o_in+=lyw;ff_in+=lyw
    ins={'r95':r95_in,'feed':f_in,'head':h_in,'oproj':o_in,'ffn':ff_in,'bank':bk_in}
    for k in ORDER:assert len(ins[k])==n_in(ch,k),(k,len(ins[k]),n_in(ch,k))
    outs=BK[0:25]+[AND(keep,b.inv(idle)),AND(keep,done)]
    nd=[w for k in ORDER for w in cd[k]]
    extra=[]
    if shared:   # R132 shared weight table address: layer*6144 + region offset + local word
        sel=R5[76:87];oaddr=OP[23:32];gu=FF[32:44];dn=FF[44:55];owner=FF[55]
        own_o=OR(S(14),S(15),S(16));own_f=OR(S(17),S(18),S(19),S(20))
        if fault=='owner_swapped':owner=b.inv(owner)
        z=lambda bits,n:bits+[0]*(n-len(bits))
        cst=lambda v,n:[v>>t&1 for t in range(n)]
        o_loc=oaddr+[1,1]                                                    # 1536 + row<<2|group
        gu_loc=b.add(z(gu[:11],13),[b.land(gu[11],t) for t in cst(1344,13)])[0]
        gu_loc=b.add(gu_loc,cst(2048,13))[0]                                  # 2048 + up*1344 + row*4+group
        row,grp=dn[4:11],dn[0:4]
        r11=b.add(b.add(z([0,0,0]+row,13),z([0]+row,13))[0],z(row,13))[0]   # row*11
        dn_loc=b.add(b.add(r11,z(grp,13))[0],cst(4736,13))[0]               # 4736 + row*11 + group
        f_loc=[b.mux(owner,x,y) for x,y in zip(gu_loc,dn_loc)]
        loc=[b.mux(own_f,b.mux(own_o,x,y),w) for x,y,w in zip(z(sel,13),z(o_loc,13),f_loc)]
        l3=b.add(z(ly,4),z([0]+ly,4))[0]                                      # layer*3
        extra=loc[:11]+b.add(z(loc[11:],4),l3)[0]                              # + (layer*3)<<11
    return b.finish(nctl+nd+outs+[w for k in ORDER for w in ins[k]]+extra)


def reference(ch,layers=1):
    """Independent behavioural RTL of the controller shell (child next-states pass through)."""
    off,NS=layout(ch,layers);SCT=sum(ctl(layers).values());sts={k:ch[k].n_state for k in ORDER};outn={k:ch[k].n_out for k in ORDER};inn={k:n_in(ch,k) for k in ORDER}
    shared='wt' in ch
    nin=NS+NI+sum(sts[k]+outn[k] for k in ORDER);nout=SCT+sum(sts.values())+NO+sum(inn.values())+(15 if shared else 0)
    lines=[f'module top(input [{nin-1}:0] din,output [{nout-1}:0] dout);',f'wire [{NI-1}:0] p=din[{NS+NI-1}:{NS}];']
    o=0
    for k,n in ctl(layers).items():lines.append(f'wire [{n-1}:0] c_{k}=din[{o+n-1}:{o}];');o+=n
    o=NS+NI
    for k in ORDER:
        lines.append(f'wire [{sts[k]-1}:0] d_{k}=din[{o+sts[k]-1}:{o}];');o+=sts[k]
        lines.append(f'wire [{outn[k]-1}:0] o_{k}=din[{o+outn[k]-1}:{o}];');o+=outn[k]
    lines.append('''wire reset=p[0],start=p[1];wire [4:0] Lin=p[6:2];wire hstart=p[7];wire [5:0] hrow=p[13:8];wire [1:0] hmode=p[15:14];wire [19:0] hdata=p[35:16];wire hvalid=p[36],hread=p[37];
wire keep=!reset;wire [4:0] ph=c_ph;
wire r95_ready=o_r95[74],r95_xready=o_r95[7],r95_pdone=o_r95[10],r95_valid=o_r95[69];wire [6:0] r95_row=o_r95[66:60];wire [19:0] r95_res=o_r95[59:40];
wire fd_idle=!o_feed[278],fd_inready=o_feed[277],fd_wvalid=o_feed[276],fd_qvalid=o_feed[304];
wire hd_busy=o_head[28],hd_done=o_head[29],hd_qready=o_head[39],hd_valid=o_head[25],hd_last=o_head[26];
wire op_idle=!o_oproj[22],op_hready=o_oproj[0],op_yvalid=o_oproj[21];wire [19:0] op_y=o_oproj[20:1];
wire ff_busy=o_ffn[29],ff_xready=o_ffn[27],ff_avail=o_ffn[30];
wire bk_busy=o_bank[23],bk_inready=o_bank[20],bk_valid=o_bank[21],bk_last=o_bank[22];wire [19:0] bk_data=o_bank[19:0];
wire [4:0] L=c_L;wire [3:0] pp=c_p,ss=c_s,cc=c_c;wire [1:0] jj=c_j;wire [4:0] kk=c_k;wire kv=c_kv[0],done=c_done[0];
wire idle=ph==0;wire begin_op=keep&&start&&idle&&Lin!=0;
wire a1=keep&&ph==1&&r95_ready,a2=keep&&ph==2&&!bk_busy,a3x=keep&&ph==3&&bk_valid&&r95_xready,a3end=a3x&&bk_last,a3fin=a3end&&cc==11,a4=keep&&ph==4&&r95_pdone;
wire [4:0] pinc5={1'b0,pp}+5'd1;wire p_is_last=pinc5==L;
wire b5=keep&&ph==5&&r95_ready&&fd_idle&&(jj!=0||op_idle),b8=keep&&ph==8&&r95_ready&&fd_idle;
wire rows_state=ph==6||ph==9;wire seg=r95_row[6:5]==jj;
wire racc=keep&&rows_state&&r95_valid&&(!seg||fd_inready);wire rlast=racc&&r95_row==7'd127;
wire b7take=keep&&ph==7&&fd_qvalid&&hd_qready,b10=keep&&ph==10&&fd_wvalid&&!hd_busy,b11=keep&&ph==11&&hd_done;
wire s_past=ss==pp;wire b12=keep&&ph==12&&!hd_busy,b13take=keep&&ph==13&&hd_valid&&op_hready,b13end=b13take&&hd_last;
wire c14=keep&&ph==14&&!ff_busy,c15=keep&&ph==15&&!bk_busy,xfer=keep&&ph==16&&op_yvalid&&ff_xready,c16end=xfer&&bk_last;
wire d17=keep&&ph==17&&ff_avail,d18=keep&&ph==18&&!bk_busy,d19=keep&&ph==19&&bk_inready&&ff_avail,d19end=d19&&kk==31,d20=keep&&ph==20&&bk_valid,d20end=d20&&bk_last;
wire q7done=keep&&ph==7&&fd_idle;
reg [4:0] np;
always @* begin np=ph;
 if(begin_op)np=1; if(a1)np=2; if(a2)np=3; if(a3end&&cc!=11)np=2; if(a3fin)np=4;
 if(a4&&!p_is_last)np=1; if(a4&&p_is_last)np=5;
 if(b5)np=6; if(rlast&&ph==6)np=7; if(q7done)np=8; if(b8)np=9; if(rlast&&ph==9)np=10; if(b10)np=11;
 if(b11&&!kv)np=8; if(b11&&kv&&!s_past)np=8; if(b11&&kv&&s_past)np=12;
 if(b12)np=13; if(b13end&&jj!=3)np=5; if(b13end&&jj==3)np=14;
 if(c14)np=15; if(c15)np=16; if(c16end&&cc!=3)np=15; if(c16end&&cc==3)np=17;
 if(d17)np=18; if(d18)np=19; if(d19end)np=20; if(d20end&&cc!=3)np=18;
 if(d20end&&cc==3&&!p_is_last)np=5; if(d20end&&cc==3&&p_is_last)np=0;
 if(reset)np=0; end
wire [4:0] nL=begin_op?Lin:L;
wire p_clear=begin_op||(a4&&p_is_last),p_inc=(a4&&!p_is_last)||(d20end&&cc==3&&!p_is_last);
wire [3:0] npp=p_clear?4'd0:(p_inc?pp+4'd1:pp);
wire s_clear=begin_op||q7done||(b11&&kv&&s_past),s_inc=b11&&kv&&!s_past;wire [3:0] nss=s_clear?4'd0:(s_inc?ss+4'd1:ss);
wire j_clear=begin_op||(b13end&&jj==3),j_inc=b13end&&jj!=3;wire [1:0] njj=j_clear?2'd0:(j_inc?jj+2'd1:jj);
wire c_clear=begin_op||a1||a3fin||(c16end&&cc==3)||c14||d17||(d20end&&cc==3);
wire c_inc=(a3end&&cc!=11)||(c16end&&cc!=3)||(d20end&&cc!=3);wire [3:0] ncc=c_clear?4'd0:(c_inc?cc+4'd1:cc);
wire k_clear=begin_op||d18||d19end;wire [4:0] nkk=k_clear?5'd0:(d19?kk+5'd1:kk);
wire nkv=!(begin_op||q7done||(b11&&kv))&&(kv||(b11&&!kv));
wire ndone=keep&&!begin_op&&(done||(d20end&&cc==3&&p_is_last));
wire [3:0] pos_m=(ph==5)?pp:ss;wire [1:0] mat={ph==8&&kv,ph==8&&!kv};wire mstart=b5||b8;
wire [3:0] rpos=(ph==1)?pp:pos_m;
wire [43:0] r95_in={12'd0,racc,mat,mstart,rpos,1'b1,a3x,bk_data,a1,reset};
wire [1:0] fmode={ph==5,ph==5||(ph==8&&!kv)};
wire [30:0] f_in={b7take,b10,r95_res,keep&&rows_state&&r95_valid&&seg,pos_m,fmode,mstart,reset};
wire [4:0] n5={1'b0,pp}+5'd1;wire qsel=keep&&ph==7&&fd_qvalid;
wire [4:0] haddr=(ph==7)?o_feed[303:299]:{!kv,ss};
wire [275:0] hword=(ph==7)?{256'd0,o_feed[298:279]}:o_feed[275:0];
wire [290:0] h_in={b13take,hword,haddr,qsel,n5,b10||qsel,b10||b12||qsel,reset};
wire op_start=b5&&jj==0;
wire [44:0] o_in={keep&&ph==16&&ff_xready,bk_data,keep&&ph==16&&bk_valid,o_head[19:0],keep&&ph==13&&hd_valid,op_start,reset};
wire [25:0] ff_in={d19,1'b1,1'b1,keep&&ph==16&&op_yvalid,op_y,c14,reset};
wire bstart=a2||c15||d18;wire [1:0] bmode={1'b0,a2||c15};wire [5:0] brow={pp,cc[1:0]};
wire [31:0] bk_ctl={a3x||xfer||d20,d19,o_ffn[19:0],bmode,brow,bstart,reset};
wire [31:0] bk_host={hread,hvalid,hdata,hmode,hrow,hstart,reset};
wire [31:0] bk_in=idle?bk_host:bk_ctl;
wire [26:0] outs={keep&&done,keep&&!idle,o_bank[24:0]};''')
    if layers>1:
        t=lines[-1];last=layers-1
        sub=[("wire q7done=keep&&ph==7&&fd_idle;","wire q7done=keep&&ph==7&&fd_idle;wire [2:0] ly=c_ly;wire lastly=ly==3'd%d;wire Eend=d20end&&cc==3&&p_is_last;wire more=Eend&&!lastly;"%last),
             (" if(d20end&&cc==3&&!p_is_last)np=5; if(d20end&&cc==3&&p_is_last)np=0;"," if(d20end&&cc==3&&!p_is_last)np=5; if(Eend&&lastly)np=0; if(more)np=1;"),
             ("wire p_clear=begin_op||(a4&&p_is_last),","wire p_clear=begin_op||(a4&&p_is_last)||more,"),
             ("wire ndone=keep&&!begin_op&&(done||(d20end&&cc==3&&p_is_last));","wire ndone=keep&&!begin_op&&(done||(Eend&&lastly));wire [2:0] nly=(keep&&!begin_op)?(more?ly+3'd1:ly):3'd0;"),
             ("wire [43:0] r95_in={12'd0,","wire [46:0] r95_in={ly,12'd0,"),
             ("wire [44:0] o_in={","wire [47:0] o_in={ly,"),
             ("wire [25:0] ff_in={","wire [28:0] ff_in={ly,")]
        for a_,b_ in sub:
            assert t.count(a_)==1,a_;t=t.replace(a_,b_)
        lines[-1]=t
    nd='{'+','.join(f'd_{k}' for k in reversed(ORDER))+'}'
    ins='{'+','.join(n for n in reversed(['r95_in','f_in','h_in','o_in','ff_in','bk_in']))+'}'
    if shared:
        lines.append('''wire [10:0] sel=o_r95[86:76];wire [8:0] oaddr=o_oproj[31:23];wire [11:0] gu=o_ffn[43:32];wire [10:0] dn=o_ffn[54:44];wire owner=o_ffn[55];
wire own_o=ph==14||ph==15||ph==16,own_f=ph==17||ph==18||ph==19||ph==20;
wire [12:0] o_loc=13'd1536+oaddr,gu_loc=13'd2048+(gu[11]?13'd1344:13'd0)+gu[10:0],dn_loc=13'd4736+dn[10:4]*13'd11+dn[3:0];
wire [12:0] loc=own_f?(owner?dn_loc:gu_loc):(own_o?o_loc:{2'd0,sel});
wire [14:0] waddr={2'd0,loc}+{c_ly*4'd3,11'd0};''')
    lines.append(f'assign dout={{{"waddr," if shared else ""}{ins},outs,{nd},{"nly," if layers>1 else ""}ndone,nkv,nkk,ncc,njj,nss,npp,nL,np}};')
    lines.append('endmodule')
    return '\n'.join(lines)+'\n'


def connect(shell_net,ch,layers=1):
    off,NS=layout(ch,layers);b=Builder(NS+NI);pins=list(range(2,NS+NI+2));reset=pins[NS];shared='wt' in ch
    st={k:pins[off[k][0]:off[k][0]+off[k][1]] for k in ORDER}
    outs={k:import_net(b,ch[k],[reset]+[0]*(ch[k].n_in-1),st[k])[1] for k in ORDER}
    dsts={k:[0]*ch[k].n_state for k in ORDER}
    for rnd in range(6):
        flat=pins+[w for k in ORDER for w in dsts[k]+outs[k]]
        _,y=import_net(b,shell_net,flat);o=NS+NO;ins={}
        for k in ORDER:ins[k]=y[o:o+n_in(ch,k)];o+=n_in(ch,k)
        if shared:
            _,word=import_net(b,ch['wt'],y[o:o+15])
            for k in WORDED:ins[k]=ins[k]+word
        new={};nds={}
        for k in ORDER:nds[k],new[k]=import_net(b,ch[k],ins[k],st[k])
        if all(new[k]==outs[k] for k in ORDER) and all(nds[k]==dsts[k] for k in ORDER):break
        outs,dsts=new,nds
    else:raise AssertionError('child/shell wiring did not converge')
    comb=b.finish(y[:NS+NO]);return with_state(comb,NS),comb


CASES=[(1,7),(2,11),(3,13)]
VFLAGS=['--cc','--exe','--build','-O2','-Wno-fatal','--x-assign','fast','--x-initial','fast','--prefix','Vdut','--top-module','layer0',
        '-j','4','--output-split','20000','--output-split-cfuncs','2000','layer0.v','tb.cpp','-o','vsim']


def vrun(d,case,limit,timeout):
    res=subprocess.run([str(d/'obj_dir/vsim'),str(case),str(limit)],capture_output=True,text=True,timeout=timeout)
    lines=[l for l in res.stdout.splitlines() if l.startswith('{')]
    return dict(rc=res.returncode,result=json.loads(lines[-1]) if lines else None,tail=res.stdout[-400:])


def cloud(ch,sh,net):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import sampler as smp,layer0_tb as tb
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'shell';prefix.with_suffix('.ref.v').write_text(reference(ch))
    off,NS=layout(ch);ref=smp.mapped_reference(prefix,sh.n_in,sh.n_out);proofs={}
    for k,n in [('source',sh),('negative',flip_output(sh)),('no_rows_skip',shell(ch,'no_rows_skip')),('reference',ref)]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(n))
    for k,w in [('source','equivalent'),('negative','different'),('no_rows_skip','different')]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    cases={}
    for L,seed in CASES:cases[f'L{L}']=tb.write_case(OUT/f'case_L{L}.txt',L,seed)
    builds={}
    for k,n in [('source',net),('no_rows_skip',connect(shell(ch,'no_rows_skip'),ch)[0]),('output_flip',flip_output(net))]:
        bd=OUT/('vlt_'+k);tb.write_tb(bd,n);t=time.monotonic()
        r=subprocess.run(['verilator']+VFLAGS,cwd=bd,capture_output=True,text=True,timeout=3600)
        (OUT/f'verilator_{k}.log').write_text(r.stdout[-20000:]+r.stderr[-20000:]);assert r.returncode==0,k
        builds[k]=dict(seconds=round(time.monotonic()-t,1),rtl_sha256=sha((bd/'layer0.v').read_bytes()))
    layer={}
    for L,seed in CASES:
        r=vrun(OUT/'vlt_source',OUT/f'case_L{L}.txt',40_000_000*L,4*3600);layer[f'L{L}']=r
        assert r['rc']==0 and r['result']['status']=='pass' and r['result']['mismatches']==0,(L,r)
    neg={}
    for k,c in [('no_rows_skip','L2'),('output_flip','L1')]:
        lim=2*layer[c]['result']['layer_clocks'];r=vrun(OUT/('vlt_'+k),OUT/f'case_{c}.txt',lim,3*3600);neg[k]=dict(r,case=c,limit=lim)
        assert r['rc']!=0,k
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),cases=cases,verilator_builds=builds,layer_runs=layer,actual_rtl_faults_rejected=neg)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True);t0=time.monotonic();ch=children();sh=shell(ch);net,comb=connect(sh,ch)
    for k,n in (('layer0',net),('shell',sh)):(OUT/(k+'.nl')).write_bytes(n.encode())
    import layer0_tb,sampler,ci  # cloud-only imports, listed so their sources are bound too
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','integer_opt/final_a8_golden.c','physical/model.bin','physical/nl_sim.c','integer_opt/layer0_units/manifest.json')+tuple('integer_opt/layer0_units/'+f for f in ('r95_norm_qkv.nl','r72_head.nl','r98_ffn.nl','r52_xbank.nl')):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='children bound to accepted R95/R123/R72/R124/R98/R52; shell CEC and whole-layer Verilator runs await Actions',
        metrics=metrics(net),comb_metrics=metrics(comb),shell=metrics(sh),children={k:metrics(ch[k]) for k in ORDER},controller_state_bits=SCT,
        contract='reset,start,L5,host bank port(start,row6,mode2,data20,valid,read) -> bank outputs25,busy,done; X[p] in bank rows 4p..4p+3; start runs int_model.c layer 0 over positions 0..L-1',
        scope='autonomous layer 0: all K/V from the layer input via the R95 cache, per-position Q/RoPE/4 heads/O/residual/norm1/FFN/residual written back',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(ch,sh,net);report['status']='shell CEC vs independent RTL; actual layer graph (Verilator) == int_run layer-0 trace for L=1..3; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('layer0',metrics(net),'shell',metrics(sh)['nNand'],{k:metrics(ch[k])['nNand'] for k in ORDER},round(time.monotonic()-t0,1))


if __name__=='__main__':main()
