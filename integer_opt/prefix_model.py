#!/usr/bin/env python3
"""Check complete-model prefix ring scheduling, alone and with KV/A8 reuse."""
import ctypes as ct
import json
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import time

from ring_model import ROOT,HERE,sha,load,forward,variant as kv_variant,schedule as kv_schedule
from workspace_model import variant as workspace_variant


def variant(source):
    replacements={
        'static int32_t x[32][128];':'',
        'static void attention(':(HERE/'prefix_access.c').read_text()+'\nstatic void attention(',
        'saturated=0;largest=0;':'if(length>PREFIX_ROWS/4)return -1;\n    prefix_begin();saturated=0;largest=0;',
        'for(int i=0;i<128;i++)x[p][i]=sat(int_rne((int64_t)embedding[token*128+i]*escale[token],4096));':
            'for(int i=0;i<128;i++)prefix_work_h[i]=sat(int_rne((int64_t)embedding[token*128+i]*escale[token],4096));\n        prefix_store_row(p,prefix_work_h);',
        'int32_t h[128],a[128],ff[336];':'int32_t *h=prefix_work_h,a[128],ff[336];',
        'int32_t h[128];':'int32_t *h=prefix_work_h;',
        'norm(x[p],2*layer+1,h);':'norm(a,2*layer+1,h);',
        'norm(x[p],10,h);':'prefix_norm(p,10,h);',
        'for(int i=0;i<128;i++)x[p][i]=sat((int64_t)x[p][i]+a[i]);':'prefix_residual(p,a);',
        'for(int i=0;i<128;i++)x[p][i]=sat((int64_t)x[p][i]+h[i]);':'prefix_residual(p,h);',
    }
    for old,new in replacements.items():assert source.count(old)==1,old;source=source.replace(old,new)
    assert source.count('norm(x[p],2*layer,h);')==2
    source=source.replace('norm(x[p],2*layer,h);','prefix_norm(p,2*layer,h);')
    assert source.count('x[p][i]')==2
    source=source.replace('x[p][i]','prefix_peek(p,i)');assert 'x[p]' not in source
    return source


def schedule(context,length,last_head_only=False):
    rows=4*context;cursor=0;rotations=reads=writes=0
    def access(address,write=False):
        nonlocal cursor,rotations,reads,writes
        rotations+=(address-cursor)%rows;cursor=address
        if write:rotations+=1;cursor=(cursor+1)%rows;writes+=1
        else:reads+=1
    def read_row(pos):
        for c in range(4):access(4*pos+c)
    for pos in range(length):
        for c in range(4):access(4*pos+c,True)
    for _ in range(5):
        for pos in range(length):read_row(pos)  # shared n1 for K and V
        for pos in range(length):
            read_row(pos)  # n1 for Q
            for _ in range(2):
                for c in range(4):access(4*pos+c);access(4*pos+c,True)
            # n2 reads the updated A staging vector directly, with no X fetch.
    for pos in ([length-1] if last_head_only else range(length)):read_row(pos)
    return dict(rotations=rotations,word_reads=reads,word_writes=writes)


def observe(lib,ids):
    result=forward(lib,ids);lib.prefix_count.argtypes=[ct.c_uint];lib.prefix_count.restype=ct.c_uint64
    result['prefix']=dict(zip(('rotations','word_reads','word_writes','homing'),(lib.prefix_count(i) for i in range(4))))
    return result


def main():
    signal.alarm(55);begin=time.monotonic()
    source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    html=(ROOT/'docs/index.html').read_text();payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',html,re.S)[1])
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==payload['sha256'];rows=[]
    variants={'prefix':variant(source),'combined':variant(kv_variant(workspace_variant(source)))}
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);ref=tmp/'golden.so'
        subprocess.run(['cc','-O2','-std=c99','-shared','-fPIC',str(ROOT/'integer/int_model.c'),'-o',str(ref)],check=True,timeout=30)
        golden=load(ref,blob)
        for context in (16,32):
            rng=random.Random(260643+context)
            fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
            fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
            expected=[forward(golden,f['ids']) for f in fixtures]
            for mode,text in variants.items():
                c=tmp/(mode+'.c');c.write_text(text);libraries=[]
                for bad in (False,True):
                    so=tmp/f'{mode}_{context}_{int(bad)}.so'
                    cmd=['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',f'-DPREFIX_ROWS={4*context}',f'-DBANK_ROWS={2*context}']
                    if bad:cmd.append('-DBAD_PREFIX_READ')
                    subprocess.run(cmd+[str(c),'-o',str(so)],check=True,timeout=30);libraries.append(load(so,blob))
                normal,negative=libraries;cases=[];rejected=0
                for f,want in zip(fixtures,expected):
                    got=observe(normal,f['ids']);assert got['logits']==want['logits'] and got['trace']==want['trace'],f['name']
                    if any(f['name']==p['name'] and f['ids']==p['ids'] for p in payload['fixtures']):
                        assert got['logits']==f['logit_sha256'] and got['trace']==f['trace_sha256']
                    planned=schedule(context,len(f['ids']));actual=got['prefix']
                    assert actual['rotations']-actual['homing']==planned['rotations']
                    assert all(actual[k]==planned[k] for k in ('word_reads','word_writes'))
                    if mode=='combined':
                        kv=kv_schedule(context,len(f['ids']))
                        assert got['rotations']-got['homing']==kv['rotations']
                    bad=observe(negative,f['ids']);rejected+=int(bad['logits']!=want['logits'])
                    cases.append(dict(name=f['name'],length=len(f['ids']),**got))
                assert rejected>0
                by_length={}
                for n in range(1,context+1):
                    all_heads=schedule(context,n);last=schedule(context,n,True)
                    assert all_heads['rotations']==last['rotations']
                    by_length[str(n)]=last
                rows.append(dict(context=context,mode=mode,cases=cases,actual_read_address_mutations_rejected=rejected,
                                 cycles_by_length=by_length,max_entry_homing_rotations=4*context-1))
    report=dict(status='pass',numerical_contract_changed=False,scope='C physical-ring access order, not complete control RTL',
                c_sha256=sha(source.encode()),model_sha256=sha(blob),variant_sha256={k:sha(v.encode()) for k,v in variants.items()},
                results=rows,seconds=time.monotonic()-begin,
                staging='Existing H holds prefix reads; A/H hold residual write-back chunks; n2 directly reads updated A. No extra row buffer.',
                trace='Non-mutating state observations only, excluded from inference accesses',
                source_hashes={p.name:sha(p.read_bytes()) for p in [Path(__file__),HERE/'prefix_access.c',HERE/'workspace_model.py',HERE/'ring_model.py',HERE/'ring_access.c']})
    out=ROOT/'build/integer_opt/prefix_model.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    for row in rows:print(row['context'],row['mode'],len(row['cases']),row['actual_read_address_mutations_rejected'],row['cycles_by_length'][str(row['context'])])


if __name__=='__main__':main()
