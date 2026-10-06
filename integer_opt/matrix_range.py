#!/usr/bin/env python3
"""Restrict the cached path's weight selector to four128x128 matrices.

Prove reset/inductive cursor range before using the2048-word cofactor.
No numeric change, no local large-gate evaluation, no whole-model credit.
"""
from pathlib import Path
from contextlib import contextmanager
import os,sys,json,hashlib,random,signal,argparse,ctypes as ct,subprocess,shutil
R=Path(os.environ.get('H3_MATRIX_RANGE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import cache_matrix as base
import linear_engine as engine
import weight_cursor
from export import import_net,rtl,weight_words
from nand import Builder,metrics,flip_output
from golden import Netlist
from bench import lookup
from gate_check import verify
OUT=Path(os.environ.get('H3_MATRIX_RANGE_OUT',str(R/'build/integer_opt/matrix_range')))
sha=lambda x:hashlib.sha256(x).hexdigest()
OLD_TOP='a1077e15df79cb1535fa7c3387ce86a21098130f26929a5476aac30b4df55d3d'
OLD_WEIGHT='885b90713a1d6a39039402d67bf419770a1680b02ae811bf4bb648634a837bf9'


def selectors():
 raw=(R/'integer_opt/pilot_units/weights_layer0.nl').read_bytes();assert sha(raw)==OLD_WEIGHT
 original=Netlist.decode(raw,13,64)
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==engine.MODEL_SHA
 words=weight_words(blob)[:2048];narrow=lookup(words,64,'phase')
 assert narrow.n_in==11
 b=Builder(11);_,y=import_net(b,original,list(range(2,13))+[0,0]);cofactor=b.finish(y)
 # Constant-fold the already mapped true selector; smaller than rebuilding.
 b=Builder(13);_,y=import_net(b,cofactor,list(range(2,13)));adapted=b.finish(y)
 assert all(13 not in rec[1:] and 14 not in rec[1:] for rec in adapted.records)
 b=Builder(11);_,y=import_net(b,adapted,list(range(2,13))+[0,0]);rebound=b.finish(y)
 assert sha(rebound.encode())==sha(cofactor.encode())
 return original,narrow,adapted,cofactor,words


def encoded(row,group,matrix,active):
 return group+row+matrix+[0]*4+row+[0]*2+group+[0]*2+matrix+[0]+[active]


def invariant_graphs():
 cursor=weight_cursor.make()[1]
 b=Builder(17);row=list(range(2,9));group=[9,10];matrix=[11,12];active=13
 old=encoded(row,group,matrix,active)
 ds,_=import_net(b,cursor,[14,15,16,0,0,0,17,18,0],old)
 canonical=encoded(ds[15:22],ds[24:26],ds[28:30],ds[31])
 same=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(ds,canonical)],b.land,1)
 inductive=b.finish([same])
 b=Builder(36);ds,_=import_net(b,cursor,[1,34,35,0,0,0,36,37,0],list(range(2,34)))
 reset=b.finish([b.inv(b.reduce(ds,b.lor,0))])
 rng=random.Random(260785);samples=[rng.getrandbits(17) for _ in range(4096)]
 # All active/matrix/group/boundaryrow transitions; fully exhaustive in Actions.
 samples += [row+(g<<7)+(m<<9)+(a<<11)+(ctrl<<12)+(newmat<<15)
  for row in (0,1,126,127) for g in range(4) for m in range(4) for a in range(2) for ctrl in range(8) for newmat in range(4)]
 assert max(metrics(x)['nNand'] for x in (inductive,reset))<=4000
 checked=dict(inductive=verify(inductive,samples,[1]*len(samples)),reset=verify(reset,[rng.getrandbits(36) for _ in range(512)],[1]*512))
 return inductive,reset,checked


@contextmanager
def replacement(table,engine_sha=None):
 original=engine.Netlist;old_sha=base.ENGINE_SHA
 class Weights:
  @staticmethod
  def decode(raw,ni,no):
   assert ni==13 and no==64 and sha(raw)==OLD_WEIGHT
   return table
 engine.Netlist=Weights
 if engine_sha is not None:base.ENGINE_SHA=engine_sha
 try:yield
 finally:engine.Netlist=original;base.ENGINE_SHA=old_sha


def make(table,original):
 with replacement(original):
  old,_=base.make();assert sha(old.encode())==OLD_TOP
 with replacement(table):new_engine=engine.make(True)
 with replacement(table,sha(new_engine.encode())):new,parts=base.make()
 assert new.n_state==old.n_state and new.n_in==old.n_in and new.n_out==old.n_out
 parts['scope']='same original engine arithmetic/control; constant network specialized to matrices0..3; this owner calls Q/K/V only'
 return new,new_engine,dict(before=metrics(old),after=metrics(new),new_engine=metrics(new_engine),
  parts=parts,changed_leaf_only='weight selector; same frozen construction and503 engine state bits',
  rollback_reassembled_original_sha256=sha(old.encode()),extra_vector_bits=0)


def c_words(words):
 so=OUT/'weights.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(R/'integer_opt/weights_golden.c'),'-o',str(so)],check=True,timeout=30)
 g=ct.CDLL(str(so));blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
 g.true_weight_word.argtypes=[ct.c_uint,ct.c_int];g.true_weight_word.restype=ct.c_uint64
 actual=[int(g.true_weight_word(i,0)) for i in range(2048)];assert actual==words
 return dict(words=2048,trits=65536,all_words_match=True,sha256=sha(b''.join(w.to_bytes(8,'little') for w in words)))


