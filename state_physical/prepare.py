#!/usr/bin/env python3
"""Held64x20 prefix-state leaf for a free-runner physical measurement.

This is one of32 width slices, with a shared external cursor. It does not
change any model value or ring timing. Only the4x8 bank is simulated locally;
full NAND/RTL/CEC and all EDA run exclusively on GitHub Actions.
"""
from pathlib import Path
import sys,ctypes as ct,subprocess,json,hashlib,random,signal,os,argparse,importlib.util
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from ports import make as bank,reference as bank_reference,cases as bank_cases
from nand import metrics,verify_state,flip_output
from export import rtl
OUT=R/'build/state_physical';NAME='int_c16_prefix_leaf';NI=22;NO=20
sha=lambda b:hashlib.sha256(b).hexdigest()
GOLDEN_SHA='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
MODEL_SHA='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'


def small():
 comb,net=bank('ring',4,8);assert metrics(net)['nNand']+metrics(net)['nLatch']<=4000
 xs,ys=bank_cases('ring',4,8);r=verify_state(comb,xs,ys,32);r.pop('nl_hex');return r


def reference():
 assert sha((R/'integer/int_model.c').read_bytes())==GOLDEN_SHA
 text='#include '+json.dumps(str(R/'integer/int_model.c'))+'\n'+r'''
/* Addressed C oracle, independent of the physical shift-register graph. */
static uint32_t leaf_words[64];
static uint64_t leaf_valid;
static unsigned leaf_at;
void leaf_begin(void) {leaf_at=0;leaf_valid=0;}
unsigned leaf_cursor(void) {return leaf_at;}
unsigned leaf_known(void) {return (unsigned)(leaf_valid>>leaf_at)&1;}
uint32_t leaf_head(void) {return leaf_words[leaf_at];}
void leaf_tick(uint32_t data,int advance,int write) {
    if(advance) {
        if(write) {leaf_words[leaf_at]=data&1048575U;leaf_valid|=(uint64_t)1<<leaf_at;}
        leaf_at=(leaf_at+1)&63;
    }
}
'''
 p=OUT/'reference.c';p.write_text(text)
 so=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(so)],check=True,timeout=30)
 lib=ct.CDLL(str(so));blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
 lib.int_init.argtypes=[ct.c_void_p,ct.c_int];assert lib.int_init(blob,len(blob))==0
 ptr=ct.POINTER(ct.c_int32);lib.int_run.argtypes=[ptr,ct.c_int,ptr,ptr]
 lib.leaf_head.restype=ct.c_uint32;lib.leaf_cursor.restype=ct.c_uint;lib.leaf_known.restype=ct.c_uint
 lib.leaf_tick.argtypes=[ct.c_uint32,ct.c_int,ct.c_int]
 return lib


def snapshots(c):
 f=json.loads((Path(__file__).with_name('fixture.json')).read_text())
 ids=f['ids'];assert len(ids)==16
 out=(ct.c_int32*(16*192))();trace=(ct.c_int32*(6*16*128))()
 assert c.int_run((ct.c_int32*16)(*ids),16,out,trace)==0
 encode=lambda xs:b''.join(int(x).to_bytes(4,'little',signed=True) for x in xs)
 assert sha(encode(out))==f['logit_sha256'] and sha(encode(trace))==f['trace_sha256']
 assert all(-524288<=v<=524287 for v in trace)
 cases=[]
 for snapshot in range(6):
  for lane in range(32):
   values=[trace[(snapshot*16+p)*128+chunk*32+lane] for p in range(16) for chunk in range(4)]
   cases.append(dict(snapshot=snapshot,lane=lane,values=values))
 assert len(cases)==192
 return cases,f


