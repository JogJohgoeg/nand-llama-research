#!/usr/bin/env python3
"""True norm[0] -> original A20 slot -> in-place A8 -> byte cache.

The caller replays the original X. No extra vector: norm, quantizer and cache
share the same A slot. Full graph proof/simulation are Actions-only.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse,random,math,ctypes as ct,subprocess,shutil
R=Path(os.environ.get('H3_NORM_FILL_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_stream as norm
import quant_stream as quant
import cache_client as cache
from nand import Builder,metrics,with_state,verify_state,flip_output,blif,from_yosys
from export import import_net,rtl
from gate_check import verify
OUT=Path(os.environ.get('H3_NORM_FILL_OUT',str(R/'build/integer_opt/norm_cache_fill')))
NI,NO,NC=40,40,7
NS=cache.NS+512+169+NC
sha=lambda b:hashlib.sha256(b).hexdigest()


def norm0():
 # Reuse the exact norm arithmetic/control constructor; replace only its
 # single fixed coefficient table and verify the original table binding.
 blob=(R/'physical/model.bin').read_bytes()
 assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
 get=lambda which:[int.from_bytes(blob[268692+2*(which*128+i):268694+2*(which*128+i)],'little') for i in range(128)]
 original=norm.lookup;words=get(0);calls=[]
 def lookup(values,width,method):
  assert values==get(1) and width==16 and method=='shannon';calls.append(True)
  return original(words,width,method)
 norm.lookup=lookup
 try:net,weights,_,_=norm.make()
 finally:norm.lookup=original
 assert len(calls)==1
 return net,weights,words


def owner(b,s,p):
 phase=s[:3];pos=s[3:];reset,start=p[:2];asked=p[2:6];nfinish,qfinish,cdone=p[6:]
 eq=lambda bits,n:b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 keep=b.inv(reset);ps=[eq(phase,i) for i in range(7)];idle=b.lor(ps[0],ps[6])
 begin=AND(keep,idle,start);normal=AND(keep,ps[1]);qstart=AND(keep,ps[2]);qrunning=AND(keep,ps[3]);cstart=AND(keep,ps[4])
 events=[(begin,1),(AND(normal,nfinish),2),(qstart,3),(AND(qrunning,qfinish),4),(cstart,5),(AND(keep,ps[5],cdone),6)]
 nxt=phase[:]
 for enable,value in events:nxt=[b.mux(enable,x,value>>j&1) for j,x in enumerate(nxt)]
 nxt += [b.mux(begin,x,y) for x,y in zip(pos,asked)]
 return [AND(keep,x) for x in nxt],[begin,normal,qstart,qrunning,cstart,AND(keep,b.inv(idle)),AND(keep,ps[6],b.inv(start)),AND(keep,idle,b.inv(start))]


def owner_step(s,p):
 phase=s&7;pos=s>>3;reset=p&1;start=p>>1&1;asked=p>>2&15;nf,qf,cd=[p>>j&1 for j in range(6,9)]
 begin=not reset and phase in (0,6) and start;n=phase
 if begin:n=1;pos=asked
 if not reset:
  if phase==1 and nf:n=2
  if phase==2:n=3
  if phase==3 and qf:n=4
  if phase==4:n=5
  if phase==5 and cd:n=6
 flags=[begin,not reset and phase==1,not reset and phase==2,not reset and phase==3,not reset and phase==4,
        not reset and phase not in (0,6),not reset and phase==6 and not start,not reset and phase in (0,6) and not start]
 return (0 if reset else n+(pos<<3)),sum(int(v)<<j for j,v in enumerate(flags))


def small():
 b=Builder(NC+9);d,o=owner(b,list(range(2,9)),list(range(9,18)));g=b.finish(d+o)
 assert metrics(g)['nNand']<=4000
 xs=list(range(1<<16));ys=[]
 for x in xs:d,o=owner_step(x&127,x>>7);ys.append(d+(o<<7))
 v=verify_state(g,xs,ys,7);v.pop('nl_hex');return v


def connect(b,s,p,n,q,done,head,cursor,bad_rotation=False):
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 reset,start=p[:2];x=p[2:22];xvalid,en=p[22:24];asked=p[24:28];readaddr=p[28:40]
 nl=b.reduce(n[20:27],b.land,1);ql=eq(q[8:17],127)
 d,a=owner(b,s,[reset,start]+asked+[AND(n[53],en,nl),AND(q[38],en,ql),done])
 begin,normal,qstart,qrunning,cstart,busy,complete,manual=a
 nack=AND(normal,n[53],en);qack=AND(qrunning,q[38],en);qscan=AND(qrunning,q[37],en,b.inv(q[40]))
 lastn=b.reduce(n[20:25],b.land,1);lastq=b.reduce(q[8:13],b.land,1)
 rotate=b.lor(AND(nack,lastn),AND(b.lor(qack,qscan),lastq))
 if bad_rotation:rotate=0
 value=[b.mux(normal,x,y) for x,y in zip(q[:8]+[q[7]]*12,n[:20])]
 index=[b.mux(normal,x,y) for x,y in zip(q[8:13],n[20:25])]
 qi=cache.stage.old.select(b,[head[i*20:(i+1)*20] for i in range(32)],q[8:13])
 ni=[reset,begin]+x+[AND(normal,xvalid),AND(normal,en)]
 qp=[reset,qstart]+[(128>>j)&1 for j in range(9)]+qi+[AND(qrunning,en)]*2
 cp=[reset,cstart,1]+s[3:]+q[17:37]+[0]*641+[en,0]
 wp=[0]*640+value+index+[0,rotate,b.lor(nack,qack)]
 install=AND(qrunning,q[38],en,ql);owns=b.lor(normal,qrunning)
 rr=AND(manual,b.reduce([b.inv(b.xor(x,y)) for x,y in zip(readaddr,cursor)],b.land,1))
 public=n[20:27]+[AND(normal,n[51]),AND(normal,n[52]),busy,complete,rr]
 assert len(ni)==24 and len(qp)==33 and len(cp)==670 and len(wp)==668
 return d,ni,qp,cp,wp,[install,owns],public


def slot_port(b,slot,left,right,choose,lanes=32,merged=True):
 work,_=cache.stage.make(lanes)
 if merged:
  pins=[b.mux(choose,x,y) for x,y in zip(left,right)]
  return import_net(b,work,pins,slot)[0]
 ld,_=import_net(b,work,left,slot);rd,_=import_net(b,work,right,slot)
 return [b.mux(choose,x,y) for x,y in zip(ld,rd)]


def small_port():
 lanes=2;bits=160;pinbits=64;b=Builder(bits+pinbits*2+1)
 s=list(range(2,2+bits));p=list(range(2+bits,2+bits+pinbits*2+1));comb=[]
 for merge in (False,True):comb.append(b.finish(slot_port(b,s,p[:pinbits],p[pinbits:-1],p[-1],lanes,merge)+s[:40]))
 assert all(metrics(with_state(g,bits))['nNand']+bits<=4000 for g in comb)
 rng=random.Random(260791);xs=[];ys=[];modes=set()
 for _ in range(512):
  memory=[[rng.getrandbits(20) for _ in range(lanes)] for _ in range(4)]
  values=[rng.getrandbits(pinbits) for _ in range(2)];choose=rng.randrange(2);selected=values[choose]
  incoming=[selected>>(20*i)&1048575 for i in range(lanes)];value=selected>>40&1048575;index=selected>>60&1
  fill,advance,update=[selected>>j&1 for j in range(61,64)]
  modes.add((choose,fill,advance,update))
  result=cache.stage.transition(memory,value,index,fill,advance,update,incoming)
  xs.append(cache.stage.pack(sum(memory,[]))+(values[0]<<bits)+(values[1]<<(bits+pinbits))+(choose<<(bits+2*pinbits)))
  ys.append(cache.stage.pack(sum(result,[]))+(cache.stage.pack(memory[0])<<bits))
 assert len(modes)==16
 checks=[]
 for g in comb:r=verify_state(g,xs,ys,bits);r.pop('nl_hex');checks.append(r)
 return dict(before=checks[0],merged=checks[1],selected_control_modes=len(modes),all_flags_and_arbitrary_old_values=True)


def make(bad_rotation=False,merged=True):
 cn,_,_=cache.make();nn,weights,words=norm0();qn=quant.make();wn,_=cache.stage.make()
 assert (cn.n_state,nn.n_state,qn.n_state,wn.n_state)==(19378,512,169,2560)
 assert sha(cn.encode())=='dbc91954e918aabc8702d6b514edd4bfb6a0d5785544546c6d4eb1f212d5fa88'
 b=Builder(NS+NI);old=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
 cs=old[:cache.NS];rs=old[cache.NS:cache.NS+512];qs=old[cache.NS+512:-NC];os_=old[-NC:]
 head=cs[cache.bank.NS:cache.bank.NS+640];slot=cs[cache.bank.NS:cache.bank.NS+2560]
 _,no=import_net(b,nn,[p[0]]+[0]*23,rs);_,qo=import_net(b,qn,[p[0]]+[0]*32,qs)
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 done=b.land(b.inv(p[0]),eq(cs[-38:-36],2))
 od,ni,qp,cp,wp,flags,public=connect(b,os_,p,no,qo,done,head,cs[cache.bank.NS-12:cache.bank.NS],bad_rotation)
 nd,nout=import_net(b,nn,ni,rs);qd,qout=import_net(b,qn,qp,qs);assert nout==no and qout==qo
 cd,_=import_net(b,cn,cp,cs)
 install,owns=flags
 wc=cs[-cache.NC:];address=cache.address(b,wc[6:10],wc[10:18])
 ready=b.land(b.inv(p[0]),b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cs[cache.bank.NS-12:cache.bank.NS],address)],b.land,1))
 dc,act=cache.controller(b,wc,cp[:28]+cp[668:670]+[ready]+cs[:8]);assert dc==cd[-cache.NC:]
 cached_pins=cp[28:668]+cs[:8]+[cs[7]]*12+wc[10:15]+act[:3]
 cd[cache.bank.NS:cache.bank.NS+2560]=slot_port(b,slot,cached_pins,wp,owns,merged=merged)
 for j in range(3):cd[cache.NS-38+2+j]=b.mux(install,cd[cache.NS-38+2+j],(4>>j)&1)
 out=public[:11]+cs[:8]+[public[11]]+cs[-20:]
 net=with_state(b.finish(cd+nd+qd+od+out),NS)
 assert net.n_in==NI and net.n_out==NO
 return net,dict(cache=metrics(cn),norm0=metrics(nn),quantizer=metrics(qn),slot_bits=2560,extra_vector_bits=0,
  owner_bits=NC,arithmetic_shared=False,one_shared_slot_port=merged),weights,words


def port_identity(before,after):
 assert before.n_state==after.n_state==NS
 b=Builder(NS+NI);state=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
 bd,bo=import_net(b,before,p,state);ad,ao=import_net(b,after,p,state);start=cache.bank.NS;end=start+2560
 assert bd[:start]+bd[end:]+bo==ad[:start]+ad[end:]+ao
 return b.finish(bd[start:end]),b.finish(ad[start:end])


def connector():
 size=7+40+55+41+1+640+12;b=Builder(size);i=2;pieces=[]
 for n in [7,40,55,41,1,640,12]:pieces.append(list(range(i,i+n)));i+=n
 s,p,n,q,c,h,cur=pieces
 return b.finish(sum(connect(b,s,p,n,q,c[0],h,cur),[]))


def connector_ref():
 return '''module top(input [795:0] din,output [1415:0] dout);
wire [6:0] s=din[6:0];wire [39:0] p=din[46:7];wire [54:0] n=din[101:47];wire [40:0] q=din[142:102];
wire cd=din[143];wire [639:0] head=din[783:144];wire [11:0] cursor=din[795:784];
wire reset=p[0],start=p[1],en=p[23];wire [2:0] phase=s[2:0];wire [3:0] pos=s[6:3];
wire begin_row=!reset && (phase==0 || phase==6) && start;
wire normal=!reset && phase==1,qs=!reset && phase==2,qr=!reset && phase==3,cs=!reset && phase==4;
wire busy=!reset && phase!=0 && phase!=6,complete=!reset && phase==6 && !start;
wire manual=!reset && (phase==0 || phase==6) && !start;
wire na=normal && n[53] && en,qa=qr && q[38] && en,scan=qr && q[37] && en && !q[40];
wire rotate=(na && (&n[24:20])) || ((qa || scan) && (&q[12:8]));
wire install=qa && q[16:8]==127,owns=normal || qr;
reg [2:0] np;reg [3:0] posnext;reg [19:0] selected;
always @* begin
 np=phase;posnext=pos;
 if(begin_row)begin np=1;posnext=p[27:24];end
 if(normal && n[53] && en && (&n[26:20]))np=2;
 if(qs)np=3;
 if(install)np=4;
 if(cs)np=5;
 if(!reset && phase==5 && cd)np=6;
 if(reset)begin np=0;posnext=0;end
 selected=head[q[12:8]*20+:20];
end
wire [23:0] ni={normal && en,normal && p[22],p[21:2],begin_row,reset};
wire [32:0] qi={qr && en,qr && en,selected,9'd128,qs,reset};
wire [669:0] ci={1'b0,en,641'b0,q[36:17],pos,1'b1,cs,reset};
wire [19:0] value=normal?n[19:0]:{{12{q[7]}},q[7:0]};wire [4:0] index=normal?n[24:20]:q[12:8];
wire [667:0] wi={na || qa,rotate,1'b0,index,value,640'b0};
wire [11:0] pub={manual && p[39:28]==cursor,complete,busy,normal && n[52],normal && n[51],n[26:20]};
assign dout={pub,owns,install,wi,ci,qi,ni,posnext,np};
endmodule
'''


def fixtures():
 frozen=R/'integer/int_model.c';blob=(R/'physical/model.bin').read_bytes()
 assert sha(frozen.read_bytes())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(frozen))+'\n'+'''
int32_t fill_reference(const int32_t *a,int32_t *h,int8_t *q) {norm(a,0,h);return quant(h,128,q);}
void embed_reference(int t,int32_t *a) {for(int i=0;i<128;i++)a[i]=sat(int_rne((int64_t)embedding[t*128+i]*escale[t],4096));}
uint16_t norm0_weight(int i){return (uint16_t)norms[i];}
''')
 lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(lib)],check=True,timeout=30)
 c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int];assert c.int_init(blob,len(blob))==0
 c.fill_reference.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8)];c.fill_reference.restype=ct.c_int32
 c.embed_reference.argtypes=[ct.c_int,ct.POINTER(ct.c_int32)];c.norm0_weight.argtypes=[ct.c_int];c.norm0_weight.restype=ct.c_uint16
 cache.bank.OUT=OUT/'c_vectors';cache.bank.OUT.mkdir(parents=True,exist_ok=True);prior=cache.bank.golden()
 values=[]
 for token in prior['ids']:
  a=(ct.c_int32*128)();c.embed_reference(token,a);values.append(list(a))
 rng=random.Random(260790)
 values += [[0]*128,[-524288]*128,[524287]*128,[(-524288,524287)[i%2] for i in range(128)],
            [0]*127+[-524288],[524287]+[0]*127,list(range(-64,64))]
 values += [[rng.randrange(-524288,524288)>>shift for _ in range(128)] for shift in (0,4,10)]
 weights=[int(c.norm0_weight(i)) for i in range(128)];signed=[x-65536 if x>=32768 else x for x in weights]
 def rne(n,d):
  q,r=divmod(abs(n),d);q+=int(2*r>d or 2*r==d and q%2);return -q if n<0 else q
 cases=[]
 for i,x in enumerate(values):
  h=(ct.c_int32*128)();q=(ct.c_int8*128)();maximum=int(c.fill_reference((ct.c_int32*128)(*x),h,q))
  root=math.isqrt(2*sum(v*v for v in x)+4295)
  assert list(h)==[max(-524288,min(524287,rne(4*v*w,root))) for v,w in zip(x,signed)]
  assert maximum==max([1]+[abs(v) for v in h]) and list(q)==[rne(v*127,maximum) for v in h]
  packed=[v&255 for v in q]+[(maximum>>(8*j))&255 for j in range(3)]
  if i<16:assert packed==prior['layers'][0][i]
  cases.append(dict(x=x,h=list(h),q=list(q),maximum=maximum,root=root,packed=packed,
                    source='true layer0 token '+str(prior['ids'][i]) if i<16 else 'boundary/random signed20'))
 result=dict(cases=cases,weights=weights,true_embedding_vectors=16,frozen_cache_cases_sha256=sha((cache.bank.OUT/'cases.json').read_bytes()))
 (OUT/'cases.json').write_text(json.dumps(result,indent=2)+'\n');return result


class Model:
 def __init__(self):
  self.cache=cache.Model();self.owner=0;self.n=dict(p=0,idx=0,left=0,root=0,result=0,total=0)
  self.q=dict(p=0,idx=0,left=0,maximum=0,result=0);self.case=None
  self.counts=dict(norm_scan=0,norm_replay=0,norm_writes=0,quant_scan=0,quant_replay=0,quant_writes=0,
                   installed=0,completed=0,aborted=0,cache_reads=0,slot_rotations=0,busy_starts=0)
 def tick(self,p,case=None):
  reset=p&1;start=p>>1&1;x=p>>2&1048575;x=x-1048576 if x>=524288 else x
  xv=p>>22&1;en=p>>23&1;asked=p>>24&15;readaddr=p>>28&4095
  n=self.n;q=self.q;np=n['p'];qp=q['p'];ni=n['idx'];qi=q['idx'];phase=self.owner&7
  normal=not reset and phase==1;qr=not reset and phase==3
  nv=not reset and np==9;qv=not reset and qp==4;nready=not reset and np in (1,5);qready=not reset and qp in (1,2)
  nf=nv and en and ni==127;qf=qv and en and qi==127;cd=not reset and self.cache.ctl&3==2
  od,flags=owner_step(self.owner,reset+(start<<1)+(asked<<2)+(int(nf)<<6)+(int(qf)<<7)+(int(cd)<<8))
  begin,normal_,qs,qr_,cs,busy,done,manual=[flags>>j&1 for j in range(8)];assert normal==normal_ and qr==qr_
  rr=manual and self.cache.bank.cursor==readaddr
  out=ni+(int(normal and nready)<<7)+(int(normal and np>=5)<<8)+(busy<<9)+(done<<10)
  out+=(self.cache.bank.memory[0]<<11)+(int(rr)<<19)+((self.cache.ctl>>18)<<20)
  mask=((1<<NO)-1)^((255<<11) if not rr or not self.cache.bank.known[0] else 0)
  if rr and self.cache.bank.known[0]:self.counts['cache_reads']+=1
  na=normal and nv and en;qa=qr and qv and en;scan=qr and qready and en and qp==1
  rotate=na and ni%32==31 or (qa or scan) and qi%32==31
  install=qr and qv and en and qi==127
  oldwork=[v[:] for v in self.cache.work];oldknown=[v[:] for v in self.cache.known]
  self.cache.tick(reset+(cs<<1)+(1<<2)+((self.owner>>3)<<3)+(q['maximum']<<7)+(en<<668))
  if normal or qr:
   value=n['result'] if normal else q['result'];index=(ni if normal else qi)%32
   self.cache.work=cache.stage.transition(oldwork,value&1048575,index,0,rotate,na or qa,[0]*32)
   self.cache.known=cache.stage.transition(oldknown,True,index,0,rotate,na or qa,[False]*32)
  if install:self.cache.ctl=(self.cache.ctl&~(7<<2))|(4<<2)
  stats=self.counts;stats['slot_rotations']+=int(rotate);stats['installed']+=int(install)
  stats['norm_writes']+=int(na);stats['quant_writes']+=int(qa);stats['busy_starts']+=int(busy and start)
  stats['completed']+=int(phase==5 and od&7==6 and not reset)
  if reset:
   stats['aborted']+=int(phase not in (0,6));self.n=dict(p=0,idx=0,left=0,root=0,result=0,total=0)
   self.q=dict(p=0,idx=0,left=0,maximum=0,result=0);self.case=None
  else:
   if begin:
    assert case is not None;self.case=case;n.update(p=1,idx=0,left=0,total=0)
   elif np in (1,5) and normal and xv:
    assert x==self.case['x'][ni]
    if np==1:n.update(p=2,left=20,total=n['total']+x*x);stats['norm_scan']+=1
    else:n.update(p=6,left=16);stats['norm_replay']+=1
   elif np in (2,4,6,8):
    if n['left']:n['left']-=1
    elif np==2:n.update(p=3 if ni==127 else 1,idx=(ni+1)&127)
    elif np==4:
     assert math.isqrt(2*n['total']+4295)==self.case['root'];n.update(p=5,idx=0,root=self.case['root'])
    elif np==6:n['p']=7
    else:n.update(p=9,result=self.case['h'][ni])
   elif np==3:n.update(p=4,left=24)
   elif np==7:n.update(p=8,left=38)
   elif np==9 and normal and en:n.update(p=0 if ni==127 else 5,idx=(ni+1)&127)
   if qs:
    assert sum(oldwork,[])==[v&1048575 for v in self.case['h']];q.update(p=1,idx=0,maximum=1)
   elif qp in (1,2) and qr and en:
    actual=oldwork[0][qi%32];actual=actual-1048576 if actual>=524288 else actual
    assert actual==self.case['h'][qi]
    if qp==1:
     stats['quant_scan']+=1;q['maximum']=max(q['maximum'],abs(actual))
     if qi==127:assert q['maximum']==self.case['maximum'];q.update(p=2,idx=0)
     else:q['idx']+=1
    else:stats['quant_replay']+=1;q.update(p=3,left=28)
   elif qp==3:
    q['left']-=1
    if q['left']==0:q.update(p=4,result=self.case['q'][qi])
   elif qp==4 and qr and en:
    if qi==127:q['p']=0
    else:q.update(p=2,idx=qi+1)
   if cs:
    assert self.cache.ctl&3==1 and sum(oldwork,[])==[v&1048575 for v in self.case['q']]
  self.owner=od
  return out,mask


def vectors(cases):
 m=Model();rows=[];calls=[];stored={}
 def tick(reset=0,start=0,x=0,xv=0,en=1,pos=0,address=4095,case=None):
  p=reset+(start<<1)+((x&1048575)<<2)+(xv<<22)+(en<<23)+(pos<<24)+(address<<28)
  y,mask=m.tick(p,case);rows.append((p,y,mask if len(rows) else 0));return y
 def reset():tick(reset=1,start=1,x=-524288,xv=1);stored.clear();tick()
 def read(pos,want):
  for j,byte in enumerate(want):
   for _ in range(cache.bank.SLOTS+1):
    y=tick(address=pos*131+j)
    if y>>19&1:assert y>>11&255==byte;break
   else:raise AssertionError('cache read never ready')
 def transact(case,pos,stall=False,abort=None):
  start=len(rows);tick(start=1,pos=pos,case=case);assert m.owner&7==1
  rotations=m.counts['slot_rotations'];acks=m.cache.counts['cache_bytes'];disabled=set()
  for clock in range(100000):
   phase=m.owner&7
   if abort and abort(m):reset();return False
   if phase==6:break
   np=m.n['p'];idx=m.n['idx'];x=case['x'][idx] if np in (1,5) else -524288
   en=int(not stall or clock%11!=2);xv=int(not stall or clock%7!=3)
   if stall and phase==5 and m.cache.bank.cursor==((m.cache.ctl>>6&15)*131+(m.cache.ctl>>10&255)):
    ci=m.cache.ctl>>10&255
    if ci in (31,127,130) and ci not in disabled:en=0;disabled.add(ci)
   tick(start=int(clock%97==0),x=x,xv=xv,en=en,pos=(pos+1)%16)
  else:raise AssertionError('producer timeout')
  y=tick();assert y>>10&1 and y>>20==case['maximum'];assert m.cache.counts['cache_bytes']-acks==131
  assert m.counts['slot_rotations']-rotations==12
  stored[pos]=case['packed'];read(pos,case['packed'])
  calls.append(dict(position=pos,clocks_to_observed_done=clock+2,source=case['source'],stalled=stall,
                    disabled_cache_positions=sorted(disabled),cache_bytes=131,producer_rotations=12))
  assert len(rows)>start;return True
 reset()
 for i,c in enumerate(cases):assert transact(c,i%16,stall=i in (1,17))
 for p,v in sorted(stored.items()):read(p,v)
 # Abort with an uncommitted norm word, during each A8 pass, and in a partial
 # cache write. Reset invalidates contents; every subsequent read is refilled.
 aborts=[lambda x:x.n['p']==2 and x.n['idx']==3,
         lambda x:x.n['p']==9 and x.n['idx']==31,
         lambda x:x.owner&7==3 and x.q['p']==1 and x.q['idx']==31,
         lambda x:x.owner&7==3 and x.q['p']==3 and x.q['idx']==127,
         lambda x:x.owner&7==5 and x.cache.ctl>>10&255==64]
 for abort in aborts:
  assert not transact(cases[-1],0,abort=abort);assert transact(cases[2],0)
 assert m.counts['completed']==len(cases)+5 and m.counts['aborted']==5
 return rows,dict(clocks=len(rows),calls=calls,counts=m.counts,cache_counts=m.cache.counts,
  complete_c_cases=len(cases),scope='true norm0 + full in-place quant + same A slot +131byte cache writes and actual cache readback; no matrix/whole-token controller')


def prove_connector(graph):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 ys=OUT/'connector.ys'
 ys.write_text(f'read_verilog {OUT}/connector.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/connector.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'connector.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
 reference=from_yosys(json.loads((OUT/'connector.ref.json').read_text()),graph.n_in,graph.n_out)
 for name,net in [('connector',graph),('reference',reference),('negative',flip_output(graph))]:(OUT/(name+'.blif')).write_text(blif(net))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'connector.blif',OUT/'reference.blif',OUT/'connector.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
 return dict(proof=good,negative=bad,scope='all7 owner D bits, all norm/quant/cache/A-slot input connections, install/ownership controls and public control; leaf internals retain prior proofs, not whole-component universal arithmetic proof')


def cloud_check(net,cut,weights,words,rows,port_before,port_after):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 import verify as checks
 from weights import cloud_check as check_weights
 proof=prove_connector(cut)
 from ci import cec
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 for name,graph in [('port_before',port_before),('port_after',port_after),('port_negative',flip_output(port_after))]:
  (OUT/(name+'.blif')).write_text(blif(graph))
 port_good=cec(abc,OUT/'port_before.blif',OUT/'port_after.blif',OUT/'port.cec.log');assert port_good['verdict']=='equivalent'
 port_bad=cec(abc,OUT/'port_before.blif',OUT/'port_negative.blif',OUT/'port.negative.log');assert port_bad['verdict']=='different'
 wd=OUT/'weights';wd.mkdir(exist_ok=True);wp=check_weights(wd,{'norm0':weights},words)
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 # Mutate an actual cached-data output, not merely a ready or counter bit.
 from golden import Netlist
 records=net.records.copy();target=len(records)-NO+11;inv=records[target][1];op,a,b=records[inv-NI-2]
 assert op==0 and a==b;records[target]=(0,a,a);bad=Netlist(NI,NO,records)
 prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask>>11&1)+132]
 wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
 no_rotation=make(True)[0];assert sha(no_rotation.encode())!=sha(net.encode())
 rotation_wrong=checks.check_nand(prefix,no_rotation.encode());assert rotation_wrong>0
 (OUT/'negative.nl').write_bytes(bad.encode());(OUT/'no_rotation.nl').write_bytes(no_rotation.encode())
 (OUT/'negative.v').write_text(rtl(bad,'norm_cache_fill'))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'norm_cache_fill',str(OUT/'vectors.txt')))
 checks.run([checks.compile_rtl('source',OUT/'fill.v')],600)
 failed=subprocess.run([str(checks.compile_rtl('negative',OUT/'negative.v'))],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
 assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
 return dict(status='pass',connector_proof=proof,shared_slot_proof=dict(proof=port_good,negative=port_bad,arbitrary_state=True,slot_D_bits=2560),
  weight_proof=wp,clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,
  negative_prefix_clocks=len(prefix),actual_cached_data_fault_mismatches=wrong,actual_removed_rotation_mismatches=rotation_wrong,
  actual_RTL_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();sp=small_port();cases=fixtures();net,parts,weights,words=make();cut=connector()
 before=make(merged=False)[0];left,right=port_identity(before,net)
 assert (cut.n_in,cut.n_out)==(796,1416)
 assert words==cases['weights'] and metrics(weights)['nNand']<=4000
 weight_check=verify(weights,list(range(128)),words)
 rows,expected=vectors(cases['cases'])
 (OUT/'fill.nl').write_bytes(net.encode());(OUT/'fill.v').write_text(rtl(net,'norm_cache_fill'))
 (OUT/'connector.ref.v').write_text(connector_ref());(OUT/'connector.nl').write_bytes(cut.encode())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','integer_opt/weights.py','physical/verify.py','physical/nl_sim.c','integer/int_model.c','physical/model.bin',
              'integer_opt/reverse_attention.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
              'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl']:paths.add(R/name)
 report=dict(status='small controller/true coefficient gates and complete C-data protocol pass; full actual gates pending',metrics=metrics(net),before=metrics(before),parts=parts,
  small=sm,small_slot_port=sp,weight_check=weight_check,connector_metrics=metrics(cut),expected=expected,
  shared_slot_proof_metrics=dict(before=metrics(left),after=metrics(right)),other_D_and_outputs_canonically_identical=True,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  scope='real layer0 attention norm then A8 in original A20 and byte cache; X storage/replay external; matrix consumer and shared arithmetic not integrated',
  contract='din reset0,start1,X20[21:2],Xvalid22,enable23,position[27:24],cache_read_address[39:28]; dout norm_index[6:0],Xready7,replay8,busy9,done10,cache_byte[18:11],read_ready19,maximum[39:20]',
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['verification']=cloud_check(net,cut,weights,words,rows,left,right);report['status']='connector/weight/shared-slot proofs and full NAND/RTL/C pass with actual cache-data and removed-rotation faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','before','parts','connector_metrics']},indent=2));print(json.dumps(expected['counts'],indent=2));print('clocks',len(rows))


if __name__=='__main__':main()
