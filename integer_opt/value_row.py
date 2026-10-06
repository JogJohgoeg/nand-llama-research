#!/usr/bin/env python3
"""C16 weighted V-row accumulation in the existing circulating KV and FF banks.

Four commands: CLEAR32, ACCUMULATE one KV row, READ32 accumulators, LOAD_KV.
Scores/denominator/division and whole-head scheduling stay outside this block.
"""
from pathlib import Path
import sys,os,json,hashlib,random,signal,ctypes as ct,subprocess,argparse,shutil
R=Path(os.environ.get('H3_VALUE_TEST_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state,flip_output
from export import import_net,load_unit,rtl
import kv_client as kv
import ff_acc_port as ff
from gate_check import Snapshot
sha=lambda raw:hashlib.sha256(raw).hexdigest()
OUT=R/'build/integer_opt/value_row'
NI,NO=303,73


def control(b,old,pins):
    phase=old[:4];mode=old[4:6];lane=old[6:11];count=old[11:16]
    reset,start=pins[:2];command=pins[2:4];ka,kd,fa,fd,read=pins[4:]
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    def AND(*xs):return b.reduce(xs,b.land,1)
    keep=b.inv(reset);p=[eq(phase,j) for j in range(16)];m=[eq(mode,j) for j in range(4)]
    begin=AND(keep,start,b.lor(p[0],p[15]));last=b.reduce(lane,b.land,1)
    mul_load=AND(keep,p[6],m[1],fa);mul_finish=AND(keep,p[7],eq(count,17))
    write_done=AND(keep,p[4],fd);take=AND(keep,p[6],m[2],fa,read)
    advance=b.lor(write_done,take)
    ph=phase[:]
    def change(event,n):
        nonlocal ph
        ph=[b.mux(event,v,n>>j&1) for j,v in enumerate(ph)]
    for cmd,target in [(0,3),(1,2),(2,5),(3,1)]:change(b.land(begin,eq(command,cmd)),target)
    change(AND(keep,p[1],kd),15);change(AND(keep,p[2],ka),5)
    change(AND(keep,p[3]),4);change(AND(keep,p[5]),6);change(mul_load,7);change(mul_finish,4)
    change(AND(write_done,b.inv(last),m[0]),3)
    change(AND(write_done,b.inv(last),b.inv(m[0])),5)
    change(AND(take,b.inv(last)),5);change(AND(advance,last),8)
    change(AND(keep,p[8]),9);change(AND(keep,p[9],fd),15)
    clear=b.lor(reset,begin)
    ds=[b.land(keep,v) for v in ph]+[b.land(keep,b.mux(begin,x,y)) for x,y in zip(mode,command)]
    ds += [b.land(b.inv(clear),v) for v in b.add(lane,[0]*5,advance)[0]]
    ds += [AND(keep,p[7],v) for v in b.add(count,[0]*5,1)[0]]
    # Action order is fixed in both structural composition and independent RTL.
    actions=[begin,AND(begin,b.lor(eq(command,1),eq(command,3))),
             b.lor(AND(keep,p[3]),mul_finish),AND(keep,p[5]),AND(keep,p[8]),
             b.lor(mul_load,take),AND(write_done,m[1]),mul_load]
    available=AND(keep,p[6],m[2],fa);busy=AND(keep,b.inv(b.lor(p[0],p[15])))
    flags=[available,b.land(available,last),busy,AND(keep,p[15],b.inv(begin))]
    return ds,actions+flags


def ctl_step(state,pins):
    phase=state&15;mode=state>>4&3;lane=state>>6&31;count=state>>11&31
    reset=pins&1;start=pins>>1&1;command=pins>>2&3;ka,kd,fa,fd,read=[pins>>j&1 for j in range(4,9)]
    begin=not reset and start and phase in (0,15);last=lane==31
    ml=not reset and phase==6 and mode==1 and fa;mf=not reset and phase==7 and count==17
    wd=not reset and phase==4 and fd;take=not reset and phase==6 and mode==2 and fa and read
    advance=wd or take;available=not reset and phase==6 and mode==2 and fa
    actions=[begin,begin and command in (1,3),not reset and phase==3 or mf,
             not reset and phase==5,not reset and phase==8,ml or take,wd and mode==1,ml]
    flags=[available,available and last,not reset and phase not in (0,15),not reset and phase==15 and not begin]
    np=phase;nm=mode;nl=lane;nc=(count+1)&31 if phase==7 else 0
    if reset:np=nm=nl=nc=0
    elif begin:np=(3,2,5,1)[command];nm=command;nl=0
    elif phase==1 and kd:np=15
    elif phase==2 and ka:np=5
    elif phase==3:np=4
    elif phase==5:np=6
    elif ml:np=7
    elif mf:np=4
    elif advance:np=8 if last else 3 if mode==0 else 5;nl=(lane+1)&31
    elif phase==8:np=9
    elif phase==9 and fd:np=15
    return np+(nm<<4)+(nl<<6)+(nc<<11),sum(int(v)<<j for j,v in enumerate(actions+flags))


def make():
    kn,_,kp=kv.make();fn,_,fp=ff.make(21);mul=load_unit('serial_mul')
    nk,nf,nm=kn.n_state,fn.n_state,mul.n_state;ns=nk+nf+nm+16+17
    b=Builder(ns+NI);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+NI))
    ks=old[:nk];fs=old[nk:nk+nf];ms=old[nk+nf:nk+nf+nm];cs=old[-33:-17];weight=old[-17:]
    reset,start=pins[:2];cmd=pins[2:4];address=pins[4:9];w=pins[9:26];word=pins[26:302];read=pins[302]
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    ka=b.land(b.inv(reset),eq(ks[-13:-11],2));kd=b.land(b.inv(reset),eq(ks[-13:-11],3))
    fa=b.reduce([b.inv(reset),eq(fs[-13:-11],2),b.inv(eq(fs[-2:],1))],b.land,1)
    fd=b.land(b.inv(reset),eq(fs[-13:-11],3))
    cd,act=control(b,cs,[reset,start]+cmd+[ka,kd,fa,fd,read])
    begin,kstart,fwrite,fread,fhome,ftake,ktake,mload=act[:8];flags=act[8:]
    kds,ko=import_net(b,kn,[reset,kstart,eq(cmd,3)]+address+word+[ktake],ks)
    deq=ko[:20]
    mds,mo=import_net(b,mul,[mload]+deq+[deq[-1]]*44+weight+[0]*47,ms)
    product=mo[nm:];assert len(product)==64
    # Reuse held current FF halfword as the old accumulator while MUL runs.
    accumulator=[b.mux(fs[-8],x,y) for x,y in zip(fs[:64],fs[64:128])]
    added=b.add(accumulator,product)[0]
    summand=[b.land(b.inv(eq(cs[4:6],0)),v) for v in added]
    fstart=b.lor(fwrite,b.lor(fread,fhome))
    fds,fo=import_net(b,fn,[reset,fstart]+cs[6:11]+[0]+[fwrite,fhome]+summand+[1,ftake],fs)
    wd=[b.land(b.inv(reset),b.mux(begin,x,y)) for x,y in zip(weight,w)]
    outputs=fo[:64]+cs[6:11]+flags;all_d=kds+fds+mds+cd+wd
    comb=b.finish(all_d+outputs);net=with_state(comb,ns)
    return net,comb,dict(kv=metrics(kn),ff=metrics(fn),mul=metrics(mul),control=16,weight=17,
        reuse='FF current halfword holds old accumulator until write captures sum; no extra64-bit accumulator register',
        state_order='KV client, FF port, serial MUL, control16, weight17')


