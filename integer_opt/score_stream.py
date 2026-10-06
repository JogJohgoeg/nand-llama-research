#!/usr/bin/env python3
"""Bounded C16 score FIFO, signed maximum and exact streamed EXP weights.

Standalone controller candidate. Its score bits can fit the existing misc
allocation, but physical storage sharing with sampling/FF is not integrated.
"""
from pathlib import Path
import os,sys,ctypes as ct,hashlib,json,subprocess,random,signal,argparse,re,shutil
R=Path(os.environ.get('H3_SCORE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state,flip_output,verilog,blif,from_yosys
from export import import_net,load_unit,rtl
from gate_check import Snapshot
from ring_model import load,forward
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_SCORE_OUT',str(R/'build/integer_opt/score_stream')))
GOLDEN='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'


def make(rows=16):
 assert rows in (2,4,16)
 a=(rows-1).bit_length();cw=a+1;ns=rows*32+32+2*cw+a+3;ni=36+cw;no=22+a
 b=Builder(ns+ni);s=list(range(2,2+ns));p=list(range(2+ns,2+ns+ni));end=rows*32
 memory=s[:end];maximum=s[end:end+32];length=s[end+32:end+32+cw];count=s[end+32+cw:end+32+2*cw]
 index=s[end+32+2*cw:end+32+2*cw+a];phase=s[-3:-1];done=s[-1]
 reset,start=p[:2];asked=p[2:2+cw];score=p[2+cw:34+cw];incoming,take=p[-2:]
 def eq(xs,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(xs)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);ph=[eq(phase,j) for j in range(4)]
 legal=AND(b.inv(eq(asked,0)),b.lor(b.inv(asked[-1]),eq(asked[:-1],0)))
 begin=AND(keep,ph[0],start,legal);ready=AND(keep,ph[1]);valid=AND(keep,ph[3])
 fill=AND(ready,incoming);accept=AND(valid,take)
 next_count=b.add(count,[0]*cw,1)[0]
 equal_count=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(next_count,length)],b.land,1)
 last=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(b.add(index+[0],[0]*cw,1)[0],length)],b.land,1)
 at_end=eq(count,rows-1);align=AND(keep,ph[2]);move=b.lor(fill,b.lor(align,accept))
 tail=[b.mux(fill,x,y) for x,y in zip(memory[:32],score)]
 md=[b.mux(move,x,y) for x,y in zip(memory,memory[32:]+tail)]
 _,ge=b.add(score,[b.inv(v) for v in maximum],1)
 ge=b.mux(b.xor(score[-1],maximum[-1]),ge,b.inv(score[-1]))
 changed=AND(fill,ge);mx=[b.mux(changed,x,y) for x,y in zip(maximum,score)]
 clear=b.lor(reset,begin);mx=[b.mux(clear,v,int(j==31)) for j,v in enumerate(mx)]
 nd=phase[:]
 def jump(en,value):
  nonlocal nd
  nd=[b.mux(en,v,value>>j&1) for j,v in enumerate(nd)]
 jump(begin,1);jump(AND(fill,equal_count,b.inv(at_end)),2);jump(AND(fill,equal_count,at_end),3)
 jump(AND(align,at_end),3);jump(AND(accept,last),0)
 nd=[AND(keep,v) for v in nd]
 ld=[AND(keep,b.mux(begin,x,y)) for x,y in zip(length,asked)]
 cd=[AND(b.inv(clear),b.mux(b.lor(fill,align),x,y)) for x,y in zip(count,next_count)]
 ix=[AND(b.inv(clear),v) for v in b.add(index,[0]*a,accept)[0]]
 dn=AND(accept,last);unit=load_unit('exp');assert unit.n_in==64 and unit.n_out==17
 _,weight=import_net(b,unit,maximum+memory[:32])
 outputs=weight+index+[ready,valid,AND(valid,last),AND(keep,b.inv(ph[0])),AND(keep,done,b.inv(begin))]
 comb=b.finish(md+mx+ld+cd+ix+nd+[dn]+outputs);net=with_state(comb,ns)
 return net,comb,dict(rows=rows,score_bits=rows*32,maximum_bits=32,controller_bits=2*cw+a+3,
  state_order='score FIFO,maximum32,length,count,index,phase2,done',exp=metrics(unit))


