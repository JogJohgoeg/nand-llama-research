#!/usr/bin/env python3
"""Exact per-layer A8 prefix cache for the R73 K/V recomputation schedule.

Value/lifetime prototype only; no EDA, gate packing or physical timing claim.
"""
from pathlib import Path
import os,sys,re,json,hashlib,ctypes as ct,subprocess,signal,argparse,random,time
R=Path(os.environ.get('H3_CACHE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R)]
import reverse_kv
from ring_model import load
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_CACHE_OUT',str(R/'build/integer_opt/norm_cache')))


def variant(source):
 s=reverse_kv.variant(source)
 def once(a,b):
  nonlocal s
  assert s.count(a)==1,a;s=s.replace(a,b)
 once('static uint64_t reverse_stats[8];','static uint64_t reverse_stats[10];')
 once('return i<8?reverse_stats[i]:0;','return i<10?reverse_stats[i]:0;')
 once('for(int i=0;i<8;i++)reverse_stats[i]=0;','for(int i=0;i<10;i++)reverse_stats[i]=0;')
 once('static uint8_t rewritten[32];','''static int8_t cached_codes[32][128];
static int32_t cached_max[32];
/* Epoch tags are verification diagnostics, not claimed hardware state. */
static int cached_epoch[32];''')
 i=s.index('static void reverse_norm_codes(');j=s.index('static void reverse_linear_head(',i)
 s=s[:i]+'''static void fill_norm_cache(int length,int layer,int32_t *scratch) {
#ifdef BAD_CACHE_EPOCH
    if(layer>0)return; /* Actually retain preceding-layer codes. */
#endif
    for(int p=0;p<length;p++) {
        norm(x[p],2*layer,scratch);reverse_stats[0]++;
        cached_max[p]=quant_in_place(scratch,128);
        for(int i=0;i<128;i++)cached_codes[p][i]=(int8_t)scratch[i];
#ifdef BAD_CACHE_CODE
        cached_codes[p][0]=(int8_t)((uint8_t)cached_codes[p][0]^1);
#endif
        cached_epoch[p]=layer;reverse_stats[9]++;
    }
}
static void reverse_norm_codes(int position,int layer,int32_t *scratch,int32_t *maximum) {
    reverse_stats[8]++;
    if(cached_epoch[position]!=layer)reverse_stats[7]++;
    for(int i=0;i<128;i++)scratch[i]=cached_codes[position][i];
    *maximum=cached_max[position];
}
'''+s[j:]
 once('for(int i=0;i<32;i++)rewritten[i]=0;','fill_norm_cache(length,layer,a);')
 once('            rewritten[p]=1;','')
 once('#ifdef BAD_REVERSE_ORDER','#ifdef ASCENDING_CACHE_ORDER')
 assert 'rewritten[' not in s
 return s


def forward_bytes(lib,ids):
 n=len(ids);out=(ct.c_int32*(n*192))();trace=(ct.c_int32*(n*128*6))()
 tokens=(ct.c_int32*n)(*ids);assert lib.int_run(tokens,n,out,trace)==0
 # Compare every byte BEFORE hashing the stored receipts.
 return bytes(out),bytes(trace)


def expected(n):
 old=reverse_kv.expected(n)
 return [5*n]+old[1:]+[old[0],5*n]


def main():
 signal.alarm(55);start=time.monotonic();ap=argparse.ArgumentParser()
 ap.add_argument('--context',type=int,choices=(16,32),required=True)
 a=ap.parse_args();OUT.mkdir(parents=True,exist_ok=True)
 source=(R/'integer/int_model.c').read_text()
 assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 blob=(R/'physical/model.bin').read_bytes()
 assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
 payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1])
 fixtures=[dict(f,ids=f['ids'][-a.context:]) for f in payload['fixtures']]
 rng=random.Random(260667+a.context)  # Same exact family as R73.
 fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,a.context+1)]
 modified=variant(source);(OUT/'cached.c').write_text(modified);(OUT/'frozen.c').write_text(source)
 libs={}
 flags={'frozen':[],'cached':[],'ascending':['-DASCENDING_CACHE_ORDER'],
        'stale_epoch':['-DBAD_CACHE_EPOCH'],'code_fault':['-DBAD_CACHE_CODE']}
 for name,options in flags.items():
  c=OUT/('frozen.c' if name=='frozen' else 'cached.c');so=OUT/(name+'.so')
  subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC']+options+[str(c),'-o',str(so)],check=True,timeout=30)
  libs[name]=load(so,blob)
  if name!='frozen':
   libs[name].reverse_count.argtypes=[ct.c_uint];libs[name].reverse_count.restype=ct.c_uint64
 results=[]
 for f in fixtures:
  want=forward_bytes(libs['frozen'],f['ids']);got=forward_bytes(libs['cached'],f['ids']);assert want==got,f['name']
  stats=[int(libs['cached'].reverse_count(i)) for i in range(10)]
  assert stats[:6]+stats[8:]==expected(len(f['ids'])) and stats[7]==0
  assert stats[6]<(1<<41)
  assert forward_bytes(libs['ascending'],f['ids'])==want,f['name']
  assert libs['ascending'].reverse_count(7)==0
  stale=forward_bytes(libs['stale_epoch'],f['ids']);stale_reads=int(libs['stale_epoch'].reverse_count(7))
  assert stale!=want and stale_reads>0,f['name']
  assert forward_bytes(libs['code_fault'],f['ids'])!=want,f['name']
  results.append(dict(name=f['name'],length=len(f['ids']),ids=f['ids'],logits=sha(got[0]),trace=sha(got[1]),
   compared_int32_words=[len(x)//4 for x in got],operation_counts=stats[:6],cache_vector_reads=stats[8],cache_vector_writes=stats[9],
   max_abs_numerator=stats[6],wrong_epoch_reads=stats[7],ascending_exact=True,stale_cache_rejected=True,
   stale_cache_reads=stale_reads,actual_code_bit_rejected=True))
  print(a.context,len(results),f['name'],round(time.monotonic()-start,3),flush=True)
 sources=[Path(__file__)]+[R/n for n in ['integer_opt/reverse_kv.py','integer_opt/reverse_attention.c',
  'integer_opt/ring_model.py','integer_opt/workspace_model.py','integer_opt/ff_recompute.py',
  'integer_opt/prefix_packed_model.py','integer_opt/prefix_model.py','integer_opt/prefix_codec.py',
  'integer_opt/prefix_codec12.py','integer/int_model.c','physical/model.bin','docs/index.html']]
 history=2*a.context*128*8+2*a.context*4*20;cache=a.context*(128*8+20)
 report=dict(status='pass',context=a.context,complete_fixture_set=True,cases=results,
  compared_word_count=sum(sum(c['compared_int32_words']) for c in results),ascending_exact_cases=len(results),
  stale_cache_mutations_rejected=len(results),code_bit_mutations_rejected=len(results),
  generated_c_sha256=sha(modified.encode()),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sources},
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  state_lifetime=dict(history_kv_bits_replaced=history,norm_A8_code_bits=a.context*128*8,norm_max_bits=a.context*20,
   norm_cache_bits=cache,net_data_bits_removed=history-cache,attention_pack_bits=2624,ff_bank_capacity_bits=2688,
   raw_prefix_preserved=True,diagnostic_epoch_bits_excluded=True),
  counter_labels=['attention_norm_calls','Q_rows','K_rows','V_rows','K_head_quantizations','V_head_quantizations'],
  scope='full C byte comparisons/lifetime with per-layer immutable A8 prefix cache; not packed gates, ports, controller or physical timing',
  seconds=round(time.monotonic()-start,3),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'))
 (OUT/f'c{a.context}.json').write_text(json.dumps(report,indent=2)+'\n')
 print('PASS',a.context,len(results),report['compared_word_count'],report['seconds'])


if __name__=='__main__':main()
