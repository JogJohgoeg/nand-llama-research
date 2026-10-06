#!/usr/bin/env python3
"""Exact score shift rounding plus a four-adder 46341 chain in R68.

RNE uses arithmetic floor plus a one-bit correction, including negatives.
The frozen integer rule, output width and head schedule are unchanged.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,shutil
R=Path(os.environ.get('H3_SCORE_CHAIN_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_packed as previous
import score_dot as dot
import value_row as base
from nand import Builder,metrics,simulate,flip_output,blif
from export import rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_SCORE_CHAIN_OUT',str(R/'build/integer_opt/attention_score')))


def extend(x,width):
 assert len(x)<=width
 return x+[x[-1]]*(width-len(x))


def rounded(b,x,shift,width,half_up=False):
 # x = floor(x/2^s)*2^s + unsigned low bits, also for negative x.
 # Increment iff remainder > half, or exactly half with an odd quotient.
 sticky=b.reduce(x[:shift-1],b.lor,0)
 up=x[shift-1] if half_up else b.land(x[shift-1],b.lor(sticky,x[shift]))
 return b.add(extend(x[shift:],width),[0]*width,up)[0]


def score(first_half_up=False,second_half_up=False):
 b=Builder(45);x=list(range(2,47));q=rounded(b,x,12,34,first_half_up)
 # q in [-2^32,2^32]. Every intermediate uses an exact signed bound.
 five=b.add(extend(q,36),[0]*2+q)[0]
 fortyfive=b.add(extend(five,39),[0]*3+five)[0]
 oneeightyone=b.add(extend(q,41),[0]*2+fortyfive)[0]
 product=b.add(extend(five,49),[0]*8+oneeightyone)[0]
 return b.finish(rounded(b,product,18,32,second_half_up))


def small(g,cases):
 old=dot.unit();new=score();rng=random.Random(260773);lo=-(1<<44);hi=(1<<44)-1
 xs=[lo,lo+1,lo+2047,lo+2048,lo+2049,hi-2049,hi-2048,hi-2047,hi,-1,0,1]
 xs += [rng.randint(lo,hi) for _ in range(8192)]
 qs=[-(1<<32),-(1<<32)+1,(1<<32)-1,1<<32]+list(range(-128,129))
 # 46341 is odd. q == 2^17 (mod 2^18) gives an exact outer half.
 qs += [((k<<18)+(1<<17)) for k in range(-128,129)]
 for q in qs:
  xs.extend(q*4096+r for r in (-2049,-2048,-2047,-1,0,1,2047,2048,2049) if lo<=q*4096+r<=hi)
 xs += [c['dot'] for c in cases['cases']]
 values=[x&((1<<45)-1) for x in xs];expected=[int(g.qk_score(x))&0xffffffff for x in xs]
 results={}
 for label,net in [('old',old),('new',new),('output_fault',flip_output(new)),
                   ('inner_half_up',score(True)),('outer_half_up',score(False,True))]:
  assert metrics(net)['nNand']+net.n_state<=4000
  actual=simulate(net,values);bad=sum(a!=b for a,b in zip(actual,expected))
  assert bad==0 if label in ('old','new') else bad>0,label
  results[label]=dict(metrics=metrics(net),mismatches=bad)
 (OUT/'score.vectors.json').write_text(json.dumps(dict(input=values,expected=expected))+'\n')
 for label,net in [('old',old),('new',new),('negative',flip_output(new))]:
  (OUT/('score.'+label+'.nl')).write_bytes(net.encode())
  (OUT/('score.'+label+'.blif')).write_text(blif(net))
 return dict(cases=len(xs),real_scores=80,results=results,
  vectors_sha256=sha((OUT/'score.vectors.json').read_bytes()),
  identity='q=R(t,4096); f=5q; g=9f; h=4g+q; p=256h+f=46341q; output=R(p,2^18)',
  signed_widths=dict(q=34,five=36,fortyfive=39,oneeightyone=41,product=49,output=32))


def make():
 original=dot.unit
 try:
  dot.unit=score
  return previous.make()
 finally:dot.unit=original


def reference():
 # The standalone all45-input proof checks this replacement against the
 # original pinned score. Top-level composition then uses that proven leaf.
 original=dot.unit
 try:
  dot.unit=score
  return previous.reference()
 finally:dot.unit=original


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);previous.OUT=OUT
 vg,sg,dg,cases=previous.golden()
 dots=json.loads((OUT/'kv/pairs/dot/cases.json').read_text());sm=small(dg,dots)
 rows,expected=previous.vectors(vg,sg,dg,cases)
 net,comb,_=make();prior=previous.make()[0]
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 assert sha((OUT/'vectors.txt').read_bytes())=='2fa700ebc62ae901d9f27f0ceab3cc4a48b14589ee8608938ded60c00350e9a4'
 assert sha((OUT/'cases.json').read_bytes())=='289e63b5ab029884966a9dbe2b1b37efd3f4569ada0267a60069efa508d388e5'
 report=dict(status='small exact score and unchanged full-head C schedule pass; full cloud pending',metrics=metrics(net),previous=metrics(prior),
  small=sm,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,
  scope='same packed preloaded head and clocks as R68; exact score leaf replacement; no whole-model sharing or physical timing claim')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','physical/golden_slice.c','docs/index.html',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/exp.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','integer_opt/score_units/manifest.json','integer_opt/score_units/score.nl']:paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  from ci import cec
  abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
  proof=cec(abc,OUT/'score.old.blif',OUT/'score.new.blif',OUT/'score.cec.log');assert proof['verdict']=='equivalent',proof
  negative=cec(abc,OUT/'score.old.blif',OUT/'score.negative.blif',OUT/'score.negative.log');assert negative['verdict']=='different',negative
  report['score_cec']=proof;report['score_actual_gate_mutation']=negative
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
  base.OUT=OUT;base.reference=reference;base.NI=previous.NI;base.NO=previous.NO
  report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='score all45-input CEC against frozen old operator; allD/output independent head composition with new proven score and original EXP/DIV/dequantizer; not unbounded model reachability'
  report['status']='score leaf all-input CEC and complete NAND/RTL/C head pass with actual faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print('clocks',expected['clocks'],'sources',len(paths))


if __name__=='__main__':main()