class Model:
    def __init__(self,dequant):
        self.k=kv.Model(32,32,dequant,True);self.f=ff.Model(21)
        self.control=self.weight=self.ma=self.my=self.mp=0;self.mask64=(1<<64)-1
        self.counts=dict(commands=0,completed=0,aborts=0,multiplies=0,busy_starts=0,read_stalls=0)
    def tick(self,x):
        reset=x&1;start=x>>1&1;cmd=x>>2&3;address=x>>4&31;w=x>>9&131071;word=x>>26&((1<<276)-1);read=x>>302&1
        c=self.control;phase=c&15;mode=c>>4&3;lane=c>>6&31
        ka=int(not reset and self.k.phase==2);kd=int(not reset and self.k.phase==3)
        fa=int(not reset and self.f.phase==2 and self.f.mode!=1);fd=int(not reset and self.f.phase==3)
        nc,act=ctl_step(c,reset+(start<<1)+(cmd<<2)+(ka<<4)+(kd<<5)+(fa<<6)+(fd<<7)+(read<<8))
        begin,kstart,fwrite,fread,fhome,ftake,ktake,mload=[act>>j&1 for j in range(8)]
        flags=act>>8
        accumulator=self.f.memory[0]>>(64*(self.f.address&1))&self.mask64
        out=accumulator+(lane<<64)+(flags<<69);mask=((1<<NO)-1) if flags&1 else 15<<69
        q=self.k.cache>>(8*self.k.index)&255;m=self.k.cache>>256;value=self.k.dequant(q-256 if q>=128 else q,m)
        payload=(accumulator+self.mp)&self.mask64 if mode else 0
        kx=reset+(kstart<<1)+(int(cmd==3)<<2)+(address<<3)+(word<<8)+(ktake<<284)
        fx=reset+((fwrite or fread or fhome)<<1)+(lane<<2)+((fwrite+2*fhome)<<8)+(payload<<10)+(1<<74)+(ftake<<75)
        self.k.tick(kx);self.f.tick(fx)
        if mload:
            assert self.k.index==lane
            self.mp=0;self.ma=value&self.mask64;self.my=self.weight;self.counts['multiplies']+=1
        else:
            self.mp=(self.mp+(self.ma if self.my&1 else 0))&self.mask64
            self.ma=self.ma<<1&self.mask64;self.my>>=1
        if begin:self.weight=w;self.counts['commands']+=1
        if reset:self.weight=0
        self.counts['aborts']+=int(reset and phase not in (0,15))
        self.counts['completed']+=int(nc&15==15 and phase!=15)
        self.counts['busy_starts']+=int(start and not reset and phase not in (0,15))
        self.counts['read_stalls']+=int(flags&1 and not read)
        self.control=nc
        return out,mask


