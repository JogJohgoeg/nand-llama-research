#!/usr/bin/env python3
"""Specialized rotating attention memory:32 rows of s42 numerator+s20 Q.

This is a component candidate, not yet a replacement of the R66 head.
The frozen s42 bound is retained; arbitrary64-bit FFN storage is out of scope.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse
R=Path(os.environ.get('H3_HEAD_STORE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import value_row as base
import score_dot as dot
import ff_continuous as old_ff
from nand import Builder,metrics,with_state,verify_state,flip_output
from gate_check import Snapshot
from export import rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_HEAD_STORE_OUT',str(R/'build/integer_opt/head_store')))
NS,NI,NO=2040,79,89
MASK42=(1<<42)-1;MASK20=(1<<20)-1


def make():
 b=Builder(NS+NI);old=list(range(2,2+NS));p=list(range(2+NS,2+NS+NI))
 mem=old[:1984];payload=old[1984:2026];cursor=old[2026:2031];phase=old[2031:2033];address=old[2033:2038];mode=old[2038:]
 reset,start=p[:2];asked=p[2:7];cmd=p[7:9];data=p[9:51];enable,read,qvalid=p[51:54];qa=p[54:59];qdata=p[59:79]
 def eq(xs,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(xs)],b.land,1)
 def match(xs,ys):return b.reduce([b.inv(b.xor(x,y)) for x,y in zip(xs,ys)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);idle=b.lor(eq(phase,0),eq(phase,3));home=eq(mode,2);writing=eq(mode,1)
 begin=AND(keep,start,idle,b.inv(eq(cmd,3)));target=[AND(b.inv(home),v) for v in address]
 accept=AND(keep,eq(phase,1),match(cursor,target),b.lor(b.inv(writing),enable))
 store=AND(accept,writing);capture=AND(accept,b.inv(b.lor(home,writing)))
 available=AND(keep,eq(phase,2));consume=AND(available,read)
 qmatch=AND(keep,match(cursor,qa));qready=AND(qmatch,idle);qstore=AND(qready,qvalid)
 tail=[b.mux(store,x,y) for x,y in zip(mem[:42],payload)]+[b.mux(qstore,x,y) for x,y in zip(mem[42:62],qdata)]
 md=mem[62:]+tail;pd=[b.mux(AND(begin,eq(cmd,1)),x,y) for x,y in zip(payload,data)]
 pd=[b.mux(capture,x,y) for x,y in zip(pd,mem[:42])]
 cd=[AND(keep,v) for v in b.add(cursor,[0]*5,1)[0]]
 ph=phase[:]
 for event,value in ((begin,[1,0]),(accept,[b.lor(home,writing),1]),(consume,[1,1])):ph=[b.mux(event,x,y) for x,y in zip(ph,value)]
 ctl=[AND(keep,v) for v in ph]+[AND(keep,b.mux(begin,x,y)) for x,y in zip(address,asked)]+[AND(keep,b.mux(begin,x,y)) for x,y in zip(mode,cmd)]
 busy=AND(keep,b.lor(eq(phase,1),eq(phase,2)));done=AND(keep,eq(phase,3),b.inv(begin))
 outputs=payload+[payload[-1]]*22+[available,busy,done]+mem[42:62]+[qready,qmatch]
 comb=b.finish(md+pd+cd+ctl+outputs);return with_state(comb,NS),comb


class Model:
 def __init__(self):
  self.memory=[0]*32;self.known=[0]*32;self.payload=self.cursor=self.phase=self.address=self.mode=0
  self.counts=dict(clocks=0,commands=0,completed=0,aborts=0,acc_writes=0,acc_reads=0,q_writes=0,q_observed=0,holds=0,write_stalls=0,invalid_commands=0)
 def state(self):
  return sum(v<<(62*i) for i,v in enumerate(self.memory))+(self.payload<<1984)+(self.cursor<<2026)+(self.phase<<2031)+(self.address<<2033)+(self.mode<<2038)
 def tick(self,x):
  r=x&1;start=x>>1&1;asked=x>>2&31;cmd=x>>7&3;data=x>>9&MASK42;enable=x>>51&1;read=x>>52&1;qvalid=x>>53&1;qa=x>>54&31;qdata=x>>59&MASK20
  p=self.phase;idle=p in (0,3);begin=not r and start and idle and cmd!=3
  target=0 if self.mode==2 else self.address;accept=not r and p==1 and self.cursor==target and (self.mode!=1 or enable)
  available=not r and p==2;qmatch=not r and self.cursor==qa;qready=qmatch and idle;qstore=qready and qvalid
  signed=self.payload-(1<<42) if self.payload>>41 else self.payload
  y=(signed&((1<<64)-1))+(int(available)<<64)+(int(not r and p in (1,2))<<65)+(int(not r and p==3 and not begin)<<66)+(self.memory[0]>>42<<67)+(int(qready)<<87)+(int(qmatch)<<88)
  mask=(7<<64)+(3<<87)
  if available:mask|=(1<<64)-1
  if qmatch and self.known[0]&2:mask|=MASK20<<67
  tail=self.memory[0];known=self.known[0];c=self.counts;c['clocks']+=1;c['invalid_commands']+=int(not r and start and idle and cmd==3)
  c['holds']+=int(available and not read);c['write_stalls']+=int(not r and p==1 and self.mode==1 and self.cursor==target and not enable)
  c['q_observed']+=int(qmatch and bool(known&2))
  if r:c['aborts']+=int(p in (1,2));self.phase=self.address=self.mode=0
  elif begin:
   self.phase=1;self.address=asked;self.mode=cmd;c['commands']+=1
   if cmd==1:self.payload=data
  elif accept:
   if self.mode==1:tail=(tail&~MASK42)|self.payload;known|=1;self.phase=3;c['acc_writes']+=1;c['completed']+=1
   elif self.mode==2:self.phase=3;c['completed']+=1
   else:assert known&1;self.payload=tail&MASK42;self.phase=2
  elif available and read:self.phase=3;c['acc_reads']+=1;c['completed']+=1
  if qstore:tail=(tail&MASK42)+(qdata<<42);known|=2;c['q_writes']+=1
  self.memory=self.memory[1:]+[tail];self.known=self.known[1:]+[known];self.cursor=0 if r else (self.cursor+1)&31
  if r:self.known=[0]*32
  return y,mask


def golden():
 base.OUT=OUT/'values';base.OUT.mkdir(parents=True,exist_ok=True);g,vc=base.golden()
 dot.OUT=OUT/'q';dot.OUT.mkdir(parents=True,exist_ok=True);dg,qc=dot.golden()
 cases=[]
 for layer,v in enumerate(vc['cases']):
  q=qc['cases'][16*layer]['q'];assert all(-(1<<41)<=n<(1<<41) for part in v['partials'] for n in part)
  cases.append(dict(**v,q=q))
 # A8 dequantization is sat20;16 legal u17 terms fit strictly within s42.
 lo=-524288*131071*16;hi=524287*131071*16;assert -(1<<41)<=lo<=hi<(1<<41)
 d=dict(cases=cases,minimum_bound=lo,maximum_bound=hi,accumulator_bits=42,values_sha256=sha((base.OUT/'cases.json').read_bytes()),q_sha256=sha((dot.OUT/'cases.json').read_bytes()),
  scope='real frozen-C Q and all16 V numerator prefixes of five heads; only s42 C16 storage,not arbitrary64-bit FFN')
 (OUT/'cases.json').write_text(json.dumps(d,indent=2)+'\n');return d


def vectors(cases):
 m=Model();rng=random.Random(260769);rows=[];logical=[None]*32;ql=[None]*32
 def tick(reset=0,start=0,address=0,cmd=0,data=0,enable=1,read=0,qvalid=0,qa=None,qdata=0):
  if qa is None:qa=rng.randrange(32)
  x=reset+(start<<1)+(address<<2)+(cmd<<7)+((data&MASK42)<<9)+(enable<<51)+(read<<52)+(qvalid<<53)+(qa<<54)+((qdata&MASK20)<<59)
  y,mask=m.tick(x);rows.append((x,y,mask))
  if not reset and y>>88&1 and ql[qa] is not None:assert y>>67&MASK20==ql[qa]&MASK20
  if y>>87&1 and qvalid:ql[qa]=qdata
  if reset:logical[:]=[None]*32;ql[:]=[None]*32
  return y
 def qload(q):
  for i,v in enumerate(q):
   before=len(rows)
   while not tick(qvalid=1,qa=i,qdata=v)>>87&1:assert len(rows)-before<33
 def command(cmd,address=0,data=0,abort=None):
  assert m.phase in (0,3);before=len(rows);tick(start=1,address=address,cmd=cmd,data=data);value=None
  for cycle in range(200):
   if abort and abort(m):tick(reset=1,start=1,cmd=3,qvalid=1,qdata=-1);return False
   if m.phase==3:break
   # Bound the test's artificial stalls: after two turns the consumer releases.
   take=int(cycle>=64 or rng.randrange(4)!=0);enable=int(cycle>=64 or rng.randrange(4)!=0)
   y=tick(start=1,cmd=rng.randrange(4),address=rng.randrange(32),data=rng.getrandbits(42),enable=enable,read=take)
   if y>>64&1 and take:value=y&((1<<64)-1)
  else:raise AssertionError(('head store command timeout',cmd,address,len(rows),m.phase,m.address,m.mode,m.cursor,m.counts))
  if cmd==0:assert value==logical[address]&((1<<64)-1)
  if cmd==1:logical[address]=data
  return True
 tick(reset=1)
 for c in cases['cases']:
  qload(c['q'])
  for partial in c['partials']:
   for i,v in enumerate(partial):command(1,i,v)
   for i in range(32):command(0,i)
  command(2)
  # Idle invalid commands cannot corrupt either packed field.
  for _ in range(32):tick(start=1,cmd=3,qvalid=0)
 for i,v in enumerate([-(1<<41),(1<<41)-1,-1,0,1]+[cases['minimum_bound'],cases['maximum_bound']]):command(1,i,v);command(0,i)
 q=[-524288,524287,-1,0]+list(range(28));qload(q)
 for mode,predicate in ((1,lambda s:s.phase==1),(0,lambda s:s.phase==2)):
  assert not command(mode,0,17,abort=predicate);qload(q)
  for i in range(32):command(1,i,i-16)
  for i in range(32):command(0,i)
 return rows,dict(clocks=len(rows),counts=m.counts,scope='all5x16x32 frozen C V prefixes,all real Q lanes,42bit endpoints,stalls,invalidcmd and reset/refill')


def small(net,comb,rows):
 assert metrics(net)['nNand']+NS<=4000
 rng=random.Random(260770);xs=[];ys=[]
 for i in range(512):
  m=Model();m.memory=[rng.getrandbits(62) for _ in range(32)];m.known=[3]*32;m.payload=rng.getrandbits(42);m.cursor=rng.randrange(32);m.phase=i&3;m.address=rng.randrange(32);m.mode=i>>2&3
  old=m.state();x=rng.getrandbits(NI);y,_=m.tick(x);xs.append(old+(x<<NS));ys.append(m.state()+(y<<NS))
 proof=verify_state(comb,xs,ys,NS);proof.pop('nl_hex')
 prefix=rows[:4096]
 def run(graph):
  g=Snapshot.decode(graph.encode(),NI,NO);state=bytes(NS);wrong=0
  for x,y,mask in prefix:
   state,out=g.step(state,bytes(x>>i&1 for i in range(NI)));wrong+=int(bool((sum(v<<i for i,v in enumerate(out))^y)&mask))
  return wrong
 assert run(net)==0;bad=run(flip_output(net));assert bad>0
 return dict(arbitrary_state=proof,actual_prefix_clocks=len(prefix),actual_prefix_mismatches=0,actual_data_gate_mismatches=bad)


def reference():
 return '''module top(input [2118:0] din,output [2128:0] dout);
wire [1983:0] memory=din[1983:0];wire [41:0] payload=din[2025:1984];
wire [4:0] cursor=din[2030:2026],address=din[2037:2033];wire [1:0] phase=din[2032:2031],mode=din[2039:2038];
wire [78:0] pins=din[2118:2040];wire reset=pins[0],start=pins[1],enable=pins[51],read=pins[52],qvalid=pins[53];
wire [4:0] asked=pins[6:2],qa=pins[58:54];wire [1:0] command=pins[8:7];wire [41:0] data=pins[50:9];wire [19:0] qdata=pins[78:59];
wire idle=phase==0 || phase==3,begin_command=!reset && start && idle && command!=3;
wire [4:0] target=mode==2 ? 5'd0 : address;
wire accept=!reset && phase==1 && cursor==target && (mode!=1 || enable);
wire store=accept && mode==1,capture=accept && mode!=1 && mode!=2;
wire available=!reset && phase==2,consume=available && read;
wire qmatch=!reset && cursor==qa,qready=qmatch && idle,qstore=qready && qvalid;
reg [1:0] next_phase;reg [4:0] next_address;reg [1:0] next_mode;
always @* begin
 next_phase=phase;next_address=address;next_mode=mode;
 if(reset)begin next_phase=0;next_address=0;next_mode=0;end
 else if(begin_command)begin next_phase=1;next_address=asked;next_mode=command;end
 else if(accept)next_phase=(mode==1 || mode==2)?3:2;
 else if(consume)next_phase=3;
end
assign dout[1983:0]={qstore?qdata:memory[61:42],store?payload:memory[41:0],memory[1983:62]};
assign dout[2025:1984]=capture?memory[41:0]:(begin_command && command==1)?data:payload;
assign dout[2030:2026]=reset?5'd0:cursor+5'd1;
assign dout[2039:2031]={next_mode,next_address,next_phase};
assign dout[2128:2040]={qmatch,qready,memory[61:42],!reset && phase==3 && !begin_command,!reset && (phase==1 || phase==2),available,{{22{payload[41]}},payload}};
endmodule
'''


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);cases=golden();rows,expected=vectors(cases);net,comb=make();sm=small(net,comb,rows)
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='full-size small component prefix/arbitrary state and complete C storage schedule pass; full cloud pending',metrics=metrics(net),small=sm,expected=expected,
  previous_ff=metrics(old_ff.make()[0]),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,scope='specialized s42 V numerator+s20 Q memory only; no generic64-bit equivalence or R66/fullmodel integration')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','docs/index.html',
  'physical/units/manifest.json','physical/units/serial_mul.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl','integer_opt/score_units/manifest.json','integer_opt/score_units/score.nl']:paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  base.OUT=OUT;base.reference=reference;base.NI=NI;base.NO=NO;report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='all2040 state bits and89outputs for packed42/20 memory; does not prove caller arithmetic or replace arbitrary64-bit FFN storage'
  report['status']='full CEC and actual NAND/RTL/C memory schedule pass with real faults';(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources',)},indent=2))


if __name__=='__main__':main()
