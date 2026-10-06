#!/usr/bin/env python3
"""Select commands before the shared cache/A20 port, keeping every D bit."""
from pathlib import Path
import os,sys,json,hashlib,signal,random,argparse
R=Path(os.environ.get('H3_NORM_QKV_PORTS_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_qkv as base
from nand import Builder,metrics,with_state
from export import import_net,rtl
from gate_check import verify
from state_projection import prune
pmod,qmod,cache=base.producer,base.consumer,base.cache
NI,NO,NS=base.NI,base.NO,base.NS
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_NORM_QKV_PORTS_OUT',str(R/'build/integer_opt/norm_qkv_ports')))


def producer_commands(b,ps,p,shared_parts,live_kept):
 # Expand the exact two recorded state projections, including the DIV alias.
 k84=shared_parts['kept'];m84={v:i for i,v in enumerate(k84)};m85={v:i for i,v in enumerate(live_kept)}
 old=[]
 for i in range(20066):
  j=base.shared.ND+i-base.shared.QD if i in base.shared.REMOVED else i
  k=m84[j];old.append(ps[m85[k]] if k in m85 else 0)
 nn,_,_=pmod.norm0();qn=pmod.quant.make()
 _,no=import_net(b,nn,[p[0]]+[0]*23,old[cache.NS:cache.NS+512])
 _,qo=import_net(b,qn,[p[0]]+[0]*32,old[cache.NS+512:-7])
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 done=b.land(b.inv(p[0]),eq(ps[cache.NS-38:cache.NS-36],2))
 result=pmod.connect(b,old[-7:],p,no,qo,done,ps[cache.bank.NS:cache.bank.NS+640],ps[cache.bank.NS-12:cache.bank.NS])
 _,_,_,cp,wp,flags,_=result
 return cp,wp,flags


def consumer_commands(b,qs,p):
 cs=qs[:cache.NS];es=qs[cache.NS:-4];owner=qs[-4:];wc=cs[-38:];work=cs[cache.bank.NS:cache.bank.NS+2560]
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 keep=b.inv(p[0]);idle=b.lor(eq(wc[:2],0),eq(wc[:2],2))
 c=[work[j*20+k] for j in range(32) for k in range(8)]+wc[18:]+[
  b.land(keep,eq(wc[:2],1)),b.land(keep,b.land(idle,eq(wc[2:5],4))),b.land(keep,eq(wc[:2],2))]
 en=qmod.engine.make(True);_,eo=import_net(b,en,[p[0]]+[0]*(qmod.engine.NI-1),es)
 _,ci,_,_=qmod.connect(b,owner,p,c,eo)
 return ci


def common_port(b,cn,cs,p,wp,owns,install):
 cd,_=import_net(b,cn,p,cs);wc=cs[-38:];slot=cs[cache.bank.NS:cache.bank.NS+2560]
 address=cache.address(b,wc[6:10],wc[10:18])
 ready=b.land(b.inv(p[0]),b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cs[cache.bank.NS-12:cache.bank.NS],address)],b.land,1))
 dc,act=cache.controller(b,wc,p[:28]+p[668:670]+[ready]+cs[:8]);assert dc==cd[-38:]
 cached=p[28:668]+cs[:8]+[cs[7]]*12+wc[10:15]+act[:3]
 pins=[b.mux(owns,x,y) for x,y in zip(cached,wp)]
 wn,_=cache.stage.make();cd[cache.bank.NS:cache.bank.NS+2560]=import_net(b,wn,pins,slot)[0]
 for j in range(3):cd[cache.NS-38+2+j]=b.mux(install,cd[cache.NS-38+2+j],(4>>j)&1)
 return cd


