#!/usr/bin/env python3
"""A single physical KV bank/client/dequantizer for the QK and V phases.

The first16 addresses hold V; the last16 hold K. The old complete head is
imported once; its KV D cone is replaced by one input-arbitrated client.
No full-bank D mux and no second bank/cache/dequantizer is retained.
Q values remain an explicit handshaken scalar interface.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,re,ctypes as ct,subprocess
R=Path(os.environ.get('H3_ATTENTION_KV_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_pairs as prior
import attention_stream as head
import value_row as base
import kv_client as kv
from ring_model import load,forward
from nand import Builder,metrics,with_state,simulate,flip_output
from export import import_net,rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_ATTENTION_KV_OUT',str(R/'build/integer_opt/attention_kv')))
NS,KS,NI,NO=12816,9126,311,39


def arbitrate(b,pins):
 owner,begin,ready,available,incoming,vs,vw,vt=pins[:8];history=pins[8:12];address=pins[12:17]
 accepted=b.reduce([ready,available,incoming],b.land,1)
 start=b.mux(owner,vs,begin);write=b.land(b.inv(owner),vw)
 take=b.mux(owner,vt,accepted)
 addr=[b.mux(owner,x,y) for x,y in zip(address,history+[1])]
 return [start,write]+addr+[take,b.land(available,incoming),b.land(ready,available)]


def small():
 b=Builder(17);net=b.finish(arbitrate(b,list(range(2,19))));xs=[];ys=[]
 # All Boolean modes, all history values, and both V address halves.
 for flags in range(256):
  for history in range(16):
   for high in (0,1):
    address=((history*7+3)&15)+(high<<4);x=flags+(history<<8)+(address<<12)
    owner,begin,ready,available,incoming,vs,vw,vt=[flags>>i&1 for i in range(8)]
    start=begin if owner else vs;write=0 if owner else vw;take=ready and available and incoming if owner else vt
    addr=16+history if owner else address;din=available and incoming;qready=ready and available
    xs.append(x);ys.append(start+(write<<1)+(addr<<2)+(int(take)<<7)+(int(din)<<8)+(int(qready)<<9))
 assert metrics(net)['nNand']<=4000 and simulate(net,xs)==ys
 wrong=sum(a!=b for a,b in zip(simulate(flip_output(net),xs),ys));assert wrong==len(xs)
 return dict(metrics(net),vectors=len(xs),mismatches=0,actual_gate_fault_mismatches=wrong)


def value_pins(b,old,pins):
 """Original V-side KV requests, before sharing the one bank input port."""
 reset,start,load=pins[:3];n=pins[3:8];address=pins[29:34]
 ss=old[12134:12695];phase=old[12695:12698];cs=old[11930:11946]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);valid=AND(keep,eq(ss[558:560],3));index=ss[554:558]
 last=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(b.add(index+[0],[0]*5,1)[0],ss[544:549])],b.land,1)
 busy=AND(keep,b.inv(b.lor(eq(cs[:4],0),eq(cs[:4],15))));done=AND(keep,eq(cs[:4],15))
 _,acts=head.control(b,phase,[reset,start,load]+n+[valid,last,busy,done])
 _,_,vstart,cmd0,cmd1,accept,_,_,_=acts
 _,allowed=head.value.denominator(b,old[12107:12134],[reset,vstart,cmd0,cmd1,busy]+[0]*17)
 begin=AND(keep,allowed,b.lor(eq(cs[:4],0),eq(cs[:4],15)))
 kstart=AND(begin,cmd0);write=AND(cmd0,cmd1)
 take=AND(keep,eq(cs[:4],4),eq(old[11883:11885],3),eq(cs[4:6],1))
 addr=[b.mux(accept,x,y) for x,y in zip(address,index+[0])]
 return [kstart,write,take]+addr


def make():
 pn=prior.make()[0];kn=kv.make()[0];assert pn.n_state==NS and kn.n_state==KS
 b=Builder(NS+NI);old=list(range(2,2+NS));pins=list(range(2+NS,2+NS+NI))
 reset,start,load=pins[:3];incoming=pins[8];q=pins[9:29];address=pins[29:34];word=pins[34:310];take=pins[310]
 ds=old[12698:];ss=old[12134:12695]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 keep=b.inv(reset);begin,owner=prior.connect(b,reset,ss[558:560],ds[115:117],ds[117])
 ready=b.land(keep,eq(ds[115:117],1));available=b.land(keep,eq(old[9113:9115],2))
 vs,vw,vt,*va=value_pins(b,old,pins)
 a=arbitrate(b,[owner,begin,ready,available,incoming,vs,vw,vt]+ss[549:553]+va)
 kstart,write=a[:2];addr=a[2:7];ktake,din,qready=a[7:]
 kd,ko=import_net(b,kn,[reset,kstart,write]+addr+word+[ktake],old[:KS])
 pd,po=import_net(b,pn,pins[:8]+[din]+q+ko[:20]+address+word+[take],old)
 outputs=po[:27]+[qready]+po[28:]
 comb=b.finish(kd+pd[KS:]+outputs);net=with_state(comb,NS)
 return net,comb,dict(previous=metrics(pn),shared_kv=metrics(kn),new_state_bits=0,
  reuse='one32x276 bank,captured276 bits,dequantizer and KV control; V addresses0..15,K16..31; only KV input controls muxed',
  state_order='identical R64; first9126 bits belong to sole shared KV client',
  scope='scalar Q storage still external; scores and normalized V internal; whole-model shared arithmetic/control unimplemented')


class SharedKV(kv.Model):
 def __init__(self,dequant):super().__init__(32,32,dequant,True);self.override=None;self.owner_clocks=0;self.conflicts=0
 def tick(self,x):
  if self.override is not None:
   self.owner_clocks+=1;self.conflicts+=int(bool(x&2 or x>>284&1))
   assert not (x&2 or x>>284&1),'V touched shared bank during QK phase'
   x=self.override
  return super().tick(x)


class Model(prior.Model):
 def __init__(self,vg,sg,dg):
  super().__init__(vg,sg,dg);self.h.v.v.k=SharedKV(vg.value_dequant);self.held_q=0
 def tick(self,x):
  reset=x&1;incoming=x>>8&1;q=x>>9&1048575;address=x>>29&31;word=x>>34&((1<<276)-1);take=x>>310&1
  k=self.h.v.v.k;owner=not reset and self.h.s.phase==1;begin=owner and self.d.phase==0 and not self.d.done
  ready=not reset and self.d.phase==1;available=not reset and k.phase==2;accept=ready and available and incoming
  if accept:assert k.index==self.d.lane and k.address==16+self.h.s.count
  code=k.cache>>(8*k.index)&255;value=k.dequant(code-256 if code>=128 else code,k.cache>>256)
  k.override=reset+(int(begin)<<1)+((16+(self.h.s.count&15))<<3)+(int(accept)<<284) if owner else None
  old=(x&255)+(int(available and incoming)<<8)+(q<<9)+((value&1048575)<<29)+(address<<49)+(word<<54)+(take<<330)
  y,mask=super().tick(old);qready=int(ready and available)
  y=(y&~(1<<27))+(qready<<27);mask=(mask&~(511<<30))|((511<<30) if qready else 0)
  return y,mask


def golden():
 prior.OUT=OUT/'pairs';prior.OUT.mkdir(parents=True,exist_ok=True);vg,sg,dg,pc=prior.golden()
 source=(R/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 helpers='''static unsigned saved_heads;
static int8_t saved_k[5][16][32];static int32_t saved_m[5][16];
unsigned saved_count(void){return saved_heads;}
int32_t saved_field(unsigned h,unsigned s,unsigned i){return i<32?saved_k[h][s][i]:saved_m[h][s];}
'''
 marker='static void attention(';assert source.count(marker)==1;source=source.replace(marker,helpers+'\n'+marker)
 marker='for(int s=0;s<=pos;s++){w[s]=exp_weight((int64_t)maximum-scores[s]);denominator+=w[s];}'
 hook='''
        if(pos==15 && h==0 && saved_heads<5){
            for(int s=0;s<16;s++){
                saved_m[saved_heads][s]=km[s][h];
                for(int i=0;i<32;i++)saved_k[saved_heads][s][i]=keys[s][h][i];
            }
            saved_heads++;
        }'''
 assert source.count(marker)==1;source=source.replace(marker,marker+hook);p=OUT/'keys.c';p.write_text(source);so=OUT/'keys.so'
 subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(so)],check=True,timeout=30)
 blob=(R/'physical/model.bin').read_bytes();g=load(so,blob);g.saved_field.argtypes=[ct.c_uint]*3;g.saved_field.restype=ct.c_int32
 payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1]);fixture=next(f for f in payload['fixtures'] if f['name']=='random16');actual=forward(g,fixture['ids']);assert actual['logits']==fixture['logit_sha256'] and actual['trace']==fixture['trace_sha256'];assert g.saved_count()==5
 cases=[]
 for h,c in enumerate(pc['cases']):
  words=[]
  for s in range(16):
   codes=[g.saved_field(h,s,i) for i in range(32)];maximum=g.saved_field(h,s,32)
   assert [vg.value_dequant(code,maximum) for code in codes]==c['keys'][s]
   words.append(sum((code&255)<<(i*8) for i,code in enumerate(codes))+(maximum<<256))
  cases.append(dict(**c,key_words=words))
 report=dict(cases=cases,pair_cases_sha256=sha((prior.OUT/'cases.json').read_bytes()),fixture=fixture,actual=actual,
  raw_key_word_le_sha256=sha(b''.join(w.to_bytes(35,'little') for c in cases for w in c['key_words'])),
  scope='fourth independent frozen-C observation adds actual K8 codes/maxima; dequantized keys match every prior QK operand')
 (OUT/'cases.json').write_text(json.dumps(report,indent=2)+'\n');return vg,sg,dg,report


def vectors(vg,sg,dg,cases):
 m=Model(vg,sg,dg);rng=random.Random(260766);rows=[];calls=[]
 def tick(reset=0,start=0,load=0,n=0,incoming=0,q=0,address=0,word=0,take=0):
  x=reset+(start<<1)+(load<<2)+(n<<3)+(incoming<<8)+((q&1048575)<<9)+(address<<29)+(word<<34)+(take<<310)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def preload(c):
  for j,word in enumerate(c['words']+c['key_words']):
   assert m.h.phase in (0,7);tick(start=1,load=1,address=j,word=word);before=len(rows)
   while m.h.phase!=7:
    tick(start=1,load=rng.randrange(2),n=rng.randrange(32),incoming=1,q=rng.randrange(1048576),address=rng.randrange(32),word=rng.getrandbits(276),take=1)
    assert len(rows)-before<100
 def command(c,n,abort=None,stall=True):
  before=len(rows);tick(start=1,n=n);values=[];pairs=0
  for _ in range(60000):
   if abort and abort(m):tick(reset=1,start=1,load=1,n=31,incoming=1,q=-524288,take=1);return False
   incoming=int(not stall or rng.randrange(4)!=0);take=int(not stall or rng.randrange(4)!=0)
   q=c['q'][m.d.lane] if m.d.phase==1 else rng.randrange(1048576)
   y=tick(start=1,load=rng.randrange(2),n=rng.randrange(32),incoming=incoming,q=q,address=rng.randrange(32),word=rng.getrandbits(276),take=take)
   if y>>27&1 and incoming:
    assert y>>30&31==pairs%32 and y>>35&15==pairs//32;pairs+=1
   if y>>25&1 and take:
    assert y>>20&31==len(values);v=y&1048575;values.append(v-(1<<20) if v>>19 else v)
   if m.h.phase==7:break
  else:raise AssertionError('shared KV head timeout')
  assert pairs==32*n
  weights=[int(sg.score_exp(max(c['scores'][:n]),s)) for s in c['scores'][:n]];wanted=[]
  for lane in range(32):
   acc=0
   for w,word in zip(weights,c['words']):
    code=word>>(8*lane)&255;acc=vg.value_acc(acc,code-256 if code>=128 else code,word>>256,w)
   wanted.append(int(vg.slice_sat(vg.int_rne(acc,sum(weights)))))
  assert values==wanted and m.h.v.den==sum(weights) and m.h.v.row_count==n
  calls.append(dict(length=n,clocks=len(rows)-before,pairs=pairs,values=values,stall=stall));tick();return True
 tick(reset=1)
 for n in (0,17,31):tick(start=1,n=n,incoming=1,take=1);assert m.h.phase==0
 for c in cases['cases']:
  preload(c);command(c,1);command(c,16,stall=False)
 c=cases['cases'][0]
 predicates=[lambda m:m.h.s.phase==1 and m.h.v.v.k.phase==1,
             lambda m:m.d.phase==1 and m.h.v.v.k.phase==2 and m.d.lane==0,
             lambda m:m.d.phase==2 and m.d.count==19,
             lambda m:m.d.done,
             lambda m:m.h.s.count==3 and m.d.phase==2,
             lambda m:m.h.phase in (3,4) and m.h.v.v.control&15==7,
             lambda m:m.h.phase==5 and m.h.v.control&3==2]
 for pred in predicates:
  tick(reset=1);preload(c);assert not command(c,16,abort=pred)
  preload(c);command(c,3)
 k=m.h.v.v.k;assert not k.conflicts
 return rows,dict(clocks=len(rows),counts=m.counts,head_counts=m.h.counts,dot_counts=m.d.counts,kv_counts=k.counts,
  owner_clocks=k.owner_clocks,ownership_conflicts=k.conflicts,calls=calls,
  scope='five true C16 heads with actual K8/V8 words,all32 bank addresses,scalar Q stalls,seven abort/reload stages')


def reference():
 s=prior.reference().replace('module top(', 'module pair_ref(',1)
 s+='''module top(input [13126:0] din,output [12854:0] dout);
wire [12815:0] old=din[12815:0];wire [310:0] pins=din[13126:12816];
wire reset=pins[0],start=pins[1],load=pins[2];wire [4:0] n=pins[7:3];
wire [560:0] ss=old[12694:12134];wire [2:0] phase=old[12697:12695];wire [117:0] ds=old[12815:12698];
wire [15:0] vc=old[11945:11930];wire owner=!reset && ss[559:558]==1;
wire begin_dot=owner && ds[116:115]==0 && !ds[117];
wire dot_ready=!reset && ds[116:115]==1,available=!reset && old[9114:9113]==2;
wire incoming=pins[8] && available,qready=dot_ready && available;
wire accepted=qready && pins[8];
wire begin_head=!reset && (phase==0 || phase==7) && start && (load || (n>=1 && n<=16));
wire vbusy=!reset && vc[3:0]!=0 && vc[3:0]!=15,vdone=!reset && vc[3:0]==15;
wire accept_weight=!reset && phase==2 && ss[559:558]==3 && !vbusy;
wire read_values=!reset && phase==4 && vdone;
wire vstart=begin_head || accept_weight || read_values;
wire [1:0] command={begin_head && load || read_values,begin_head && load || accept_weight};
wire allowed=!reset && vstart && (command!=1 || old[12133:12129]<16) && (command!=2 || old[12128:12107]!=0);
wire vkstart=allowed && (vc[3:0]==0 || vc[3:0]==15) && (command==1 || command==3);
wire vwrite=command==3;wire vtake=!reset && vc[3:0]==4 && old[11884:11883]==3 && vc[5:4]==1;
wire [4:0] vaddress=accept_weight ? {1'b0,ss[557:554]} : pins[33:29];
wire kstart=owner ? begin_dot : vkstart,kread=owner ? accepted : vtake,kwrite=!owner && vwrite;
wire [4:0] kaddress=owner ? {1'b1,ss[552:549]} : vaddress;
wire [9162:0] kres;kv_ref shared_bank({kread,pins[309:34],kaddress,kwrite,kstart,reset,old[9125:0]},kres);
wire [19:0] kval;deq_ref shared_dequant(kres[9153:9126],kval);
wire [12854:0] full;pair_ref remainder({pins[310],pins[309:34],pins[33:29],kval,pins[28:9],incoming,pins[7:0],old},full);
assign dout[12815:0]={full[12815:9126],kres[9125:0]};
assign dout[12854:12816]={full[12854:12844],qready,full[12842:12816]};
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
 report=dict(status='small arbitration and full C numerical schedule pass; full source prepared only',metrics=metrics(net),parts=parts,small=sm,
  expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,scope='one real K/V bank with shared dequantizer; scalar Q storage and whole-model resource/control integration remain external')
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
  report['verification']['formal_scope']='all D/output independent composition with one arbitrated bank and pinned score/EXP/DIV/dequantizer; observed ownership asserted each C clock,not unbounded model reachability'
  report['status']='full CEC and NAND/RTL/C shared KV head pass with actual faults';(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print(expected['clocks'],expected['counts'],expected['head_counts'],expected['kv_counts'])


if __name__=='__main__':main()