def small_control():
    b=Builder(25);ds,acts=control(b,list(range(2,18)),list(range(18,27)))
    net=b.finish(ds+acts);rng=random.Random(260725);xs=[];ys=[]
    for j in range(8192):
        state=rng.getrandbits(16);pins=rng.getrandbits(9)
        if j%4==0:state=(state&2047)|((j//4%32)<<11)
        d,a=ctl_step(state,pins);xs.append(state+(pins<<16));ys.append(d+(a<<16))
    r=verify_state(net,xs,ys,16);r.pop('nl_hex');return r,net


def small_multiplier():
    unit=load_unit('serial_mul');ns=unit.n_state;b=Builder(ns+unit.n_in)
    ds,out=import_net(b,unit,list(range(2+ns,2+ns+unit.n_in)),list(range(2,2+ns)))
    net=with_state(b.finish(ds+out[ns:]),ns)
    assert metrics(net)['nNand']+ns<=4000
    rng=random.Random(260726);cases=[(q,w) for q in (-524288,-1,0,1,524287) for w in (0,1,2,65535,65536,131071)]
    cases += [(rng.randint(-524288,524287),rng.randrange(131072)) for _ in range(98)]
    rows=[];mask=(1<<64)-1
    for q,w in cases:
        rows.append((1+((q&mask)<<1)+(w<<65),0,0))
        for _ in range(17):rows.append((0,0,0))
        rows.append((0,(q*w)&mask,mask))
    def check(graph):
        g=Snapshot.decode(graph.encode(),graph.n_in,graph.n_out);state=bytes(ns);wrong=0
        for x,y,m in rows:
            state,out=g.step(state,bytes(x>>j&1 for j in range(graph.n_in)))
            wrong+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&m))
        return wrong
    assert check(net)==0;wrong=check(flip_output(net));assert wrong==len(cases)
    return dict(metrics=metrics(net),products=len(cases),clocks=len(rows),rhs_bits=17,
        mismatches=0,actual_product_gate_mutation_mismatches=wrong)


