#!/usr/bin/env python3
"""Exact signed20 x signed20 serial QK dot32 and frozen score rounding.

Each accepted pair is captured. Twenty product steps replace the general
64-step multiplier; the sign bit subtracts in the final step. No local EDA.
"""
from pathlib import Path
import os,sys,ctypes as ct,hashlib,json,random,signal,argparse,subprocess,re
R=Path(os.environ.get('H3_SCORE_DOT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state,flip_output,verilog
from golden import Netlist
from export import import_net,rtl
from gate_check import Snapshot
from ring_model import load,forward
import value_row as base
OUT=Path(os.environ.get('H3_SCORE_DOT_OUT',str(R/'build/integer_opt/score_dot')))
UNITS=Path(os.environ.get('H3_SCORE_DOT_UNITS',str(R/'integer_opt/score_units')))
sha=lambda b:hashlib.sha256(b).hexdigest()
NI,NO,NS=44,40,118
MASK40=(1<<40)-1


def mul_d(b,old,pins):
 load,step,last=pins[:3];x=pins[3:23];y=pins[23:]
 # High part needs21 bits before shifting, including -(-2^19).
 high=old[20:]+[old[39]];addend=[b.land(old[0],v) for v in x+[x[-1]]]
 subtract=b.land(last,old[0]);operand=[b.xor(v,subtract) for v in addend]
 added=b.add(high,operand,subtract)[0];shifted=old[1:20]+added
 ds=[b.mux(step,x,y) for x,y in zip(old,shifted)]
 return [b.mux(load,x,y) for x,y in zip(ds,y+[0]*20)]


def advance(p,load,step,last,x,y):
 if load:return y&1048575
 if not step:return p
 signed=p-(1<<40) if p>>39 else p
 return ((signed+((-x if last else x)<<20 if p&1 else 0))>>1)&MASK40


def multiply():
 b=Builder(83);old=list(range(2,42));pins=list(range(42,85));comb=b.finish(mul_d(b,old,pins)+old)
 return with_state(comb,40),comb


def unit():
 d=json.loads((UNITS/'manifest.json').read_text());raw=(UNITS/'score.nl').read_bytes();assert sha(raw)==d['metrics']['sha256']
 assert d['golden_c_sha256']=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 return Netlist.decode(raw,45,32)


def make(scored=True):
 b=Builder(NS+NI);s=list(range(2,2+NS));p=list(range(2+NS,2+NS+NI))
 product=s[:40];xhold=s[40:60];acc=s[60:105];lane=s[105:110];count=s[110:115];phase=s[115:117];done=s[117]
 reset,start,incoming=p[:3];x=p[3:23];y=p[23:43];take=p[43]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);ph=[eq(phase,j) for j in range(4)]
 begin=AND(keep,ph[0],b.inv(done),start);ready=AND(keep,ph[1]);accept=AND(ready,incoming)
 step=AND(keep,ph[2]);last_step=eq(count,19);add=AND(keep,ph[3]);last=eq(lane,31)
 pd=mul_d(b,product,[accept,step,last_step]+xhold+y)
 xd=[b.mux(accept,a,c) for a,c in zip(xhold,x)]
 extended=product+[product[39]]*5;summed=b.add(acc,extended)[0];clear=b.lor(reset,begin)
 ad=[AND(b.inv(clear),b.mux(add,a,c)) for a,c in zip(acc,summed)]
 ld=[AND(b.inv(clear),v) for v in b.add(lane,[0]*5,AND(add,b.inv(last)))[0]]
 cd=[AND(keep,ph[2],v) for v in b.add(count,[0]*5,1)[0]]
 phn=phase[:]
 def jump(event,n):
  nonlocal phn
  phn=[b.mux(event,v,n>>j&1) for j,v in enumerate(phn)]
 jump(begin,1);jump(accept,2);jump(AND(step,last_step),3);jump(AND(add,b.inv(last)),1);jump(AND(add,last),0)
 phn=[AND(keep,v) for v in phn]
 dn=AND(keep,b.lor(AND(add,last),AND(done,b.inv(take))))
 if scored:_,result=import_net(b,unit(),acc)
 else:result=acc
 outputs=result+lane+[ready,AND(keep,done),AND(keep,b.inv(ph[0]))]
 comb=b.finish(pd+xd+ad+ld+cd+phn+[dn]+outputs);return with_state(comb,NS),comb


class Model:
 def __init__(self,g,scored=True):
  self.g=g;self.scored=scored;self.product=self.x=self.acc=self.lane=self.count=self.phase=self.done=0
  self.counts=dict(clocks=0,commands=0,completed=0,aborts=0,pairs=0,mul_steps=0,input_stalls=0,output_stalls=0,busy_starts=0)
 def tick(self,p):
  r=p&1;start=p>>1&1;incoming=p>>2&1;x=p>>3&1048575;y=p>>23&1048575;take=p>>43&1
  x-=((x>>19)<<20);y-=((y>>19)<<20);ph=self.phase
  begin=not r and ph==0 and not self.done and start;ready=int(not r and ph==1);valid=int(not r and self.done)
  accept=ready and incoming;step=not r and ph==2;add=not r and ph==3;last=self.lane==31
  width=32 if self.scored else 45;value=int(self.g.qk_score(self.acc)) if self.scored else self.acc
  out=(value&((1<<width)-1))+(self.lane<<width)+(ready<<(width+5))+(valid<<(width+6))+(int(not r and ph!=0)<<(width+7))
  mask=(1<<(width+8))-1 if valid else 7<<(width+5)
  c=self.counts;c['clocks']+=1;c['commands']+=int(begin);c['pairs']+=int(accept);c['mul_steps']+=int(step)
  c['input_stalls']+=int(ready and not incoming);c['output_stalls']+=int(valid and not take);c['busy_starts']+=int(not r and start and not begin)
  if add:
   value=self.product-(1<<40) if self.product>>39 else self.product;self.acc+=value
   self.acc=((self.acc+(1<<44))&((1<<45)-1))-(1<<44)
   if last:self.phase=0;self.done=1;c['completed']+=1
   else:self.phase=1;self.lane=(self.lane+1)&31
  if step and self.count==19:self.phase=3
  self.product=advance(self.product,accept,step,self.count==19,self.x,y)
  self.count=(self.count+1)&31 if not r and ph==2 else 0
  if accept:self.x=x;self.phase=2
  if valid and take and not(add and last):self.done=0
  if r:
   c['aborts']+=int(ph!=0 or self.done);self.acc=self.lane=self.count=self.phase=self.done=0
  elif begin:self.acc=self.lane=0;self.phase=1
  return out,mask


def golden():
 source=(R/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 helpers='''static unsigned qk_count;
static int32_t qk_q[5][32],qk_k[5][16][32],qk_s[5][16];
unsigned qk_cases(void){return qk_count;}
int32_t qk_field(unsigned c,unsigned s,unsigned i){return i<32?qk_q[c][i]:i<64?qk_k[c][s][i-32]:qk_s[c][s];}
int32_t qk_score(int64_t dot){return (int32_t)int_rne(int_rne(dot,4096)*46341,262144);}
'''
 marker='static void attention(';assert source.count(marker)==1;source=source.replace(marker,helpers+'\n'+marker)
 marker='for(int s=0;s<=pos;s++){w[s]=exp_weight((int64_t)maximum-scores[s]);denominator+=w[s];}'
 hook='''
        if(pos==15 && h==0 && qk_count<5) {
            for(int i=0;i<32;i++)qk_q[qk_count][i]=q[i];
            for(int s=0;s<16;s++){
                qk_s[qk_count][s]=scores[s];
                for(int i=0;i<32;i++)qk_k[qk_count][s][i]=kv_dequant(keys[s][0][i],km[s][0]);
            }
            qk_count++;
        }'''
 assert source.count(marker)==1;source=source.replace(marker,marker+hook);p=OUT/'golden.c';p.write_text(source)
 so=OUT/'golden.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(so)],check=True,timeout=30)
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
 g=load(so,blob);g.qk_field.argtypes=[ct.c_uint]*3;g.qk_field.restype=ct.c_int32;g.qk_score.argtypes=[ct.c_int64];g.qk_score.restype=ct.c_int32
 payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1]);fixture=next(f for f in payload['fixtures'] if f['name']=='random16');actual=forward(g,fixture['ids']);assert actual['logits']==fixture['logit_sha256'] and actual['trace']==fixture['trace_sha256'];assert g.qk_cases()==5
 cases=[]
 for layer in range(5):
  q=[g.qk_field(layer,0,i) for i in range(32)]
  for history in range(16):
   k=[g.qk_field(layer,history,i+32) for i in range(32)];dot=sum(x*y for x,y in zip(q,k));score=g.qk_field(layer,history,64);assert g.qk_score(dot)==score
   cases.append(dict(layer=layer,history=history,q=q,k=k,dot=dot,score=score))
 score_hash=sha(b''.join(c['score'].to_bytes(4,'little',signed=True) for c in cases))
 assert score_hash=='c0aa7d35570aaf54951cd57c633cb4c83de58e0b38167705961e35ec0ba79554'
 report=dict(fixture=fixture,actual=actual,cases=cases,scores_le_sha256=score_hash,golden_instrumented_sha256=sha(source.encode()),scope='80 exact QK scores from head0/position15 across all5 layers of unchanged frozen random16 forward; same score hash as R61')
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return g,report


