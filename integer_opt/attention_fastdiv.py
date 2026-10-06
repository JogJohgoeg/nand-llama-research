#!/usr/bin/env python3
"""R71's exact s42/u22 divider inside the complete R70 attention head.

An explicit legacy-shape adapter keeps the existing hierarchy reviewable.
The outer composition removes every adapter-only bit and the old count bit.
No hidden copy of old division state remains; model numerical rules stay v1.1.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse,inspect,contextlib,subprocess,shutil
R=Path(os.environ.get('H3_FASTDIV_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import attention_dequant as previous
import attention_packed as packed
import attention_divider as compact
import value_normalize as norm
import value_denominator as den
import value_row as base
from nand import Builder,metrics,with_state,simulate,flip_output,blif,from_yosys
from export import import_net,rtl
sha=lambda x:hashlib.sha256(x).hexdigest()
OUT=Path(os.environ.get('H3_FASTDIV_OUT',str(R/'build/integer_opt/attention_fastdiv')))
NI,NO=packed.NI,packed.NO
FIRST=norm.CORE_NS-727
DROP=set(range(FIRST+compact.DS,FIRST+115))|{FIRST+115+8}
KEEP=[i for i in range(packed.NS) if i not in DROP]
NS=len(KEEP)
assert (FIRST,NS,len(DROP))==(11236,12060,29)


def compatible_divider():
 b=Builder(115+90);s=list(range(2,117));p=list(range(117,207))
 dd,y=import_net(b,compact.raw()[0],[p[0]]+p[1:43]+p[65:87],s[:87])
 return with_state(b.finish(dd+[0]*28+[0]*179+y),115)


def altered(fn,before,after):
 s=inspect.getsource(fn);assert s.count(before)==1
 env=dict(fn.__globals__);exec(compile(s.replace(before,after),'<42-step attention control>','exec'),env)
 return env[fn.__name__]


control42=altered(norm.control,'eq(count,64)','eq(count,42)')
step42=altered(norm.ctl_step,'c==64','c==42')


@contextlib.contextmanager
def replacement():
 # value_denominator generated its independent Model.tick using a copied
 # namespace. Update that exact copied reference as well, then restore both.
 e=den.Model.tick.__globals__;saved=(norm.divider,norm.control,norm.ctl_step,e['ctl_step'])
 try:
  norm.divider=compatible_divider;norm.control=control42;norm.ctl_step=step42;e['ctl_step']=step42
  yield
 finally:norm.divider,norm.control,norm.ctl_step,e['ctl_step']=saved


def virtual(old):
 it=iter(old);out=[0 if i in DROP else next(it) for i in range(packed.NS)]
 assert len(old)==NS
 return out


def make():
 with replacement():prior=previous.make()[0]
 assert prior.n_state==packed.NS
 b=Builder(NS+NI);s=list(range(2,2+NS));p=list(range(2+NS,2+NS+NI))
 dd,y=import_net(b,prior,p,virtual(s));comb=b.finish([dd[i] for i in KEEP]+y)
 return with_state(comb,NS),comb


def reference():
 with replacement():s=previous.reference()
 assert s.count('count==64')==1;s=s.replace('count==64','count==42')
 s=s.replace('module top(','module old_shape_ref(',1)
 # Explicit bit map rather than relying on adjacent reversed slice arithmetic.
 state=[];j=0
 for i in range(packed.NS):
  state.append("1'b0" if i in DROP else f'din[{j}]')
  if i not in DROP:j+=1
 assert j==NS
 s+=f'module top(input [{NS+NI-1}:0] din,output [{NS+NO-1}:0] dout);\n'
 s+=f'wire [{packed.NS-1}:0] expanded={{'+','.join(reversed(state))+'};\n'
 s+=f'wire [{packed.NS+NO-1}:0] full;old_shape_ref core({{din[{NS+NI-1}:{NS}],expanded}},full);\n'
 s+=f'assign dout[0 +: {NS}]={{'+','.join(f'full[{i}]' for i in reversed(KEEP))+'};\n'
 s+=f'assign dout[{NS+NO-1}:{NS}]=full[{packed.NS+NO-1}:{packed.NS}];\nendmodule\n'
 return s


def small():
 # The same controller is checked over all 512 states and all eight inputs.
 b=Builder(12);d,a=control42(b,list(range(2,11)),list(range(11,14)));net=b.finish(d+a)
 xs=list(range(4096));ys=[]
 for x in xs:
  d,a=step42(x&511,x>>9);ys.append(d+(a<<9))
 assert metrics(net)['nNand']<=4000 and simulate(net,xs)==ys
 wrong=sum(x!=y for x,y in zip(simulate(flip_output(net),xs),ys));assert wrong==4096
 # Tied zero count bit is closed under 0..42 running states. Load/finish/reset
 # clear it; waiting/held result states never increment it.
 for state in range(3):
  for count in range(43):
   for p in range(8):assert step42(state+(count<<2),p)[0]>>8==0
 raw,comb=compact.raw();(OUT/'divider.blif').write_text(blif(comb))
 (OUT/'divider.negative.blif').write_text(blif(flip_output(comb)))
 (OUT/'divider.ref.v').write_text(compact.reference(True))
 return dict(control=metrics(net),control_transitions=4096,actual_D_mutation_mismatches=wrong,
  divider=metrics(raw),arbitrary_divider=compact.arbitrary(),removed_state_indices=sorted(DROP),
  no_shadow_divider_state=True,scope='raw divider same exact R71 bytes; count high-bit invariant after reset/load; full composition pending cloud')


def prove_divider():
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 y=OUT/'divider.ys';rp=OUT/'divider.ref.json'
 y.write_text(f'read_verilog {OUT}/divider.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {rp}\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'divider.yosys.log'),'-s',str(y)],stdout=subprocess.DEVNULL,check=True,timeout=60)
 comb=compact.raw()[1];ref=from_yosys(json.loads(rp.read_text()),comb.n_in,comb.n_out)
 (OUT/'divider.ref.blif').write_text(blif(ref))
 good=cec(abc,OUT/'divider.blif',OUT/'divider.ref.blif',OUT/'divider.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'divider.negative.blif',OUT/'divider.ref.blif',OUT/'divider.negative.log');assert bad['verdict']=='different'
 return dict(status='pass',all_raw_state_output_cec=good,actual_D_mutation=bad,raw_sha256=metrics(compact.raw()[0])['sha256'])


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);packed.OUT=OUT;previous.OUT=OUT;previous.previous.OUT=OUT
 leaves=dict(score=sha(previous.previous.score().encode()),dequant=sha(previous.operator().encode()),divider=sha(compact.raw()[0].encode()))
 assert leaves==dict(score='b2966bf760babbf33b11224862e89f1cf092bfe94e43cb624b8e4a1df3ca3ffd',dequant='230e0aa345a8462e150c6248e2cfed1bd2c5155878a01eb143fddfe5743108ca',divider='e70d8eb79a05612b441a4da57ea20be2e0ce7b09891fb4f4a843b3d8063b7ab7')
 sm=small();vg,sg,dg,cases=packed.golden()
 with replacement():rows,expected=packed.vectors(vg,sg,dg,cases)
 print('C schedule',len(rows),flush=True);net,comb=make();prior=previous.make()[0]
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 assert sha((OUT/'cases.json').read_bytes())=='289e63b5ab029884966a9dbe2b1b37efd3f4569ada0267a60069efa508d388e5'
 result_values=[call['values'] for call in expected['calls']]
 assert sha(json.dumps(result_values).encode())=='fda5109817d887dcf00965567e88364f613b9c88003939f72b180db1bbf23eb2'
 # These are independent C-normalized results, not the old timing waveform.
 report=dict(status='local small divider/control and complete C schedule pass; full gates pending Actions',metrics=metrics(net),previous=metrics(prior),small=sm,expected=expected,proved_leaf_sha256=leaves,
  results_sha256=sha(json.dumps(result_values).encode()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,
  scope='R70 full preloaded head with R71 raw divider and six-bit42-step count; exact outputs, changed timing; no whole-model sharing/physical signoff')
 paths=previous.source_paths()|{Path(__file__).resolve()}
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['divider_proof']=prove_divider()
  base.OUT=OUT;base.reference=reference;base.NI=NI;base.NO=NO
  report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='every raw divider D/output against independent RTL; all complete head D/output composition; complete frozen-C numerical replay; fixed exact score/dequant leaves inherited from R69/R70, no full-model unbounded reachability claim'
  report['status']='independent raw divider proof and all complete head NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2));print('sources',len(paths))
 print('true head clocks',[x['clocks'] for x in expected['calls'] if x['length']==16 and not x['stall']])


if __name__=='__main__':main()
