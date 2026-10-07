#!/usr/bin/env python3
"""Replay norm0 twice: max scan then A8 conversion, with no stored raw A20.

The caller holds/replays the same X for both complete norm passes. Numerical
rules are unchanged. More cycles buy a smaller original A slot; full gate
simulation and all large proofs run only in Actions.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse,random,math,shutil
R=Path(os.environ.get('H3_NORM_RECOMPUTE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache_fill as base
import norm_cache_shared as shared
from state_projection import prune,structural_identity,small as small_projection
from nand import Builder,metrics,with_state,flip_output,blif
from export import import_net,rtl
from golden import Netlist
from gate_check import verify
OUT=Path(os.environ.get('H3_NORM_RECOMPUTE_OUT',str(R/'build/integer_opt/norm_cache_recompute')))
sha=lambda b:hashlib.sha256(b).hexdigest()
NI,NO=base.NI,base.NO
_base_make=base.make


def connect(b,s,p,n,q,done,head,cursor,bad_rotation=False):
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 reset,start=p[:2];en=p[23]
 nf=AND(n[53],q[37],en,eq(n[20:27],127));qf=AND(q[38],en,eq(q[8:17],127))
 d,a=base.owner(b,s,p[:2]+p[24:28]+[nf,qf,done])
 begin,first,second_start,second,cstart,busy,complete,manual=a
 running=b.lor(first,second)
 na=AND(en,b.lor(AND(first,q[37]),AND(second,q[38])))
 qa=AND(second,q[38],en);rotate=AND(qa,eq(q[8:13],31))
 if bad_rotation:rotate=0
 ni=[reset,b.lor(begin,second_start)]+p[2:22]+[AND(running,p[22]),na]
 qi=[reset,begin]+[(128>>j)&1 for j in range(9)]+n[:20]+[AND(running,n[53],en),AND(second,en)]
 ci=[reset,cstart,1]+s[3:]+q[17:37]+[0]*641+[en,0]
 wi=[0]*640+q[:8]+[q[7]]*12+q[8:13]+[0,rotate,qa]
 install=AND(second,qf)
 rr=AND(manual,eq([b.xor(x,y) for x,y in zip(p[28:40],cursor)],0))
 public=n[20:27]+[AND(running,n[51]),AND(running,n[52]),busy,complete,rr]
 return d,ni,qi,ci,wi,[install,second],public


def make_unshared(bad_rotation=False,merged=True):
 old=base.connect;base.connect=connect
 try:return _base_make(bad_rotation=bad_rotation,merged=merged)
 finally:base.connect=old


def connector():
 old=base.connect;base.connect=connect
 try:return base.connector()
 finally:base.connect=old


def connector_ref():
 return '''module top(input [795:0] din,output [1415:0] dout);
wire [6:0] s=din[6:0];wire [39:0] p=din[46:7];wire [54:0] n=din[101:47];wire [40:0] q=din[142:102];
wire cd=din[143];wire [11:0] cursor=din[795:784];
wire reset=p[0],start=p[1],en=p[23];wire [2:0] phase=s[2:0];wire [3:0] pos=s[6:3];
wire begin_row=!reset && (phase==0 || phase==6) && start;
wire first=!reset && phase==1,again=!reset && phase==2,second=!reset && phase==3,cs=!reset && phase==4;
wire running=first || second,busy=!reset && phase!=0 && phase!=6,complete=!reset && phase==6 && !start;
wire manual=!reset && (phase==0 || phase==6) && !start;
wire nf=n[53] && q[37] && en && n[26:20]==127;
wire qa=second && q[38] && en,rotate=qa && q[12:8]==31,install=qa && q[16:8]==127;
reg [2:0] np;reg [3:0] posnext;
always @* begin
 np=phase;posnext=pos;
 if(begin_row)begin np=1;posnext=p[27:24];end
 if(first && nf)np=2;
 if(again)np=3;
 if(install)np=4;
 if(cs)np=5;
 if(!reset && phase==5 && cd)np=6;
 if(reset)begin np=0;posnext=0;end
end
wire [23:0] ni={en && ((first && q[37]) || (second && q[38])),running && p[22],p[21:2],begin_row || again,reset};
wire [32:0] qi={second && en,running && n[53] && en,n[19:0],9'd128,begin_row,reset};
wire [669:0] ci={1'b0,en,641'b0,q[36:17],pos,1'b1,cs,reset};
wire [667:0] wi={qa,rotate,1'b0,q[12:8],{12{q[7]}},q[7:0],640'b0};
wire [11:0] pub={manual && p[39:28]==cursor,complete,busy,running && n[52],running && n[51],n[26:20]};
assign dout={pub,second,install,wi,ci,qi,ni,posnext,np};
endmodule
'''


def connector_model(value):
 def take(width):
  nonlocal value
  v=value&((1<<width)-1);value>>=width;return v
 s,p,n,q,cd,head,cursor=[take(w) for w in [7,40,55,41,1,640,12]]
 reset=p&1;start=p>>1&1;en=p>>23&1;phase=s&7
 first=not reset and phase==1;second=not reset and phase==3
 nf=bool(n>>53&1 and q>>37&1 and en and n>>20&127==127)
 qf=bool(q>>38&1 and en and q>>8&511==127)
 d,a=base.owner_step(s,reset+(start<<1)+((p>>24&15)<<2)+(int(nf)<<6)+(int(qf)<<7)+(cd<<8))
 begin,_,again,_,cs,busy,complete,manual=[a>>i&1 for i in range(8)]
 running=first or second;na=en and ((first and q>>37&1) or (second and q>>38&1))
 qa=bool(second and q>>38&1 and en);rotate=qa and q>>8&31==31
 ni=reset+((begin or again)<<1)+((p>>2&1048575)<<2)+(int(running and p>>22&1)<<22)+(int(na)<<23)
 qi=reset+(begin<<1)+(128<<2)+((n&1048575)<<11)+(int(running and n>>53&1 and en)<<31)+(int(second and en)<<32)
 ci=reset+(cs<<1)+(1<<2)+((s>>3)<<3)+((q>>17&1048575)<<7)+(en<<668)
 code=q&255;code=code if code<128 else code-256
 wi=((code&1048575)<<640)+((q>>8&31)<<660)+(int(rotate)<<666)+(int(qa)<<667)
 public=(n>>20&127)+(int(running and n>>51&1)<<7)+(int(running and n>>52&1)<<8)+(busy<<9)+(complete<<10)+(int(manual and p>>28&4095==cursor)<<11)
 result=0;offset=0
 for v,w in [(d,7),(ni,24),(qi,33),(ci,670),(wi,668),(int(second and qf),1),(int(second),1),(public,12)]:result|=v<<offset;offset+=w
 assert offset==1416
 return result


def small_connector(cut):
 assert metrics(cut)['nNand']<=4000
 rng=random.Random(260795);xs=[rng.getrandbits(cut.n_in) for _ in range(1536)]
 return verify(cut,xs,[connector_model(x) for x in xs])


def sharing(net,bad_owner=False):
 ND=base.cache.NS+192;QD=base.cache.NS+512;removed=list(range(QD,QD+115))
 kept=[i for i in range(net.n_state) if i not in removed];index={old:new for new,old in enumerate(kept)}
 b=Builder(len(kept)+NI);old=[2+index[ND+i-QD if i in removed else i] for i in range(net.n_state)]
 pins=list(range(2+len(kept),2+len(kept)+NI));ds,out=import_net(b,net,pins,old)
 ns=old[base.cache.NS:base.cache.NS+512];qs=old[QD:QD+169];owner=old[-7:]
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 second=AND(b.inv(pins[0]),eq(owner[:3],3));running=b.lor(AND(b.inv(pins[0]),eq(owner[:3],1)),second)
 valid=AND(b.inv(pins[0]),eq(ns[488:492],9))
 choose_quant=AND(second,valid)
 nload=eq(ns[488:492],7);nn=[0]*28+ns[:36];nd=ns[451:475]+[0]
 receive=eq(qs[153:156],2);qready=AND(b.inv(pins[0]),b.lor(eq(qs[153:156],1),receive))
 qload=b.land(b.land(qready,AND(running,valid,pins[23])),receive)
 x=ns[492:512];product=b.add([0]*7+x,[b.inv(v) for v in x+[x[-1]]*7],1)[0]
 normpins=[nload]+nn+nd;quantpins=[qload]+[0]*37+product+qs[115:135]+[0]*5
 manifest=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
 raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==manifest['sha256']
 div=Netlist.decode(raw,manifest['nIn'],manifest['nOut']);assert div.n_state==115
 select=b.inv(choose_quant) if bad_owner else choose_quant
 dd,_=import_net(b,div,[b.mux(select,n,q) for n,q in zip(normpins,quantpins)],old[ND:ND+115])
 nd0,_=import_net(b,div,normpins,old[ND:ND+115]);qd0,_=import_net(b,div,quantpins,old[ND:ND+115])
 assert nd0==ds[ND:ND+115], 'actual norm operand D binding'
 assert qd0==ds[QD:QD+115], 'actual quant operand D binding'
 left=b.finish([b.mux(choose_quant,n,q) for n,q in zip(nd0,qd0)]);right=b.finish(dd)
 new=with_state(b.finish([dd[i-ND] if ND<=i<ND+115 else ds[i] for i in kept]+out),len(kept))
 return new,left,right,dict(exact_old_operand_D_binding=True,shared_DIV_bits=115,removed=removed,
  scope='conditional selected115D after state identification; norm holds result while A8 uses DIV; not independent unbounded lifetime theorem')


def make(bad_rotation=False,bad_owner=False):
 before,parts,weights,words=make_unshared(bad_rotation)
 shared_net,dl,dr,binding=sharing(before,bad_owner);new,kept=prune(shared_net)
 return new,shared_net,dl,dr,kept,binding,weights,words


class Model:
 def __init__(self):
  self.cache=base.cache.Model();self.owner=0;self.n=dict(p=0,idx=0,left=0,root=0,result=0,total=0)
  self.q=dict(p=0,idx=0,left=0,maximum=0,result=0);self.case=None
  self.counts=dict(norm_scan=0,norm_replay=0,norm_outputs=0,quant_scan=0,quant_replay=0,quant_writes=0,
   installed=0,completed=0,aborted=0,cache_reads=0,slot_rotations=0,busy_starts=0,second_norm_starts=0)
 def tick(self,p,case=None):
  reset=p&1;start=p>>1&1;x=p>>2&1048575;x=x-1048576 if x>=524288 else x
  xv=p>>22&1;en=p>>23&1;asked=p>>24&15;readaddr=p>>28&4095
  n=self.n;q=self.q;np=n['p'];qp=q['p'];ni=n['idx'];qi=q['idx'];phase=self.owner&7
  first=not reset and phase==1;second=not reset and phase==3;running=first or second
  nv=not reset and np==9;qv=not reset and qp==4;nready=not reset and np in (1,5);qready=not reset and qp in (1,2)
  na=nv and en and (first and qready or second and qv);qa=second and qv and en
  nf=nv and qready and en and ni==127;qf=qv and en and qi==127;cd=not reset and self.cache.ctl&3==2
  od,flags=base.owner_step(self.owner,reset+(start<<1)+(asked<<2)+(int(nf)<<6)+(int(qf)<<7)+(int(cd)<<8))
  begin,first_,again,second_,cs,busy,done,manual=[flags>>j&1 for j in range(8)];assert first==first_ and second==second_
  rr=manual and self.cache.bank.cursor==readaddr
  out=ni+(int(running and nready)<<7)+(int(running and np>=5)<<8)+(busy<<9)+(done<<10)
  out+=(self.cache.bank.memory[0]<<11)+(int(rr)<<19)+((self.cache.ctl>>18)<<20)
  mask=((1<<NO)-1)^((255<<11) if not rr or not self.cache.bank.known[0] else 0)
  if rr and self.cache.bank.known[0]:self.counts['cache_reads']+=1
  rotate=qa and qi%32==31;install=qa and qi==127
  oldwork=[v[:] for v in self.cache.work];oldknown=[v[:] for v in self.cache.known]
  self.cache.tick(reset+(cs<<1)+(1<<2)+((self.owner>>3)<<3)+(q['maximum']<<7)+(en<<668))
  if second:
   self.cache.work=base.cache.stage.transition(oldwork,q['result']&1048575,qi%32,0,rotate,qa,[0]*32)
   self.cache.known=base.cache.stage.transition(oldknown,True,qi%32,0,rotate,qa,[False]*32)
  if install:self.cache.ctl=(self.cache.ctl&~(7<<2))|(4<<2)
  stats=self.counts;stats['slot_rotations']+=int(rotate);stats['installed']+=int(install)
  stats['norm_outputs']+=int(na);stats['quant_writes']+=int(qa);stats['busy_starts']+=int(busy and start)
  stats['completed']+=int(phase==5 and od&7==6 and not reset)
  if reset:
   stats['aborted']+=int(phase not in (0,6));self.n=dict(p=0,idx=0,left=0,root=0,result=0,total=0)
   self.q=dict(p=0,idx=0,left=0,maximum=0,result=0);self.case=None
  else:
   norm_value=n['result']
   if begin or again:
    if begin:assert case is not None;self.case=case
    else:stats['second_norm_starts']+=1
    n.update(p=1,idx=0,left=0,total=0)
   elif np in (1,5) and running and xv:
    assert x==self.case['x'][ni]
    if np==1:n.update(p=2,left=20,total=n['total']+x*x);stats['norm_scan']+=1
    else:n.update(p=6,left=16);stats['norm_replay']+=1
   elif np in (2,4,6,8):
    if n['left']:n['left']-=1
    elif np==2:n.update(p=3 if ni==127 else 1,idx=(ni+1)&127)
    elif np==4:
     assert math.isqrt(2*n['total']+4295)==self.case['root'];n.update(p=5,idx=0,root=self.case['root'])
    elif np==6:n['p']=7
    else:n.update(p=9,result=self.case['h'][ni])
   elif np==3:n.update(p=4,left=24)
   elif np==7:n.update(p=8,left=38)
   elif np==9 and na:n.update(p=0 if ni==127 else 5,idx=(ni+1)&127)
   if begin:q.update(p=1,idx=0,maximum=1)
   elif qp in (1,2) and running and nv and en:
    assert ni==qi and norm_value==self.case['h'][qi]
    if qp==1:
     assert first;stats['quant_scan']+=1;q['maximum']=max(q['maximum'],abs(norm_value))
     if qi==127:assert q['maximum']==self.case['maximum'];q.update(p=2,idx=0)
     else:q['idx']+=1
    else:assert second;stats['quant_replay']+=1;q.update(p=3,left=28)
   elif qp==3:
    assert second and nv and ni==qi, 'norm output must remain held during A8 DIV'
    q['left']-=1
    if q['left']==0:q.update(p=4,result=self.case['q'][qi])
   elif qp==4 and qa:
    if qi==127:q['p']=0
    else:q.update(p=2,idx=qi+1)
   if cs:assert self.cache.ctl&3==1 and sum(oldwork,[])==[v&1048575 for v in self.case['q']]
  self.owner=od
  return out,mask


def vectors(cases):
 m=Model();rows=[];calls=[];stored={}
 def tick(reset=0,start=0,x=0,xv=0,en=1,pos=0,address=4095,case=None):
  p=reset+(start<<1)+((x&1048575)<<2)+(xv<<22)+(en<<23)+(pos<<24)+(address<<28)
  y,mask=m.tick(p,case);rows.append((p,y,mask if len(rows) else 0));return y
 def reset():tick(reset=1,start=1,x=-524288,xv=1);stored.clear();tick()
 def read(pos,want):
  for j,byte in enumerate(want):
   for _ in range(base.cache.bank.SLOTS+1):
    y=tick(address=pos*131+j)
    if y>>19&1:assert y>>11&255==byte;break
   else:raise AssertionError('cache read never ready')
 def transact(case,pos,stall=False,abort=None):
  tick(start=1,pos=pos,case=case);assert m.owner&7==1
  rotations=m.counts['slot_rotations'];acks=m.cache.counts['cache_bytes'];disabled=set();before=m.counts.copy()
  for clock in range(100000):
   phase=m.owner&7
   if abort and abort(m):reset();return False
   if phase==6:break
   np=m.n['p'];idx=m.n['idx'];x=case['x'][idx] if np in (1,5) else -524288
   en=int(not stall or clock%11!=2);xv=int(not stall or clock%7!=3)
   if stall and phase==5 and m.cache.bank.cursor==((m.cache.ctl>>6&15)*131+(m.cache.ctl>>10&255)):
    ci=m.cache.ctl>>10&255
    if ci in (31,127,130) and ci not in disabled:en=0;disabled.add(ci)
   tick(start=int(clock%97==0),x=x,xv=xv,en=en,pos=(pos+1)%16)
  else:raise AssertionError('producer timeout')
  y=tick();assert y>>10&1 and y>>20==case['maximum'];assert m.cache.counts['cache_bytes']-acks==131
  assert m.counts['slot_rotations']-rotations==4
  for k,count in [('norm_scan',256),('norm_replay',256),('norm_outputs',256),('quant_scan',128),('quant_replay',128),('quant_writes',128)]:assert m.counts[k]-before[k]==count,k
  stored[pos]=case['packed'];read(pos,case['packed'])
  calls.append(dict(position=pos,clocks_to_observed_done=clock+2,source=case['source'],stalled=stall,
   disabled_cache_positions=sorted(disabled),cache_bytes=131,producer_rotations=4,full_norm_passes=2,external_X_scalars=512))
  return True
 reset()
 for i,c in enumerate(cases):assert transact(c,i%16,stall=i in (1,17))
 for p,v in sorted(stored.items()):read(p,v)
 aborts=[lambda x:x.n['p']==2 and x.n['idx']==3,
  lambda x:x.owner&7==1 and x.n['p']==9 and x.n['idx']==31,
  lambda x:x.owner&7==3 and x.n['p']==2 and x.n['idx']==31,
  lambda x:x.owner&7==3 and x.q['p']==3 and x.q['idx']==31,
  lambda x:x.owner&7==3 and x.q['p']==4 and x.q['idx']==127,
  lambda x:x.owner&7==5 and x.cache.ctl>>10&255==64]
 for abort in aborts:
  assert not transact(cases[-1],0,abort=abort);assert transact(cases[2],0)
 assert m.counts['completed']==len(cases)+len(aborts) and m.counts['aborted']==len(aborts)
 return rows,dict(clocks=len(rows),calls=calls,counts=m.counts,cache_counts=m.cache.counts,complete_c_cases=len(cases),
  scope='twice true norm0, first max scan, second A8 into same pruned slot,131byte cache and actual readback; external repeated X, no matrix/whole-token controller')


def reference_data(cloud,directory):
 if cloud:return base.fixtures()
 r=json.loads((directory/'receipt.json').read_text())
 assert r['metrics']['sha256']=='f3173a27342369a2d29ff516d13346da98af5d71fb4a1915b8144a822ab188a7'
 for n,h in r['sources'].items():assert sha((R/n).read_bytes())==h,n
 assert sha((directory/'cases.json').read_bytes())==r['cases_sha256']
 for p in directory.rglob('*'):
  if p.is_file() and (p.name in ('cases.json','reference.c') or p.parent.name=='c_vectors' and p.suffix in ('.c','.json')):
   dest=OUT/p.relative_to(directory);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 return json.loads((OUT/'cases.json').read_text())


def cloud_check(net,raw,cut,weights,words,rows,dl,dr,pl,pr,slot_left,slot_right):
 assert os.getenv('GITHUB_ACTIONS')=='true';base.OUT=OUT;shared.OUT=OUT
 divider=shared.prove(dl,dr)
 shared.OUT=OUT/'projection';shared.OUT.mkdir(exist_ok=True)
 try:projection=shared.prove(pl,pr)
 finally:shared.OUT=OUT
 projection=dict(proof=projection['proof'],negative=projection['negative'],retained_state_count=net.n_state,
  output_count=NO,arbitrary_removed_old_state=True,reset_or_range_precondition=False)
 def optimized(bad_rotation=False,**kwargs):
  n,*_=make(bad_rotation);return n,{},weights,words
 original=base.make;base.make=optimized
 try:check=base.cloud_check(net,cut,weights,words,rows,slot_left,slot_right)
 finally:base.make=original
 import verify as checks
 fault=prune(sharing(raw,bad_owner=True)[0])[0]
 assert sha(fault.encode())!=sha(net.encode())
 wrong=checks.check_nand(rows[:check['negative_prefix_clocks']],fault.encode());assert wrong>0
 (OUT/'wrong_div_owner.nl').write_bytes(fault.encode());check['actual_wrong_divider_owner_mismatches']=wrong
 return divider,projection,check


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
 ap.add_argument('--references',type=Path,default=R/'build/integer_opt/norm_cache_live');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 cut=connector();sm=small_connector(cut);feedback=small_projection();owner=base.small();mux=shared.small()
 raw,parts,weights,words=make_unshared();unpruned,dl,dr,binding=sharing(raw);net,kept=prune(unpruned)
 pl,pr,same=structural_identity(unpruned,net,kept);assert same and pl.encode()==pr.encode()
 assert not structural_identity(unpruned,flip_output(net),kept)[2]
 removed=sorted(set(range(unpruned.n_state))-set(kept))
 groups=dict(raw_A_high12=[base.cache.bank.NS+20*j+k for j in range(128) for k in range(8,20)],
  product_high24=list(range(base.cache.NS+40,base.cache.NS+64)),multiplicand_high24=list(range(base.cache.NS+104,base.cache.NS+128)))
 assert removed==sorted(i for ids in groups.values() for i in ids)
 prior=make_unshared(merged=False)[0];slot_left,slot_right=base.port_identity(prior,raw)
 print('unshared/shared/pruned',metrics(raw),metrics(unpruned),metrics(net),flush=True)
 cases=reference_data(args.cloud,args.references);assert words==cases['weights']
 assert metrics(weights)['nNand']<=4000;wc=verify(weights,list(range(128)),words)
 rows,expected=vectors(cases['cases'])
 (OUT/'fill.nl').write_bytes(net.encode());(OUT/'fill.v').write_text(rtl(net,'norm_cache_fill'));(OUT/'connector.ref.v').write_text(connector_ref())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer_opt/weights.py','physical/verify.py','physical/nl_sim.c','integer/int_model.c','physical/model.bin',
  'integer_opt/reverse_attention.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl']:paths.add(R/n)
 report=dict(status='small connector/control and exact C-data protocol pass; large gates/proofs await Actions',
  metrics=metrics(net),unshared=metrics(raw),unpruned_shared=metrics(unpruned),kept=kept,removed=removed,removed_groups=groups,
  small=sm,feedback=feedback,owner=owner,small_mux=mux,weight_check=wc,binding=binding,expected=expected,
  proof_metrics={'DIV_reference':metrics(dl),'DIV_candidate':metrics(dr),'projection':metrics(pl),'connector':metrics(cut)},
  same_canonical_D_output=True,actual_mutation_breaks_identity=True,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  reference_policy='local R85 source/hash-checked C data; Actions regenerates26 cases from frozen C and the complete new protocol',
  contract='same40/40 public interface as R83; X is replayed for two full norms (512 scalar accepts); output/cache bytes are unchanged',
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['divider_proof'],report['projection_proof'],report['verification']=cloud_check(net,raw,cut,weights,words,rows,dl,dr,pl,pr,slot_left,slot_right)
  report['status']='conditional shared DIV, arbitrary-state projection, connector/slot/weight proofs and all actual NAND/RTL/C clocks/faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('clocks',expected['clocks'],json.dumps(expected['counts']),flush=True)


if __name__=='__main__':main()
