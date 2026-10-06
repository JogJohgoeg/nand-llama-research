#!/usr/bin/env python3
"""Remove only sequential state with no path to any present/future output.

A structural next-state/output identity proves the retained-state projection.
Large gates and C replay remain cloud-only. Original external pins are retained.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse
R=Path(os.environ.get('H3_QKV_LIVE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from golden import Netlist
from nand import metrics,flip_output
from export import rtl
from state_projection import prune,structural_identity,small
import qkv_range as qkv
import cache_matrix as base
OUT=Path(os.environ.get('H3_QKV_LIVE_OUT',str(R/'build/integer_opt/qkv_live')))
sha=lambda b:hashlib.sha256(b).hexdigest()
OLD_SHA='51ec0ff2deb2bed8b308b92d921e318db4c89ac26f930ee6611909ba40acf9c9'


def source_graph():
 if os.getenv('GITHUB_ACTIONS')!='true':
  root=R/'build/integer_opt/qkv_range';r=json.loads((root/'receipt.json').read_text())
  assert all(sha((R/n).read_bytes())==h for n,h in r['sources'].items())
  raw=(root/'matrix.nl').read_bytes();assert sha(raw)==OLD_SHA
  return Netlist.decode(raw,r['metrics']['nIn'],r['metrics']['nOut']),None
 original,before,cofactor,table,graph,words,choices=qkv.candidates()
 old,en,_=qkv.prior.make(table,original);assert sha(old.encode())==OLD_SHA
 return old,(en,table,cofactor,graph,words)


def classify(removed):
 groups=dict(A_high12=[16780+j*20+k for j in range(128) for k in range(8,20)],
  cursor_address_high4=list(range(19389,19393)),
  scale_state_high9_a=list(range(19465,19474)),
  scale_state_high9_b=list(range(19529,19538)),
  scale_temp_high9=list(range(19772,19781)))
 assert sorted(x for xs in groups.values() for x in xs)==removed
 return {k:dict(count=len(v),old_indices=v) for k,v in groups.items()}


def cloud_check(net,source,left,right,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 proof=qkv.prior.prove(left,right,'state_projection')
 proof.update(retained_state_count=net.n_state,output_count=net.n_out,
  arbitrary_removed_old_state=True,reset_or_range_precondition=False)
 (OUT/'projection_proof.json').write_text(json.dumps(proof,indent=2)+'\n')
 en,table,cofactor,graph,words=source
 step,reset,_=qkv.owner_graphs();cursor,_=qkv.cursor_graph()
 # Apply the same mechanical pruning to the real missing-rotation mutant.
 old_make=base.make
 def live_make(*args,**kwargs):
  graph,parts=old_make(*args,**kwargs);return prune(graph)[0],parts
 base.make=live_make
 try:props,verification=qkv.cloud_check(net,en,table,cofactor,graph,step,reset,cursor,rows,words)
 finally:base.make=old_make
 return proof,props,verification


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);qkv.OUT=OUT;qkv.prior.OUT=OUT;sm=small()
 old,source=source_graph();pins=qkv.pin_check();new,kept=prune(old)
 left,right,same=structural_identity(old,new,kept);assert same and left.encode()==right.encode()
 _,_,mutated=structural_identity(old,flip_output(new),kept);assert not mutated
 removed=sorted(set(range(old.n_state))-set(kept));groups=classify(removed)
 rows,expected,csha,vsha=qkv.prior.references()
 (OUT/'matrix.nl').write_bytes(new.encode());(OUT/'matrix.v').write_text(rtl(new,'cache_matrix'))
 (OUT/'connector.ref.v').write_text(base.connector_ref())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows));assert sha((OUT/'vectors.txt').read_bytes())==vsha
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['integer_opt/reverse_attention.c','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','physical/nl_sim.c']:paths.add(R/n)
 result=dict(status='small gates and exact retained D/output structural identity pass; full actual gates and cloud proof pending',
  metrics=metrics(new),before=metrics(old),kept=kept,removed=removed,removed_groups=groups,
  proof_metrics=metrics(left),proof_sha256=sha(left.encode()),same_canonical_D_output=True,actual_mutation_breaks_identity=True,
  small=sm,pin_binding=pins,expected=expected,vector_sha256=vsha,cases_sha256=csha,
  scope='all retained state D and outputs equal for arbitrary old state/input, no reset/range precondition added by pruning; full path inherits R80 reset/range contract',
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
 if a.cloud:
  result['projection_proof'],result['range_proof'],result['verification']=cloud_check(new,source,left,right,rows)
  result['status']='all retained D/output projection and inherited ranges proved; full actual NAND/RTL/C passes with pruned faults'
  (OUT/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:result[k] for k in ['status','metrics','before','proof_metrics','proof_sha256']},indent=2))
 print('removed groups', {k:v['count'] for k,v in groups.items()})


if __name__=='__main__':main()