def vectors(g,cases,scored=True,random_cases=32):
 m=Model(g,scored);rng=random.Random(260763);rows=[];calls=[]
 def tick(reset=0,start=0,incoming=0,x=0,y=0,take=0):
  p=reset+(start<<1)+(incoming<<2)+((x&1048575)<<3)+((y&1048575)<<23)+(take<<43);o,mask=m.tick(p);rows.append((p,o,mask));return o
 def command(q,k,abort=None,stall=True):
  assert m.phase==0 and not m.done;before=len(rows);tick(start=1);sent=0
  while not m.done:
   if abort and abort(m):tick(reset=1,start=1,incoming=1,x=524287,y=-524288,take=1);return False
   incoming=int(m.phase==1 and (not stall or rng.randrange(4)!=0));x=q[sent] if m.phase==1 else rng.randrange(1048576);y=k[sent] if m.phase==1 else rng.randrange(1048576)
   tick(start=1,incoming=incoming,x=x,y=y);sent+=incoming
   assert len(rows)-before<1500
  expected=sum(x*y for x,y in zip(q,k));expected=int(g.qk_score(expected)) if scored else expected;width=32 if scored else 45
  first_valid=len(rows)-before
  for _ in range(3):
   o=tick(start=1,incoming=1,x=rng.randrange(1048576),y=rng.randrange(1048576));assert o&((1<<width)-1)==expected&((1<<width)-1)
  tick(start=1,take=1);assert not m.done
  calls.append(dict(clocks=len(rows)-before,first_valid_clock_offset=first_valid,score=int(g.qk_score(sum(x*y for x,y in zip(q,k)))),dot=sum(x*y for x,y in zip(q,k)),stall=stall));return True
 tick(reset=1)
 selected=cases['cases'] if scored else cases['cases'][:4]
 for c in selected:command(c['q'],c['k'],stall=False)
 for x,y in [(-524288,-524288),(-524288,524287),(524287,524287),(-1,-1),(0,0)]:command([x]*32,[y]*32)
 for _ in range(random_cases):command([rng.randint(-524288,524287) for _ in range(32)],[rng.randint(-524288,524287) for _ in range(32)])
 c=cases['cases'][0]
 for pred in [lambda m:m.phase==1 and m.lane==0,lambda m:m.phase==2 and m.count==19,lambda m:m.phase==3 and m.lane==31]:
  assert not command(c['q'],c['k'],abort=pred);command(c['q'],c['k'])
 return rows,dict(clocks=len(rows),counts=m.counts,calls=calls,scope='frozen real scores,signed20 extremes,random pairs,stalls,input changes after capture,last-sign-bit and final-ADD resets')


