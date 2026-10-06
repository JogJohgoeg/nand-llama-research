#!/usr/bin/env python3
"""Prune only producer state with no path to a present or future output."""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse
R=Path(os.environ.get('H3_NORM_LIVE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache_shared as shared
from state_projection import prune,structural_identity,small
from nand import metrics,flip_output
from export import rtl
from gate_check import verify
base=shared.base
OUT=Path(os.environ.get('H3_NORM_LIVE_OUT',str(R/'build/integer_opt/norm_cache_live')))
sha=lambda b:hashlib.sha256(b).hexdigest()


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT;shared.OUT=OUT
 sm=small();unshared,_,weights,words=base.make();old,dl,dr,_=shared.sharing(unshared)
 assert sha(old.encode())=='5dad2a87edb479d755481f1577e55fe3c62316b4c09f8d37032b2d6cb1b53b77'
 new,kept=prune(old);left,right,same=structural_identity(old,new,kept)
 assert same and left.encode()==right.encode()
 assert not structural_identity(old,flip_output(new),kept)[2]
 removed=sorted(set(range(old.n_state))-set(kept))
 groups={'product_high24':list(range(base.cache.NS+40,base.cache.NS+64)),
         'shifted_multiplicand_high24':list(range(base.cache.NS+104,base.cache.NS+128))}
 assert sorted(i for xs in groups.values() for i in xs)==removed
 cases=base.fixtures();rows,expected=base.vectors(cases['cases']);cut=base.connector()
 assert expected['clocks']==684106 and words==cases['weights']
 assert metrics(weights)['nNand']<=4000;wc=verify(weights,list(range(128)),words)
 prior=base.make(merged=False)[0];slot_left,slot_right=base.port_identity(prior,unshared)
 (OUT/'fill.nl').write_bytes(new.encode());(OUT/'fill.v').write_text(rtl(new,'norm_cache_fill'))
 (OUT/'connector.ref.v').write_text(base.connector_ref())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if (R in p.parents or p.parent==Path(__file__).resolve().parent) and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer_opt/weights.py','physical/verify.py','physical/nl_sim.c','integer/int_model.c','physical/model.bin',
           'integer_opt/reverse_attention.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
           'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl']:paths.add(R/n)
 report=dict(status='small feedback and all retained D/output structural identity pass; full graph cloud proof pending',
  before=metrics(old),metrics=metrics(new),kept=kept,removed=removed,removed_groups=groups,
  same_canonical_D_output=True,actual_output_mutation_breaks_identity=True,projection_metrics=metrics(left),
  small=sm,weight_check=wc,expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['divider_proof']=shared.prove(dl,dr)
  # Same proof runner, separate output path. This projection has no owner,
  # range or reset precondition; the removed old states remain arbitrary.
  saved=shared.OUT;shared.OUT=OUT/'projection';shared.OUT.mkdir(exist_ok=True)
  try:proof=shared.prove(left,right)
  finally:shared.OUT=saved
  report['projection_proof']=dict(proof=proof['proof'],negative=proof['negative'],
    retained_state_count=new.n_state,output_count=new.n_out,arbitrary_removed_old_state=True,
    reset_or_range_precondition=False)
  original=base.make
  def live_make(*a,**kw):
   result=original(*a,**kw);return (prune(shared.sharing(result[0])[0])[0],)+result[1:]
  base.make=live_make
  try:report['verification']=base.cloud_check(new,cut,weights,words,rows,slot_left,slot_right)
  finally:base.make=original
  import verify as checks
  bad=prune(shared.sharing(unshared,bad_owner=True)[0])[0]
  assert sha(bad.encode())!=sha(new.encode())
  wrong=checks.check_nand(rows[:report['verification']['negative_prefix_clocks']],bad.encode());assert wrong>0
  (OUT/'wrong_div_owner.nl').write_bytes(bad.encode())
  report['verification']['actual_wrong_divider_owner_mismatches']=wrong
  report['status']='arbitrary-state retained D/output projection and inherited proofs pass; full pruned NAND/RTL/C and real faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','before','metrics','projection_metrics','removed_groups']},indent=2))


if __name__=='__main__':main()
