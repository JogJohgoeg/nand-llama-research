#!/usr/bin/env python3
"""R58 V accumulator with serial exact RNE normalization to signed20.

READ32 captures a nonzero unsigned22 denominator and holds each source
numerator while the existing DIV core runs. No score/EXP generation here.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,ctypes as ct,subprocess
R=Path(os.environ.get('H3_NORMALIZE_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import value_compact_mul as source
import value_row as base
from nand import Builder,metrics,with_state,verify_state,verilog
from export import import_net,rtl
from golden import Netlist
sha=lambda raw:hashlib.sha256(raw).hexdigest()
OUT=R/'build/integer_opt/value_normalize'
NI,NO=325,29
CORE_NS=11963


def divider():
 d=R/'integer_opt/pilot_units';m=json.loads((d/'manifest.json').read_text())['serial_div'];raw=(d/'serial_div.nl').read_bytes()
 assert sha(raw)==m['sha256']=='a7d02bfc2dd7dfa50b03dddb8a538b8c2620086c63547058f9f84ddd4268f124'
 return Netlist.decode(raw,m['nIn'],m['nOut'])


def control(b,old,pins):
 phase=old[:2];count=old[2:];reset,available,read=pins
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);begin=AND(keep,eq(phase,0),available);finish=AND(keep,eq(phase,1),eq(count,64));valid=AND(keep,eq(phase,2));take=AND(valid,read)
 ph=phase[:]
 for event,n in ((begin,1),(finish,2),(take,0)):ph=[b.mux(event,x,n>>j&1) for j,x in enumerate(ph)]
 clear=b.lor(reset,b.lor(begin,finish));inc=b.add(count,[0]*7,eq(phase,1))[0]
 ds=[AND(keep,v) for v in ph]+[AND(b.inv(clear),v) for v in inc]
 return ds,[begin,finish,valid,take]


def ctl_step(state,pins):
 p=state&3;c=state>>2;r=pins&1;a=pins>>1&1;read=pins>>2&1
 begin=not r and p==0 and a;finish=not r and p==1 and c==64;valid=not r and p==2;take=valid and read
 np=0 if r or take else 1 if begin else 2 if finish else p
 nc=0 if r or begin or finish else (c+int(p==1))&127
 return np+(nc<<2),int(begin)+(int(finish)<<1)+(int(valid)<<2)+(int(take)<<3)


def small_control():
 b=Builder(12);d,a=control(b,list(range(2,11)),list(range(11,14)));net=b.finish(d+a)
 xs=list(range(1<<12));ys=[]
 for x in xs:
  d,a=ctl_step(x&511,x>>9);ys.append(d+(a<<9))
 v=verify_state(net,xs,ys,9);v.pop('nl_hex');return v


def make():
 core=source.make()[0];div=divider();assert core.n_state==CORE_NS and div.n_state==115
 ns=CORE_NS+115+9+20+22;b=Builder(ns+NI);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+NI))
 vs=old[:CORE_NS];ds=old[CORE_NS:CORE_NS+115];ctl=old[CORE_NS+115:CORE_NS+124];result=old[-42:-22];den=old[-22:]
 reset,start=pins[:2];command=pins[2:4];read=pins[302];asked=pins[303:]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);readcmd=eq(command,2);nonzero=b.reduce(asked,b.lor,0)
 allowed=AND(start,b.lor(b.inv(readcmd),nonzero));take=AND(keep,eq(ctl[:2],2),read)
 vp=pins[:303];vp[1]=allowed;vp[302]=take
 vd,vo=import_net(b,core,vp,vs)
 cd,acts=control(b,ctl,[reset,vo[69],read]);load,finish,valid,consume=acts;assert consume==take
 dd,do=import_net(b,div,[load]+vo[:64]+den+[0]*3,ds);scaled=do[179:199];assert len(scaled)==20
 capture_den=AND(keep,allowed,readcmd,b.inv(vo[71]))
 rd=[AND(keep,b.mux(finish,x,y)) for x,y in zip(result,scaled)]
 den_d=[AND(keep,b.mux(capture_den,x,y)) for x,y in zip(den,asked)]
 last=AND(valid,b.reduce(vo[64:69],b.land,1))
 outputs=result+vo[64:69]+[valid,last,vo[71],vo[72]]
 comb=b.finish(vd+dd+cd+rd+den_d+outputs);net=with_state(comb,ns)
 return net,comb,dict(value=metrics(core),divider=metrics(div),control_bits=9,result_bits=20,denominator_bits=22,
   state_order='R58 value, DIV115,phase2/count7,result20,denominator22',
   scope='same4 commands; READ32 uses captured nonzero u22 denominator and returns S(R(n,d)); no extra numerator register')


class Model:
 def __init__(self,g):
  self.v=source.Model(g.value_dequant);self.g=g;self.control=self.den=self.result=self.pending=0
  self.counts=dict(divisions=0,completed=0,outputs=0,aborts=0,read_stalls=0,invalid_zero_reads=0)
 def tick(self,x):
  reset=x&1;start=x>>1&1;cmd=x>>2&3;read=x>>302&1;asked=x>>303&4194303
  allowed=int(start and (cmd!=2 or asked!=0));phase=self.control&3
  take=int(not reset and phase==2 and read)
  vp=(x&((1<<303)-1)&~(2|(1<<302)))+(allowed<<1)+(take<<302)
  vo,_=self.v.tick(vp);available=vo>>69&1;busy=vo>>71&1
  cd,act=ctl_step(self.control,reset+(available<<1)+(read<<2));load,finish,valid,consume=[act>>j&1 for j in range(4)]
  assert consume==take
  lane=vo>>64&31;out=(self.result&1048575)+(lane<<20)+(valid<<25)+(int(valid and lane==31)<<26)+(busy<<27)+((vo>>72&1)<<28)
  mask=(1<<NO)-1 if valid else 15<<25
  self.counts['invalid_zero_reads']+=int(not reset and start and cmd==2 and not asked and not busy)
  self.counts['read_stalls']+=int(valid and not read);self.counts['outputs']+=consume
  if reset:
   self.counts['aborts']+=int(phase!=0);self.den=self.result=0
  else:
   if allowed and cmd==2 and not busy:self.den=asked
   if load:
    n=vo&((1<<64)-1);n=n-(1<<64) if n>>63 else n;assert self.den
    q,r=divmod(abs(n),self.den);q+=int(2*r>self.den or 2*r==self.den and q&1)
    py=max(-524288,min(524287,-q if n<0 else q))
    self.pending=int(self.g.slice_sat(self.g.int_rne(n,self.den)));assert self.pending==py
    self.counts['divisions']+=1
   if finish:self.result=self.pending;self.counts['completed']+=1
  self.control=cd
  return out,mask


def golden():
 g,cases=base.golden();lib=OUT/'normalization.so'
 subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(R/'physical/golden_slice.c'),'-o',str(lib)],check=True,timeout=30)
 norm=ct.CDLL(str(lib));norm.int_rne.argtypes=[ct.c_int64,ct.c_int64];norm.int_rne.restype=ct.c_int64
 norm.slice_sat.argtypes=[ct.c_int64];norm.slice_sat.restype=ct.c_int32
 g.int_rne=norm.int_rne;g.slice_sat=norm.slice_sat;g._normalization_library=norm
 return g,cases


def small_divider(g):
 # Only the isolated arithmetic/control client, below4000 total gates/state.
 from nand import flip_output
 from gate_check import Snapshot
 div=divider();ns=div.n_state+9+20;b=Builder(ns+92);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+92))
 ds=old[:115];cs=old[115:124];result=old[-20:];reset,start,read=pins[:3]
 cd,acts=control(b,cs,[reset,start,read]);load,finish,valid,take=acts
 dd,do=import_net(b,div,[load]+pins[3:67]+pins[67:],ds)
 rd=[b.land(b.inv(reset),b.mux(finish,x,y)) for x,y in zip(result,do[179:199])]
 busy=b.land(b.inv(reset),b.land(cs[0],b.inv(cs[1])))
 net=with_state(b.finish(dd+cd+rd+result+[valid,busy]),ns)
 assert metrics(net)['nNand']+ns<=4000
 rng=random.Random(260733);rows=[];remaining=result=valid=pending=0;counts=dict(completed=0,aborts=0,busy_starts=0,holds=0)
 def tick(n=0,d=1,start=0,read=0,reset=0):
  nonlocal remaining,result,valid,pending
  x=reset+(start<<1)+(read<<2)+((n&((1<<64)-1))<<3)+(d<<67)
  y=(result&1048575)+(valid<<20)+(int(remaining>0)<<21);rows.append((x,y,0 if reset else (1<<22)-1))
  if reset:counts['aborts']+=int(remaining>0 or valid);remaining=result=valid=0
  elif valid:
   if read:valid=0
   else:counts['holds']+=1
  elif remaining:
   counts['busy_starts']+=int(start);remaining-=1
   if not remaining:result=pending;valid=1;counts['completed']+=1
  elif start:
   pending=int(g.slice_sat(g.int_rne(n,d)));remaining=65
 cases=[(n,d) for n in (-2199023255552,-7,-3,-1,0,1,3,7,2199023255551) for d in (1,2,65536,4194303)]
 cases += [(rng.randint(-2199023255552,2199023255551),rng.randint(1,4194303)) for _ in range(28)]
 tick(reset=1)
 for n,d in cases:
  tick(n,d,start=1)
  for _ in range(65):tick(rng.randint(-2199023255552,2199023255551),rng.randint(1,4194303),start=int(rng.randrange(3)==0),read=1)
  assert valid and not remaining
  for _ in range(4):tick(start=1,n=131071,d=1)
  tick(read=1);tick()
 for elapsed in (1,17,64,65,66):
  tick(3,2,start=1)
  for _ in range(elapsed-1):tick()
  tick(reset=1,start=1,read=1);tick()
 def check(graph):
  v=Snapshot.decode(graph.encode(),graph.n_in,graph.n_out);state=bytes(ns);wrong=0
  for x,y,m in rows:
   state,out=v.step(state,bytes(x>>j&1 for j in range(92)))
   wrong+=int(bool((sum(z<<j for j,z in enumerate(out))^y)&m))
  return wrong
 assert check(net)==0;bad=check(flip_output(net));assert bad
 return dict(metrics=metrics(net),cases=len(cases),clocks=len(rows),counts=counts,
             actual_result_gate_mismatches=bad,clocks_from_load_through_capture=66)


def vectors(g,cases):
 m=Model(g);rng=random.Random(260732);rows=[];calls=[];logical=[0]*32
 def tick(reset=0,start=0,cmd=0,address=0,w=0,word=0,read=0,den=0):
  x=reset+(start<<1)+(cmd<<2)+(address<<4)+(w<<9)+(word<<26)+(read<<302)+(den<<303)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def command(cmd,address=0,w=0,word=0,den=1,abort=None):
  assert m.v.control&15 in (0,15) and m.control&3==0
  before=len(rows);tick(start=1,cmd=cmd,address=address,w=w,word=word,den=den);values=[]
  while m.v.control&15!=15:
   if abort and abort(m):tick(reset=1,start=1,cmd=2,den=1,read=1);return False
   take=int(rng.randrange(4)!=0)
   y=tick(start=int(rng.randrange(13)==0),cmd=rng.randrange(4),address=rng.randrange(32),w=rng.randrange(131072),word=rng.getrandbits(276),read=take,den=rng.randrange(4194304))
   if y>>25&1 and take:
    assert y>>20&31==len(values)
    val=y&1048575;values.append(val-(1<<20) if val>>19 else val)
   assert len(rows)-before<6000,(cmd,m.v.control&15,m.control&3)
  assert m.control&3==0
  if cmd==2:
   expected=[int(g.slice_sat(g.int_rne(n,den))) for n in logical];assert values==expected
  calls.append(dict(command=cmd,denominator=den,clocks=len(rows)-before,values=values));return True
 def clear():command(0);logical[:]=[0]*32
 def apply(address,w,word):
  command(1,address,w)
  for i in range(32):
   code=word>>(8*i)&255;logical[i]=g.value_acc(logical[i],code-256 if code>=128 else code,word>>256,w)
 tick(reset=1)
 for case in cases['cases']:
  clear();den=sum(case['weights']);assert 65536<=den<=16*65536
  command(2,den=den)
  for j,word in enumerate(case['words']):command(3,16+j,word=word)
  for j,(word,w) in enumerate(zip(case['words'],case['weights'])):
   apply(16+j,w,word);assert logical==case['partials'][j];command(2,den=den)
 boundary=[]
 for code,maximum,w,den in [(1,127,1,2),(1,127,3,2),(-1,127,1,2),(-1,127,3,2),(127,1048575,131071,1),(-128,1048575,131071,1),(127,1048575,131071,4194303),(0,0,0,1)]:
  clear();word=sum((code&255)<<(8*i) for i in range(32))+(maximum<<256);boundary.append((word,w,den))
  command(3,0,word=word);apply(0,w,word);command(2,den=den)
 # A zero denominator READ is ignored while idle/done, with no DIV started.
 before=m.counts['divisions'];tick(start=1,cmd=2,den=0);assert m.v.control&15==15 and m.counts['divisions']==before
 for cmd,predicate in [(2,lambda s:s.control&3==1 and s.control>>2==17),(2,lambda s:s.control&3==2),(2,lambda s:s.v.control&15==5),(1,lambda s:s.v.control&15==7 and s.v.control>>11==8)]:
  assert not command(cmd,0,65536,den=65536,abort=predicate)
  clear();word,w,den=boundary[4];command(3,0,word=word);apply(0,w,word);command(2,den=den)
 return rows,dict(clocks=len(rows),counts=m.counts,value_counts=m.v.counts,ff_counts=m.v.f.counts,calls=calls,
   scope='five real full C heads plus every partial numerator normalized by final true denominator; ties, signs, saturation, stalls and4 aborts')


def reference():
 core=source.reference().replace('module top(','module value_ref(',1)
 div=divider();b=Builder(div.n_state+div.n_in);bits=list(range(2,2+div.n_state+div.n_in))
 dd,do=import_net(b,div,bits[div.n_state:],bits[:div.n_state]);dv=verilog(b.finish(dd+do)).replace('module top(','module div_ref(',1)
 top='''module top(input [12453:0] din,output [12157:0] dout);
wire [11962:0] vs=din[11962:0];wire [114:0] ds=din[12077:11963];
wire [8:0] ctl=din[12086:12078];wire [19:0] result=din[12106:12087];wire [21:0] den=din[12128:12107];
wire [324:0] pins=din[12453:12129];wire reset=pins[0],start=pins[1],read=pins[302];
wire [1:0] command=pins[3:2],phase=ctl[1:0];wire [6:0] count=ctl[8:2];wire [21:0] asked=pins[324:303];
wire allowed=start && (command!=2 || asked!=0);wire valid=!reset && phase==2,take=valid && read;
wire [302:0] vp={take,pins[301:2],allowed,reset};wire [12035:0] vres;value_ref value({vp,vs},vres);
wire [72:0] vo=vres[12035:11963];wire available=vo[69],busy=vo[71];
wire load=!reset && phase==0 && available,finish=!reset && phase==1 && count==64;
reg [1:0] next_phase;reg [6:0] next_count;
always @* begin
 next_phase=phase;next_count=count+(phase==1);
 if(reset)begin next_phase=0;next_count=0;end
 else if(load)begin next_phase=1;next_count=0;end
 else if(finish)begin next_phase=2;next_count=0;end
 else if(take)next_phase=0;
end
wire [89:0] dp={3'd0,den,vo[63:0],load};wire [313:0] dres;div_ref divider({dp,ds},dres);
wire [19:0] next_result=reset?20'd0:finish?dres[313:294]:result;
wire [21:0] next_den=reset?22'd0:allowed && command==2 && !busy?asked:den;
assign dout[12128:0]={next_den,next_result,next_count,next_phase,dres[114:0],vres[11962:0]};
assign dout[12157:12129]={vo[72],busy,valid && vo[68:64]==31,valid,vo[68:64],result};
endmodule
'''
 return core+dv+top


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 small=small_control();g,cases=golden();isolated=small_divider(g);rows,expected=vectors(g,cases);net,comb,parts=make()
 assert sha(net.encode())=='426d186aa6c3d723395970c2914c69cf65b2d343b3ddc9f60a635d3462d36d03'
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small normalization control and full C reference pass; full source constructed only',metrics=metrics(net),parts=parts,
  control=small,isolated_divider=isolated,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,
  scope='V numerator plus exact serial S(R(n,d)), externally supplied exp weights and denominator; score/EXP/whole-layer ownership remain outside')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if (R in p.parents or p.parent==Path(__file__).resolve().parent) and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','physical/golden_slice.c','docs/index.html',
           'physical/units/manifest.json','physical/units/serial_mul.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl',
           'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  base.reference=reference;base.NI=NI;base.NO=NO;report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='Independent complete R58 behavior and normalization parent, composed with identical pinned DIV/dequantizer gates; not a new proof of those arithmetic units'
  report['status']='whole D/output composition and actual NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print(expected['clocks'],expected['counts'])


if __name__=='__main__':main()
