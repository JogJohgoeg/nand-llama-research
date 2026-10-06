#!/usr/bin/env python3
"""Store Q in the640 unused FF bits, alongside the2048 V accumulator bits.

Five128-bit Q words use FF logical rows16..20. A ready handshake writes a
word directly when its physical row circulates past the port. A144-bit
front window supplies each20-bit Q scalar, including cross-row values.
No Q register, validity bitmap or captured write buffer is added.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,re
R=Path(os.environ.get('H3_LOCAL_Q_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_kv as prior
import attention_stream as head
import value_row as base
import ff_continuous as ff
from prefix_store import select
from nand import Builder,metrics,with_state,simulate,flip_output
from export import import_net,rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_LOCAL_Q_OUT',str(R/'build/integer_opt/attention_local_q')))
NS,NI,NO=12816,291,40


def port(b,front,tail,word,cursor,lane,flags,address,compact=True):
 reset,start,load,qload,idle=flags
 def eq(xs,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(xs)],b.land,1)
 def match(xs,ys):return b.reduce([b.inv(b.xor(x,y)) for x,y in zip(xs,ys)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 # Divide20*lane by128: nibble position is5*lane, with5 low selector bits.
 pos=b.add(lane+[0]*3,[0]*2+lane+[0])[0]
 target=pos[5:8]+[0,1];qmatch=match(cursor,target)
 if compact:
  # Shift the largest distance first and retain only the still-needed window.
  value=front
  for bit in range(4,-1,-1):
   shift=4<<bit;value=[b.mux(pos[bit],value[j],value[j+shift]) for j in range(shift+16)]
  assert len(value)==20
 else:value=select(b,[front[4*j:4*j+20] for j in range(32)],pos[:5])
 legal=b.reduce([eq(address,j) for j in range(5)],b.lor,0)
 ready=AND(b.inv(reset),idle,load,qload,legal,match(cursor,address[:3]+[0,1]))
 store=AND(ready,start);next_tail=[b.mux(store,x,y) for x,y in zip(tail,word)]
 legacy_start=AND(start,b.inv(qload))
 return value+[qmatch,ready,store,legacy_start]+next_tail


def small():
 ni=144+128+128+5+5+5+5;b=Builder(ni);p=list(range(2,2+ni))
 net=b.finish(port(b,p[:144],p[144:272],p[272:400],p[400:405],p[405:410],p[410:415],p[415:420]));xs=[];ys=[];rng=random.Random(260767)
 bb=Builder(ni);baseline=bb.finish(port(bb,p[:144],p[144:272],p[272:400],p[400:405],p[405:410],p[410:415],p[415:420],False))
 assert metrics(net)['nNand']<=4000
 for i in range(4096):
  front=rng.getrandbits(144);tail=rng.getrandbits(128);word=rng.getrandbits(128);lane=i%32;flags=i//32%32;address=(i//1024*8+lane//4)%32
  cursor=(16+address) if i%3==0 and address<5 else (16+(20*lane//128)) if i%3==1 else rng.randrange(32)
  r,start,load,qload,idle=[flags>>j&1 for j in range(5)]
  ready=not r and idle and load and qload and address<5 and cursor==16+address;store=ready and start
  value=front>>(20*lane%128)&1048575;qmatch=cursor==16+20*lane//128
  x=front+(tail<<144)+(word<<272)+(cursor<<400)+(lane<<405)+(flags<<410)+(address<<415)
  y=value+(int(qmatch)<<20)+(int(ready)<<21)+(int(store)<<22)+(int(start and not qload)<<23)+((word if store else tail)<<24)
  xs.append(x);ys.append(y)
 assert simulate(net,xs)==ys and simulate(baseline,xs)==ys
 wrong=sum(a!=b for a,b in zip(simulate(flip_output(net),xs),ys));assert wrong==len(xs)
 return dict(metrics(net),baseline=metrics(baseline),vectors=len(xs),mismatches=0,actual_gate_fault_mismatches=wrong)


def make(compact=True):
 pn=prior.make()[0];assert pn.n_state==NS;b=Builder(NS+NI);old=list(range(2,2+NS));pins=list(range(2+NS,2+NS+NI))
 reset,start,load=pins[:3];qload=pins[8];address=pins[9:14];word=pins[14:290];take=pins[290]
 phase=old[12695:12698];idle=b.lor(b.inv(b.reduce(phase,b.lor,0)),b.reduce(phase,b.land,1))
 # Preview the current tail; the original FF write result is connected below.
 a=port(b,old[9126:9270],[0]*128,word[:128],old[11878:11883],old[12803:12808],
        [reset,start,load,qload,idle],address,compact)
 q=a[:20];qmatch,ready,store,legacy_start=a[20:24]
 pd,po=import_net(b,pn,[reset,legacy_start,load]+pins[3:8]+[qmatch]+q+address+word+[take],old)
 tail=[b.mux(store,x,y) for x,y in zip(pd[11686:11814],word[:128])]
 ds=pd[:11686]+tail+pd[11814:];outputs=po[:27]+[b.land(qmatch,po[27])]+po[28:]+[ready]
 comb=b.finish(ds+outputs);net=with_state(comb,NS)
 return net,comb,dict(previous=metrics(pn),new_state_bits=0,q_bits_reused=640,v_accumulator_bits=2048,
  reuse='FF rows0..15=32x64-bit V sums; FF rows16..20=32x20-bit Q; one144-bit front tap spans20-bit row crossings',
  protocol='QLOAD=start+load+qload,address0..4,low128 word bits held until ready; normal LOAD fills KV; HEAD autonomous once Q/K/V preloaded',
  scope='one preloaded attention head; Q/K/V production,model-wide ownership and arithmetic sharing outside')


class QFF(ff.Model):
 def __init__(self):super().__init__(21);self.qwrite=None;self.qwrites=self.conflicts=0
 def tick(self,x):
  write=self.qwrite
  if write is not None:
   clash=self.phase==1 and self.mode==1 and self.cursor==self.address//2 and x>>74&1
   self.conflicts+=int(clash);assert not clash,'Q and V simultaneously write FF row'
  result=super().tick(x)
  if write is not None:self.memory[-1]=write;self.known[-1]=3;self.qwrites+=1
  return result


class Model(prior.Model):
 def __init__(self,vg,sg,dg):
  super().__init__(vg,sg,dg);self.h.v.v.f=QFF();self.qreads=0;self.last_q=None
 def tick(self,x):
  reset=x&1;start=x>>1&1;load=x>>2&1;qload=x>>8&1;address=x>>9&31;word=x>>14&((1<<276)-1);take=x>>290&1
  f=self.h.v.v.f;lane=self.d.lane;bitpos=20*lane;qmatch=f.cursor==16+bitpos//128
  q=(f.memory[0]+(f.memory[1]<<128))>>(bitpos%128)&1048575
  ready=not reset and self.h.phase in (0,7) and load and qload and address<5 and f.cursor==16+address
  f.qwrite=(word&((1<<128)-1)) if ready and start else None
  if not reset and self.d.phase==1 and self.h.v.v.k.phase==2 and qmatch:
   assert f.known[0]==3 and (bitpos%128<=108 or f.known[1]==3)
   self.qreads+=1;self.last_q=(lane,self.h.s.count,q-(1<<20) if q>>19 else q)
  old=reset+(int(start and not qload)<<1)+(load<<2)+(x&248)+(int(qmatch)<<8)+(q<<9)+(address<<29)+(word<<34)+(take<<310)
  y,mask=super().tick(old);y=(y&~(1<<27))+(int(bool(y>>27&1) and qmatch)<<27)+(int(ready)<<39)
  mask=(mask&~(511<<30))|((511<<30) if y>>27&1 else 0)|(1<<39)
  return y,mask


def golden():
 prior.OUT=OUT/'kv';prior.OUT.mkdir(parents=True,exist_ok=True);vg,sg,dg,c=prior.golden()
 cases=[]
 for v in c['cases']:
  packed=sum((q&1048575)<<(20*i) for i,q in enumerate(v['q']));words=[packed>>(128*j)&((1<<128)-1) for j in range(5)]
  assert [sum(w<<(128*j) for j,w in enumerate(words))>>(20*i)&1048575 for i in range(32)]==[q&1048575 for q in v['q']]
  cases.append(dict(**v,q_words=words))
 report=dict(cases=cases,kv_cases_sha256=sha((prior.OUT/'cases.json').read_bytes()),scope='same five real C heads; exact20-bit Q concatenation into640 unused FF bits, no rounding or padding')
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return vg,sg,dg,report


def vectors(vg,sg,dg,cases):
 m=Model(vg,sg,dg);rng=random.Random(260768);rows=[];calls=[]
 def tick(reset=0,start=0,load=0,n=0,qload=0,address=0,word=0,take=0):
  x=reset+(start<<1)+(load<<2)+(n<<3)+(qload<<8)+(address<<9)+(word<<14)+(take<<290)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def preload(c):
  for j,word in enumerate(c['words']+c['key_words']):
   assert m.h.phase in (0,7);tick(start=1,load=1,address=j,word=word);before=len(rows)
   while m.h.phase!=7:
    tick(start=1,load=rng.randrange(2),n=rng.randrange(32),address=rng.randrange(32),word=rng.getrandbits(276),take=1)
    assert len(rows)-before<100
  for j,word in enumerate(c['q_words']):
   before=len(rows)
   while True:
    y=tick(start=1,load=1,qload=1,address=j,word=word)
    if y>>39&1:break
    assert len(rows)-before<22
 def command(c,n,abort=None,stall=True):
  before=len(rows);tick(start=1,n=n);values=[];pairs=0
  for _ in range(65000):
   if abort and abort(m):tick(reset=1,start=1,load=1,qload=1,n=31,take=1);return False
   take=int(not stall or rng.randrange(4)!=0)
   y=tick(start=1,load=rng.randrange(2),qload=rng.randrange(2),n=rng.randrange(32),address=rng.randrange(32),word=rng.getrandbits(276),take=take)
   if y>>27&1:
    assert y>>30&31==pairs%32 and y>>35&15==pairs//32
    assert m.last_q==(pairs%32,pairs//32,c['q'][pairs%32]);pairs+=1
   if y>>25&1 and take:
    assert y>>20&31==len(values);v=y&1048575;values.append(v-(1<<20) if v>>19 else v)
   if m.h.phase==7:break
  else:raise AssertionError('stored Q head timeout')
  assert pairs==32*n
  weights=[int(sg.score_exp(max(c['scores'][:n]),s)) for s in c['scores'][:n]];wanted=[]
  for lane in range(32):
   acc=0
   for w,word in zip(weights,c['words']):
    code=word>>(8*lane)&255;acc=vg.value_acc(acc,code-256 if code>=128 else code,word>>256,w)
   wanted.append(int(vg.slice_sat(vg.int_rne(acc,sum(weights)))))
  assert values==wanted and m.h.v.den==sum(weights)
  calls.append(dict(length=n,clocks=len(rows)-before,pairs=pairs,values=values,stall=stall));tick();return True
 tick(reset=1)
 for a in (5,16,31):
  for _ in range(21):assert not tick(start=1,load=1,qload=1,address=a,word=(1<<276)-1)>>39&1
 for n in (0,17,31):tick(start=1,n=n,take=1);assert m.h.phase==0
 for c in cases['cases']:
  preload(c);command(c,1);command(c,16,stall=False)
 c=cases['cases'][0]
 predicates=[lambda m:m.h.s.phase==1 and m.h.v.v.k.phase==1,
             lambda m:m.d.phase==1 and m.h.v.v.k.phase==2 and m.h.v.v.f.cursor<16,
             lambda m:m.d.phase==2 and m.d.count==19,
             lambda m:m.d.done,
             lambda m:m.h.s.count==3 and m.d.phase==2,
             lambda m:m.h.phase in (3,4) and m.h.v.v.control&15==7,
             lambda m:m.h.phase==5 and m.h.v.control&3==2]
 for pred in predicates:
  tick(reset=1);preload(c);assert not command(c,16,abort=pred)
  preload(c);command(c,3)
 # One arithmetic stress head exercises every stored Q bit and K saturation.
 q=[-524288,524287,-1,0]+[(-1 if i&1 else 1)*(1<<(i%19)) for i in range(28)]
 keys=[];words=[]
 for j in range(16):
  codes=[-128 if (i+j)&1 else 127 for i in range(32)];words.append(sum((x&255)<<(8*i) for i,x in enumerate(codes))+(1048575<<256));keys.append([int(vg.value_dequant(x,1048575)) for x in codes])
 scores=[int(dg.qk_score(sum(x*y for x,y in zip(q,k)))) for k in keys];packed=sum((x&1048575)<<(20*i) for i,x in enumerate(q))
 stress=dict(c,q=q,keys=keys,key_words=words,scores=scores,q_words=[packed>>(128*j)&((1<<128)-1) for j in range(5)])
 preload(stress);command(stress,16)
 k=m.h.v.v.k;f=m.h.v.v.f;assert not k.conflicts and not f.conflicts
 return rows,dict(clocks=len(rows),counts=m.counts,head_counts=m.h.counts,dot_counts=m.d.counts,kv_counts=k.counts,ff_counts=f.counts,
  kv_ownership_conflicts=k.conflicts,q_write_conflicts=f.conflicts,q_writes=f.qwrites,q_reads=m.qreads,calls=calls,
  scope='five true heads plus exact arithmetic extrema head; same640 FF bits; invalidQ addresses;7reset/reload phases; no Q input during HEAD')


def reference():
 s=prior.reference().replace('module top(', 'module kv_head_ref(',1)
 s+='''module top(input [13106:0] din,output [12855:0] dout);
wire [12815:0] old=din[12815:0];wire [290:0] pins=din[13106:12816];
wire reset=pins[0],start=pins[1],load=pins[2],qload=pins[8];wire [4:0] address=pins[13:9];
wire [2:0] phase=old[12697:12695];wire idle=phase==0 || phase==7;
wire [4:0] cursor=old[11882:11878],lane=old[12807:12803];
wire [9:0] position={5'd0,lane}*10'd20;
wire [4:0] target=5'd16+{2'd0,position[9:7]};wire qmatch=cursor==target;
wire [143:0] front=old[9269:9126];wire [19:0] q=front[position[6:0] +:20];
wire ready=!reset && idle && load && qload && address<5 && cursor==5'd16+address;
wire store=ready && start,legacy_start=start && !qload;
wire [12854:0] full;kv_head_ref core({pins[290],pins[289:14],address,q,qmatch,pins[7:2],legacy_start,reset,old},full);
wire [127:0] tail=store ? pins[141:14] : full[11813:11686];
assign dout[12815:0]={full[12815:11814],tail,full[11685:0]};
assign dout[12855:12816]={ready,full[12854:12844],qmatch && full[12843],full[12842:12816]};
endmodule
'''
 names=re.findall(r'module (\w+)\(',s);assert len(names)==len(set(names)),names
 return s


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();vg,sg,dg,cases=golden();rows,expected=vectors(vg,sg,dg,cases);net,comb,parts=make()
 parts['wide_mux_baseline']=metrics(make(False)[0])
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small Q port and complete preloaded C heads pass; full source prepared only',metrics=metrics(net),parts=parts,small=sm,
  expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,scope='one head with preloaded Q/K/V, no scalar Q request; producers/global ownership and shared arithmetic outside')
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
  report['verification']['formal_scope']='all D/output independent composition with FF shared Q port and pinned score/EXP/DIV/dequantizer; C clocks check Q/V memory ownership,not unbounded model reachability'
  report['status']='full CEC and NAND/RTL/C stored-Q head pass with actual faults';(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print(expected['clocks'],expected['head_counts'],expected['q_writes'],expected['q_reads'])


if __name__=='__main__':main()
