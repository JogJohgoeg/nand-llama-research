#!/usr/bin/env python3
"""Use the verified dual-stride Q candidate in the existing physical flow.

Original prepare.py remains the held-bank baseline. Layout settings, signoff,
post-route C vectors and automatic viewer publication use the same flow.
"""
from pathlib import Path
import os,sys,json,hashlib,shutil,re
R=Path(os.environ.get('H3_QRING_LAYOUT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
OUT=R/'build/matrix_physical'
SHA='ce3ace8a3c03629a70b459c4bf444d09a307280eff4b7fd909589463674182ff'
sha=lambda b:hashlib.sha256(b).hexdigest()


def stage_verified(source,target):
 assert source.resolve()!=target.resolve()
 r=json.loads((source/'receipt.json').read_text());v=r.get('verification',{})
 assert r['stride']=='word' and r['metrics']['sha256']==SHA
 assert (r['metrics']['nNand'],r['metrics']['nLatch'])==(20023,1505)
 assert v.get('status')=='pass' and v['bank_all_state_proof']['proof']['verdict']=='equivalent'
 assert v['bank_all_state_proof']['negative']['verdict']=='different'
 f=v['full'];assert f['status']=='pass' and f['clocks']==f['rtl_clocks']==r['expected']['clocks']==301045
 assert f['nand_mismatches']==0 and f['negative_nand_mismatches']>0 and f['actual_rtl_mutation_rejected']
 assert f['weight_proof']['proof']['verdict']=='equivalent' and f['weight_proof']['negative']['verdict']=='different'
 assert f['all_512_weight_words_match_C'] and v['actual_missing_rotation_mismatches']>0
 for name,key in [('slice.nl','sha256'),('vectors.txt','vector_sha256'),('cases.json','cases_sha256')]:
  expected=r['metrics'][key] if key=='sha256' else r[key]
  assert sha((source/name).read_bytes())==expected,name
 for name,h in r['sources'].items():assert sha((R/name).read_bytes())==h,name
 from golden import Netlist
 from export import rtl
 graph=Netlist.decode((source/'slice.nl').read_bytes(),33,32)
 assert (source/'slice.v').read_text()==rtl(graph,'int_c16_q_matrix'),'RTL does not encode the verified NAND graph'
 # The source job used the same testbench under its own vector path. Only the
 # file path changes for the mapped job's flattened downloaded artifact.
 import verify
 tb=verify.testbench(33,32,'int_c16_q_matrix',str(target/'vectors.txt'))
 old_tb=(source/'tb.v').read_text();paths=re.findall(r'file=\$fopen\("([^"\n]+)","r"\)',old_tb)
 assert len(paths)==1 and Path(paths[0]).name=='vectors.txt'
 previous=verify.testbench(33,32,'int_c16_q_matrix',paths[0]);assert old_tb==previous
 target.mkdir(parents=True,exist_ok=True)
 copied={}
 for p in sorted(source.iterdir()):
  if p.is_file() and p.suffix in ('.json','.c','.v','.nl','.txt','.log','.blif','.ys'):
   dest=target/p.name;shutil.copyfile(p,dest);assert dest.read_bytes()==p.read_bytes();copied[p.name]=sha(dest.read_bytes())
 (target/'tb.v').write_text(tb);copied['tb.v']=sha(tb.encode())
 model_sha=sha((R/'physical/model.bin').read_bytes())
 (target/'source.json').write_text(json.dumps(dict(design='int_c16_q_matrix',variant='R82 dual-stride A8 ring, true layer0 Q128x128',
  metrics=r['metrics'],model_sha256=model_sha,status='source NAND/RTL/C and full bank/weights proofs pass',clocks=301045,
  extra_cursor_bits=7,constant_bank_activity=True,whole_model=False),indent=2)+'\n')
 (target/'ring_adapter.json').write_text(json.dumps(dict(status='pass',source_receipt_sha256=sha((source/'receipt.json').read_bytes()),
  adapter_sha256=sha(Path(__file__).read_bytes()),copied=copied,source_vectors_path=paths[0],testbench_change='only vector path; full generator equality checked',
  config_sha256=sha((R/'matrix_physical/config.json').read_bytes()),constraints_sha256=sha((R/'physical/constraints.sdc').read_bytes())),indent=2)+'\n')
 return dict(status='pass',copied_files=len(copied),nand=20023,latch=1505,clocks=301045)


def main():
 assert os.getenv('GITHUB_ACTIONS')=='true','Full proof, gate simulation and layout stay on Actions'
 import qmatrix_ring
 qmatrix_ring.OUT=OUT/'ring_candidate'
 sys.argv=[sys.argv[0],'--cloud','--stride','word'];qmatrix_ring.main()
 print(json.dumps(stage_verified(qmatrix_ring.OUT,OUT),indent=2))


if __name__=='__main__':main()
