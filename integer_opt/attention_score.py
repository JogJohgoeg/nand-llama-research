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
from nand import Builder,metrics,simulate,flip_output,blif,from_yosys
from export import rtl,import_net
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


def contracts():
 # Each cut has arbitrary input bits; truncating additions are modulo 2^w.
 defs=[('round12',45,34,lambda b,v:rounded(b,v,12,34)),
  ('five',34,36,lambda b,v:b.add(extend(v,36),[0]*2+v)[0]),
  ('fortyfive',36,39,lambda b,v:b.add(extend(v,39),[0]*3+v)[0]),
  ('oneeightyone',73,41,lambda b,v:b.add(extend(v[39:],41),[0]*2+v[:39])[0]),
  ('product',77,49,lambda b,v:b.add(extend(v[41:],49),[0]*8+v[:41])[0]),
  ('round18',49,32,lambda b,v:rounded(b,v,18,32))]
 refs={
  'five':"wire signed [35:0] x={{2{din[33]}},din}; assign dout=x+(x<<<2);",
  'fortyfive':"wire signed [38:0] f={{3{din[35]}},din}; assign dout=f+(f<<<3);",
  'oneeightyone':"wire signed [40:0] g={{2{din[38]}},din[38:0]},q={{7{din[72]}},din[72:39]}; assign dout=(g<<<2)+q;",
  'product':"wire signed [48:0] h={{8{din[40]}},din[40:0]},f={{13{din[76]}},din[76:41]}; assign dout=(h<<<8)+f;"}
 result=[]
 for name,ni,no,fn in defs:
  b=Builder(ni);net=b.finish(fn(b,list(range(2,ni+2))))
  if name.startswith('round'):
   shift=int(name[5:])
   # Independent symmetric-magnitude formulation, not floor+sticky logic.
   body=f'''wire signed [{ni}:0] x={{din[{ni-1}],din}};
wire [{ni}:0] magnitude=x<0 ? -x : x;
wire [{no-1}:0] quotient=magnitude>>{shift};
wire [{shift-1}:0] remainder=magnitude[{shift-1}:0];
wire increment=(remainder>{shift}'d{1<<(shift-1)}) || (remainder=={shift}'d{1<<(shift-1)} && quotient[0]);
wire [{no-1}:0] rounded_magnitude=quotient+increment;
assign dout=x<0 ? -rounded_magnitude : rounded_magnitude;'''
  else:body=refs[name]
  ref=f'module top(input [{ni-1}:0] din,output [{no-1}:0] dout);\n'+body+'\nendmodule\n'
  result.append((name,net,ref))
 return result


def prepare_contracts():
 units={name:net for name,net,_ in contracts()};b=Builder(45)
 def apply(name,inputs):return import_net(b,units[name],inputs)[1]
 q=apply('round12',list(range(2,47)));f=apply('five',q);g=apply('fortyfive',f)
 h=apply('oneeightyone',g+q);p=apply('product',h+f);out=apply('round18',p)
 stitched=b.finish(out);actual=score()
 # finish() prunes and copies outputs, so stage boundaries reorder wire IDs.
 # Intern both DAGs together using only exact NAND identities (constants,
 # commuted inputs, double inversion); compare actual output wire IDs, no SAT
 # or sampling. This binds every cut to the delivered graph without assuming
 # that separately numbered, byte-different DAGs have equivalent functions.
 canonical=Builder(45);inputs=list(range(2,47))
 actual_out=import_net(canonical,actual,inputs)[1]
 assert actual_out==import_net(canonical,stitched,inputs)[1], 'cut DAG does not reconstruct actual score'
 assert actual_out!=import_net(canonical,flip_output(actual),inputs)[1], 'actual output mutation escaped binding'
 coeff={'q':1,'f':5,'g':9*5,'h':4*9*5+1,'p':256*(4*9*5+1)+5};assert coeff['p']==46341
 widths={'q':34,'f':36,'g':39,'h':41,'p':49}
 bounds={k:(1<<32)*v for k,v in coeff.items()}
 assert all(bounds[k]<(1<<(widths[k]-1)) for k in widths)
 output_bound=(bounds['p']+(1<<17))>>18;assert output_bound<(1<<31)
 rng=random.Random(260775);checks={}
 def signed(x,w):return (x&((1<<w)-1))-(1<<w) if x>>(w-1)&1 else x&((1<<w)-1)
 def rne(x,d):
  q,r=divmod(abs(x),d);q+=int(2*r>d or 2*r==d and q&1);return -q if x<0 else q
 for name,net,ref in contracts():
  n,w=net.n_in,net.n_out;xs=[0,(1<<n)-1,1<<(n-1),(1<<(n-1))-1]+[rng.getrandbits(n) for _ in range(252)]
  if name=='round12':ys=[rne(signed(x,n),4096) for x in xs]
  elif name=='round18':ys=[rne(signed(x,n),262144) for x in xs]
  elif name=='five':ys=[5*signed(x,n) for x in xs]
  elif name=='fortyfive':ys=[9*signed(x,n) for x in xs]
  elif name=='oneeightyone':ys=[4*signed(x,39)+signed(x>>39,34) for x in xs]
  else:ys=[256*signed(x,41)+signed(x>>41,36) for x in xs]
  ys=[v&((1<<w)-1) for v in ys];assert metrics(net)['nNand']<=4000 and simulate(net,xs)==ys
  bad=sum(a!=c for a,c in zip(simulate(flip_output(net),xs),ys));assert bad==len(xs)
  prefix=OUT/('score.stage.'+name)
  prefix.with_suffix(prefix.suffix+'.blif').write_text(blif(net))
  prefix.with_suffix(prefix.suffix+'.negative.blif').write_text(blif(flip_output(net)))
  prefix.with_suffix(prefix.suffix+'.ref.v').write_text(ref)
  checks[name]=dict(metrics=metrics(net),cases=len(xs),actual_gate_fault_mismatches=bad)
 return dict(method='six universal stage CEC obligations plus exact structural reassembly and integer range/coefficient certificate',
  stitched_sha256=sha(stitched.encode()),actual_sha256=sha(actual.encode()),
  structural_rules=['NAND input commutation and sharing','constant NAND identities','double inversion'],
  actual_graph_matches_reassembly=True,actual_graph_mutation_rejected=True,
  coefficients=coeff,absolute_bounds=bounds,signed_widths=widths,output_absolute_bound=output_bound,checks=checks,
  scope='integer-spec proof for all signed45 inputs; not a claim that monolithic old-v-new ABC CEC passed')


