#!/usr/bin/env python3
"""Identify the norm/A8 divider state and choose its complete load command.

Conditional owner-selected next-state proof plus full C protocol, not an
unbounded whole-model lifetime theorem. No extra vector or changed schedule.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse,shutil
R=Path(os.environ.get('H3_NORM_SHARED_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache_fill as base
from nand import Builder,metrics,with_state,blif,flip_output
from export import import_net,load_unit,rtl
from golden import Netlist
from gate_check import verify
OUT=Path(os.environ.get('H3_NORM_SHARED_OUT',str(R/'build/integer_opt/norm_cache_shared')))
sha=lambda b:hashlib.sha256(b).hexdigest()
ND=base.cache.NS+192
QD=base.cache.NS+512
REMOVED=list(range(QD,QD+115))


def sharing(net,bad_owner=False):
 assert net.n_state==20066 and net.n_in==40 and net.n_out==40
 kept=[i for i in range(net.n_state) if i not in REMOVED];index={old:new for new,old in enumerate(kept)}
 def primary(i):return ND+i-QD if i in REMOVED else i
 b=Builder(len(kept)+net.n_in);old=[2+index[primary(i)] for i in range(net.n_state)]
 pins=list(range(2+len(kept),2+len(kept)+net.n_in));ds,out=import_net(b,net,pins,old)
 ns=old[base.cache.NS:base.cache.NS+512];qs=old[QD:QD+169];owner=old[-7:]
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 choose=eq(owner[:3],1)
 nload=eq(ns[488:492],7);nn=[0]*28+ns[:36];nd=ns[451:475]+[0]
 keep=b.inv(pins[0]);qrun=b.land(keep,eq(owner[:3],3))
 receive=eq(qs[153:156],2);qready=b.land(keep,b.lor(eq(qs[153:156],1),receive))
 qload=b.land(b.land(qready,b.land(qrun,pins[23])),receive)
 head=old[base.cache.bank.NS:base.cache.bank.NS+640]
 x=base.cache.stage.old.select(b,[head[j*20:(j+1)*20] for j in range(32)],qs[144:149])
 product=b.add([0]*7+x,[b.inv(v) for v in x+[x[-1]]*7],1)[0]
 qn=[0]*37+product;qd=qs[115:135]+[0]*5
 normpins=[nload]+nn+nd;quantpins=[qload]+qn+qd
 selected=b.inv(choose) if bad_owner else choose
 command=[b.mux(selected,q,n) for q,n in zip(quantpins,normpins)]
 manifest=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
 raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==manifest['sha256']
 div=Netlist.decode(raw,manifest['nIn'],manifest['nOut']);assert div.n_state==115
 dd,_=import_net(b,div,command,old[ND:ND+115])
 # Verify operand reconstruction against the actual old divider instances;
 # identical canonical expressions bind every D, not just named formulas.
 actual_n,_=import_net(b,div,normpins,old[ND:ND+115])
 actual_q,_=import_net(b,div,quantpins,old[ND:ND+115])
 assert actual_n==ds[ND:ND+115], 'norm DIV command binding'
 assert actual_q==ds[QD:QD+115], 'quant DIV command binding'
 reference=b.finish([b.mux(choose,q,n) for q,n in zip(ds[QD:QD+115],ds[ND:ND+115])])
 proof=b.finish(dd)
 nxt=[dd[i-ND] if ND<=i<ND+115 else ds[i] for i in kept]
 candidate=with_state(b.finish(nxt+out),len(kept))
 return candidate,reference,proof,dict(removed=REMOVED,kept=kept,removed_divider_bits=115,extra_vector_bits=0,
  divider_instances=1,multiplier_instances=1,sqrt_instances=1,exact_old_operand_D_binding=True,
  proof_scope='owner-selected115 D bits after identifying old norm/quant divider state; every other D/output retained under this state mapping; not unbounded lifetime proof')


def small():
 import random
 b=Builder(181);left=list(range(2,92));right=list(range(92,182));choose=182
 net=b.finish([b.mux(choose,x,y) for x,y in zip(left,right)]);assert metrics(net)['nNand']<=4000
 rng=random.Random(260792);xs=[];ys=[]
 for i in range(512):
  l=rng.getrandbits(90);r=rng.getrandbits(90);c=i&1
  xs.append(l+(r<<90)+(c<<180));ys.append(r if c else l)
 return dict(metrics=metrics(net),verification=verify(net,xs,ys))


def prove(left,right):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 for name,net in [('div_reference',left),('div_shared',right),('div_negative',flip_output(right))]:(OUT/(name+'.blif')).write_text(blif(net))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'div_reference.blif',OUT/'div_shared.blif',OUT/'div.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'div_reference.blif',OUT/'div_negative.blif',OUT/'div.negative.log');assert bad['verdict']=='different'
 return dict(proof=good,negative=bad,state_identification=True,conditional_owner_selected_D=True,unbounded_lifetime_proof=False)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 sm=small();old,_,weights,words=base.make();assert sha(old.encode())=='e3a234f81ac7c29b7a078bfc54f3ad4b95e01a85313d83b269a85536df1633f9'
 new,left,right,parts=sharing(old);cases=base.fixtures();rows,expected=base.vectors(cases['cases']);cut=base.connector()
 assert expected['clocks']==684106 and words==cases['weights']
 assert metrics(weights)['nNand']<=4000;wc=verify(weights,list(range(128)),words)
 prior=base.make(merged=False)[0];slot_left,slot_right=base.port_identity(prior,old)
 (OUT/'fill.nl').write_bytes(new.encode());(OUT/'fill.v').write_text(rtl(new,'norm_cache_fill'));(OUT/'connector.ref.v').write_text(base.connector_ref())
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
 report=dict(status='small command mux/C protocol/actual operand structural binding pass; full gate proof pending',
  before=metrics(old),metrics=metrics(new),parts=parts,small=sm,weight_check=wc,expected=expected,
  proof_metrics=dict(reference=metrics(left),candidate=metrics(right)),
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['divider_proof']=prove(left,right)
  original=base.make
  def shared_make(*a,**kw):
   result=original(*a,**kw);return (sharing(result[0])[0],)+result[1:]
  base.make=shared_make
  try:report['verification']=base.cloud_check(new,cut,weights,words,rows,slot_left,slot_right)
  finally:base.make=original
  import verify as checks
  fault=sharing(old,bad_owner=True)[0];assert sha(fault.encode())!=sha(new.encode())
  prefix=rows[:report['verification']['negative_prefix_clocks']]
  wrong=checks.check_nand(prefix,fault.encode());assert wrong>0
  (OUT/'wrong_div_owner.nl').write_bytes(fault.encode())
  report['verification']['actual_wrong_divider_owner_mismatches']=wrong
  report['status']='owner-selected divider D and inherited port/connector/weights proofs pass; all actual shared NAND/RTL/C clocks and shared-graph faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','before','metrics','proof_metrics']},indent=2))


if __name__=='__main__':main()