def make(bad_owner=False):
 raw=pmod.make()[0];sh,_,_,sp=base.shared.sharing(raw);pn,kept=prune(sh)
 original,_,_,table,_,_,_=base.qkv.candidates();qn,_,_=base.qkv.prior.make(table,original)
 assert sha(pn.encode())=='f3173a27342369a2d29ff516d13346da98af5d71fb4a1915b8144a822ab188a7'
 assert sha(qn.encode())=='51ec0ff2deb2bed8b308b92d921e318db4c89ac26f930ee6611909ba40acf9c9'
 b=Builder(NS+NI);old=list(range(2,NS+2));pins=list(range(NS+2,NS+NI+2))
 ps=old[:base.PN];qs=old[:base.COMMON]+old[base.PN:-1]
 _,po=import_net(b,pn,[pins[0]]+[0]*39,ps);_,qo=import_net(b,qn,[pins[0]]+[0]*675,qs)
 md,pp,qp,public=base.connect(b,old[-1],pins,po[9],qo[30],qo[32])
 pd,po=import_net(b,pn,pp,ps);qd,qo=import_net(b,qn,qp,qs)
 cp,wp,(install,owns)=producer_commands(b,ps,pp,sp,kept)
 cq=consumer_commands(b,qs,qp);cn=cache.make()[0]
 # Binding against actual child D expressions is exact for arbitrary old state.
 bound=common_port(b,cn,ps[:cache.NS],cp,wp,owns,install)
 assert bound==pd[:cache.NS],'producer shared-state command binding'
 assert import_net(b,cn,cq,qs[:cache.NS])[0]==qd[:cache.NS],'consumer shared-state command binding'
 select=b.inv(public[1]) if bad_owner else public[1]
 command=[b.mux(select,x,y) for x,y in zip(cp,cq)]
 active=b.inv(select)
 cd=common_port(b,cn,ps[:cache.NS],command,wp,b.land(active,owns),b.land(active,install))
 reference=[b.mux(public[1],x,y) for x,y in zip(pd[:cache.NS],qd[:cache.NS])]
 rest=pd[cache.NS:]+qd[cache.NS:]+md+po+qo+public
 before=with_state(b.finish(reference+rest),NS)
 assert sha(before.encode())=='176b76c8f6902bf4b4362e4d1a437456075fff57cc1e9d2fbf403574740334a4'
 after=with_state(b.finish(cd+rest),NS)
 return before,after,b.finish(reference),b.finish(cd),dict(shared_D_bits=cache.NS,
  exact_producer_D_command_binding=True,exact_consumer_D_command_binding=True,
  private_D_and_public_outputs_identical=True,arbitrary_old_state_and_inputs=True,
  state_identification_changed=False,reset_or_range_precondition=False)


def small():
 # All controller flags and arbitrary state are covered; no legal-state filter.
 b=Builder(38+39*2+1);s=list(range(2,40));p=list(range(40,79));q=list(range(79,118));sel=118
 d1,a1=cache.controller(b,s,p);d2,a2=cache.controller(b,s,q)
 md,ma=cache.controller(b,s,[b.mux(sel,x,y) for x,y in zip(p,q)])
 left=b.finish([b.mux(sel,x,y) for x,y in zip(d1+a1,d2+a2)]);right=b.finish(md+ma)
 assert all(metrics(g)['nNand']<=4000 for g in (left,right))
 rng=random.Random(260796);xs=[];ys=[]
 for _ in range(2048):
  st=rng.getrandbits(38);p1=rng.getrandbits(39);p2=rng.getrandbits(39);choose=rng.randrange(2)
  d,a=cache.ctl_step(st,p2 if choose else p1)
  xs.append(st+(p1<<38)+(p2<<77)+(choose<<116));ys.append(d+(a<<38))
 return dict(controller_before=verify(left,xs,ys),controller_after=verify(right,xs,ys),
  controller_metrics={'before':metrics(left),'after':metrics(right)},slot=pmod.small_port())


def cloud_check(net,rows,left,right,step,reset):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 base.OUT=OUT
 proof=base.prove(left,right,'shared_state')
 proof.update(shared_D_bits=cache.NS,arbitrary_old_state_and_inputs=True,reset_or_range_precondition=False,
  all_private_D_and_outputs_same=True,unbounded_equivalence_after_equal_state=True)
 original=base.make
 def optimized(*args,**kw):
  _,candidate,_,_,binding=make(*args,**kw);return candidate,binding
 base.make=optimized
 try:checked=base.cloud_check(net,rows,step,reset)
 finally:base.make=original
 return proof,checked


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 sm=small();step,reset,ownership=base.ownership();before,net,left,right,binding=make()
 print('before/after',metrics(before),metrics(net),flush=True)
 cases,refs=base.fixtures();rows,expected=base.vectors(cases['cases'],refs)
 assert expected['clocks']==1503175
 (OUT/'core.nl').write_bytes(net.encode());(OUT/'core.v').write_text(rtl(net,'norm_qkv'))
 (OUT/'connector.ref.v').write_text(base.connector_ref())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c',
              'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl',
              'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl']:
  paths.add(R/name)
 report=dict(status='actual child command bindings and small controller/slot checks pass; full state CEC and NAND/RTL await Actions',
  before=metrics(before),metrics=metrics(net),binding=binding,small=sm,ownership_checks=ownership,
  proof_metrics={'reference':metrics(left),'candidate':metrics(right)},expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['shared_state_proof'],report['verification']=cloud_check(net,rows,left,right,step,reset)
  report['status']='all shared D equivalent for arbitrary state; inherited ownership proofs and complete actual optimized NAND/RTL/C with real faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('clocks',len(rows),expected['counts'])


if __name__=='__main__':main()