class Model:
 def __init__(self,rows,exp):
  self.rows=rows;self.a=(rows-1).bit_length();self.cw=self.a+1;self.exp=exp
  self.memory=[0]*rows;self.maximum=-(1<<31);self.length=self.count=self.index=self.phase=self.done=0
  self.counts=dict(clocks=0,commands=0,completed=0,aborted=0,input_stalls=0,output_stalls=0,busy_starts=0,invalid_starts=0,weights=0,align=0)
 def state(self):
  x=sum((v&0xffffffff)<<(32*j) for j,v in enumerate(self.memory));shift=self.rows*32
  for value,width in [(self.maximum&0xffffffff,32),(self.length,self.cw),(self.count,self.cw),(self.index,self.a),(self.phase,2),(self.done,1)]:
   x+=value<<shift;shift+=width
  return x
 def tick(self,x):
  cw=self.cw;reset=x&1;start=x>>1&1;n=x>>2&((1<<cw)-1);score=x>>(2+cw)&0xffffffff;score-=((score>>31)<<32)
  incoming=x>>(34+cw)&1;take=x>>(35+cw)&1;p=self.phase
  begin=not reset and p==0 and start and 1<=n<=self.rows
  ready=int(not reset and p==1);valid=int(not reset and p==3);last=self.index+1==self.length
  weight=self.exp(self.maximum,self.memory[0]) if valid else 0
  flags=ready+(valid<<1)+((valid and last)<<2)+(int(not reset and p!=0)<<3)+(int(not reset and self.done and not begin)<<4)
  out=weight+(self.index<<17)+(flags<<(17+self.a));mask=((1<<(22+self.a))-1) if valid else (31<<(17+self.a))
  fill=ready and incoming;accept=valid and take;align=not reset and p==2;at_end=self.count==self.rows-1
  c=self.counts;c['clocks']+=1;c['busy_starts']+=int(not reset and start and p!=0)
  c['invalid_starts']+=int(not reset and start and p==0 and not begin)
  c['input_stalls']+=int(ready and not incoming);c['output_stalls']+=int(valid and not take);c['weights']+=int(accept);c['align']+=int(align)
  if fill or align or accept:self.memory=self.memory[1:]+([score] if fill else [self.memory[0]])
  if fill:self.maximum=max(self.maximum,score)
  self.done=int(accept and last)
  if reset:
   c['aborted']+=int(p!=0);self.maximum=-(1<<31);self.length=self.count=self.index=self.phase=self.done=0
  elif begin:
   c['commands']+=1;self.maximum=-(1<<31);self.length=n;self.count=self.index=0;self.phase=1
  else:
   if fill or align:self.count=(self.count+1)&((1<<cw)-1)
   if fill and self.count==self.length:self.phase=3 if at_end else 2
   if align and at_end:self.phase=3
   if accept:
    self.index=(self.index+1)&((1<<self.a)-1)
    if last:self.phase=0;c['completed']+=1
  return out,mask


