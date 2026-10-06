#!/usr/bin/env python3
"""Autonomous QK-pair -> score -> EXP -> normalized V head composition.

No new state: the score FIFO count supplies the requested history index,
and the dot controller supplies the requested Q/K lane. Storage/dequantized
Q/K pairs remain an explicit caller interface; V words use the existing bank.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,re
R=Path(os.environ.get('H3_PAIRS_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_stream as head
import score_dot as dot
import value_row as base
from nand import Builder,metrics,with_state,simulate,flip_output
from export import import_net,rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_PAIRS_OUT',str(R/'build/integer_opt/attention_pairs')))
HS,DS,NI,NO=12698,118,331,39


def connect(b,reset,score_phase,dot_phase,valid):
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 ready=b.land(b.inv(reset),eq(score_phase,1));idle=eq(dot_phase,0)
 start=b.reduce([ready,idle,b.inv(valid)],b.land,1)
 return [start,ready]


def small():
 b=Builder(6);acts=connect(b,2,[3,4],[5,6],7);net=b.finish(acts)
 xs=list(range(64));ys=[]
 for x in xs:
  reset=x&1;sp=x>>1&3;dp=x>>3&3;valid=x>>5&1;ready=not reset and sp==1
  ys.append(int(ready and dp==0 and not valid)+(int(ready)<<1))
 assert metrics(net)['nNand']<=4000 and simulate(net,xs)==ys
 bad=flip_output(net);wrong=sum(a!=b for a,b in zip(simulate(bad,xs),ys));assert wrong==64
 return dict(metrics(net),vectors=64,mismatches=0,actual_gate_fault_mismatches=wrong)


def make():
 hn=head.make()[0];dn=dot.make()[0];assert hn.n_state==HS and dn.n_state==DS
 ns=HS+DS;b=Builder(ns+NI);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+NI));hs=old[:HS];ds=old[HS:]
 reset,start,load=pins[:3];n=pins[3:8];incoming=pins[8];q=pins[9:29];k=pins[29:49];address=pins[49:54];word=pins[54:330];take=pins[330]
 start_dot,take_dot=connect(b,reset,hs[head.VS+558:head.VS+560],ds[115:117],ds[117])
 dd,do=import_net(b,dn,[reset,start_dot,incoming]+q+k+[take_dot],ds)
 hd,ho=import_net(b,hn,[reset,start,load]+n+do[:32]+[do[38]]+address+word+[take],hs)
 history=hs[head.VS+549:head.VS+553]
 outputs=ho[:27]+[do[37]]+ho[28:30]+do[32:37]+history
 comb=b.finish(hd+dd+outputs);return with_state(comb,ns),comb,dict(head=metrics(hn),dot=metrics(dn),new_state_bits=0,
  reuse='R61 fill count names history; R63 lane names scalar; no new transaction/score register',
  scope='caller supplies stable handshaken dequantized s20 Q/K pairs; V8 words still use real existing KV bank; Q/K storage and global arithmetic sharing external')


class Model:
 def __init__(self,vg,sg,dg):self.h=head.Model(vg,sg);self.d=dot.Model(dg);self.counts=dict(clocks=0,pairs=0,score_transfers=0)
 def tick(self,x):
  reset=x&1;start=x>>1&1;load=x>>2&1;n=x>>3&31;incoming=x>>8&1;q=x>>9&1048575;k=x>>29&1048575;address=x>>49&31;word=x>>54&((1<<276)-1);take=x>>330&1
  ready=int(not reset and self.h.s.phase==1);begin=int(ready and self.d.phase==0 and not self.d.done);history=self.h.s.count&15
  do,_=self.d.tick(reset+(begin<<1)+(incoming<<2)+(q<<3)+(k<<23)+(ready<<43))
  valid=do>>38&1;score=do&0xffffffff;lane=do>>32&31;pair_ready=do>>37&1
  ho,mask=self.h.tick(reset+(start<<1)+(load<<2)+(n<<3)+(score<<8)+(valid<<40)+(address<<41)+(word<<46)+(take<<322))
  out=(ho&~(1<<27))+(pair_ready<<27)+(lane<<30)+(history<<35)
  mask|=(511<<30) if pair_ready else 0
  self.counts['clocks']+=1;self.counts['pairs']+=int(pair_ready and incoming);self.counts['score_transfers']+=int(ready and valid)
  return out,mask


def golden():
 head.OUT=OUT/'head';head.OUT.mkdir(parents=True,exist_ok=True);vg,sg,hc=head.golden()
 dot.OUT=OUT/'dot';dot.OUT.mkdir(parents=True,exist_ok=True);dg,dc=dot.golden()
 assert len(dc['cases'])==80;cases=[]
 for layer,c in enumerate(hc['cases']):
  dots=dc['cases'][16*layer:16*layer+16];assert [d['score'] for d in dots]==c['scores'];assert all(d['q']==dots[0]['q'] for d in dots)
  cases.append(dict(**c,q=dots[0]['q'],keys=[d['k'] for d in dots]))
 report=dict(cases=cases,head_cases_sha256=sha((head.OUT/'cases.json').read_bytes()),dot_cases_sha256=sha((dot.OUT/'cases.json').read_bytes()),
  scope='independent frozen-C observations of raw Q/K, scores and V/partials joined for each real layer/head/history')
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return vg,sg,dg,report


def vectors(vg,sg,dg,cases):
 m=Model(vg,sg,dg);rng=random.Random(260765);rows=[];calls=[]
 def tick(reset=0,start=0,load=0,n=0,incoming=0,q=0,k=0,address=0,word=0,take=0):
  x=reset+(start<<1)+(load<<2)+(n<<3)+(incoming<<8)+((q&1048575)<<9)+((k&1048575)<<29)+(address<<49)+(word<<54)+(take<<330)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def preload(words):
  for j,word in enumerate(words):
   assert m.h.phase in (0,7);tick(start=1,load=1,address=j,word=word);before=len(rows)
   while m.h.phase!=7:
    tick(start=1,load=rng.randrange(2),n=rng.randrange(32),incoming=1,q=rng.randrange(1048576),k=rng.randrange(1048576),address=rng.randrange(32),word=rng.getrandbits(276),take=1)
    assert len(rows)-before<100
 def command(c,n,abort=None,stall=True):
  before=len(rows);tick(start=1,n=n);values=[];pairs=0
  for _ in range(55000):
   if abort and abort(m):tick(reset=1,start=1,load=1,n=31,incoming=1,q=-524288,k=-524288,take=1);return False
   incoming=int(m.d.phase==1 and (not stall or rng.randrange(4)!=0));take=int(not stall or rng.randrange(4)!=0)
   if m.d.phase==1:
    history=m.h.s.count;lane=m.d.lane;assert 0<=history<n
    q=c['q'][lane];k=c['keys'][history][lane]
   else:q,k=rng.randrange(1048576),rng.randrange(1048576)
   y=tick(start=1,load=rng.randrange(2),n=rng.randrange(32),incoming=incoming,q=q,k=k,address=rng.randrange(32),word=rng.getrandbits(276),take=take)
   if y>>27&1 and incoming:
    assert y>>30&31==pairs%32 and y>>35&15==pairs//32;pairs+=1
   if y>>25&1 and take:
    assert y>>20&31==len(values);v=y&1048575;values.append(v-(1<<20) if v>>19 else v)
   if m.h.phase==7:break
  else:raise AssertionError('pair head timeout')
  assert pairs==32*n
  weights=[int(sg.score_exp(max(c['scores'][:n]),s)) for s in c['scores'][:n]];wanted=[]
  for lane in range(32):
   acc=0
   for w,word in zip(weights,c['words']):
    q=word>>(8*lane)&255;acc=vg.value_acc(acc,q-256 if q>=128 else q,word>>256,w)
   wanted.append(int(vg.slice_sat(vg.int_rne(acc,sum(weights)))))
  assert values==wanted and m.h.v.den==sum(weights) and m.h.v.row_count==n
  calls.append(dict(length=n,clocks=len(rows)-before,pairs=pairs,values=values,stall=stall));tick();return True
 tick(reset=1)
 for n in (0,17,31):tick(start=1,n=n,incoming=1,take=1);assert m.h.phase==0
 for c in cases['cases']:
  preload(c['words']);command(c,1);command(c,16,stall=False)
 c=cases['cases'][0]
 predicates=[lambda m:m.d.phase==1 and m.d.lane==0,
             lambda m:m.d.phase==2 and m.d.count==19,
             lambda m:m.d.done,
             lambda m:m.h.s.count==3 and m.d.phase==2,
             lambda m:m.h.phase in (3,4) and m.h.v.v.control&15==7,
             lambda m:m.h.phase==5 and m.h.v.control&3==2]
 for pred in predicates:
  tick(reset=1);preload(c['words']);assert not command(c,16,abort=pred)
  preload(c['words']);command(c,3)
 return rows,dict(clocks=len(rows),counts=m.counts,head_counts=m.h.counts,dot_counts=m.d.counts,calls=calls,
  scope='five true full16-row heads,arithmetic prefixes,handshaken Q/K lane/history,random dead inputs,six abort/reload/restarts')


def reference():
 hr=head.reference().replace('module top(', 'module head_ref(',1);dr=dot.reference().replace('module top(', 'module dot_ref(',1)
 ns=HS+DS
 s=hr+dr+f'''module top(input [{ns+NI-1}:0] din,output [{ns+NO-1}:0] dout);
wire [12697:0] hs=din[12697:0];wire [117:0] ds=din[{ns-1}:12698];wire [330:0] pins=din[{ns+NI-1}:{ns}];
wire reset=pins[0],score_ready=!reset && hs[{head.VS+559}:{head.VS+558}]==1;
wire start_dot=score_ready && ds[116:115]==0 && !ds[117];
wire [157:0] dres;dot_ref dot_unit({{score_ready,pins[48:29],pins[28:9],pins[8],start_dot,reset,ds}},dres);
wire [12727:0] hres;head_ref head_unit({{pins[330],pins[329:54],pins[53:49],dres[156],dres[149:118],pins[7:0],hs}},hres);
assign dout[{ns-1}:0]={{dres[117:0],hres[12697:0]}};
assign dout[{ns+NO-1}:{ns}]={{hs[{head.VS+552}:{head.VS+549}],dres[154:150],hres[12727:12726],dres[155],hres[12724:12698]}};
endmodule
'''
 names=re.findall(r'module (\w+)\(',s);assert len(names)==len(set(names)),names
 return s


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();vg,sg,dg,cases=golden();rows,expected=vectors(vg,sg,dg,cases);net,comb,parts=make()
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small connector exhaustive and full C numerical schedule pass; full source prepared only',metrics=metrics(net),parts=parts,
  small=sm,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,scope='Q/K dequantized pair requests are external; scores/weights/denominator/means internal; not full-model storage/control')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','physical/golden_slice.c','docs/index.html',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/exp.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','integer_opt/score_units/manifest.json','integer_opt/score_units/score.nl']:paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  base.OUT=OUT;base.reference=reference;base.NI=NI;base.NO=NO;report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='all D/output independent composition with pinned score/EXP/DIV/dequantizer boundaries; no unbounded reachability or physical proof'
  report['status']='full CEC and NAND/RTL/C pair-to-mean head pass with actual faults';(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print(expected['clocks'],expected['counts'],expected['head_counts'])


if __name__=='__main__':main()
