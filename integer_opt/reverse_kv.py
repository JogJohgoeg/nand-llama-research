#!/usr/bin/env python3
"""Exact descending-prefix recomputation eliminating all retained K/V history.

No new weights, numeric approximation or local EDA. Pure C value/lifetime
prototype; controllers, pack/unpack ports and full physical timing not proved.
"""
from pathlib import Path
import os,sys,re,json,hashlib,ctypes as ct,subprocess,signal,argparse,random,time
R=Path(os.environ.get('H3_REVERSE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R)]
from ring_model import load,forward
from workspace_model import variant as workspace
from ff_recompute import variant as ff_recompute
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_REVERSE_OUT',str(R/'build/integer_opt/reverse_kv')))
CHELPER=Path(os.environ.get('H3_REVERSE_HELPER',str(Path(__file__).with_name('reverse_attention.c'))))


def variant(source):
 s=ff_recompute(workspace(source))[1]
 def once(a,b):
  nonlocal s
  assert s.count(a)==1,a;s=s.replace(a,b)
 once('static int8_t keys[32][4][32], values[32][4][32];','')
 once('static int32_t km[32][4], vm[32][4];','')
 # Remove two now-unused old helpers, not any live numerical primitive.
 i=s.index('static int32_t quant(');j=s.index('static int32_t quant_in_place(',i);s=s[:i]+s[j:]
 i=s.index('static void rope(');j=s.index('static uint32_t exp_weight(',i);s=s[:i]+s[j:]
 i=s.index('static void attention(');j=s.index('static uint64_t ff_evaluations',i);s=s[:i]+CHELPER.read_text()+'\n'+s[j:]
 once('int32_t h[128],a[128],ff[336];','int32_t h[128],a[128];reverse_work work;int32_t *ff=work.ff;\n        for(int i=0;i<32;i++)rewritten[i]=0;')
 i=s.index('        /* All old-prefix K/V');j=s.index('        for(int p=0;p<length;p++) {',i);end=s.index('        for(int p=0;p<length;p++) {',j+1)
 s=s[:i]+'''#ifdef BAD_REVERSE_ORDER
        for(int p=0;p<length;p++) {
#else
        for(int p=length-1;p>=0;p--) {
#endif
'''+s[end+len('        for(int p=0;p<length;p++) {\n'):]
 once('norm(x[p],2*layer,h);int32_t m1=quant_in_place(h,128);linear_q(h,m1,layer,0,a);rope(a,p);',
      'int32_t m1;reverse_attention(p,layer,h,a,&work);')
 once('attention(a,p,h);m1=quant_in_place(h,128);linear_q(h,m1,layer,3,a);','m1=quant_in_place(h,128);linear_q(h,m1,layer,3,a);')
 once('for(int i=0;i<128;i++)x[p][i]=sat((int64_t)x[p][i]+h[i]);','for(int i=0;i<128;i++)x[p][i]=sat((int64_t)x[p][i]+h[i]);\n            rewritten[p]=1;')
 once('saturated=0;largest=0;','saturated=0;largest=0;for(int i=0;i<8;i++)reverse_stats[i]=0;')
 assert not any(x in s for x in ['keys[','values[','km[','vm['])
 return s


def expected(n):
 pair=n*(n+1)//2
 return [5*(4*n+8*pair),5*128*n,5*128*pair,5*128*pair,5*4*pair,5*4*pair]


def main():
 signal.alarm(55);start=time.monotonic();ap=argparse.ArgumentParser();ap.add_argument('--context',type=int,choices=(16,32),required=True);ap.add_argument('--limit',type=int,default=0);a=ap.parse_args()
 OUT.mkdir(parents=True,exist_ok=True);source=(R/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
 payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1])
 fixtures=[dict(f,ids=f['ids'][-a.context:]) for f in payload['fixtures']];rng=random.Random(260667+a.context)
 fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,a.context+1)]
 if a.limit:fixtures=fixtures[:a.limit]
 modified=variant(source);(OUT/'recomputed.c').write_text(modified);(OUT/'frozen.c').write_text(source)
 libs={}
 for name,flags in [('frozen',[]),('recomputed',[]),('forward_order',['-DBAD_REVERSE_ORDER']),('bad_value',['-DBAD_STREAM_VALUE'])]:
  p=OUT/('frozen.c' if name=='frozen' else 'recomputed.c');so=OUT/(name+'.so')
  subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC']+flags+[str(p),'-o',str(so)],check=True,timeout=30)
  libs[name]=load(so,blob)
  if name!='frozen':libs[name].reverse_count.argtypes=[ct.c_uint];libs[name].reverse_count.restype=ct.c_uint64
 results=[];caught_order=caught_value=0
 for f in fixtures:
  want=forward(libs['frozen'],f['ids']);got=forward(libs['recomputed'],f['ids']);assert want==got,f['name']
  stats=[int(libs['recomputed'].reverse_count(i)) for i in range(8)];assert stats[:6]==expected(len(f['ids'])) and stats[7]==0
  assert stats[6]<(1<<41)
  b=forward(libs['forward_order'],f['ids']);wrong_order=b!=want;wrong_reads=int(libs['forward_order'].reverse_count(7))
  if len(f['ids'])>1:assert wrong_order and wrong_reads>0,f['name']
  else:assert b==want and wrong_reads==0
  v=forward(libs['bad_value'],f['ids']);wrong_value=v!=want;assert wrong_value,f['name']
  caught_order+=wrong_order;caught_value+=wrong_value
  results.append(dict(name=f['name'],length=len(f['ids']),ids=f['ids'],**got,operation_counts=stats[:6],max_abs_numerator=stats[6],premature_prefix_reads=stats[7],forward_order_rejected=wrong_order,forward_order_premature_reads=wrong_reads,value_bit_fault_rejected=wrong_value))
  print(a.context,len(results),f['name'],round(time.monotonic()-start,3),flush=True)
 sources=[Path(__file__),CHELPER]+[R/n for n in ['integer_opt/ring_model.py','integer_opt/workspace_model.py','integer_opt/ff_recompute.py','integer_opt/prefix_packed_model.py','integer_opt/prefix_model.py','integer_opt/prefix_codec.py','integer_opt/prefix_codec12.py','integer/int_model.c','physical/model.bin','docs/index.html']]
 report=dict(status='pass',context=a.context,complete_fixture_set=not a.limit,cases=results,
  forward_order_mutations_rejected=caught_order,value_bit_mutations_rejected=caught_value,
  generated_c_sha256=sha(modified.encode()),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sources},
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  state_lifetime=dict(history_kv_bits_removed=2*a.context*128*8+2*a.context*4*20,head_q_bits=640,temporary_kv_bits=640,numerator_bits=1344,ff_bank_capacity_bits=2688,attention_pack_bits=2624,new_vector_bits=0,score_bits=a.context*32),
  counter_labels=['attention_norm_calls','Q_rows','K_rows','V_rows','K_head_quantizations','V_head_quantizations'],
  scope='full C numerical and lifetime prototype, descending layer positions and recomputed K/V; not gate packing/ports/controller proof or physical area/cycle measurement',seconds=round(time.monotonic()-start,3),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'))
 (OUT/f'c{a.context}.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS',a.context,len(results),caught_order,caught_value,report['seconds'])


if __name__=='__main__':main()
