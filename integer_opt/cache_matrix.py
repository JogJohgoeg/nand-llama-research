#!/usr/bin/env python3
"""Actual cache -> original A slot -> true layer0 Q/K/V matrix engine.

No new vector. The4-bit owner captures matrix and sequences cache/engine.
Full gate replay and cut RTL proof are Actions-only; not full inference.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,subprocess,shutil,ctypes as ct
R=Path(os.environ.get('H3_CACHE_MATRIX_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import cache_client as cache
import linear_engine as engine
from nand import Builder,metrics,with_state,verify_state,flip_output
from export import import_net,rtl
OUT=Path(os.environ.get('H3_CACHE_MATRIX_OUT',str(R/'build/integer_opt/cache_matrix')))
sha=lambda x:hashlib.sha256(x).hexdigest()
NI,NO=676,34
NS=cache.NS+503+4
CACHE_SHA='dbc91954e918aabc8702d6b514edd4bfb6a0d5785544546c6d4eb1f212d5fa88'
ENGINE_SHA='b18fca2b1a200a61e8cb8995379174ef9c29a626bcf5497bb4364822bd74accd'


def control(b,state,p):
 phase=state[:2];mat=state[2:4]
 reset,start=p[:2];asked=p[2:4];cbusy,cview,cdone,valid,last,ready=p[4:10]
 eq=lambda bits,n:b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 keep=b.inv(reset);idle=eq(phase,0);run=AND(keep,eq(phase,3))
 begin=AND(keep,idle,b.inv(cbusy),start,b.inv(b.land(*asked)))
 launch=AND(keep,eq(phase,2));manual=AND(keep,idle,b.inv(begin))
 done=AND(run,valid,last,ready);fetched=AND(keep,eq(phase,1),cview,cdone)
 nxt=phase[:]
 for event,value in ((begin,1),(fetched,2),(launch,3),(done,0)):
  nxt=[b.mux(event,x,value>>j&1) for j,x in enumerate(nxt)]
 nxt += [b.mux(begin,x,y) for x,y in zip(mat,asked)]
 return [b.land(keep,x) for x in nxt],[begin,launch,run,manual,AND(keep,b.inv(idle))]


def ctl_step(s,p):
 ph=s&3;mat=s>>2&3;reset=p&1;start=p>>1&1;asked=p>>2&3
 cbusy,cview,cdone,valid,last,ready=[p>>j&1 for j in range(4,10)]
 begin=not reset and ph==0 and not cbusy and start and asked<3
 launch=not reset and ph==2;run=not reset and ph==3;manual=not reset and ph==0 and not begin
 n=ph
 if begin:n=1;mat=asked
 if not reset and ph==1 and cview and cdone:n=2
 if launch:n=3
 if run and valid and last and ready:n=0
 out=sum(int(x)<<j for j,x in enumerate([begin,launch,run,manual,not reset and ph!=0]))
 return (0 if reset else n+(mat<<2)),out


def small():
 b=Builder(14);d,a=control(b,list(range(2,6)),list(range(6,16)));g=b.finish(d+a)
 assert metrics(with_state(g,4))['nNand']+4<=4000
 xs=list(range(1<<14));ys=[]
 for x in xs:n,o=ctl_step(x&15,x>>4);ys.append(n+(o<<4))
 r=verify_state(g,xs,ys,4);r.pop('nl_hex');return r


def connect(b,state,p,c,e,bad_rotation=False):
 # c = head256, maximum20, busy, view, done; state-only observations.
 # e = the original36 engine outputs, all state-derived except reset gating.
 reset,fill=p[:2];incoming=p[2:642];wstart=p[642];wp=p[643:647];wm=p[647:667]
 start=p[667];rp=p[668:672];mat=p[672:674];enable,yready=p[674:]
 last=b.reduce([v if 127>>j&1 else b.inv(v) for j,v in enumerate(e[20:29])],b.land,1)
 d,a=control(b,state,[reset,start]+mat+c[276:]+[e[34],last,yready])
 begin,launch,run,manual,busy=a
 write=b.land(manual,wstart);cstart=b.lor(begin,write)
 pos=[b.mux(begin,x,y) for x,y in zip(wp,rp)]
 xf=b.land(run,b.land(enable,c[277]));take=b.land(xf,e[33])
 ci=[reset,cstart,write]+pos+wm+[b.land(manual,fill)]+incoming+[enable,0 if bad_rotation else take]
 ei=[reset,launch]+state[2:4]+[0]+c[256:276]+c[:256]+[xf,b.land(run,yready)]
 fill_ready=b.land(manual,b.land(b.inv(c[276]),b.inv(cstart)))
 out=e[:29]+[b.land(run,e[34]),busy,fill_ready,c[276],b.land(c[278],b.inv(cstart))]
 # Row is9 bits; unusedhigh2 are retained at this interface.
 assert len(out)==NO
 return d,ci,ei,out


def make(bad_rotation=False):
 cn,_,_=cache.make();en=engine.make(True)
 assert sha(cn.encode())==CACHE_SHA and sha(en.encode())==ENGINE_SHA
 assert en.n_state==503 and cn.n_state==cache.NS
 b=Builder(NS+NI);old=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
 cs=old[:cache.NS];es=old[cache.NS:cache.NS+503];owner=old[-4:]
 # Use state-only cache observations to avoid a combinational feedback loop.
 wc=cs[-cache.NC:];work=cs[cache.bank.NS:cache.bank.NS+2560]
 eq=lambda bits,n:b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 keep=b.inv(p[0]);idle=b.lor(eq(wc[:2],0),eq(wc[:2],2))
 cb=b.land(keep,eq(wc[:2],1));cv=b.land(keep,b.land(idle,eq(wc[2:5],4)));done=b.land(keep,eq(wc[:2],2))
 c=[work[j*20+k] for j in range(32) for k in range(8)]+wc[18:]+[cb,cv,done]
 _,eo=import_net(b,en,[p[0]]+[0]*(engine.NI-1),es)
 od,ci,ei,out=connect(b,owner,p,c,eo,bad_rotation)
 cd,_=import_net(b,cn,ci,cs);ed,actual=import_net(b,en,ei,es)
 # A second independent structural binding checks that engine observations
 # are state-only; identity follows Builder's canonical NAND sharing.
 assert actual==eo
 comb=b.finish(cd+ed+od+out)
 return with_state(comb,NS),dict(cache=metrics(cn),engine=metrics(en),owner_latch=4,extra_vector_bits=0,
  scope='layer0 Q/K/V streaming results; original engine retains all7 matrix weight logic; norm/quantizer producer and attention controller absent')


def connector():
 b=Builder(4+NI+279+36);i=2;state=list(range(i,i+4));i+=4;p=list(range(i,i+NI));i+=NI;c=list(range(i,i+279));i+=279;e=list(range(i,i+36))
 d,ci,ei,o=connect(b,state,p,c,e)
 return b.finish(d+ci+ei+o)


def small_connector(cut):
 from gate_check import verify
 assert metrics(cut)['nNand']<=4000
 rng=random.Random(260784);xs=[];ys=[]
 for _ in range(512):
  state=rng.getrandbits(4);p=rng.getrandbits(NI);c=rng.getrandbits(279);e=rng.getrandbits(36)
  reset=p&1;start=p>>667&1;mat=p>>672&3;enable=p>>674&1;ready=p>>675&1
  cb,cv,cd=[c>>j&1 for j in range(276,279)];valid=e>>34&1;last=(e>>20&511)==127
  cp=reset+(start<<1)+(mat<<2)+(cb<<4)+(cv<<5)+(cd<<6)+(valid<<7)+(int(last)<<8)+(ready<<9)
  d,a=ctl_step(state,cp);begin,launch,run,manual,busy=[a>>j&1 for j in range(5)]
  write=manual and p>>642&1;cs=begin or write;pos=(p>>668&15) if begin else (p>>643&15)
  xvalid=run and enable and cv;take=xvalid and e>>33&1
  ci=reset+(int(cs)<<1)+(int(write)<<2)+(pos<<3)+((p>>647&1048575)<<7)+(int(manual and p>>1&1)<<27)+((p>>2&((1<<640)-1))<<28)+(enable<<668)+(int(take)<<669)
  ei=reset+(launch<<1)+((state>>2)<<2)+((c>>256&1048575)<<5)+((c&((1<<256)-1))<<25)+(int(xvalid)<<281)+(int(run and ready)<<282)
  out=(e&((1<<29)-1))+(int(run and valid)<<29)+(busy<<30)+(int(manual and not cb and not cs)<<31)+(cb<<32)+(int(cd and not cs)<<33)
  xs.append(state+(p<<4)+(c<<(4+NI))+(e<<(4+NI+279)))
  ys.append(d+(ci<<4)+(ei<<674)+(out<<957))
 return verify(cut,xs,ys)


def control_ref():
 return '''module owner_ref(input [13:0] din,output [8:0] dout);
wire [3:0] s=din[3:0];wire [9:0] p=din[13:4];wire [1:0] phase=s[1:0],mat=s[3:2];
wire reset=p[0],start=p[1];wire [1:0] asked=p[3:2];
wire cbusy=p[4],cview=p[5],cdone=p[6],valid=p[7],last=p[8],ready=p[9];
wire begin_copy=!reset && phase==0 && !cbusy && start && asked<3;
wire launch=!reset && phase==2,run=!reset && phase==3,manual=!reset && phase==0 && !begin_copy;
reg [1:0] np,nm;always @* begin
 np=phase;nm=mat;
 if(begin_copy)begin np=1;nm=asked;end
 if(!reset && phase==1 && cview && cdone)np=2;
 if(launch)np=3;
 if(run && valid && last && ready)np=0;
 if(reset)begin np=0;nm=0;end
end
assign dout={(!reset && phase!=0),manual,run,launch,begin_copy,nm,np};
endmodule
'''


def connector_ref():
 return control_ref()+'''module top(input [994:0] din,output [990:0] dout);
wire [3:0] state=din[3:0];wire [675:0] p=din[679:4];wire [278:0] c=din[958:680];wire [35:0] e=din[994:959];
wire [8:0] owner;owner_ref u({p[675],e[28:20]==9'd127,e[34],c[278:276],p[673:672],p[667],p[0],state},owner);
wire begin_copy=owner[4],launch=owner[5],run=owner[6],manual=owner[7],busy=owner[8];
wire writing=manual && p[642];wire cstart=begin_copy || writing;
wire [3:0] pos=begin_copy?p[671:668]:p[646:643];wire xvalid=run && p[674] && c[277];
wire take=xvalid && e[33];wire [669:0] ci={take,p[674],p[641:2],(manual && p[1]),p[666:647],pos,writing,cstart,p[0]};
wire [282:0] ei={(run && p[675]),xvalid,c[255:0],c[275:256],1'b0,state[3:2],launch,p[0]};
wire [33:0] out={(c[278] && !cstart),c[276],(manual && !c[276] && !cstart),busy,(run && e[34]),e[28:0]};
assign dout={out,ei,ci,owner[3:0]};
endmodule
'''


class Engine:
 def __init__(self):self.busy=0;self.wait=0;self.available=0;self.group=0;self.row=0;self.outrow=0;self.last=False;self.pending=0;self.case=None
 def observe(self,reset):
  return (self.pending&1048575)+(self.outrow<<20)+(self.group<<29)+(int(self.busy and not self.wait and not self.available and not reset)<<33)+(int(self.available and not reset)<<34)+(int(self.busy)<<35)
 def tick(self,p,case=None):
  reset=p&1;start=p>>1&1;mat=p>>2&7;mx=p>>5&1048575;q=p>>25&((1<<256)-1);xv=p>>281&1;yr=p>>282&1
  out=self.observe(reset);accepted=False;result=False
  if reset:self.__init__()
  elif not self.busy and start and mat<7:
   assert case and case['matrix']==mat and case['m']==mx
   self.busy=1;self.group=0;self.row=0;self.wait=0;self.available=0;self.case=case
  elif self.busy:
   if self.available:
    if yr:result=True;self.available=0;self.busy=int(not self.last)
   elif self.wait:
    self.wait-=1
    if not self.wait:self.available=1
   elif xv:
    c=self.case;assert q==sum((v&255)<<(8*j) for j,v in enumerate(c['q'][32*self.group:32*self.group+32]))
    accepted=True
    if self.group==3:
     self.wait=engine.BOUNDED_LATENCY+1;self.outrow=self.row;self.pending=c['result'][self.row];self.last=self.row==127
     if not self.last:self.group=0;self.row+=1
    else:self.group+=1
  return out,accepted,result


def golden(cases):
 p=OUT/'matrix_golden.c';p.write_text('#include '+json.dumps(str(R/'integer_opt/weights_golden.c'))+'\n'+'''
void cache_matrix_reference(const int8_t *q,int32_t m,int mat,int32_t *out) {
 for(int row=0;row<128;row++) {
  int32_t sum=0;
  for(int col=0;col<128;col++){sum+=(int32_t)q[col]*weights[offsets[mat]+128*row+col];}
  out[row]=sat(int_rne((int64_t)sum*m*alpha[mat],33292288));
 }
}
''')
 so=OUT/'matrix_golden.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(so)],check=True,timeout=30)
 g=ct.CDLL(str(so));blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
 g.cache_matrix_reference.argtypes=[ct.POINTER(ct.c_int8),ct.c_int32,ct.c_int,ct.POINTER(ct.c_int32)]
 result={}
 for pos,raw in enumerate(cases['layers'][0]):
  q=[v-256 if v>=128 else v for v in raw[:128]];mx=sum(raw[128+j]<<(8*j) for j in range(3))
  for mat in range(3):
   out=(ct.c_int32*128)();g.cache_matrix_reference((ct.c_int8*128)(*q),mx,mat,out)
   alpha=int.from_bytes(blob[243208+4*mat:243212+4*mat],'little');py=[]
   for row in range(128):
    value=0
    for j,x in enumerate(q):
     k=mat*16384+row*128+j;w=blob[8+k//4]>>(2*(k%4))&3;assert w!=3;value+=x*(-1 if w==2 else w)
    n=value*mx*alpha;d=33292288;a,r=divmod(abs(n),d);a+=int(r*2>d or r*2==d and a%2)
    py.append(max(-524288,min(524287,-a if n<0 else a)))
   assert py==list(out)
   result[pos,mat]=dict(position=pos,matrix=mat,q=q,m=mx,result=list(out))
 return result


def vectors(cases,expected):
 c=cache.Model();e=Engine();owner=0;context=None;rows=[];rng=random.Random(260783);calls=[]
 counts=dict(clocks=0,matrices=0,results=0,groups=0,owner_aborts=0,busy_starts=0,busy_fills=0,x_stalls=0,y_stalls=0)
 def tick(reset=0,fill=0,word=None,write=0,wp=0,wm=0,start=0,pos=0,mat=0,enable=1,yready=1):
  nonlocal owner,context
  p=reset+(fill<<1)+(cache.stage.pack(word or [0]*32)<<2)+(write<<642)+(wp<<643)+(wm<<647)+(start<<667)+(pos<<668)+(mat<<672)+(enable<<674)+(yready<<675)
  ph=c.ctl&3;nf=c.ctl>>2&7;cb=int(not reset and ph==1);cv=int(not reset and ph in (0,2) and nf==4);cd=int(not reset and ph==2)
  old_e=e.observe(reset);valid=old_e>>34&1;last=(old_e>>20&511)==127
  cp=reset+(start<<1)+(mat<<2)+(cb<<4)+(cv<<5)+(cd<<6)+(valid<<7)+(int(last)<<8)+(yready<<9)
  nd,a=ctl_step(owner,cp);begin,launch,run,manual,busy=[a>>j&1 for j in range(5)]
  w=manual and write;cs=begin or w;xf=run and enable and cv;take=xf and (old_e>>33&1)
  head=sum((v&255)<<(8*j) for j,v in enumerate(c.work[0]));maximum=c.ctl>>18
  ci=reset+(int(cs)<<1)+(int(w)<<2)+((pos if begin else wp)<<3)+(wm<<7)+(int(manual and fill)<<27)+(cache.stage.pack(word or [0]*32)<<28)+(enable<<668)+(int(take)<<669)
  ei=reset+(launch<<1)+((owner>>2)<<2)+(maximum<<5)+(head<<25)+(int(xf)<<281)+(int(run and yready)<<282)
  out=(old_e&((1<<29)-1))+(int(run and valid)<<29)+(busy<<30)+(int(manual and not cb and not cs)<<31)+(cb<<32)+(int(cd and not cs)<<33);mask=31<<29
  if run and valid:mask|=(1<<29)-1
  rows.append((p,out,mask))
  if begin:context=expected[pos,mat]
  eo,accepted,result=e.tick(ei,context if launch else None);assert eo==old_e
  c.tick(ci)
  counts['clocks']+=1;counts['groups']+=int(accepted);counts['results']+=int(result)
  counts['owner_aborts']+=int(reset and owner&3!=0);counts['matrices']+=int(owner&3==3 and nd&3==0 and not reset)
  counts['busy_starts']+=int(busy and start);counts['busy_fills']+=int(busy and fill)
  counts['x_stalls']+=int(run and old_e>>33&1 and not enable);counts['y_stalls']+=int(run and valid and not yready)
  owner=nd
  return out
 def fill_cache():
  for pos,raw in enumerate(cases['layers'][0]):
   q=[v-256 if v>=128 else v for v in raw[:128]]
   for j in range(4):tick(fill=1,word=q[j*32:j*32+32])
   maximum=sum(raw[128+j]<<(8*j) for j in range(3));tick(write=1,wp=pos,wm=maximum)
   for _ in range(2230):
    if c.ctl&3==2:break
    tick(start=1,pos=pos,mat=2) # Busy cache must prevent starting the engine.
   else:raise AssertionError('write timeout')
   assert owner==0
 def matrix(pos,mat,stall=False,abort=None):
  before=counts['results'];begin=len(rows);tick(start=1,pos=pos,mat=mat);assert owner&3==1
  for elapsed in range(20000):
   if not owner&3:break
   if abort is not None and elapsed==abort:tick(reset=1);return False
   tick(start=int(elapsed%31==0),pos=rng.randrange(16),mat=rng.randrange(4),fill=int(elapsed%47==0),word=[-1]*32,write=int(elapsed%71==0),
    enable=int(not stall or owner&3!=3 or rng.randrange(7)!=0),yready=int(not stall or rng.randrange(5)!=0))
  else:raise AssertionError('matrix timeout')
  assert counts['results']-before==128 and not e.busy
  assert [v&255 for v in sum(c.work,[])]==cases['layers'][0][pos][:128]
  calls.append(dict(position=pos,matrix=mat,clocks=len(rows)-begin,stalled=stall,result_sha256=sha(b''.join(v.to_bytes(4,'little',signed=True) for v in expected[pos,mat]['result']))))
  return True
 tick(reset=1);tick(start=1,mat=3);assert owner==0
 fill_cache()
 for pos in range(15,-1,-1):
  for mat in range(3):assert matrix(pos,mat,stall=pos==15)
 for abort in (2,2300,2450):
  assert not matrix(0,1,abort=abort);fill_cache()
 assert matrix(0,2,stall=True)
 tick()
 assert counts['matrices']==49 and counts['owner_aborts']==3
 assert counts['groups']>=49*128*4 and counts['results']>=49*128
 return rows,dict(clocks=len(rows),counts=counts,calls=calls,
  scope='all16 layer0 cached normalized inputs through all Q/K/V full128-row matrices,stalls/reset/refill andAgroup ownership; no fullattention or transformer')


def cloud_check(net,cut,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from nand import blif,from_yosys
 from ci import cec
 import verify as checks
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 (OUT/'connector.blif').write_text(blif(cut));ys=OUT/'reference.ys'
 ys.write_text(f'read_verilog {OUT}/connector.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=300)
 ref=from_yosys(json.loads((OUT/'reference.json').read_text()),cut.n_in,cut.n_out)
 (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(cut)))
 good=cec(abc,OUT/'connector.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 first=next(i for i,(_,_,m) in enumerate(rows) if m&1);prefix=rows[:first+513]
 wrong=checks.check_nand(prefix,flip_output(net).encode());assert wrong>0
 wrong_rotation=make(True)[0];mis=checks.check_nand(prefix,wrong_rotation.encode());assert mis>0
 (OUT/'negative.v').write_text(rtl(flip_output(net),'cache_matrix'))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'cache_matrix',str(OUT/'vectors.txt')))
 checks.run([checks.compile_rtl('source',OUT/'matrix.v')],600)
 failed=subprocess.run([str(checks.compile_rtl('negative',OUT/'negative.v'))],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
 assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
 return dict(status='pass',connector_all_input_cec=good,actual_owner_D_mutation=bad,clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,
  negative_prefix_clocks=len(prefix),actual_data_gate_mismatches=wrong,actual_A_rotation_removed_mismatches=mis,actual_RTL_mutation_rejected=True,
  proof_scope='all4 ownerD andeveryleafinput/output connection forarbitrary cuts; actualfullgate/C sequence; NOT independent fullengine allstate proof')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();cache.bank.OUT=OUT/'c_vectors';cache.bank.OUT.mkdir(parents=True,exist_ok=True)
 cases=cache.bank.golden();refs=golden(cases);rows,expected=vectors(cases,refs);net,parts=make();cut=connector();cut_small=small_connector(cut)
 (OUT/'matrix.nl').write_bytes(net.encode());(OUT/'matrix.v').write_text(rtl(net,'cache_matrix'));(OUT/'connector.ref.v').write_text(connector_ref())
 assert cut.n_in==995 and cut.n_out==991
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','bench.py','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl']:paths.add(R/n)
 report=dict(status='small owner and true C-data matrix reference pass; full gates pending',metrics=metrics(net),parts=parts,small=sm,expected=expected,
  connector=metrics(cut),small_connector=cut_small,leaf_sha256=dict(cache=CACHE_SHA,engine=ENGINE_SHA),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'c_vectors/cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,scope=expected['scope'],
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['verification']=cloud_check(net,cut,rows);report['status']='connector CEC and full actual NAND/RTL/C-data matrices pass with faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','parts','small','connector']},indent=2))
 print(json.dumps({k:v for k,v in expected.items() if k!='calls'},indent=2))


if __name__=='__main__':main()