def simulate(net,rows):
 assert metrics(net)['nNand']+net.n_state<=4000
 g=Snapshot.decode(net.encode(),net.n_in,net.n_out);state=bytes(net.n_state);wrong=0
 for x,y,mask in rows:
  state,out=g.step(state,bytes(x>>j&1 for j in range(net.n_in)));wrong+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&mask))
 return wrong


def small(g,cases):
 net,comb=multiply();rng=random.Random(260764);xs=[];ys=[]
 for i in range(4096):
  p=rng.getrandbits(40);x=rng.randint(-524288,524287);y=rng.randint(-524288,524287);load=i&1;step=i>>1&1;last=i>>2&1
  pins=load+(step<<1)+(last<<2)+((x&1048575)<<3)+((y&1048575)<<23)
  xs.append(p+(pins<<40));ys.append(advance(p,load,step,last,x,y)+(p<<40))
 proof=verify_state(comb,xs,ys,40);proof.pop('nl_hex')
 mac,_=make(False);rows,expected=vectors(g,cases,False,8);assert simulate(mac,rows)==0;bad=simulate(flip_output(mac),rows);assert bad
 u=unit();dots=[-(1<<44),(1<<44)-1,-2049,-2048,-2047,-1,0,1,2047,2048,2049]+[rng.randint(-(1<<44),(1<<44)-1) for _ in range(1013)]
 expr=[(v&((1<<45)-1),g.qk_score(v)&0xffffffff,0xffffffff) for v in dots];assert simulate(u,expr)==0;ubad=simulate(flip_output(u),expr);assert ubad
 return dict(multiplier=metrics(net),arbitrary_state=proof,mac=metrics(mac),mac_expected=expected,mac_fault_clocks=bad,score_operator=metrics(u),score_cases=len(dots),score_fault_cases=ubad)


