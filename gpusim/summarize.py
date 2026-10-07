#!/usr/bin/env python3
"""Fold m149 results (check, scale, Verilator) into results/bench.json. Pure bookkeeping."""
import json,glob,re
from pathlib import Path
H=Path(__file__).resolve().parent/'results'
res={'check':json.loads((H/'check.json').read_text()),'scale':{},'verilator':{}}
for f in sorted(H.glob('scale_*.json')):res['scale'][f.stem]=json.loads(f.read_text())
nets={'sampler_R112':3860,'qmatrix_ring_word':20023,'norm_cache_R92':12873,'x_head_R118':62311}
for l in (H/'verilator.txt').read_text().splitlines():
    m=re.match(r'(\S+) threads=(\d+) (.*)',l.strip());d=json.loads(m.group(3));n=nets[m.group(1)]
    res['verilator'].setdefault(m.group(1),{})[m.group(2)]=dict(d,clocks_per_s=d['clocks']/d['seconds'],gate_clocks_per_s=n*d['clocks']/d['seconds'])
best={};cpu={}
for k,v in res['scale'].items():
    for name,x in v.items():
        for row in x['rows']:
            if row['gpu_kernel_G']>best.get(name,{}).get('gpu_kernel_G',0):
                best[name]=dict(words=row['words'],lanes=row['lanes'],vec=row.get('vec',1),gpu_kernel_G=row['gpu_kernel_G'],gpu_e2e_G=row['gpu_e2e_G'],cpu_ref_G_same_size=row['cpu_ref_G'])
        cpu[name]=max([cpu.get(name,0)]+[r['cpu_ref_G'] for r in x['rows']])
for name,b in best.items():
    b['cpu_ref_best_G']=cpu[name]
    v=res['verilator'][{'q_matrix_ring_word':'qmatrix_ring_word'}.get(name,name)]
    vb=max(x['gate_clocks_per_s'] for x in v.values())/1e9
    b.update(verilator_best_single_instance_G=vb,gpu_over_verilator_instance=b['gpu_kernel_G']/vb,gpu_over_32_verilator_processes=b['gpu_kernel_G']/(32*vb))
res['best_gpu']=best
res['units']='G = 1e9 NAND-gate evaluations x clocks x independent stimulus lanes per second (LATCH records not counted)'
(H/'bench.json').write_text(json.dumps(res,indent=2)+'\n')
for n,b in best.items():print(n,{k:(round(v,1) if isinstance(v,float) else v) for k,v in b.items()})
