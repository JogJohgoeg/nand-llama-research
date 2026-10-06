#!/usr/bin/env python3
"""C16 score stream connected to V cache/accumulation/normalization.

Preload V words, then submit scores. Parent sequencing owns clear, ordered
weight acceptance, accumulation and normalized output; QK scores stay external.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,re
R=Path(os.environ.get('H3_ATTENTION_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import score_stream as scores
import value_denominator as value
import value_row as base
from nand import Builder,metrics,with_state,verify_state
from export import import_net,rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_ATTENTION_OUT',str(R/'build/integer_opt/attention_stream')))
NI,NO=323,30
VS,SS=12134,561


def control(b,old,pins):
 phase=old;reset,start,load=pins[:3];n=pins[3:8];valid,last,vbusy,vdone=pins[8:]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);ph=[eq(phase,j) for j in range(8)];idle=b.lor(ph[0],ph[7])
 legal=AND(b.inv(eq(n,0)),b.lor(b.inv(n[4]),eq(n[:4],0)))
 begin=AND(keep,idle,start,b.lor(load,legal));head=AND(begin,b.inv(load));write=AND(begin,load)
 accept=AND(keep,ph[2],valid,b.inv(vbusy));read=AND(keep,ph[4],vdone)
 ds=phase[:]
 def jump(event,n):
  nonlocal ds
  ds=[b.mux(event,v,n>>j&1) for j,v in enumerate(ds)]
 jump(head,1);jump(write,6);jump(AND(keep,ph[1],vdone),2)
 jump(AND(accept,b.inv(last)),3);jump(AND(accept,last),4)
 jump(AND(keep,ph[3],vdone),2);jump(read,5)
 jump(AND(keep,b.lor(ph[5],ph[6]),vdone),7)
 ds=[AND(keep,v) for v in ds]
 acts=[begin,head,b.lor(b.lor(head,write),b.lor(accept,read)),b.lor(write,accept),b.lor(write,read),accept,
       AND(keep,ph[5]),AND(keep,b.inv(idle)),AND(keep,ph[7],b.inv(begin))]
 return ds,acts


def ctl_step(state,pins):
 p=state;r=pins&1;start=pins>>1&1;load=pins>>2&1;n=pins>>3&31
 valid,last,busy,done=[pins>>j&1 for j in range(8,12)]
 begin=not r and p in (0,7) and start and (load or 1<=n<=16)
 head=begin and not load;write=begin and load;accept=not r and p==2 and valid and not busy;read=not r and p==4 and done
 np=p
 if r:np=0
 elif head:np=1
 elif write:np=6
 elif p in (1,3) and done:np=2
 elif accept:np=4 if last else 3
 elif read:np=5
 elif p in (5,6) and done:np=7
 acts=[begin,head,head or write or accept or read,write or accept,write or read,accept,
       not r and p==5,not r and p not in (0,7),not r and p==7 and not begin]
 return np,sum(int(v)<<j for j,v in enumerate(acts))


def small():
 b=Builder(15);ds,a=control(b,list(range(2,5)),list(range(5,17)));net=b.finish(ds+a)
 xs=list(range(1<<15));ys=[]
 for x in xs:
  d,a=ctl_step(x&7,x>>3);ys.append(d+(a<<3))
 report=verify_state(net,xs,ys,3);report.pop('nl_hex');return report


def make():
 vn=value.make()[0];sn=scores.make()[0];assert vn.n_state==VS and sn.n_state==SS
 ns=VS+SS+3;b=Builder(ns+NI);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+NI))
 vs,ss,ps=old[:VS],old[VS:VS+SS],old[-3:];reset,start,load=pins[:3];n=pins[3:8]
 score=pins[8:40];incoming=pins[40];address=pins[41:46];word=pins[46:322];take=pins[322]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);index=ss[554:558];length=ss[544:549]
 valid=AND(keep,eq(ss[558:560],3));last=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(b.add(index+[0],[0]*5,1)[0],length)],b.land,1)
 vb=AND(keep,b.inv(b.lor(eq(vs[11930:11934],0),eq(vs[11930:11934],15))));vd=AND(keep,eq(vs[11930:11934],15))
 pd,act=control(b,ps,[reset,start,load]+n+[valid,last,vb,vd])
 begin,head,vstart,cmd0,cmd1,accept,output,busy,done=act
 sd,so=import_net(b,sn,[reset,head]+n+score+[incoming,accept],ss)
 assert so[22]==valid
 a=[b.mux(accept,x,y) for x,y in zip(address,index+[0])]
 d,vo=import_net(b,vn,[reset,vstart,cmd0,cmd1]+a+so[:17]+word+[AND(output,take)],vs)
 outputs=vo[:25]+[AND(output,vo[25]),AND(output,vo[26]),so[21],busy,done]
 comb=b.finish(d+sd+pd+outputs);net=with_state(comb,ns)
 return net,comb,dict(value=metrics(vn),scores=metrics(sn),parent_control_bits=3,
  state_order='R60 V12134,R61 scores561,parent phase3',
  protocol='LOAD writes supplied KV address/word while idle; HEAD accepts1..16 scores and autonomously CLEAR/ACC in score order/READ; final32 signed20 values use take',
  scope='score computation and physical storage sharing remain outside; existing full shared arithmetic not merged again')


class Model:
 def __init__(self,vg,sg):
  self.v=value.Model(vg);self.s=scores.Model(16,sg.score_exp);self.phase=0
  self.counts=dict(clocks=0,heads=0,loads=0,completed_heads=0,aborts=0,busy_starts=0,invalid_heads=0,weights=0,outputs=0,output_stalls=0)
 def tick(self,x):
  r=x&1;start=x>>1&1;load=x>>2&1;n=x>>3&31;score=x>>8&0xffffffff;incoming=x>>40&1;address=x>>41&31;word=x>>46&((1<<276)-1);take=x>>322&1
  p=self.phase;sp=self.s.phase;vp=self.v.v.control&15
  valid=int(not r and sp==3);last=int(self.s.index+1==self.s.length);busy=int(not r and vp not in (0,15));done=int(not r and vp==15)
  np,acts=ctl_step(p,r+(start<<1)+(load<<2)+(n<<3)+(valid<<8)+(last<<9)+(busy<<10)+(done<<11))
  begin,head,vstart,cmd0,cmd1,accept,output,pbusy,pdone=[acts>>j&1 for j in range(9)]
  index=self.s.index;so,_=self.s.tick(r+(head<<1)+(n<<2)+(score<<7)+(incoming<<39)+(accept<<40));weight=so&131071
  addr=index if accept else address
  vo,_=self.v.tick(r+(vstart<<1)+(cmd0<<2)+(cmd1<<3)+(addr<<4)+(weight<<9)+(word<<26)+((output and take)<<302))
  ov=int(output and vo>>25&1);ol=int(output and vo>>26&1);sr=so>>21&1
  out=(vo&((1<<25)-1))+(ov<<25)+(ol<<26)+(sr<<27)+(pbusy<<28)+(pdone<<29)
  mask=(1<<NO)-1 if ov else 31<<25
  c=self.counts;c['clocks']+=1;c['heads']+=head;c['loads']+=int(begin and load);c['weights']+=accept
  c['busy_starts']+=int(not r and start and p not in (0,7));c['invalid_heads']+=int(not r and start and not load and p in (0,7) and not begin)
  c['aborts']+=int(r and p not in (0,7));c['completed_heads']+=int(p==5 and np==7);c['outputs']+=int(ov and take);c['output_stalls']+=int(ov and not take)
  if accept:assert self.v.row_count==index+1
  self.phase=np;return out,mask


def golden():
 (OUT/'value').mkdir(parents=True,exist_ok=True);(OUT/'scores').mkdir(parents=True,exist_ok=True)
 base.OUT=OUT/'value';value.norm.OUT=OUT/'value';vg,vc=value.norm.golden()
 scores.OUT=OUT/'scores';sg,sc=scores.golden()
 cases=[]
 for v,s in zip(vc['cases'],sc['cases']):
  assert v['weights']==s['weights'];cases.append(dict(**v,scores=s['scores']))
 report=dict(cases=cases,score_sha256=sha((OUT/'scores/cases.json').read_bytes()),value_sha256=sha((OUT/'value/cases.json').read_bytes()))
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return vg,sg,report


def vectors(vg,sg,cases):
 m=Model(vg,sg);rng=random.Random(260762);rows=[];calls=[]
 def tick(reset=0,start=0,load=0,n=0,score=0,incoming=0,address=0,word=0,take=0):
  x=reset+(start<<1)+(load<<2)+(n<<3)+((score&0xffffffff)<<8)+(incoming<<40)+(address<<41)+(word<<46)+(take<<322)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def preload(words):
  for j,word in enumerate(words):
   assert m.phase in (0,7);tick(start=1,load=1,address=j,word=word)
   before=len(rows)
   while m.phase!=7:
    tick(start=1,load=rng.randrange(2),n=rng.randrange(32),score=rng.getrandbits(32),incoming=1,address=rng.randrange(32),word=rng.getrandbits(276),take=1)
    assert len(rows)-before<100
 def command(case,n,abort=None,stalls=True):
  assert m.phase in (0,7);before=len(rows);tick(start=1,n=n);sent=0;values=[]
  for _ in range(40000):
   if abort and abort(m):tick(reset=1,start=1,load=1,n=31,incoming=1,take=1);return False
   incoming=int(m.s.phase==1 and (not stalls or rng.randrange(4)!=0));take=int(not stalls or rng.randrange(4)!=0)
   score=case['scores'][sent] if m.s.phase==1 else rng.getrandbits(32)
   y=tick(start=1,load=rng.randrange(2),n=rng.randrange(32),score=score,incoming=incoming,address=rng.randrange(32),word=rng.getrandbits(276),take=take)
   sent+=incoming
   if y>>25&1 and take:
    assert y>>20&31==len(values);v=y&1048575;values.append(v-(1<<20) if v>>19 else v)
   if m.phase==7:break
  else:raise AssertionError('head timeout')
  weights=[int(sg.score_exp(max(case['scores'][:n]),s)) for s in case['scores'][:n]]
  wanted=[]
  for lane in range(32):
   acc=0
   for w,word in zip(weights,case['words']):
    q=word>>(8*lane)&255;acc=vg.value_acc(acc,q-256 if q>=128 else q,word>>256,w)
   wanted.append(int(vg.slice_sat(vg.int_rne(acc,sum(weights)))))
  assert values==wanted and m.v.den==sum(weights) and m.v.row_count==n
  if n==16:assert weights==case['weights'] and wanted==[int(vg.slice_sat(vg.int_rne(v,sum(weights)))) for v in case['partials'][-1]]
  calls.append(dict(length=n,clocks=len(rows)-before,weights=weights,values=values,stalls=stalls));tick();return True
 tick(reset=1)
 for n in (0,17,31):tick(start=1,n=n,incoming=1,take=1);assert m.phase==0
 for case in cases['cases']:
  preload(case['words'])
  for n in (1,3,16):command(case,n,stalls=n!=16)
 case=cases['cases'][0]
 targets=[lambda s:s.phase==1 and s.s.phase==1 and s.s.count==0,
          lambda s:s.phase==2 and s.s.phase==3,
          lambda s:s.phase in (3,4) and s.v.v.control&15==7 and s.v.v.control>>11==8,
          lambda s:s.phase==5 and s.v.control&3==1 and s.v.control>>2==17,
          lambda s:s.phase==5 and s.v.control&3==2]
 for predicate in targets:
  tick(reset=1);preload(case['words']);assert not command(case,16,abort=predicate)
  preload(case['words']);command(case,3)
 return rows,dict(clocks=len(rows),counts=m.counts,score_counts=m.s.counts,value_counts=m.v.counts,calls=calls,
  scope='five true frozen-C heads plus1/3-row exact arithmetic prefixes; ordered weights and results, stalls,busy starts,invalid lengths,five reset stages/reload/restart')


def reference():
 vr=value.reference().replace('module top(', 'module denominator_ref(',1)
 sr=scores.reference().replace('module top(', 'module scores_ref(',1)
 ns=VS+SS+3
 text=f'''module top(input [{ns+NI-1}:0] din,output [{ns+NO-1}:0] dout);
wire [{VS-1}:0] vs=din[{VS-1}:0];wire [{SS-1}:0] ss=din[{VS+SS-1}:{VS}];wire [2:0] phase=din[{ns-1}:{ns-3}];
wire [322:0] pins=din[{ns+NI-1}:{ns}];wire reset=pins[0],start=pins[1],load=pins[2];wire [4:0] n=pins[7:3];
wire valid=!reset && ss[559:558]==3,last=({{1'b0,ss[557:554]}}+5'd1)==ss[548:544];
wire vbusy=!reset && vs[11933:11930]!=0 && vs[11933:11930]!=15,vdone=!reset && vs[11933:11930]==15;
wire begin_command=!reset && (phase==0 || phase==7) && start && (load || (n>=1 && n<=16));
wire head=begin_command && !load,write_word=begin_command && load;
wire accept=!reset && phase==2 && valid && !vbusy,read_values=!reset && phase==4 && vdone;
wire vstart=head || write_word || accept || read_values;wire [1:0] command={{write_word || read_values,write_word || accept}};
wire output_enable=!reset && phase==5;reg [2:0] next_phase;
always @* begin
 next_phase=phase;
 if(reset)next_phase=0;
 else if(head)next_phase=1;
 else if(write_word)next_phase=6;
 else if((phase==1 || phase==3) && vdone)next_phase=2;
 else if(accept)next_phase=last ? 4 : 3;
 else if(read_values)next_phase=5;
 else if((phase==5 || phase==6) && vdone)next_phase=7;
end
wire [586:0] sout;scores_ref score_unit({{accept,pins[40],pins[39:8],n,head,reset,ss}},sout);
wire [4:0] address=accept ? {{1'b0,ss[557:554]}} : pins[45:41];
wire [12162:0] vout;denominator_ref value_unit({{output_enable && pins[322],pins[321:46],sout[577:561],address,command,vstart,reset,vs}},vout);
wire busy=!reset && phase!=0 && phase!=7,done=!reset && phase==7 && !begin_command;
assign dout[{ns-1}:0]={{next_phase,sout[560:0],vout[12133:0]}};
assign dout[{ns+NO-1}:{ns}]={{done,busy,sout[582],output_enable && vout[12160],output_enable && vout[12159],vout[12158:12134]}};
endmodule
'''
 return vr+sr+text


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();vg,sg,cases=golden();rows,expected=vectors(vg,sg,cases);net,comb,parts=make()
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 assert not re.search(r"\d+'[dsDS][0-9]+\?",reference())
 names=re.findall(r'module (\w+)\(',reference());assert len(names)==len(set(names)),names
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small parent all-state check and full C reference pass; full source constructed only',metrics=metrics(net),parts=parts,
  small=sm,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  scope='autonomous score-to-normalized-V head; scores and KV words supplied externally; no whole-layer or whole-model claim')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if (R in p.parents or p.parent==Path(__file__).resolve().parent) and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','physical/golden_slice.c','docs/index.html',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/exp.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  base.OUT=OUT;base.reference=reference;base.NI=NI;base.NO=NO;report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='all parent/submodule D and outputs with independent reference composition; pinned EXP/DIV/dequantizer identical boundaries; no unbounded reachability proof'
  report['status']='whole D/output composition and actual NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2));print(expected['clocks'],expected['counts'])


if __name__=='__main__':main()