def golden():
 source=(R/'integer/int_model.c').read_text();assert sha(source.encode())==GOLDEN
 helper='''static int score_capture_count;
static int32_t captured_scores[5][16];
unsigned score_cases(void){return (unsigned)score_capture_count;}
int32_t score_field(unsigned layer,unsigned s){return captured_scores[layer][s];}
uint32_t score_exp(int32_t maximum,int32_t score){return exp_weight((int64_t)maximum-score);}
'''
 marker='static void attention(';assert source.count(marker)==1;source=source.replace(marker,helper+'\n'+marker)
 marker='for(int s=0;s<=pos;s++){w[s]=exp_weight((int64_t)maximum-scores[s]);denominator+=w[s];}'
 hook='''
        if(pos==15 && h==0 && score_capture_count<5) {
            for(int s=0;s<16;s++)captured_scores[score_capture_count][s]=scores[s];
            score_capture_count++;
        }'''
 assert source.count(marker)==1;source=source.replace(marker,marker+hook)
 path=OUT/'golden.c';path.write_text(source);so=OUT/'golden.so'
 subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(path),'-o',str(so)],check=True,timeout=30)
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1';g=load(so,blob);g.score_exp.argtypes=[ct.c_int32,ct.c_int32];g.score_exp.restype=ct.c_uint32
 g.score_field.argtypes=[ct.c_uint,ct.c_uint];g.score_field.restype=ct.c_int32
 payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1]);fixture=next(f for f in payload['fixtures'] if f['name']=='random16');actual=forward(g,fixture['ids'])
 assert actual['logits']==fixture['logit_sha256'] and actual['trace']==fixture['trace_sha256']
 assert g.score_cases()==5
 old=json.loads((R/'build/integer_opt/value_row/cases.json').read_text()) if (R/'build/integer_opt/value_row/cases.json').exists() else None
 cases=[]
 for layer in range(5):
  scores=[g.score_field(layer,s) for s in range(16)];weights=[g.score_exp(max(scores),s) for s in scores]
  if old:assert weights==old['cases'][layer]['weights']
  cases.append(dict(layer=layer,head=0,position=15,scores=scores,weights=weights))
 assert sha(b''.join(w.to_bytes(4,'little') for c in cases for w in c['weights']))=='4bd2095529aa2123b06e1ae88fb34e12bec25dc1fed2d2894b6b3cf604ccd81a'
 report=dict(fixture=fixture,actual=actual,cases=cases,golden_instrumented_sha256=sha(source.encode()),scope='true scores from unchanged frozen full random16 forward')
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return g,report