def golden():
    import re
    from ring_model import load,forward
    OUT.mkdir(parents=True,exist_ok=True)
    source=(R/'integer/int_model.c').read_text()
    assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    helpers='''static int capture_count;
static int8_t cv[5][16][32];
static int32_t cm[5][16];
static uint32_t cw[5][16];
unsigned value_cases(void){return (unsigned)capture_count;}
int32_t value_field(unsigned c,unsigned s,unsigned i){return i<32?cv[c][s][i]:i==32?cm[c][s]:(int32_t)cw[c][s];}
int64_t value_partial(unsigned c,unsigned count,unsigned lane){
    int64_t total=0;
    for(unsigned s=0;s<count;s++)total+=(int64_t)cw[c][s]*kv_dequant(cv[c][s][lane],cm[c][s]);
    return total;
}
int64_t value_acc(int64_t old,int32_t code,int32_t maximum,uint32_t weight){
    return old+(int64_t)weight*kv_dequant((int8_t)code,maximum);
}
int32_t value_dequant(int32_t code,int32_t maximum){return kv_dequant((int8_t)code,maximum);}
'''
    marker='static void attention(';assert source.count(marker)==1;source=source.replace(marker,helpers+'\n'+marker)
    marker='for(int s=0;s<=pos;s++){w[s]=exp_weight((int64_t)maximum-scores[s]);denominator+=w[s];}'
    hook='''
        if(pos==15 && h==0 && capture_count<5) {
            for(int s=0;s<16;s++) {
                cm[capture_count][s]=vm[s][h];cw[capture_count][s]=w[s];
                for(int i=0;i<32;i++)cv[capture_count][s][i]=values[s][h][i];
            }
            capture_count++;
        }'''
    assert source.count(marker)==1;source=source.replace(marker,marker+hook)
    path=OUT/'golden.c';path.write_text(source);so=OUT/'golden.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(path),'-o',str(so)],check=True,timeout=30)
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
    g=load(so,blob)
    g.value_cases.restype=ct.c_uint;g.value_field.argtypes=[ct.c_uint]*3;g.value_field.restype=ct.c_int32
    g.value_partial.argtypes=[ct.c_uint]*3;g.value_partial.restype=ct.c_int64
    g.value_acc.argtypes=[ct.c_int64,ct.c_int32,ct.c_int32,ct.c_uint32];g.value_acc.restype=ct.c_int64
    g.value_dequant.argtypes=[ct.c_int32,ct.c_int32];g.value_dequant.restype=ct.c_int32
    payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1])
    fixture=next(f for f in payload['fixtures'] if f['name']=='random16');actual=forward(g,fixture['ids'])
    assert actual['logits']==fixture['logit_sha256'] and actual['trace']==fixture['trace_sha256']
    assert g.value_cases()==5
    cases=[]
    for c in range(5):
        words=[];weights=[]
        for s in range(16):
            q=[g.value_field(c,s,i) for i in range(32)];m=g.value_field(c,s,32);weights.append(g.value_field(c,s,33))
            words.append(sum((v&255)<<(8*j) for j,v in enumerate(q))+(m<<256))
        cases.append(dict(layer=c,head=0,position=15,words=words,weights=weights,
            partials=[[g.value_partial(c,s+1,i) for i in range(32)] for s in range(16)]))
    report=dict(fixture=fixture,actual=actual,cases=cases,source_sha256=sha(source.encode()),
        scope='Five real head0/position15 cases, one from each layer of a complete unchanged random16 C forward pass')
    (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n')
    return g,report


def vectors(g,fixtures):
    m=Model(g.value_dequant);rng=random.Random(260727);stim=[];calls=[];logical=[0]*32
    def tick(reset=0,start=0,cmd=0,address=0,weight=0,word=0,read=0):
        x=reset+(start<<1)+(cmd<<2)+(address<<4)+(weight<<9)+(word<<26)+(read<<302)
        y,mask=m.tick(x);stim.append((x,y,mask));return y
    def command(cmd,address=0,weight=0,word=0,abort=None,stall=True):
        assert m.control&15 in (0,15)
        tick(start=1,cmd=cmd,address=address,weight=weight,word=word,read=1);clocks=1;values=[]
        while m.control&15!=15:
            phase=m.control&15;lane=m.control>>6&31
            if abort and abort(m):tick(reset=1,start=1,cmd=3,word=(1<<276)-1,read=1);return False
            take=int(not stall or rng.randrange(4)!=0)
            y=tick(start=int(rng.randrange(13)==0),cmd=rng.randrange(4),address=rng.randrange(32),weight=rng.randrange(131072),word=rng.getrandbits(276),read=take)
            if phase==6 and cmd==2 and y>>69&1 and take:
                assert y>>64&31==len(values)
                value=y&((1<<64)-1);value=value-(1<<64) if value>>63 else value;values.append(value)
            clocks+=1;assert clocks<2400,(cmd,phase,lane)
        assert m.f.cursor==0 or cmd==3
        if cmd==2:assert values==logical
        calls.append(dict(command=cmd,address=address,weight=weight,clocks=clocks,values=values))
        return True
    def clear():
        assert command(0);logical[:]=[0]*32
    def apply(address,w,word):
        assert command(1,address,w)
        maximum=word>>256
        for i in range(32):
            q=word>>(8*i)&255;q=q-256 if q>=128 else q
            logical[i]=g.value_acc(logical[i],q,maximum,w)
    tick(reset=1)
    for case in fixtures['cases']:
        clear();command(2)
        for s,word in enumerate(case['words']):command(3,16+s,word=word)
        for s,(word,w) in enumerate(zip(case['words'],case['weights'])):
            apply(16+s,w,word);assert logical==case['partials'][s];command(2)
    boundary=[]
    for code,maximum,w in [(-128,1048575,131071),(127,1048575,131071),(-1,127,65536),(1,127,65536),(0,0,0)]:
        word=sum((code&255)<<(8*j) for j in range(32))+(maximum<<256)
        boundary.append((word,w));command(3,0,word=word);apply(0,w,word);command(2)
    # Abort actual multiplier, half byte-write, KV seek and partial read paths.
    aborts=[(1,lambda s:(s.control&15)==7 and (s.control>>11)==8),
            (1,lambda s:(s.control&15)==4 and s.f.phase==2 and s.f.index==4),
            (1,lambda s:(s.control&15)==2),
            (2,lambda s:(s.control&15)==6 and (s.control>>6&31)==16)]
    for cmd,predicate in aborts:
        assert not command(cmd,0,65536,abort=predicate)
        clear();word,w=boundary[0];command(3,0,word=word);apply(0,w,word);command(2)
    return stim,dict(clocks=len(stim),counts=m.counts,kv_counts=m.k.counts,ff_counts=m.f.counts,calls=calls)

def control_reference():
    return '''module ctl_ref(input [24:0] din,output [27:0] dout);
wire [3:0] phase=din[3:0];wire [1:0] mode=din[5:4];wire [4:0] lane=din[10:6],count=din[15:11];
wire reset=din[16],start=din[17];wire [1:0] command=din[19:18];
wire ka=din[20],kd=din[21],fa=din[22],fd=din[23],read=din[24];
wire begin_command=!reset && start && (phase==0 || phase==15),last=lane==31;
wire ml=!reset && phase==6 && mode==1 && fa;
wire mf=!reset && phase==7 && count==17;
wire wd=!reset && phase==4 && fd;
wire take=!reset && phase==6 && mode==2 && fa && read;
wire advance=wd || take;
wire available=!reset && phase==6 && mode==2 && fa;
reg [3:0] next_phase;reg [1:0] next_mode;reg [4:0] next_lane,next_count;
always @* begin
 next_phase=phase;next_mode=mode;next_lane=lane;next_count=phase==7 ? count+5'd1 : 5'd0;
 if(reset)begin next_phase=0;next_mode=0;next_lane=0;next_count=0;end
 else if(begin_command)begin
  case(command)0:next_phase=3;1:next_phase=2;2:next_phase=5;3:next_phase=1;endcase
  next_mode=command;next_lane=0;
 end else begin
  case(phase)
   1:if(kd)next_phase=15;
   2:if(ka)next_phase=5;
   3:next_phase=4;
   5:next_phase=6;
   6:if(ml)next_phase=7;else if(take)begin next_phase=last?8:5;next_lane=lane+5'd1;end
   7:if(mf)next_phase=4;
   4:if(wd)begin next_phase=last?8:mode==0?3:5;next_lane=lane+5'd1;end
   8:next_phase=9;
   9:if(fd)next_phase=15;
   default:begin end
  endcase
 end
end
assign dout[15:0]={next_count,next_lane,next_mode,next_phase};
assign dout[16]=begin_command;
assign dout[17]=begin_command && (command==1 || command==3);
assign dout[18]=(!reset && phase==3) || mf;
assign dout[19]=!reset && phase==5;
assign dout[20]=!reset && phase==8;
assign dout[21]=ml || take;
assign dout[22]=wd && mode==1;
assign dout[23]=ml;
assign dout[24]=available;
assign dout[25]=available && last;
assign dout[26]=!reset && phase!=0 && phase!=15;
assign dout[27]=!reset && phase==15 && !begin_command;
endmodule
'''


def reference():
    from nand import verilog
    k=kv.reference(32,32).replace('module top(','module kv_ref(',1)
    f=ff.reference(21).replace('module top(','module ff_ref(',1)
    op=verilog(kv.operator()).replace('module top(','module deq_ref(',1)
    top='''module top(input [12423:0] din,output [12193:0] dout);
wire [9125:0] ks=din[9125:0];wire [2769:0] fs=din[11895:9126];
wire [191:0] ms=din[12087:11896];wire [15:0] cs=din[12103:12088];wire [16:0] weight=din[12120:12104];
wire [302:0] pins=din[12423:12121];wire reset=pins[0],start=pins[1],read=pins[302];
wire [1:0] command=pins[3:2];wire [4:0] address=pins[8:4],lane=cs[10:6];
wire [16:0] w=pins[25:9];wire [275:0] word=pins[301:26];
wire ka=!reset && ks[9114:9113]==2,kd=!reset && ks[9114:9113]==3;
wire fa=!reset && fs[2758:2757]==2 && fs[2769:2768]!=1,fd=!reset && fs[2758:2757]==3;
wire [27:0] ctl;ctl_ref controller({read,fd,fa,kd,ka,command,start,reset,cs},ctl);
wire begin_command=ctl[16],kstart=ctl[17],fwrite=ctl[18],fread=ctl[19],fhome=ctl[20];
wire ftake=ctl[21],ktake=ctl[22],mload=ctl[23];
wire [284:0] kpins={ktake,word,address,command==3,kstart,reset};
wire [9162:0] kres;kv_ref key_value({kpins,ks},kres);
wire [19:0] dequant;deq_ref deq(kres[9153:9126],dequant);
wire [63:0] acc=ms[63:0],a=ms[127:64],y=ms[191:128];
wire [63:0] next_acc=mload?64'd0:acc+(y[0]?a:64'd0);
wire [63:0] next_a=mload?{{44{dequant[19]}},dequant}:{a[62:0],1'b0};
wire [63:0] next_y=mload?{47'd0,weight}:{1'b0,y[63:1]};
wire [63:0] current=fs[2762]?fs[127:64]:fs[63:0];
wire [63:0] updated=cs[5:4]==0?64'd0:current+acc;
wire [75:0] fpins={ftake,1'b1,updated,fhome,fwrite,1'b0,lane,fwrite|fread|fhome,reset};
wire [2836:0] fres;ff_ref ff_bank({fpins,fs},fres);
wire [16:0] next_weight=reset?17'd0:begin_command?w:weight;
assign dout[12120:0]={next_weight,ctl[15:0],next_y,next_a,next_acc,fres[2769:0],kres[9125:0]};
assign dout[12193:12121]={ctl[27:24],lane,fres[2833:2770]};
endmodule
'''
    return control_reference()+k+f+op+top


def cloud_check(net,comb,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from nand import blif,from_yosys
    from ci import cec
    import verify as checks
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    (OUT/'row.blif').write_text(blif(comb));(OUT/'row.ref.v').write_text(reference())
    ys=OUT/'reference.ys';ys.write_text(f'read_verilog {OUT}/row.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=600)
    ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out)
    (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
    good=cec(abc,OUT/'row.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
    assert checks.check_nand(rows,net.encode())==0
    fault=flip_output(net);wrong=checks.check_nand(rows,fault.encode());assert wrong>0
    (OUT/'tb.v').write_text(checks.testbench(NI,NO,'value_row',str(OUT/'vectors.txt')))
    (OUT/'negative.v').write_text(rtl(fault,'value_row'))
    exe=checks.compile_rtl('source',OUT/'row.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'negative.v')
    failed=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
    assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
    return dict(status='pass',all_state_output_cec=good,actual_D_mutation=bad,
        clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),actual_data_gate_mismatches=wrong,
        actual_RTL_mutation_rejected=True,
        formal_scope='Independent banks/control/serial-MUL/add behavior, composed with same pinned dequantizer; not a new arithmetic theorem for dequantization')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True)
    small,_=small_control();mul=small_multiplier();g,cases=golden();rows,expected=vectors(g,cases)
    net,comb,parts=make();(OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'))
    (OUT/'row.ref.v').write_text(reference())
    assert sha(net.encode())=='db1d4c2d8e284633d1df672c7fe9db372766da583d8427e0a6dbde75c798b348'
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    report=dict(status='small controller/MUL pass, complete C/logical schedule reference and full source constructed only',
        metrics=metrics(net),parts=parts,control=small,multiplier=mul,expected=expected,
        vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        adopted=False,numerical_contract_changed=False,
        scope='C16 one head weighted V numerator path, externally scheduled row weights; excludes score generation/denominator/division and full layer ownership')
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','docs/index.html',
        'physical/units/manifest.json','physical/units/serial_mul.nl',
        'integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl']:
        paths.add(R/name)
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=cloud_check(net,comb,rows);report['status']='all D/output compositional CEC and actual NAND/RTL/C cases pass with real faults'
        (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2))
    print(expected['clocks'],expected['counts'],expected['ff_counts'])


if __name__=='__main__':main()
