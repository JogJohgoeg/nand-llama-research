#!/usr/bin/env python3
"""Project the integrated shared-arithmetic graph to future-observable state."""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse
R=Path(os.environ.get('H3_NORM_QKV_LIVE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_qkv_mul as prior
from state_projection import prune,structural_identity,small
from nand import metrics,flip_output
from export import rtl
base=prior.base
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_NORM_QKV_LIVE_OUT',str(R/'build/integer_opt/norm_qkv_live')))


def make(**kwargs):
 before=prior.make(**kwargs)[1]
 if not any(kwargs.values()):assert sha(before.encode())=='2652ac8391b9a11eac795c3e87fb2fd1cc9d3a2cc3b8aa5652ac127bc5f888cb'
 after,kept=prune(before);left,right,same=structural_identity(before,after,kept)
 assert same and left.encode()==right.encode()
 assert not structural_identity(before,flip_output(after),kept)[2]
 return before,after,left,right,kept


def cloud_check(net,rows,left,right,pl,pr,dl,dr,ml,mr,step,reset):
 assert os.getenv('GITHUB_ACTIONS')=='true';base.OUT=OUT;prior.OUT=OUT
 proof=base.prove(left,right,'projection')
 proof.update(retained_state_count=net.n_state,output_count=net.n_out,
  arbitrary_removed_old_state=True,reset_or_range_precondition=False)
 original=prior.make
 def projected(*a,**kw):
  result=list(original(*a,**kw));result[1]=prune(result[1])[0];return tuple(result)
 prior.make=projected
 try:parts=prior.cloud_check(net,rows,pl,pr,dl,dr,ml,mr,step,reset)
 finally:prior.make=original
 return (proof,)+parts


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
 ap.add_argument('--references',type=Path,default=R/'build/integer_opt/norm_qkv_div');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT;prior.OUT=OUT
 sm=small();step,reset,ownership=base.ownership()
 _,pnet,pl,pr,_=prior.ports.make();dnet,dl,dr,_=prior.prior.sharing(pnet)
 before,ml,mr,_=prior.sharing(dnet);net,kept=prune(before)
 assert sha(before.encode())=='2652ac8391b9a11eac795c3e87fb2fd1cc9d3a2cc3b8aa5652ac127bc5f888cb'
 left,right,same=structural_identity(before,net,kept);assert same and left.encode()==right.encode()
 assert not structural_identity(before,flip_output(net),kept)[2]
 print('before/after',metrics(before),metrics(net),flush=True)
 rows,expected=prior.reference_data(args.cloud,args.references)
 (OUT/'core.nl').write_bytes(net.encode());(OUT/'core.v').write_text(rtl(net,'norm_qkv'))
 (OUT/'connector.ref.v').write_text(base.connector_ref())
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
 live=set(kept)
 report=dict(status='arbitrary old state retained-D/output identity and small feedback checks pass; full pruned gates await Actions',
  before=metrics(before),metrics=metrics(net),kept=kept,removed=[i for i in range(before.n_state) if i not in live],
  same_canonical_D_output=True,actual_mutation_breaks_identity=True,small=sm,
  ownership_checks=ownership,proof_metrics={'reference':metrics(left),'candidate':metrics(right)},expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  reference_policy='source-checked R88 C data locally; all1503175 clocks regenerated from frozen C in Actions with fixed expected/cases/vector digests',
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['projection_proof'],report['inherited_ports_proof'],report['inherited_DIV_proof'],report['inherited_MUL_proof'],report['verification']=cloud_check(net,rows,left,right,pl,pr,dl,dr,ml,mr,step,reset)
  report['status']='all retained D/output projection and inherited proofs pass; complete actual pruned NAND/RTL/C and pruned-graph faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('reference clocks',expected['clocks'],'removed',report['removed'])


if __name__=='__main__':main()