def vectors(rows,g,fixtures,random_cases=64):
 m=Model(rows,g.score_exp);rng=random.Random(260760+rows);stim=[];calls=[]
 def tick(reset=0,start=0,n=0,score=0,incoming=0,take=0):
  x=reset+(start<<1)+(n<<2)+((score&0xffffffff)<<(2+m.cw))+(incoming<<(34+m.cw))+(take<<(35+m.cw))
  y,mask=m.tick(x);stim.append((x,y,mask));return y
 def command(scores,abort=None):
  n=len(scores);before=len(stim);tick(start=1,n=n);sent=0;received=[]
  for step in range(300):
   if abort is not None and m.phase==abort:
    tick(reset=1,start=1,n=rows,incoming=1,take=1);return
   valid=m.phase==1 and rng.randrange(4)!=0;take=int(rng.randrange(3)!=0)
   score=scores[sent] if m.phase==1 else rng.randint(-(1<<31),(1<<31)-1)
   y=tick(start=1,n=rng.randrange(1<<(m.cw)),score=score,incoming=int(valid),take=take)
   if valid:sent+=1
   if y>>(18+m.a)&1 and take:received.append((y>>17&((1<<m.a)-1),y&131071))
   if m.phase==0:break
  else:raise AssertionError('protocol timeout')
  wanted=[g.score_exp(max(scores),s) for s in scores];assert received==list(enumerate(wanted))
  assert sum(wanted)>0;calls.append(dict(length=n,clocks=len(stim)-before,weights=wanted))
  for _ in range(rng.randrange(4)):tick()
 tick(reset=1)
 for n in (0,rows+1,(1<<m.cw)-1):tick(start=1,n=n,incoming=1,take=1);assert m.phase==0
 scoresets=[f['scores'][:rows] for f in fixtures['cases']]
 scoresets += [[0]*rows,[-(1<<31)]*rows,[(1<<31)-1]*rows,[-(1<<31),(1<<31)-1]* (rows//2)]
 for n in range(1,rows+1):scoresets.append([1000000-((j*64+32) if j else 0) for j in range(n)])
 scoresets += [[0,-65503,-65504,-65505,-65535,-65536,-65567,-65568,-65569][:rows]]
 for _ in range(random_cases):scoresets.append([rng.randint(-(1<<31),(1<<31)-1) for _ in range(rng.randrange(1,rows+1))])
 for values in scoresets:command(values)
 for phase in (1,2,3):
  command([1],abort=phase);command(scoresets[0])
 return stim,dict(clocks=len(stim),counts=m.counts,calls=calls,scope='input/output stalls,busy starts,invalid lengths,reset in fill/alignment/output,real scores,signed32 extremes and RNE/clipping boundaries')


def small(g,cases):
 rows=4;net,comb,_=make(rows);assert metrics(net)['nNand']+net.n_state<=4000,metrics(net)
 stim,expected=vectors(rows,g,cases,16)
 def simulate(graph):
  c=Snapshot.decode(graph.encode(),graph.n_in,graph.n_out);state=bytes(graph.n_state);wrong=0
  for x,y,mask in stim:
   state,out=c.step(state,bytes(x>>j&1 for j in range(graph.n_in)));got=sum(v<<j for j,v in enumerate(out));wrong+=int(bool((got^y)&mask))
  return wrong
 assert simulate(net)==0;bad=simulate(flip_output(net));assert bad
 rng=random.Random(260761);xs=[];ys=[]
 assert metrics(comb)['nNand']<=4000
 for _ in range(512):
  m=Model(rows,g.score_exp);m.memory=[rng.randint(-(1<<31),(1<<31)-1) for _ in range(rows)];m.maximum=max(m.memory)
  m.length=rng.getrandbits(m.cw);m.count=rng.getrandbits(m.cw);m.index=rng.getrandbits(m.a);m.phase=rng.randrange(4);m.done=rng.randrange(2)
  old=m.state();weight=g.score_exp(m.maximum,m.memory[0]);x=rng.getrandbits(net.n_in);out,_=m.tick(x);out=(out&~131071)|weight
  xs.append(old+(x<<net.n_state));ys.append(m.state()+(out<<net.n_state))
 transition=verify_state(comb,xs,ys,net.n_state);transition.pop('nl_hex')
 return dict(metrics=metrics(net),expected=expected,actual_weight_gate_mismatches=bad,arbitrary_state=transition,
  arbitrary_scope='all control bit patterns sampled; max>=stored scores preserves EXP arithmetic domain')


def reference(rows=16):
 a=(rows-1).bit_length();cw=a+1;end=rows*32;ns=end+32+2*cw+a+3;ni=36+cw;no=22+a
 exp=verilog(load_unit('exp')).replace('module top(', 'module exp_ref(',1)
 text=f"""module top(input [{ns+ni-1}:0] din,output [{ns+no-1}:0] dout);
wire [{end-1}:0] memory=din[{end-1}:0];wire signed [31:0] maximum=din[{end+31}:{end}];
wire [{cw-1}:0] length=din[{end+31+cw}:{end+32}],count=din[{end+31+2*cw}:{end+32+cw}];
wire [{a-1}:0] index=din[{ns-4}:{end+32+2*cw}];wire [1:0] phase=din[{ns-2}:{ns-3}];wire done=din[{ns-1}];
wire [{ni-1}:0] pins=din[{ns+ni-1}:{ns}];wire reset=pins[0],start=pins[1];
wire [{cw-1}:0] asked=pins[{cw+1}:2];wire signed [31:0] score=pins[{cw+33}:{cw+2}];
wire incoming=pins[{ni-2}],take=pins[{ni-1}];
wire begin_command=!reset && phase==0 && start && asked>=1 && asked<={rows};
wire ready=!reset && phase==1,valid=!reset && phase==3;
wire [{cw-1}:0] next_count=count+1'b1,ordinal={{1'b0,index}}+1'b1;
wire last=ordinal==length,fill=ready && incoming,accept=valid && take,align=!reset && phase==2;
wire [{end-1}:0] rotated={{{{fill ? score : memory[31:0]}},memory[{end-1}:32]}};
reg [{end-1}:0] nm;reg signed [31:0] mx;reg [{cw-1}:0] nl,nc;reg [{a-1}:0] ix;reg [1:0] ph;reg dn;
always @* begin
 nm=memory;mx=maximum;nl=length;nc=count;ix=index;ph=phase;dn=accept && last;
 if(fill || align || accept)nm=rotated;
 if(fill && score>=maximum)mx=score;
 if(reset)begin mx=32'h80000000;nl=0;nc=0;ix=0;ph=0;dn=0;end
 else if(begin_command)begin mx=32'h80000000;nl=asked;nc=0;ix=0;ph=1;end
 else begin
  if(fill || align)nc=next_count;
  if(fill && next_count==length)ph=(count=={rows-1}) ? 3 : 2;
  if(align && count=={rows-1})ph=3;
  if(accept)begin ix=index+1'b1;if(last)ph=0;end
 end
end
wire [16:0] weight;exp_ref unit0({{memory[31:0],maximum}},weight);
assign dout[{ns-1}:0]={{dn,ph,ix,nc,nl,mx,nm}};
assign dout[{ns+no-1}:{ns}]={{!reset && done && !begin_command,!reset && phase!=0,valid && last,valid,ready,index,weight}};
endmodule
"""
 return exp+text


def cloud(net,comb,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 import verify as checks
 (OUT/'stream.blif').write_text(blif(comb));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
 p=OUT/'reference.ys';p.write_text(f'read_verilog {OUT}/stream.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(p)],check=True,stdout=subprocess.DEVNULL,timeout=300)
 ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out);(OUT/'reference.blif').write_text(blif(ref))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'stream.blif',OUT/'reference.blif',OUT/'cec.log');bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log')
 assert good['verdict']=='equivalent' and bad['verdict']=='different'
 checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 fault=flip_output(net);wrong=checks.check_nand(rows,fault.encode());assert wrong>0
 (OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'score_stream',str(OUT/'vectors.txt')))
 (OUT/'bad.v').write_text(rtl(fault,'score_stream'))
 exe=checks.compile_rtl('source',OUT/'stream.v');checks.run([exe],300)
 exe=checks.compile_rtl('negative',OUT/'bad.v');r=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(r.stdout+r.stderr);assert r.returncode!=0 and 'C99 comparison failed' in r.stdout+r.stderr
 return dict(status='pass',all_state_output_cec=good,actual_D_mutation=bad,clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),actual_weight_gate_mismatches=wrong,actual_RTL_mutation_rejected=True,
  formal_scope='independent FIFO/control/signed-max behavior with identical pinned EXP primitive; numerical EXP domain is max>=score, ensured after complete fill')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);g,cases=golden();sm=small(g,cases)
 net,comb,parts=make();rows,expected=vectors(16,g,cases)
 assert sha(net.encode())=='78a6140250c5079b7e69e7d6f9b67468d57b1d830e0f9b368b8ce988a9b61ba5'
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'score_stream'));(OUT/'stream.ref.v').write_text(reference())
 assert not re.search(r"\d+'[sS]?[dD][0-9_]+\?",reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
 report=dict(status='small complete score stream/C and real faults pass; full source prepared only',metrics=metrics(net),parts=parts,small=sm,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),numerical_contract_changed=False,adopted=False,
  scope='standalone score/max/EXP stream; 512 score bits fit existing misc budget but physical sharing and R60 connection not integrated')
 paths={Path(__file__).resolve()}
 for mod in list(sys.modules.values()):
  name=getattr(mod,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/units/manifest.json','physical/units/exp.nl','physical/nl_sim.c','physical/verify.py','docs/index.html']:paths.add(R/name)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['verification']=cloud(net,comb,rows);report['status']='all D/output CEC and full NAND/RTL/C score/weight stream pass with actual faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('small','expected','sources')},indent=2));print('small',sm['metrics'],sm['expected']['clocks'],sm['actual_weight_gate_mismatches']);print('full',expected['counts'])

if __name__=='__main__':main()
