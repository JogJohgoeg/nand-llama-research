#!/usr/bin/env python3
"""Integrate the s42 numerator+s20 Q bank into the complete preloaded head.

Only the old FF payload/control outputs are consumed by the head. Map these
to the new bank, discard the entire old FF D cone, and build one new client.
The original model-wide FFN bank and its arbitrary64-bit domain are unchanged.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,re,inspect
R=Path(os.environ.get('H3_PACKED_HEAD_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_kv as prior
import attention_local_q as schedule
import head_store as store
import value_row as base
from nand import Builder,metrics,with_state,simulate,flip_output
from export import import_net,rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_PACKED_HEAD_OUT',str(R/'build/integer_opt/attention_packed')))
NS,NI,NO=12089,291,40


def actions(b,cs,fp,fm,parent,norm,reset,read,payload,product):
 def eq(xs,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(xs)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);phase=cs[:4];mode=cs[4:6];lane=cs[6:11];count=cs[11:16]
 available=AND(keep,eq(fp,2),b.inv(eq(fm,1)))
 write=AND(keep,b.lor(eq(phase,3),AND(eq(phase,7),eq(count,17))))
 get=AND(keep,eq(phase,5));home=AND(keep,eq(phase,8));begin=b.lor(write,b.lor(get,home))
 mul=AND(keep,eq(phase,6),eq(mode,1),available)
 output_take=AND(keep,eq(norm,2),eq(parent,5),read)
 take=AND(keep,eq(phase,6),eq(mode,2),available,output_take)
 added=b.add(payload,product+[product[-1]]*5)[0];data=[AND(b.inv(eq(mode,0)),v) for v in added]
 return [reset,begin]+lane+[write,home]+data+[1,b.lor(mul,take)]


def small():
 b=Builder(106);p=list(range(2,108));out=actions(b,p[:16],p[16:18],p[18:20],p[20:23],p[23:25],p[25],p[26],p[27:69],p[69:106]);net=b.finish(out)
 rng=random.Random(260771);xs=[];ys=[]
 for i in range(4096):
  x=rng.getrandbits(106);x=(x&~15)|(i%16)
  if i%4==0:x=(x&~(31<<11))|((i//4%32)<<11)
  cs=x&65535;phase=cs&15;mode=cs>>4&3;lane=cs>>6&31;count=cs>>11
  fp=x>>16&3;fm=x>>18&3;parent=x>>20&7;norm=x>>23&3;r=x>>25&1;read=x>>26&1;payload=x>>27&((1<<42)-1);prod=x>>69
  if prod>>36:prod-=1<<37
  available=not r and fp==2 and fm!=1;write=not r and (phase==3 or phase==7 and count==17);home=not r and phase==8;get=not r and phase==5
  take=not r and phase==6 and available and (mode==1 or mode==2 and norm==2 and parent==5 and read)
  data=(payload+prod)&((1<<42)-1) if mode else 0
  y=r+(int(write or home or get)<<1)+(lane<<2)+(int(write)<<7)+(int(home)<<8)+(data<<9)+(1<<51)+(int(take)<<52)
  xs.append(x);ys.append(y)
 assert metrics(net)['nNand']<=4000 and simulate(net,xs)==ys
 bad=sum(a!=b for a,b in zip(simulate(flip_output(net),xs),ys));assert bad==len(xs)
 return dict(metrics(net),vectors=len(xs),mismatches=0,actual_gate_fault_mismatches=bad)


def virtual_state(old):
 fs=old[9126:11166];payload=fs[1984:2026]
 virtual_ff=[0]*2688+payload+[payload[-1]]*22+fs[2026:2033]+fs[2033:2038]+[0]+fs[2038:2040]
 assert len(virtual_ff)==2767
 return old[:9126]+virtual_ff+old[11166:]


def make():
 pn=prior.make()[0];fn=store.make()[0];assert fn.n_state==2040
 b=Builder(NS+NI);old=list(range(2,2+NS));pins=list(range(2+NS,2+NS+NI));vs=virtual_state(old);fs=old[9126:11166]
 reset,start,load=pins[:3];qload=pins[8];address=pins[9:14];word=pins[14:290];take=pins[290]
 parent=vs[12695:12698];idle=b.lor(b.inv(b.reduce(parent,b.lor,0)),b.reduce(parent,b.land,1))
 qmode=b.reduce([idle,load,qload],b.land,1);qvalid=b.land(qmode,start)
 qa=[b.mux(qmode,x,y) for x,y in zip(vs[12803:12808],address)]
 fp=actions(b,vs[11930:11946],fs[2031:2033],fs[2038:2040],parent,vs[12078:12080],reset,take,fs[1984:2026],vs[11893:11930])
 fd,fo=import_net(b,fn,fp+[qvalid]+qa+word[:20],fs)
 legacy_start=b.land(start,b.inv(qload));incoming=fo[88]
 pd,po=import_net(b,pn,[reset,legacy_start,load]+pins[3:8]+[incoming]+fo[67:87]+address+word+[take],vs)
 ds=pd[:9126]+fd+pd[11893:];assert len(ds)==NS
 outputs=po[:27]+[b.land(incoming,po[27])]+po[28:]+[b.land(qmode,fo[87])]
 comb=b.finish(ds+outputs);net=with_state(comb,NS)
 return net,comb,dict(previous=metrics(schedule.make()[0]),packed_store=metrics(fn),new_state_bits=0,state_bits_removed=727,
  state_order='KV9126,packedQ/V2040,remainder923; virtual old FF memory constant because entire old FF D cone is discarded',
  protocol='QLOAD32 scalar s20 words at0..31,valid held untilready; normal KV LOAD unchanged; HEAD autonomous afterpreload',
  scope='preloaded singlehead with exact s42 state; not arbitrary64bit FFN replacement; Q/K/V producers and full-model ownership outside')


class FFAdapter:
 def __init__(self):self.s=store.Model();self.qa=self.qdata=self.qvalid=0;self.conflicts=0;self.counts=self.s.counts
 @property
 def payload(self):
  n=self.s.payload-(1<<42) if self.s.payload>>41 else self.s.payload;return n&((1<<64)-1)
 @property
 def phase(self):return self.s.phase
 @property
 def address(self):return self.s.address
 @property
 def mode(self):return self.s.mode
 @property
 def cursor(self):return self.s.cursor
 @property
 def qwrites(self):return self.s.counts['q_writes']
 def tick(self,x):
  r=x&1;start=x>>1&1;address=x>>2&63;cmd=x>>8&3;data=x>>10&((1<<64)-1);enable=x>>74&1;read=x>>75&1
  if not r and start and cmd==1:
   signed=data-(1<<64) if data>>63 else data;assert -(1<<41)<=signed<(1<<41),'numerator exceeded frozen s42'
  if self.qvalid and not r:
   assert self.phase in (0,3),'Q request during active V transaction'
  nx=r+(start<<1)+((address&31)<<2)+(cmd<<7)+((data&((1<<42)-1))<<9)+(enable<<51)+(read<<52)+(self.qvalid<<53)+(self.qa<<54)+(self.qdata<<59)
  return self.s.tick(nx)


class Model(prior.Model):
 def __init__(self,vg,sg,dg):super().__init__(vg,sg,dg);self.h.v.v.f=FFAdapter();self.qreads=0;self.last_q=None
 def tick(self,x):
  r=x&1;start=x>>1&1;load=x>>2&1;qload=x>>8&1;address=x>>9&31;word=x>>14&((1<<276)-1);take=x>>290&1
  f=self.h.v.v.f;s=f.s;idle=self.h.phase in (0,7);qmode=idle and load and qload
  f.qa=address if qmode else self.d.lane;f.qdata=word&1048575;f.qvalid=int(qmode and start)
  qmatch=not r and s.cursor==f.qa;q=s.memory[0]>>42;ready=not r and qmode and s.phase in (0,3) and qmatch
  if not r and self.d.phase==1 and self.h.v.v.k.phase==2 and qmatch:
   assert s.known[0]&2;self.qreads+=1;self.last_q=(self.d.lane,self.h.s.count,q-(1<<20) if q>>19 else q)
  old=r+(int(start and not qload)<<1)+(load<<2)+(x&248)+(int(qmatch)<<8)+(q<<9)+(address<<29)+(word<<34)+(take<<310)
  y,mask=super().tick(old);y=(y&~(1<<27))+(int(bool(y>>27&1) and qmatch)<<27)+(int(ready)<<39)
  mask=(mask&~(511<<30))|((511<<30) if y>>27&1 else 0)|(1<<39);return y,mask


def golden():
 prior.OUT=OUT/'kv';prior.OUT.mkdir(parents=True,exist_ok=True);vg,sg,dg,pc=prior.golden()
 cases=[dict(c,q_words=[v&1048575 for v in c['q']]) for c in pc['cases']]
 report=dict(cases=cases,kv_cases_sha256=sha((prior.OUT/'cases.json').read_bytes()),scope='same real C Q/K/V operands; Q now loaded as32 exact scalar words into packed42/20 bank')
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return vg,sg,dg,report


def vectors(vg,sg,dg,cases):
 text=inspect.getsource(schedule.vectors)
 def replace(a,b):
  nonlocal text
  assert text.count(a)==1,a;text=text.replace(a,b)
 replace('assert len(rows)-before<22','assert len(rows)-before<33')
 replace(' for a in (5,16,31):\n  for _ in range(21):assert not tick(start=1,load=1,qload=1,address=a,word=(1<<276)-1)>>39&1\n','')
 replace('q_words=[packed>>(128*j)&((1<<128)-1) for j in range(5)]','q_words=[v&1048575 for v in q]')
 replace('same640 FF bits; invalidQ addresses;7reset/reload phases; no Q input during HEAD','32packed s42/s20 rows;all32Q addresses;7reset/reload phases; no Q input during HEAD')
 env=dict(schedule.__dict__,Model=Model);exec(compile(text,'<packed attention schedule>','exec'),env)
 return env['vectors'](vg,sg,dg,cases)


def reference():
 s=prior.reference().replace('module top(', 'module kv_head_ref(',1)+store.reference().replace('module top(', 'module packed_store_ref(',1)
 s+='''module top(input [12379:0] din,output [12128:0] dout);
wire [12088:0] old=din[12088:0];wire [290:0] pins=din[12379:12089];wire [2039:0] fs=old[11165:9126];
wire [12815:0] vs={old[12088:11166],fs[2039:2038],1'b0,fs[2037:2033],fs[2032:2031],fs[2030:2026],{{22{fs[2025]}},fs[2025:1984]},2688'd0,old[9125:0]};
wire reset=pins[0],start=pins[1],load=pins[2],qload=pins[8],read=pins[290];wire [4:0] address=pins[13:9];
wire [2:0] parent=vs[12697:12695];wire idle=parent==0 || parent==7,qmode=idle && load && qload;
wire [4:0] qa=qmode ? address : vs[12807:12803];wire qvalid=qmode && start;
wire [15:0] cs=vs[11945:11930];wire [3:0] phase=cs[3:0];wire [1:0] mode=cs[5:4];
wire available=!reset && fs[2032:2031]==2 && fs[2039:2038]!=1;
wire write=!reset && (phase==3 || (phase==7 && cs[15:11]==17));
wire get=!reset && phase==5,home=!reset && phase==8;
wire consume=!reset && phase==6 && available && (mode==1 || (mode==2 && vs[12079:12078]==2 && parent==5 && read));
wire signed [41:0] product={{5{vs[11929]}},vs[11929:11893]};
wire [41:0] value=mode==0 ? 42'd0 : fs[2025:1984]+product;
wire [78:0] fpins={pins[33:14],qa,qvalid,consume,1'b1,value,home,write,cs[10:6],write || get || home,reset};
wire [2128:0] fres;packed_store_ref packed_state({fpins,fs},fres);
wire legacy_start=start && !qload;wire [12854:0] full;
kv_head_ref previous({pins[290],pins[289:14],address,fres[2126:2107],fres[2128],pins[7:2],legacy_start,reset,vs},full);
assign dout[12088:0]={full[12815:11893],fres[2039:0],full[9125:0]};
assign dout[12128:12089]={qmode && fres[2127],full[12854:12844],fres[2128] && full[12843],full[12842:12816]};
endmodule
'''
 names=re.findall(r'module (\w+)\(',s);assert len(names)==len(set(names)),names;return s


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();vg,sg,dg,cases=golden();rows,expected=vectors(vg,sg,dg,cases);net,comb,parts=make()
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small actions and full packed-head C schedule pass; full gates pending cloud',metrics=metrics(net),parts=parts,small=sm,expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,scope='one preloaded attention head with frozen s42 bound and packed Q/V; no whole-model control/shared resource integration')
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
  report['verification']['formal_scope']='allD/output for packed42/20 memory and independent head composition with fixed arithmetic; observed s42/ownership bounds,not unbounded full-model equivalence'
  report['status']='full CEC and actual NAND/RTL/C packed head pass with real faults';(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print(expected['clocks'],expected['head_counts'],expected['q_writes'],expected['q_reads'])


if __name__=='__main__':main()
