#!/usr/bin/env python3
"""Plain Shannon sharing for the fully defined Q/K/V selector.

R4 already favored plain Shannon for larger tables. Keep the matrix/row/group
order but remove complemented-function canonicalization. Unlike R94, the whole
fourth matrix is explicitly V: all2048 addresses match R93, even outside the
operational domain. The complete state transition can be compared unmasked.
"""
from pathlib import Path
import os,sys,json,random,hashlib
R=Path(os.environ.get('H3_QKV_PLAIN_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import qkv_care as integrated
from bench import lookup
from nand import Builder,metrics
from export import import_net,weight_words
from gate_check import verify
OUT=Path(os.environ.get('H3_QKV_PLAIN_OUT',str(R/'build/integer_opt/qkv_plain')))
sha=lambda b:hashlib.sha256(b).hexdigest()


def ordered_full(table,width,order):
 assert len(table)==1<<len(order) and sorted(order)==list(range(len(order)))
 perm=[sum((i>>j&1)<<k for j,k in enumerate(order)) for i in range(len(table))]
 assert len(set(perm))==len(table)
 source=lookup([table[i] for i in perm],width,'shannon')
 b=Builder(len(order));_,y=import_net(b,source,[2+k for k in order]);return b.finish(y)


def weight_tables():
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==integrated.base.consumer.engine.MODEL_SHA
 words=weight_words(blob)[:2048];filled=words[:1536]+words[1024:1536]
 order=[9,10]+list(range(2,9))+[0,1]
 original=integrated.base.qkv.candidates()[4];candidate=ordered_full(filled,64,order)
 b=Builder(13);_,y=import_net(b,candidate,list(range(2,13)));adapted=b.finish(y)
 return words,original,adapted,candidate,dict(selected='plain_matrix_row_group',order=order,
  method='ordinary Shannon; explicit fourth-matrix V; no care-mask choice',
  before=metrics(original),after=metrics(candidate),all_input_addresses=2048,
  filled_words_sha256=sha(b''.join(v.to_bytes(8,'little') for v in filled)))


def small():
 rng=random.Random(260807);reports=[]
 for n in range(2,8):
  for k in range(8):
   width=4;table=[rng.getrandbits(width) for _ in range(1<<n)]
   order=list(range(n));rng.shuffle(order);g=ordered_full(table,width,order)
   assert metrics(g)['nNand']<=4000
   reports.append(dict(n=n,order=order,metrics=metrics(g),verification=verify(g,list(range(1<<n)),table)))
 return dict(cases=len(reports),known_addresses=sum(1<<v['n'] for v in reports),all_addresses_defined=True,reports=reports)


def transition_pair(old,new,bind,oldbind):
 _,previous,_,candidate,_=weight_tables()
 left,right,record=integrated.weight_cone.pair(old,new,previous,candidate,bind,oldbind,integrated.base.PN)
 record['scope']='exact actual-cone recomposition and arbitrary common-body D/output proof; separate selector proof covers all2048 addresses without a range premise'
 return left,right,record


def cloud_check(net,old,words,previous,candidate,proofs,step,reset,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true';base=integrated.base;prior=integrated.prior;base.OUT=OUT
 scope=dict(selector_full_domain=base.prove(previous,candidate,'selector_full_domain'))
 full=words[:1536]+words[1024:1536]
 scope['full_selector_truth']=dict(previous=verify(previous,list(range(2048)),full),candidate=verify(candidate,list(range(2048)),full),all_addresses=2048)
 # Rename the inherited slot: this pair is explicitly unmasked and requires no
 # cursor range. The original QKV-only interface contract itself is unchanged.
 proofs=dict(proofs);proofs['complete_transition_all_state']=proofs.pop('complete_transition_legal')
 prior.OUT=OUT;saved=prior.make;prior.make=integrated.make
 try:scope['transforms'],actual=prior.cloud_check(net,rows,proofs,step,reset)
 finally:prior.make=saved
 return scope,actual


def main():
 # Reuse the complete R94 assembler, fixture validation and cloud fault path;
 # only the selector and its now-stronger full-domain comparison are replaced.
 integrated.OUT=OUT
 integrated.weight_tables=weight_tables;integrated.small=small
 integrated.transition_pair=transition_pair;integrated.cloud_check=cloud_check
 integrated.main()
 p=OUT/'receipt.json';d=json.loads(p.read_text())
 d['sources']['integer_opt/qkv_plain.py']=sha(Path(__file__).read_bytes())
 d['proof_metrics']['complete_transition_all_state']=d['proof_metrics'].pop('complete_transition_legal')
 d['small_selector']=d.pop('small_care');d['comparison']='R93 fully defined selector; no additional legal-domain premise'
 if os.getenv('GITHUB_ACTIONS')=='true':d['status']='unconditional selector and complete-state CEC, full actual NAND/RTL/C and real faults pass'
 else:d['status']='small full-domain selectors, C words and structural bindings pass; complete unconditional CEC and gate replay await Actions'
 p.write_text(json.dumps(d,indent=2)+'\n')
 print('unmasked replacement',d['metrics']['nNand'],'NAND',d['metrics']['nLatch'],'LATCH')


if __name__=='__main__':main()
