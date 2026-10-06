#!/usr/bin/env python3
"""C16 weighted V stream with an internal exact denominator and16-row bound.

CLEAR resets sum/count. Accepted ACCUMULATE adds its u17 weight once, at
most16 times. READ requires a positive sum; weights still come from outside.
"""
from pathlib import Path
import os,sys,inspect,textwrap,json,hashlib,random,signal,argparse
R=Path(os.environ.get('H3_DENOMINATOR_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import value_normalize as norm
import value_compact_mul as source
import value_row as base
from nand import Builder,metrics,with_state,verify_state
from export import import_net,rtl
sha=lambda raw:hashlib.sha256(raw).hexdigest()
OUT=R/'build/integer_opt/value_denominator'
NI,NO=303,29


def denominator(b,old,pins):
 den=old[:22];rows=old[22:];reset,start=pins[:2];cmd=pins[2:4];busy=pins[4];w=pins[5:]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);acc=eq(cmd,1);read=eq(cmd,2);positive=b.reduce(den,b.lor,0)
 allowed=AND(keep,start,b.lor(b.inv(acc),b.inv(rows[4])),b.lor(b.inv(read),positive))
 accept=AND(allowed,b.inv(busy));clear=b.lor(reset,AND(accept,eq(cmd,0)));update=AND(accept,acc)
 summed=b.add(den,w+[0]*5)[0];counted=b.add(rows,[0]*5,1)[0]
 ds=[AND(b.inv(clear),b.mux(update,x,y)) for x,y in zip(den,summed)]
 ds += [AND(b.inv(clear),b.mux(update,x,y)) for x,y in zip(rows,counted)]
 return ds,allowed


def den_step(state,pins):
 d=state&4194303;rows=state>>22;reset=pins&1;start=pins>>1&1;cmd=pins>>2&3;busy=pins>>4&1;w=pins>>5
 allowed=int(not reset and start and (cmd!=1 or rows<16) and (cmd!=2 or d!=0));accept=allowed and not busy
 if reset or accept and cmd==0:d=rows=0
 elif accept and cmd==1:d=(d+w)&4194303;rows=(rows+1)&31
 return d+(rows<<22),allowed


def small_check():
 b=Builder(49);d,a=denominator(b,list(range(2,29)),list(range(29,51)));net=b.finish(d+[a])
 rng=random.Random(260734);xs=[];ys=[]
 for j in range(4096):
  state=(rng.getrandbits(22) if j%2 else 0)+((j%32)<<22)
  pins=rng.getrandbits(22);v,a=den_step(state,pins);xs.append(state+(pins<<27));ys.append(v+(a<<27))
 result=verify_state(net,xs,ys,27);result.pop('nl_hex')
 # Independent exact accepted-weight accounting, including the16-row limit.
 for weights in ([131071]*16,[0]*16,list(range(16)),[65536]+[0]*15):
  state=0
  for i,w in enumerate(weights):
   state,a=den_step(state,2+4+(w<<5));assert a and state&4194303==sum(weights[:i+1]) and state>>22==i+1
  before=state;state,a=den_step(state,2+4+(1<<5));assert not a and state==before
  state,a=den_step(state,2+8);assert a==int(sum(weights)>0)
  state,a=den_step(state,2);assert a and state==0
 return result


def make():
 core=source.make()[0];div=norm.divider();ns=core.n_state+115+9+20+27
 b=Builder(ns+NI);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+NI))
 vs=old[:core.n_state];ds=old[core.n_state:core.n_state+115];ctl=old[core.n_state+115:core.n_state+124]
 result=old[-47:-27];dstate=old[-27:];reset,start=pins[:2];command=pins[2:4];read=pins[302]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);core_phase=vs[11930:11934];busy=AND(keep,b.inv(b.lor(eq(core_phase,0),eq(core_phase,15))))
 den_d,allowed=denominator(b,dstate,[reset,start]+command+[busy]+pins[9:26])
 take=AND(keep,eq(ctl[:2],2),read);vp=pins[:];vp[1]=allowed;vp[302]=take
 vd,vo=import_net(b,core,vp,vs);assert busy==vo[71]
 cd,act=norm.control(b,ctl,[reset,vo[69],read]);load,finish,valid,consume=act;assert consume==take
 dd,do=import_net(b,div,[load]+vo[:64]+dstate[:22]+[0]*3,ds)
 rd=[AND(keep,b.mux(finish,x,y)) for x,y in zip(result,do[179:199])]
 last=AND(valid,b.reduce(vo[64:69],b.land,1))
 outputs=result+vo[64:69]+[valid,last,vo[71],vo[72]]
 comb=b.finish(vd+dd+cd+rd+den_d+outputs);net=with_state(comb,ns)
 return net,comb,dict(value=metrics(core),divider=metrics(div),control_bits=9,result_bits=20,denominator_bits=22,row_count_bits=5,
  state_order='R58 value,DIV115,phase2/count7,result20,denominator22,row_count5',
  bounds='at most16 accepted u17 weights, sum <=2097136; actual C16 EXP sum <=1048576; no overflow or precision change',
  protocol='CLEAR resets sum/count; each accepted ACCUMULATE adds once;17th update and zero-total READ ignored; reset invalidates old logical state')