def reference():
 op=verilog(unit()).replace('module top(', 'module score_ref(',1)
 return op+'''module top(input [161:0] din,output [157:0] dout);
wire [39:0] product=din[39:0];wire signed [19:0] xhold=din[59:40];wire signed [44:0] acc=din[104:60];
wire [4:0] lane=din[109:105],count=din[114:110];wire [1:0] phase=din[116:115];wire done=din[117];
wire [43:0] pins=din[161:118];wire reset=pins[0],start=pins[1],incoming=pins[2],take=pins[43];
wire [19:0] x=pins[22:3],y=pins[42:23];
wire begin_command=!reset && phase==0 && !done && start,ready=!reset && phase==1;
wire accept=ready && incoming,step=!reset && phase==2,add=!reset && phase==3,last=lane==31;
wire signed [20:0] high={product[39],product[39:20]},operand={xhold[19],xhold};
wire signed [20:0] summand=product[0] ? operand : 21'sd0;
wire signed [20:0] next_high=(count==19) ? high-summand : high+summand;
wire [39:0] np=accept ? {20'd0,y} : step ? {next_high,product[19:1]} : product;
wire [19:0] nx=accept ? x : xhold;
wire signed [44:0] product45={{5{product[39]}},product};
wire signed [44:0] na=(reset || begin_command) ? 45'sd0 : add ? acc+product45 : acc;
wire [4:0] nl=(reset || begin_command) ? 5'd0 : lane+(add && !last);
wire [4:0] nc=(!reset && phase==2) ? count+5'd1 : 5'd0;
reg [1:0] ph;
always @* begin
 ph=phase;
 if(reset)ph=0;
 else if(begin_command)ph=1;
 else if(accept)ph=2;
 else if(step && count==19)ph=3;
 else if(add)ph=last ? 0 : 1;
end
wire nd=!reset && ((add && last) || (done && !take));wire [31:0] score;score_ref operator0(acc,score);
assign dout[117:0]={nd,ph,nc,nl,na,nx,np};
assign dout[157:118]={!reset && phase!=0,!reset && done,ready,lane,score};
endmodule
'''


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);g,cases=golden();sm=small(g,cases);rows,expected=vectors(g,cases);net,comb=make()
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small serial MAC and pinned score/C checks pass; full component prepared only',metrics=metrics(net),small=sm,expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,scope='pair inputs already dequantized; Q storage,K cache,dequantizer and R62 integration remain outside',
  state_bits=dict(product=40,captured_x=20,accumulator=45,lane=5,count=5,phase=2,valid=1))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','docs/index.html']:paths.add(R/n)
 sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)}
 for n in ['score.nl','manifest.json']:sources['integer_opt/score_units/'+n]=sha((UNITS/n).read_bytes())
 report.update(sources=sources,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'));(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  base.OUT=OUT;base.reference=reference;base.NI=NI;base.NO=NO;report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='all118 next-state bits and40 outputs against independent serial product/accumulator/control with same pinned score primitive; full20-step product checked by C vectors'
  report['status']='full CEC and actual NAND/RTL/C score-dot pass with real faults';(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected','small')},indent=2));print('small',sm['mac'],sm['mac_fault_clocks']);print('full',expected['clocks'],expected['counts'])


if __name__=='__main__':main()
