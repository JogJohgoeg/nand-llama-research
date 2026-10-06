#!/usr/bin/env python3
"""Eliminate unused O weights after proving owner and cursor matrix<3.

All large proof/replay stays on Actions. Numerical/C schedules unchanged.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse
R=Path(os.environ.get('H3_QKV_RANGE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import matrix_range as prior
import cache_matrix as base
from nand import Builder,metrics
from export import import_net,rtl
from bench import lookup
from gate_check import verify
OUT=Path(os.environ.get('H3_QKV_RANGE_OUT',str(R/'build/integer_opt/qkv_range')))
sha=lambda b:hashlib.sha256(b).hexdigest()
R79_SHA='daee00e1e32bad1427cc227da95fc3643b7cc39f1a2963c9ecd3ec3315040b06'


def owner_graphs():
 b=Builder(14);s=list(range(2,6));p=list(range(6,16));d,_=base.control(b,s,p)
 old_good=b.inv(b.land(s[2],s[3]));new_good=b.inv(b.land(d[2],d[3]))
 step=b.finish([b.lor(b.inv(old_good),new_good)])
 b=Builder(13);d,_=base.control(b,list(range(2,6)),[1]+list(range(6,15)))
 reset=b.finish([b.inv(b.reduce(d,b.lor,0))])
 assert max(metrics(g)['nNand'] for g in (step,reset))<=4000
 checks=dict(induction=verify(step,list(range(16384)),[1]*16384),reset=verify(reset,list(range(8192)),[1]*8192))
 return step,reset,checks


def cursor_graph():
 cursor=prior.weight_cursor.make()[1];b=Builder(17)
 row=list(range(2,9));group=[9,10];mat=[11,12];old=prior.encoded(row,group,mat,13)
 ds,_=import_net(b,cursor,[14,15,16,0,0,0,17,18,0],old)
 canonical=prior.encoded(ds[15:22],ds[24:26],ds[28:30],ds[31])
 same=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(ds,canonical)],b.land,1)
 good=b.land(same,b.inv(b.land(ds[28],ds[29])))
 ignored=b.lor(b.land(*mat),b.land(17,18));net=b.finish([b.lor(ignored,good)])
 assert metrics(net)['nNand']<=4000
 rng=random.Random(260786);xs=[rng.getrandbits(17) for _ in range(4096)]
 xs += [row+(g<<7)+(m<<9)+(a<<11)+(ctrl<<12)+(new<<15)
  for row in (0,1,126,127) for g in range(4) for m in range(4) for a in range(2) for ctrl in range(8) for new in range(4)]
 return net,verify(net,xs,[1]*len(xs))


def pin_check():
 report=prior.pin_check();g=base.connector()
 def primary(output):
  w=2+g.n_in+len(g.records)-g.n_out+output;polarity=0
  while w>=2+g.n_in:
   tag,a,b=g.records[w-2-g.n_in];assert tag==0 and a==b
   w=a;polarity^=1
  return w-2,polarity
 # Owner is the first4 cut inputs; its two matrix bits reach engine pins2/3.
 assert primary(4+670+2)==(2,0) and primary(4+670+3)==(3,0)
 report['engine_low_matrix_bits_equal_owner_state']=True
 return report


def candidates():
 original,independent,before,cofactor,words=prior.selectors();choices={}
 for name,tail in [('zero',[0]*512),('Q',words[:512]),('K',words[512:1024]),('V',words[1024:1536])]:
  candidate=lookup(words[:1536]+tail,64,'phase');choices[name]=candidate
 selected=min(choices,key=lambda k:(metrics(choices[k])['nNand'],metrics(choices[k])['nand_depth']))
 graph=choices[selected];b=Builder(13);_,y=import_net(b,graph,list(range(2,13)));table=b.finish(y)
 assert all(13 not in rec[1:] and 14 not in rec[1:] for rec in table.records)
 b=Builder(11);_,y=import_net(b,table,list(range(2,13))+[0,0]);assert sha(b.finish(y).encode())==sha(graph.encode())
 return original,before,cofactor,table,graph,words,dict(selected=selected,candidates={k:metrics(v) for k,v in choices.items()},policy='choose minimum NAND,then depth; fourth matrix is outside the proved reachable domain')


def masked(graph):
 b=Builder(11);inputs=list(range(2,13));_,y=import_net(b,graph,inputs)
 valid=b.inv(b.land(inputs[9],inputs[10]));return b.finish([b.land(valid,v) for v in y])


def cloud_check(net,engine,table,cofactor,newgraph,step,reset,cursor,rows,words):
 assert os.getenv('GITHUB_ACTIONS')=='true';props={}
 for name,g in [('owner_induction',step),('owner_reset',reset),('cursor_induction',cursor)]:
  b=Builder(g.n_in);props[name]=prior.prove(g,b.finish([1]),name)
 # The independent R79 actual cursor reset predicate also establishes matrix0.
 _,cr,_=prior.invariant_graphs();b=Builder(cr.n_in);props['cursor_reset']=prior.prove(cr,b.finish([1]),'cursor_reset')
 left,right=masked(newgraph),masked(cofactor);props['selector_legal_domain']=prior.prove(left,right,'selector_legal')
 expected=words[:1536]+[0]*512
 props['selector_truth']=dict(new=verify(left,list(range(2048)),expected),previous=verify(right,list(range(2048)),expected),legal_addresses=1536,masked_unused_addresses=512)
 (OUT/'range_proof.json').write_text(json.dumps(props,indent=2)+'\n');base.OUT=OUT
 with prior.replacement(table,sha(engine.encode())):verification=base.cloud_check(net,base.connector(),rows)
 return props,verification


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);prior.OUT=OUT
 step,reset,sm=owner_graphs();cursor,cm=cursor_graph();pins=pin_check()
 original,before,cofactor,table,graph,words,choices=candidates();wordcheck=prior.c_words(words)
 old,_,_=prior.make(before,original);assert sha(old.encode())==R79_SHA
 net,en,parts=prior.make(table,original);rows,expected,csha,vsha=prior.references()
 assert net.n_state==old.n_state and metrics(net)['nNand']<metrics(old)['nNand']
 for name,g in [('matrix',net),('selector',table),('owner_step',step),('owner_reset',reset),('cursor_step',cursor)]:
  (OUT/(name+'.nl')).write_bytes(g.encode())
 (OUT/'matrix.v').write_text(rtl(net,'cache_matrix'));(OUT/'connector.ref.v').write_text(base.connector_ref())
 (OUT/'selector_words.json').write_text(json.dumps(words[:1536])+'\n')
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows));assert sha((OUT/'vectors.txt').read_bytes())==vsha
 parts['parts']['scope']='same engine arithmetic/control; Q/K/V weight selector under owner and cursor matrix<3 invariant'
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['integer_opt/reverse_attention.c','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','physical/nl_sim.c']:paths.add(R/n)
 report=dict(status='small owner/cursor range and C words pass; complete proof and actual large gates pending Actions',metrics=metrics(net),
  before=metrics(old),parts=parts,selection=choices,selector=metrics(table),small_owner=sm,small_cursor=cm,
  predicates=dict(owner_step=metrics(step),owner_reset=metrics(reset),cursor_step=metrics(cursor)),pin_binding=pins,words=wordcheck,
  expected=expected,vector_sha256=vsha,cases_sha256=csha,numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  scope='QKV-only cached path after global reset; owner and cursor matrix<3 inductive proof required; not generic7-matrix or wholemodel savings',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['range_proof'],report['verification']=cloud_check(net,en,table,cofactor,graph,step,reset,cursor,rows,words)
  report['status']='owner/cursor reset/induction and selector legal domain proved; full real NAND/RTL/C matches with faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','before','selection','predicates','small_owner','small_cursor']},indent=2))


if __name__=='__main__':main()