class Model(norm.Model):
 def __init__(self,g):super().__init__(g);self.row_count=0;self.counts['invalid_full_accumulates']=0


r=source.previous.replace_once
text=textwrap.dedent(inspect.getsource(norm.Model.tick))
text=r(text,'asked=x>>303&4194303','asked=self.den;w=x>>9&131071')
text=r(text,'allowed=int(start and (cmd!=2 or asked!=0));phase=self.control&3',
 '''old_busy=int(not reset and self.v.control&15 not in (0,15))
 nd,allowed=den_step(self.den+(self.row_count<<22),reset+(start<<1)+(cmd<<2)+(old_busy<<4)+(w<<5))
 phase=self.control&3''')
text=r(text,'vo,_=self.v.tick(vp);available=vo>>69&1;busy=vo>>71&1',
 '''vo,_=self.v.tick(vp);available=vo>>69&1;busy=vo>>71&1
 assert busy==old_busy
 self.counts['invalid_full_accumulates']+=int(not reset and start and cmd==1 and self.row_count>=16 and not busy)''')
text=r(text,'if allowed and cmd==2 and not busy:self.den=asked','pass  # denominator follows independent accepted-command step below')
text=r(text,'self.control=cd','self.control=cd;self.den=nd&4194303;self.row_count=nd>>22')
env=dict(norm.__dict__,den_step=den_step);exec(compile(text,'<internal denominator reference tick>','exec'),env);Model.tick=env['tick']