def prove_score(directory):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 import subprocess
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 report=dict(status='in_progress',stages={})
 for name,net,_ in contracts():
  stem=directory/('score.stage.'+name)
  ys=Path(str(stem)+'.ys');ref=Path(str(stem)+'.ref.json')
  ys.write_text(f'read_verilog {stem}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {ref}\n')
  subprocess.run(['yosys','-Q','-T','-l',str(stem)+'.yosys.log','-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=60)
  rn=from_yosys(json.loads(ref.read_text()),net.n_in,net.n_out);rb=Path(str(stem)+'.ref.blif');rb.write_text(blif(rn))
  good=cec(abc,Path(str(stem)+'.blif'),rb,Path(str(stem)+'.cec.log'));assert good['verdict']=='equivalent',(name,good)
  bad=cec(abc,Path(str(stem)+'.negative.blif'),rb,Path(str(stem)+'.negative.log'));assert bad['verdict']=='different',(name,bad)
  report['stages'][name]=dict(all_input_cec=good,actual_gate_mutation=bad)
  (directory/'score.proof.json').write_text(json.dumps(report,indent=2)+'\n')
 report['status']='pass';(directory/'score.proof.json').write_text(json.dumps(report,indent=2)+'\n');return report


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
 # Six universal cut proofs, exact DAG binding and no-overflow bounds prove
 # this leaf against the integer specification. Composition uses that leaf.
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
 dots=json.loads((OUT/'kv/pairs/dot/cases.json').read_text());sm=small(dg,dots);contract=prepare_contracts()
 rows,expected=previous.vectors(vg,sg,dg,cases)
 net,comb,_=make();prior=previous.make()[0]
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 assert sha((OUT/'vectors.txt').read_bytes())=='2fa700ebc62ae901d9f27f0ceab3cc4a48b14589ee8608938ded60c00350e9a4'
 assert sha((OUT/'cases.json').read_bytes())=='289e63b5ab029884966a9dbe2b1b37efd3f4569ada0267a60069efa508d388e5'
 report=dict(status='small exact score and unchanged full-head C schedule pass; full cloud pending',metrics=metrics(net),previous=metrics(prior),
  small=sm,score_contract=contract,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
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
  report['score_spec_proof']=prove_score(OUT)
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
  base.OUT=OUT;base.reference=reference;base.NI=previous.NI;base.NO=previous.NO
  report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='score all45-input integer-spec proof through six universal CEC cuts, exact structural reassembly and no-overflow bounds; allD/output head composition with proven score and original EXP/DIV/dequantizer; not monolithic old-leaf CEC or unbounded reachability'
  report['status']='score compositional integer-spec proof and complete NAND/RTL/C head pass with actual faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2));print('clocks',expected['clocks'],'sources',len(paths))


if __name__=='__main__':main()
