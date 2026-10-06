#!/usr/bin/env python3
"""Exact signed KV8 dequantization via base128 folding, inside R69.

127 is odd, so R(n,127)=floor((n+63)/127), with no half ties.
Booth rows preserve signed q8*unsigned m20, including -128 and saturation.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,subprocess,time
R=Path(os.environ.get('H3_DEQUANT_FOLD_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_score as previous
import attention_packed as packed
import kv_client as kv
import score_dot as dot
import value_row as base
from nand import Builder,metrics,simulate,flip_output,blif
from export import rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_DEQUANT_FOLD_OUT',str(R/'build/integer_opt/attention_dequant')))

def ext(x,w,signed=False):return x+[x[-1] if signed else 0]*(w-len(x))
def heap(b,rows,width,constant=0):
 cols=[[] for _ in range(width)]
 for x,shift,signed in rows:
  for i,v in enumerate(x):
   if i+shift>=width:continue
   if signed and i==len(x)-1:
    v=b.inv(v);constant-=1<<(i+shift)
   cols[i+shift].append(v)
 for i in range(width):
  if constant>>i&1:cols[i].append(1)
 while any(len(c)>2 for c in cols):
  nxt=[[] for _ in range(width)]
  for i,c in enumerate(cols):
   k=0
   while k+2<len(c):
    s,cy=b.add([c[k]],[c[k+1]],c[k+2]);nxt[i].extend(s)
    if i+1<width:nxt[i+1].append(cy)
    k+=3
   nxt[i].extend(c[k:])
  cols=nxt
 return b.add([c[0] if c else 0 for c in cols],[c[1] if len(c)>1 else 0 for c in cols])[0]
def operator(booth=True,bias=63,saturate=True):
 b=Builder(28);q=list(range(2,10));m=list(range(10,30));rows=[];constant=0
 if booth:
  for k in range(4):
   y0=q[2*k-1] if k else 0;y1,y2=q[2*k:2*k+2]
   one=b.xor(y1,y0);two=b.land(b.xor(y2,y1),b.inv(one));neg=y2
   xe=m+[0,0];row=[]
   for j,x in enumerate(xe):
    lo=xe[j-1] if j else 0
    mag=b.nand(b.nand(x,one),b.nand(lo,two));row.append(b.xor(mag,neg))
   rows += [(row,2*k,True),([neg],2*k,False)]
 else:
  for i,x in enumerate(q):
   row=[b.land(x,y) for y in m]
   if i==7:
    row=[b.inv(v) for v in row];constant-=((1<<20)-1)<<7
   rows.append((row,i,False))
 p=heap(b,rows,28,constant)
 base=heap(b,[(p[7:],0,True),(p[14:],0,True),(p[21:],0,True)],22)
 s=heap(b,[(p[:7],0,False),(p[7:14],0,False),(p[14:21],0,False),(p[21:],0,True)],9,bias)
 def ge(n):return b.add(s,[(~n>>i)&1 for i in range(9)],1)[1]
 a,c,d=ge(127),ge(254),ge(381)
 correction=[b.land(a,b.nand(c,b.inv(d))),c]
 y=b.add(base,ext(correction,22))[0]
 bad=b.lor(b.xor(y[19],y[20]),b.xor(y[19],y[21]));clamp=[b.inv(y[21])]*19+[y[21]]
 return b.finish([b.mux(bad,x,z) for x,z in zip(y[:20],clamp)] if saturate else y[:20])



def small(g,cases):
 rng=random.Random(260774)
 pairs=[(q,m) for q in range(-128,128) for m in [0,1,2,3,62,63,64,65,125,126,127,128,524286,524287,524288,524289,1048574,1048575]]
 pairs += [(q,m) for q in (-128,-127,-1,1,127) for m in range(128)]
 for q in range(-128,128):
  if q:
   # The first rounded value beyond each signed20 saturation endpoint.
   t=524288 if q>0 else 524289;edge=(127*t-63)//abs(q)
   pairs.extend((q,m) for m in range(edge-2,edge+3) if 0<=m<(1<<20))
 pairs += [(rng.randrange(-128,128),rng.randrange(1<<20)) for _ in range(4096)]
 real=[]
 for c in cases['cases']:
  for word in c['words']+c['key_words']:
   for i in range(32):
    q=word>>(8*i)&255;real.append((q-256 if q>=128 else q,word>>256))
 pairs+=real;assert len(real)==5120
 xs=[(q&255)+(m<<8) for q,m in pairs];ys=[int(g.value_dequant(q,m))&1048575 for q,m in pairs]
 nets={'old':kv.operator(),'array_fold':operator(False),'booth_fold':operator(),
       'output_fault':flip_output(operator()),'missing_round_bias':operator(bias=0),'missing_saturation':operator(saturate=False)}
 results={}
 for label,net in nets.items():
  assert metrics(net)['nNand']+net.n_state<=4000
  wrong=sum(a!=b for a,b in zip(simulate(net,xs),ys))
  assert wrong==0 if label in ('old','array_fold','booth_fold') else wrong>0,label
  results[label]=dict(metrics=metrics(net),mismatches=wrong);print('small',label,len(xs),wrong,flush=True)
 (OUT/'dequant.vectors.json').write_text(json.dumps(dict(input=xs,expected=ys))+'\n')
 for label,net in [('old',nets['old']),('new',nets['booth_fold']),('negative',nets['output_fault'])]:
  (OUT/('dequant.'+label+'.nl')).write_bytes(net.encode());(OUT/('dequant.'+label+'.blif')).write_text(blif(net))
 assert len(set((q*m)%127 for q,m in pairs))==127
 return dict(cases=len(xs),code_coverage=len(set(q for q,m in pairs)),remainder_coverage=127,real_operands=len(real),results=results,vectors_sha256=sha((OUT/'dequant.vectors.json').read_bytes()),
  product_range=[-128*((1<<20)-1),127*((1<<20)-1)],fold_range=[0,507],
  identity='n=q*m; s=d0+d1+d2+d3+63; R(n,127)=(n>>7)+(n>>14)+(n>>21)+floor(s/127), then sat20; d3 signed',
  widths=dict(product=28,fold_sum=9,fold_correction=2,quotient=22,output=20))


def make():
 original=kv.operator
 try:
  kv.operator=operator
  return previous.make()
 finally:kv.operator=original


def reference():
 original=kv.operator
 try:
  kv.operator=operator
  return previous.reference()
 finally:kv.operator=original


def exhaustive(full=False):
 # Four bounded local windows validate the 64-lane interpreter. Only Actions
 # may enumerate the full 2^28 domain of the actual canonical NAND bytes.
 if full:assert os.getenv('GITHUB_ACTIONS')=='true'
 exe=OUT/'dequant_exhaust'
 subprocess.run(['cc','-O3','-std=c99','-Wall','-Wextra','-Werror',str(R/'integer_opt/dequant_exhaust.c'),'-o',str(exe)],check=True,timeout=30)
 windows=[(0,1<<28)] if full else [(x,4096) for x in (0,1<<20,1<<27,(1<<28)-4096)]
 results=[];begin=time.monotonic()
 for first,count in windows:
  label='all' if full else str(first);log=OUT/('dequant.exhaust.'+label+'.log')
  with log.open('w') as f:
   subprocess.run([str(exe)]+[str(OUT/('dequant.'+n+'.nl')) for n in ('old','new','negative')]+[str(first),str(count)],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=900 if full else 30)
  r=json.loads(log.read_text().splitlines()[-1]);assert r['status']=='pass' and r['input_count']==count and r['first_input']==first
  assert r['old_new_mismatches']==r['frozen_C_mismatches']==0 and r['actual_output_gate_mutation_mismatches']==64
  results.append(r)
 report=dict(status='pass',full_domain=full,method='exhaustive64lane canonical NAND interpreter against old graph and frozen C',windows=results)
 if full:report['seconds']=round(time.monotonic()-begin,3)
 (OUT/('dequant.exhaustive.json' if full else 'dequant.prefix_check.json')).write_text(json.dumps(report,indent=2)+'\n')
 return report


def source_paths():
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer_opt/dequant_exhaust.c','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','physical/golden_slice.c','docs/index.html',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/exp.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','integer_opt/score_units/manifest.json','integer_opt/score_units/score.nl']:paths.add(R/n)
 return paths


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--stage',choices=('all','leaf','head'),default='all');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true' and args.stage=='all'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);packed.OUT=OUT;previous.OUT=OUT
 vg,sg,dg,cases=packed.golden()
 leaf=OUT/'leaf.prepared.json'
 paths=source_paths();stamp={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)}
 case_sha=sha((OUT/'cases.json').read_bytes())
 if args.stage=='head':
  cached=json.loads(leaf.read_text());assert cached['sources']==stamp and cached['cases_sha256']==case_sha
  sm=cached['small'];score_contract=cached['score_contract'];prefix=cached['exhaustive_prefix_checks']
 else:
  sm=small(vg,cases);score_contract=previous.prepare_contracts();prefix=exhaustive()
  leaf.write_text(json.dumps(dict(small=sm,score_contract=score_contract,exhaustive_prefix_checks=prefix,sources=stamp,cases_sha256=case_sha),indent=2)+'\n')
  if args.stage=='leaf':print('exact leaf preparation complete; run --stage head next');return
 print('small complete',flush=True)
 rows,expected=packed.vectors(vg,sg,dg,cases);print('full C schedule complete',len(rows),flush=True);net,comb,_=make();prior=previous.make()[0]
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 assert sha((OUT/'vectors.txt').read_bytes())=='2fa700ebc62ae901d9f27f0ceab3cc4a48b14589ee8608938ded60c00350e9a4'
 assert sha((OUT/'cases.json').read_bytes())=='289e63b5ab029884966a9dbe2b1b37efd3f4569ada0267a60069efa508d388e5'
 report=dict(status='small exact folded dequantization and unchanged complete C head pass; cloud pending',metrics=metrics(net),previous=metrics(prior),
  small=sm,score_contract=score_contract,exhaustive_prefix_checks=prefix,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,
  scope='R69 head with one shared exact KV dequantizer replaced; same input/state/output protocol and schedule; not whole-model or physical signoff')
 paths=source_paths()
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['score_spec_proof']=previous.prove_score(OUT)
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
  report['dequant_exhaustive_proof']=exhaustive(True)
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
  base.OUT=OUT;base.reference=reference;base.NI=packed.NI;base.NO=packed.NO
  report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='score all45-input six-cut integer-spec proof with exact DAG binding and no-overflow bounds; dequant exhaustive all2^28 actual NAND inputs against old graph and frozen C; then allD/output CEC with proven replacements and original EXP/DIV boundaries; not monolithic dequant CEC or unbounded full-model reachability'
  report['status']='exact score specification proof, exhaustive dequant proof and complete NAND/RTL/C head pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print('clocks',expected['clocks'],'sources',len(paths))


if __name__=='__main__':main()
