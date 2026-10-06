#!/usr/bin/env python3
"""Share the two mutually exclusive H scalar read selectors.

Exact frozen v1.1 values, state capacity and scheduling are unchanged.
Local execution constructs graphs and checks only <=4k-gate components.
Actions proves all D/output bits under explicit read ownership, monitors
that condition throughout C fixtures, then checks actual NAND and RTL.
This is not an unbounded sequential proof.
"""
from pathlib import Path
import sys,json,signal,hashlib,random,os,argparse,shutil
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import h_halfword as source
from nand import Builder,metrics,with_state,blif
from prefix_store import select
from bench import verify
sha=lambda b:hashlib.sha256(b).hexdigest()

def port(b,head,qindex,rindex,owner):
 index=[b.mux(owner,q,r) for q,r in zip(qindex,rindex)]
 return select(b,[head[20*j:20*(j+1)] for j in range(16)],index)

def guard(b,phase,owner):
 def eq(n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(phase)],b.land,1)
 return b.inv(b.land(owner,b.lor(eq(1),eq(2))))

def small_guard():
 b=Builder(4);net=b.finish([guard(b,list(range(2,5)),5)])
 assert metrics(net)['nNand']<=4000
 return dict(metrics=metrics(net),verification=verify(net,list(range(16)),[int(not(x>>3) or (x&7) not in (1,2)) for x in range(16)]))

def small():
 b=Builder(329);pins=list(range(2,331));h=pins[:320];q=pins[320:324];r=pins[324:328];owner=pins[328]
 read=port(b,h,q,r,owner)
 out=[b.land(b.inv(owner),w) for w in read]+[b.land(owner,w) for w in read]
 net=b.finish(out);rng=random.Random(260709);xs=[];ys=[]
 for j in range(4096):
  h=[rng.getrandbits(20) for _ in range(16)] if j>=512 else [((k*39451)^j)&1048575 for k in range(16)]
  q=j&15;r=j>>4&15;o=j>>8&1
  x=sum(v<<(20*k) for k,v in enumerate(h))+(q<<320)+(r<<324)+(o<<328)
  y=h[r]<<20 if o else h[q];xs.append(x);ys.append(y)
 assert metrics(net)['nNand']<=4000
 proof=verify(net,xs,ys)
 return dict(metrics=metrics(net),verification=proof)

def make():
 net=source.make()[0];assert sha(net.encode())=='a336b48e394dc6af1b470046d69a5ef0601f903d4e7c060c838516ef8c0bcf37'
 ns=net.n_state;ni=net.n_in
 a=Builder(ns+ni);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+ni))
 before_d,before_out,wires,_=source.source.import_with_wires(a,net,pins,old)
 roots=select(a,[old[3355+20*j:3375+20*j] for j in range(16)],old[3330:3334])
 roots+=select(a,[old[3355+20*j:3375+20*j] for j in range(16)],old[8711:8715])
 found=[[i for i,w in enumerate(wires) if w==root] for root in roots];assert all(found)
 owner_a=a.land(a.inv(old[8709]),old[8710]);care_a=guard(a,old[3339:3342],owner_a)
 reference=a.finish([a.land(care_a,v) for v in before_d+before_out])
 b=Builder(ns+ni);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+ni))
 owner=b.land(b.inv(old[8709]),old[8710])
 read=port(b,old[3355:3675],old[3330:3334],old[8711:8715],owner)
 replacements={w:read[j%20] for j,indices in enumerate(found) for w in indices}
 ds,out,_,changed=source.source.import_with_wires(b,net,pins,old,replacements)
 result=with_state(b.finish(ds+out),ns)
 care=guard(b,old[3339:3342],owner)
 candidate=b.finish([b.land(care,v) for v in ds+out])
 negative=b.finish([b.land(care,b.inv(v) if i==0 else v) for i,v in enumerate(ds+out)])
 return result,reference,candidate,negative,dict(before=metrics(net),read_root_wires=found,replaced_wires=changed,
  owner='parent phase==2 owns residual read; otherwise H quantizer read',
  care='residual owner implies H quantizer phase is not scan(1) or receive(2)',
  proof_scope='all8719 next-state bits and32 public outputs under explicit care; ownership is checked in sequences, not proved unbounded')

OUT=R/'build/integer_opt/h_read_shared'


def prove(reference,candidate,negative):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 for name,net in [('reference',reference),('candidate',candidate),('negative',negative)]:
  (OUT/(name+'.blif')).write_text(blif(net))
 good=cec(abc,OUT/'candidate.blif',OUT/'reference.blif',OUT/'cec.log')
 assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log')
 assert bad['verdict']=='different'
 return dict(scope='all8719 next-state bits and32 outputs for all inputs/old states satisfying explicit read ownership; not reachability',
  care='parent residual phase2 implies H quantizer phase not scan1/receive2',proof=good,negative=bad,
  mutation='invert actual first D bit before applying the ownership mask')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True)
 sm=small();sg=small_guard();net,ref,candidate,negative,parts=make()
 common=source.source.source.source;common.OUT=OUT;fixtures=common.cases(common.reference())
 source.source.OUT=OUT;ffcodes=source.source.c_bank_cases(fixtures)
 source.OUT=OUT;hcodes=source.h_cases(fixtures)
 from export import rtl
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_sublayer'))
 for name,data in [('cases',fixtures),('bank_cases',ffcodes),('h_cases',hcodes)]:
  (OUT/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
 expected={
  'cases.json':'e586e22a4dd3a2501781d75b3f790dff4454ec07b0e16a959525150eaa424074',
  'bank_cases.json':'bdf2bc5c437f2e4354ee113564b8f7f9b90508a9e62783a1a78afdcdcb5b6502',
  'h_cases.json':'249821383d8d5bcc0fa3548220745ef88d35134b9e69844f8576b21f4f1ba154'}
 for name,digest in expected.items():assert sha((OUT/name).read_bytes())==digest,name
 assert metrics(net)['sha256']=='5c75b1aca14e6f96c8250724fd55f8da9745c073c077bf8612152c7b11e87f96'
 report=dict(status='small read-port/ownership gates and independent C cases pass; large graph constructed only',
  metrics=metrics(net),parts=parts,small_read=sm,small_guard=sg,numerical_contract_changed=False,
  refinement_metrics=dict(reference=metrics(ref),candidate=metrics(candidate),negative=metrics(negative)),
  fixtures_sha256=expected['cases.json'],bank_cases_sha256=expected['bank_cases.json'],h_cases_sha256=expected['h_cases.json'],
  expected_schedule='unchanged R47 vectors and212701 clocks to first no-stall publication; pending full cloud verification',
  tradeoff='one16-to-1 s20 read selected by parent ownership; no new state, cycles or precision loss')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   path=Path(name).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/weights_golden.c',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl','physical/units/resid.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/name)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['refinement_proof']=prove(ref,candidate,negative)
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
  report['verification']=common.check(net,fixtures,scalar_x=True,ff_codes=ffcodes,h_codes=hcodes,shared_h_read=True)
  observed=report['verification']['observed']
  assert observed['counts']['h_read_owner_checks']==observed['clocks']==2362346
  assert observed['vector_sha256']=='5ea387ad71b85ca4fc248179d9c6857daa8c0cf206459086a71b0365dd711910'
  report['status']=report['verification']['status']
  report['expected_schedule']='R47 vectors identical; no added cycles or changed output'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','parts')},indent=2))


if __name__=='__main__':main()
