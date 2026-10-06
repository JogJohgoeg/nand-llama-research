#!/usr/bin/env python3
"""Share the exact RNE DIV between norm/A8 and sequential QKV consumers."""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse
R=Path(os.environ.get('H3_NORM_QKV_DIV_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_qkv_ports as ports
base=ports.base;cache=base.cache
from nand import Builder,metrics,with_state
from export import import_net,rtl
from golden import Netlist
NI,NO=base.NI,base.NO
PD=cache.NS+144
QD=base.PN+32+192
REMOVED=list(range(QD,QD+115))
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_NORM_QKV_DIV_OUT',str(R/'build/integer_opt/norm_qkv_div')))


def sharing(net,bad_div_owner=False):
 assert net.n_in==NI and net.n_out==NO and net.n_state==base.NS
 kept=[i for i in range(net.n_state) if i not in REMOVED];index={v:i for i,v in enumerate(kept)}
 def primary(i):return PD+i-QD if i in REMOVED else i
 b=Builder(len(kept)+NI);old=[2+index[primary(i)] for i in range(net.n_state)]
 p=list(range(2+len(kept),2+len(kept)+NI));ds,out=import_net(b,net,p,old)
 # Expand norm's48 unobservable MUL bits; the exact DIV-D binding below proves
 # they do not enter these operands. Quant's115 DIV bits already alias norm.
 live=[i for i in range(512) if i not in list(range(40,64))+list(range(104,128))]
 mi={v:i for i,v in enumerate(live)}
 ns=[old[cache.NS+mi[j]] if j in mi else 0 for j in range(512)]
 qs=old[PD:PD+115]+old[cache.NS+464:base.PN-7];assert len(qs)==169
 owner=old[base.PN-7:base.PN]
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 normal=eq(owner[:3],1);nload=eq(ns[488:492],7)
 np=[nload]+[0]*28+ns[:36]+ns[451:475]+[0]
 qrun=b.land(b.inv(p[0]),eq(owner[:3],3));receive=eq(qs[153:156],2)
 ready=b.land(b.inv(p[0]),b.lor(eq(qs[153:156],1),receive))
 qload=b.land(b.land(ready,b.land(qrun,p[23])),receive)
 head=old[cache.bank.NS:cache.bank.NS+640]
 x=cache.stage.old.select(b,[head[j*20:(j+1)*20] for j in range(32)],qs[144:149])
 product=b.add([0]*7+x,[b.inv(v) for v in x+[x[-1]]*7],1)[0]
 qp=[qload]+[0]*37+product+qs[115:135]+[0]*5
 producer=[b.mux(normal,q,n) for q,n in zip(qp,np)]
 scale=old[base.PN+32:base.PN+32+418];assert len(scale)==418
 temp=scale[307:371];count=scale[389:395];phase=scale[395:397]
 consumer=[b.land(eq(phase,3),eq(count,0))]+[0]*9+temp[:55]+[(33292288>>j)&1 for j in range(25)]
 assert len(producer)==len(consumer)==90
 meta=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
 raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
 div=Netlist.decode(raw,meta['nIn'],meta['nOut']);assert div.n_state==115
 pd,_=import_net(b,div,producer,old[PD:PD+115]);qd,_=import_net(b,div,consumer,old[QD:QD+115])
 assert pd==ds[PD:PD+115],'actual producer DIV command binding'
 assert qd==ds[QD:QD+115],'actual QKV DIV command binding'
 selected=b.inv(out[-1]) if bad_div_owner else out[-1]
 command=[b.mux(selected,x,y) for x,y in zip(producer,consumer)]
 dd,_=import_net(b,div,command,old[PD:PD+115])
 reference=b.finish([b.mux(out[-1],x,y) for x,y in zip(pd,qd)]);proof=b.finish(dd)
 nxt=[dd[i-PD] if PD<=i<PD+115 else ds[i] for i in kept]
 new=with_state(b.finish(nxt+out),len(kept))
 return new,reference,proof,dict(kept=kept,removed=REMOVED,producer_DIV_start=PD,consumer_DIV_start=QD,
  shared_DIV_bits=115,actual_producer_operand_D_binding=True,actual_consumer_operand_D_binding=True,
  selected_next_state_after_state_identification=True,unbounded_lifetime_proof=False,
  scalar_result_registers_retained=True,extra_vector_bits=0)


def make(bad_owner=False,bad_div_owner=False):
 net=ports.make(bad_owner)[1]
 if not bad_owner:assert sha(net.encode())=='6dce12cb14468972aefc40f1f287df391cf3efb471b4cb590e03867e1a89fba2'
 new,left,right,binding=sharing(net,bad_div_owner)
 return net,new,left,right,binding


def cloud_check(net,rows,left,right,port_left,port_right,step,reset):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 base.OUT=OUT
 inherited=base.prove(port_left,port_right,'inherited_ports')
 proof=base.prove(left,right,'shared_div')
 proof.update(state_identification=True,conditional_owner_selected_D=True,shared_D_bits=115,unbounded_lifetime_proof=False)
 original=base.make
 def optimized(*args,**kw):
  _,candidate,_,_,binding=make(*args,**kw);return candidate,binding
 base.make=optimized
 try:checked=base.cloud_check(net,rows,step,reset)
 finally:base.make=original
 import verify as checks
 bad=make(bad_div_owner=True)[1];assert sha(bad.encode())!=sha(net.encode())
 prefix=rows[:checked['negative_prefix_clocks']]
 wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
 (OUT/'wrong_div_owner.nl').write_bytes(bad.encode())
 checked['actual_wrong_DIV_owner_mismatches']=wrong
 return inherited,proof,checked


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 sm=base.shared.small();port_small=ports.small();step,reset,ownership=base.ownership()
 _,before,pl,pr,port_binding=ports.make();net,left,right,binding=sharing(before)
 assert sha(before.encode())=='6dce12cb14468972aefc40f1f287df391cf3efb471b4cb590e03867e1a89fba2'
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
 report=dict(status='actual shared DIV command bindings and small cuts pass; full conditional CEC and shared gates await Actions',
  before=metrics(before),metrics=metrics(net),binding=binding,small=sm,port_small=port_small,port_binding=port_binding,
  ownership_checks=ownership,proof_metrics={'reference':metrics(left),'candidate':metrics(right)},expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['inherited_ports_proof'],report['shared_DIV_proof'],report['verification']=cloud_check(net,rows,left,right,pl,pr,step,reset)
  report['status']='conditional115-bit shared DIV and inherited port/control proofs pass; complete actual shared NAND/RTL/C and faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('clocks',len(rows),expected['counts'])


if __name__=='__main__':main()
