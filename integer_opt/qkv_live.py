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
from nand import Builder,metrics,with_state,flip_output
from export import import_net,rtl
from gate_check import verify
import qkv_range as qkv
import cache_matrix as base
OUT=Path(os.environ.get('H3_QKV_LIVE_OUT',str(R/'build/integer_opt/qkv_live')))
sha=lambda b:hashlib.sha256(b).hexdigest()
OLD_SHA='51ec0ff2deb2bed8b308b92d921e318db4c89ac26f930ee6611909ba40acf9c9'


def prune(net):
 base=net.n_in+2;ns=net.n_state
 assert all(rec[0]==1 for rec in net.records[:ns]) and all(rec[0]==0 for rec in net.records[ns:])
 todo=list(range(base+len(net.records)-net.n_out,base+len(net.records)));live=set()
 while todo:
  wire=todo.pop()
  if wire<base or wire in live:continue
  live.add(wire);todo.extend(net.records[wire-base][1:])
 ids=[i for i in range(len(net.records)) if base+i in live]
 remap={i:i for i in range(base)};remap.update({base+i:base+j for j,i in enumerate(ids)})
 records=[tuple([net.records[i][0]]+[remap[x] for x in net.records[i][1:]]) for i in ids]
 reduced=Netlist(net.n_in,net.n_out,records);reduced.validate()
 kept=[i for i in range(ns) if base+i in live]
 assert reduced.n_state==len(kept)
 assert ids[-net.n_out:]==list(range(len(net.records)-net.n_out,len(net.records)))
 return reduced,kept


def structural_identity(before,after,kept):
 b=Builder(before.n_state+before.n_in);old=list(range(2,2+before.n_state));p=list(range(2+before.n_state,2+before.n_state+before.n_in))
 bd,bo=import_net(b,before,p,old);ad,ao=import_net(b,after,p,[old[i] for i in kept])
 left=[bd[i] for i in kept]+bo;right=ad+ao
 # Canonical NAND construction binds all retained D and public outputs to
 # identical expressions, with removed old state still arbitrary inputs.
 return b.finish(left),b.finish(right),left==right


def small():
 b=Builder(9);q=list(range(2,8));p=[8,9,10]
 d=[b.xor(q[1],p[0]),q[2],b.nand(q[0],p[1]),b.xor(q[4],p[2]),q[3],p[2]]
 net=with_state(b.finish(d+[q[0],b.inv(p[2])]),6);reduced,kept=prune(net);assert kept==[0,1,2]
 left,right,same=structural_identity(net,reduced,kept);assert same and left.encode()==right.encode()
 assert max(metrics(x)['nNand']+x.n_state for x in [net,reduced,left,right])<=4000
 xs=list(range(512));ys=[]
 for x in xs:
  q=[x>>i&1 for i in range(6)];p=[x>>(6+i)&1 for i in range(3)]
  d=[q[1]^p[0],q[2],1-(q[0]&p[1])];o=q[0]+((1-p[2])<<1)
  ys.append(sum(v<<j for j,v in enumerate(d))+(o<<3))
 checks=dict(before_projected=verify(left,xs,ys),after=verify(right,xs,ys))
 _,_,mutant_same=structural_identity(net,flip_output(reduced),kept);assert not mutant_same
 return dict(before=metrics(net),after=metrics(reduced),kept=kept,removed=[3,4,5],checks=checks,actual_output_mutation_breaks_identity=True)


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