def vectors(g,cases):
 m=Model(g);rng=random.Random(260735);rows=[];calls=[];logical=[0]*32;total=0;accepted_rows=0
 def tick(reset=0,start=0,cmd=0,address=0,w=0,word=0,read=0):
  x=reset+(start<<1)+(cmd<<2)+(address<<4)+(w<<9)+(word<<26)+(read<<302)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def command(cmd,address=0,w=0,word=0,abort=None):
  nonlocal total,accepted_rows
  assert m.v.control&15 in (0,15) and m.control&3==0
  assert cmd!=1 or accepted_rows<16
  assert cmd!=2 or total>0
  before=len(rows);tick(start=1,cmd=cmd,address=address,w=w,word=word);values=[]
  if cmd==0:total=accepted_rows=0
  if cmd==1:total+=w;accepted_rows+=1
  while m.v.control&15!=15:
   if abort and abort(m):tick(reset=1,start=1,cmd=1,w=131071,read=1);total=accepted_rows=0;return False
   take=int(rng.randrange(4)!=0)
   y=tick(start=int(rng.randrange(13)==0),cmd=rng.randrange(4),address=rng.randrange(32),w=rng.randrange(131072),word=rng.getrandbits(276),read=take)
   assert m.den==total and m.row_count==accepted_rows
   if y>>25&1 and take:
    assert y>>20&31==len(values)
    v=y&1048575;values.append(v-(1<<20) if v>>19 else v)
   assert len(rows)-before<6000
  assert m.control&3==0 and m.den==total and m.row_count==accepted_rows
  if cmd==2:assert values==[int(g.slice_sat(g.int_rne(n,total))) for n in logical]
  calls.append(dict(command=cmd,denominator=total,rows=accepted_rows,clocks=len(rows)-before,values=values));return True
 def clear():command(0);logical[:]=[0]*32
 def apply(address,w,word):
  command(1,address,w)
  for i in range(32):
   q=word>>(8*i)&255;logical[i]=g.value_acc(logical[i],q-256 if q>=128 else q,word>>256,w)
 def uniform(code,maximum):return sum((code&255)<<(8*i) for i in range(32))+(maximum<<256)
 tick(reset=1)
 for case in cases['cases']:
  clear();tick(start=1,cmd=2);assert m.v.control&15==15 and m.den==0
  for j,word in enumerate(case['words']):command(3,16+j,word=word)
  for j,(word,w) in enumerate(zip(case['words'],case['weights'])):
   apply(16+j,w,word);assert logical==case['partials'][j]
   if total:command(2)
  assert total==sum(case['weights']) and accepted_rows==16
  tick(start=1,cmd=1,w=131071);assert m.v.control&15==15 and m.den==total and m.row_count==16
  command(2)
 # Two-row true weighted means exercise signed ties without external denominator.
 for a,b in ((0,1),(1,2),(0,-1),(-1,-2),(127,-128),(0,0)):
  clear();wa=uniform(a,127);wb=uniform(b,127)
  command(3,0,word=wa);command(3,1,word=wb);apply(0,1,wa);apply(1,1,wb);command(2)
 # Full legal u17 weights stress exact sum bounds and16-row rejection.
 clear();word=uniform(-128,1048575);command(3,0,word=word)
 for _ in range(16):apply(0,131071,word)
 assert total==2097136;command(2);tick(start=1,cmd=1,w=1);assert m.v.control&15==15 and m.den==2097136
 # All-zero weights cannot produce a READ result.
 clear();word=uniform(127,1048575);command(3,0,word=word)
 for _ in range(16):apply(0,0,word)
 before=m.counts['divisions'];tick(start=1,cmd=2);assert m.v.control&15==15 and m.counts['divisions']==before
 clear();command(3,0,word=word);apply(0,65536,word)
 for cmd,predicate in [(2,lambda s:s.control&3==1 and s.control>>2==17),(2,lambda s:s.control&3==2),(2,lambda s:s.v.control&15==5),(1,lambda s:s.v.control&15==7 and s.v.control>>11==8)]:
  assert not command(cmd,0,65536,abort=predicate)
  clear();command(3,0,word=word);apply(0,65536,word);command(2)
 return rows,dict(clocks=len(rows),counts=m.counts,value_counts=m.v.counts,calls=calls,
  final_denominator=total,final_rows=accepted_rows,
  scope='five true C heads; partial weighted means; signed half ties; max and zero16-row sums; rejected17th update; four combined reset aborts')


def reference():
 text=norm.reference()
 text=r(text,'input [12453:0] din,output [12157:0] dout','input [12436:0] din,output [12162:0] dout')
 text=r(text,'wire [324:0] pins=din[12453:12129]','wire [4:0] rows=din[12133:12129];wire [302:0] pins=din[12436:12134]')
 text=r(text,'wire [21:0] asked=pins[324:303];','wire [16:0] weight=pins[25:9];')
 text=r(text,'wire allowed=start && (command!=2 || asked!=0);','wire allowed=!reset && start && (command!=1 || rows<16) && (command!=2 || den!=0);')
 text=r(text,"wire [21:0] next_den=reset?22'd0:allowed && command==2 && !busy?asked:den;", """wire accept=allowed && !busy,clear=reset || (accept && command==0),add=accept && command==1;
wire [21:0] next_den=clear?22'd0:add?den+{5'd0,weight}:den;
wire [4:0] next_rows=clear?5'd0:add?rows+5'd1:rows;""")
 text=r(text,'assign dout[12128:0]={next_den,next_result,next_count,next_phase,dres[114:0],vres[11962:0]};',
 'assign dout[12133:0]={next_rows,next_den,next_result,next_count,next_phase,dres[114:0],vres[11962:0]};')
 text=r(text,'assign dout[12157:12129]','assign dout[12162:12134]')
 return text


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT;norm.OUT=OUT
 small=small_check();g,cases=norm.golden();rows,expected=vectors(g,cases);net,comb,parts=make()
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small denominator control and full C reference pass; full source constructed only',metrics=metrics(net),parts=parts,
  small=small,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,
  scope='C16 normalized V stream with exact internal denominator; score/EXP and top-level scheduling remain external')
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
  report['verification']['formal_scope']='Independent R58 and normalized-parent plus denominator/count RTL, composed with pinned identical DIV/dequantizer primitives'
  report['status']='whole D/output composition and actual NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print(expected['clocks'],expected['counts'])


if __name__=='__main__':main()