def vectors(c,cases):
 c.leaf_begin();rng=random.Random(260712);rows=[];counts=dict(writes=0,reads=0,seeks=0,holds=0,masked_uninitialized=0)
 def tick(data=0,advance=0,write=0,kind='hold'):
  known=c.leaf_known();want=c.leaf_head() if known else 0;mask=1048575 if known else 0
  value=(data&1048575)+(advance<<20)+(write<<21);rows.append((value,want,mask))
  if not known:counts['masked_uninitialized']+=1
  counts['writes' if advance and write else 'seeks' if advance else 'holds']+=1
  if kind=='read':counts['reads']+=1
  c.leaf_tick(data&1048575,advance,write)
 def seek(addr):
  while c.leaf_cursor()!=addr:tick(advance=1)
 def epoch(values):
  assert len(values)==64
  seek(0)
  for value in values:tick(data=value,advance=1,write=1)
  assert c.leaf_cursor()==0
  for addr in rng.sample(list(range(64)),8)+[0,1,63]:
   seek(addr);assert c.leaf_known() and c.leaf_head()==(values[addr]&1048575)
   tick(kind='read')
   # Write pins while stopped cannot change a held row.
   tick(data=c.leaf_head()^1048575,write=1);tick(kind='read')
 for f in cases:epoch(f['values'])
 for values in [[0]*64,[-524288]*64,[524287]*64,[1<<j if j<19 else -(1<<19) for j in range(20) for _ in range(3)]+[0,1,-1,524287]]:
  epoch(values)
 tick();assert counts['writes']==196*64 and counts['masked_uninitialized']>=64
 return rows,dict(clocks=len(rows),counts=counts,model_snapshots=6,model_lanes=32,complete_64word_epochs=196,
  scope='all32 lane slices of all6 C16 embedding/layer prefix snapshots plus signed20 boundaries; every epoch fills before read',
  protocol='din[19:0] signed20 bits,advance20,write21; write takes effect only while advancing; no reset/data clearing, external shared cursor')


def check_cloud(net,comb,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 spec=importlib.util.spec_from_file_location('matrix_proofs',R/'matrix_physical/prepare.py')
 proofs=importlib.util.module_from_spec(spec);spec.loader.exec_module(proofs);proofs.OUT=OUT
 proof=proofs.prove('bank',comb,bank_reference('ring',64,20))
 import verify as checks
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
 assert checks.check_nand(rows,net.encode())==0
 bad=flip_output(net);negative=checks.check_nand(rows,bad.encode());assert negative>0
 (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,NAME))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,NAME,str(OUT/'vectors.txt')))
 binary=checks.compile_rtl('source',OUT/'slice.v');checks.run([binary],300)
 binary=checks.compile_rtl('negative',OUT/'bad.v')
 result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(result.stdout+result.stderr)
 assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
 return dict(status='pass',bank_proof=proof,nand_mismatches=0,clocks=len(rows),rtl_clocks=len(rows),
  negative_nand_mismatches=negative,actual_rtl_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True)
 sm=small();comb,net=bank('ring',64,20);c=reference();cases,fixture=snapshots(c);rows,expected=vectors(c,cases)
 assert net.n_in==NI and net.n_out==NO and net.n_state==1280
 (OUT/'slice.nl').write_bytes(net.encode());(OUT/'slice.v').write_text(rtl(net,NAME))
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 (OUT/'cases.json').write_text(json.dumps(cases,indent=2)+'\n')
 assert sha(net.encode())=='c93d731f5fd11d3a79962c0a4e2597c2fdd4131d5d3e3fdbb6a0554ff2b09a51'
 assert sha((OUT/'vectors.txt').read_bytes())=='6e5a187ae8e0163a368cd6a6f953fe033deaea9eb1cd538a972e024efd7f728d'
 report=dict(status='small actual-state checks and frozen C traces pass; full graph constructed only',
  metrics=metrics(net),small=sm,fixture=fixture,expected=expected,numerical_contract_changed=False,
  parts=dict(rows=64,width=20,state_bits=1280,replicas_for_C16_prefix=32,shared_external_cursor_bits=6,
   scope='one exact held64x20 width slice of the C16 prefix bank; not full prefix controller or language model'),
  model_sha256=MODEL_SHA,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()))
 paths={Path(__file__).resolve(),Path(__file__).with_name('fixture.json').resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','matrix_physical/prepare.py']:
  paths.add(R/name)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['verification']=check_cloud(net,comb,rows);report['status']='full source NAND/RTL/C, bank CEC and actual mutations pass'
  (OUT/'source.json').write_text(json.dumps(dict(design=NAME,metrics=report['metrics'],parts=report['parts'],
   model_sha256=MODEL_SHA,status=report['status'],clocks=len(rows)),indent=2)+'\n')
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))


if __name__=='__main__':main()