def references():
 # Reuse R78's already-verified C/logical reference locally. Cloud regenerates it
 # from the pinned sources. This avoids a second full CPU pass on the laptop.
 root=R/'build/integer_opt/cache_matrix';p=root/'receipt.json';v=root/'vectors.txt'
 if os.getenv('GITHUB_ACTIONS')!='true' and p.exists() and v.exists():
  r=json.loads(p.read_text());assert r['metrics']['sha256']==OLD_TOP
  for n,h in r['sources'].items():assert sha((R/n).read_bytes())==h,n
  assert sha(v.read_bytes())==r['vector_sha256']
  rows=[tuple(int(x,16) for x in line.split()) for line in v.read_text().splitlines()]
  assert len(rows)==r['expected']['clocks']
  shutil.copyfile(root/'matrix_golden.c',OUT/'matrix_golden.c')
  shutil.copytree(root/'c_vectors',OUT/'c_vectors',dirs_exist_ok=True)
  return rows,r['expected'],r['cases_sha256'],r['vector_sha256']
 base.OUT=OUT;base.cache.bank.OUT=OUT/'c_vectors';base.cache.bank.OUT.mkdir(parents=True,exist_ok=True)
 cases=base.cache.bank.golden();refs=base.golden(cases);rows,expected=base.vectors(cases,refs)
 raw=''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows).encode()
 return rows,expected,sha((OUT/'c_vectors/cases.json').read_bytes()),sha(raw)


def pin_check():
 cut=base.connector();values=[0,1]+[None]*cut.n_in
 for tag,x,y in cut.records:
  assert tag==0
  a,b=values[x],values[y];values.append(1 if a==0 or b==0 else 0 if a==b==1 else None)
 # cut outputs: owner D4, cache inputs670, engine inputs283, public outputs.
 assert values[-cut.n_out+4+670+4]==0
 return dict(engine_matrix_input_bit2_constant_zero=True,
  engine_source_sha256=sha((R/'integer_opt/linear_engine.py').read_bytes()),
  owner_source_sha256=sha((R/'integer_opt/cache_matrix.py').read_bytes()),
  scope='layer0 hardwired in unchanged engine constructor, matrix bit2 tied low in actual connector; no assumption that external pin is well-behaved')


def prove(left,right,name):
 from nand import blif
 from ci import cec
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 for label,g in [('left',left),('right',right),('negative',flip_output(left))]:(OUT/(name+'.'+label+'.blif')).write_text(blif(g))
 good=cec(abc,OUT/(name+'.left.blif'),OUT/(name+'.right.blif'),OUT/(name+'.cec.log'));assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/(name+'.negative.blif'),OUT/(name+'.right.blif'),OUT/(name+'.negative.cec.log'));assert bad['verdict']=='different'
 return dict(all_input_cec=good,actual_gate_mutation=bad)


def cloud_check(net,new_engine,table,narrow,cofactor,inductive,reset,rows,words):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 props={}
 for name,g in [('cursor_induction',inductive),('cursor_reset',reset)]:
  b=Builder(g.n_in);props[name]=prove(g,b.finish([1]),name)
 props['selector_cofactor']=prove(narrow,cofactor,'selector')
 # Actual large selectors exhaust all2048 legal addresses against independent
 # C-parser words; both sides and real output-gate faults are evaluated.
 props['selector_truth']=dict(new=verify(narrow,list(range(2048)),words),previous_cofactor=verify(cofactor,list(range(2048)),words))
 (OUT/'range_proof.json').write_text(json.dumps(props,indent=2)+'\n')
 base.OUT=OUT
 with replacement(table,sha(new_engine.encode())):actual=base.cloud_check(net,base.connector(),rows)
 return props,actual


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True)
 original,narrow,table,cofactor,words=selectors();inductive,reset,sm=invariant_graphs();wc=c_words(words);pins=pin_check()
 net,en,parts=make(table,original);rows,expected,cases_sha,vector_sha=references()
 (OUT/'matrix.nl').write_bytes(net.encode());(OUT/'matrix.v').write_text(rtl(net,'cache_matrix'));(OUT/'connector.ref.v').write_text(base.connector_ref())
 (OUT/'selector.nl').write_bytes(table.encode());(OUT/'selector_words.json').write_text(json.dumps(words)+'\n')
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows));assert sha((OUT/'vectors.txt').read_bytes())==vector_sha
 previous=json.loads((R/'build/integer_opt/cache_matrix/receipt.json').read_text()) if os.getenv('GITHUB_ACTIONS')!='true' else None
 if previous:assert expected==previous['expected']
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['integer_opt/reverse_attention.c','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','physical/nl_sim.c']:paths.add(R/n)
 report=dict(status='small cursor invariant gates and C table pass; full weight/refinement pending Actions',metrics=metrics(net),parts=parts,
  selectors=dict(previous=metrics(original),new=metrics(table),cofactor=metrics(cofactor),independent_rebuild_not_selected=metrics(narrow)),invariant_metrics=dict(reset=metrics(reset),inductive=metrics(inductive)),small=sm,words=wc,pin_binding=pins,
  invariant='cursor addr=512*matrix+4*row+group, matrix<4,row<128,group<4,layer0;reset establishes and each reset/start/advance preserves',
  expected=expected,vector_sha256=vector_sha,cases_sha256=cases_sha,
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  scope='R78 cached Q/K/V path weights narrowed under proved cursor range; all7-matrix engine is not equivalent outside this interface; not fullmodel weight savings',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['range_proof'],report['verification']=cloud_check(net,en,table,narrow,cofactor,inductive,reset,rows,words)
  report['status']='cursor reset/induction, full legal weight truth/cofactor and actual cached matrix NAND/RTL/C pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','selectors','small','words','pin_binding']},indent=2))


if __name__=='__main__':main()
