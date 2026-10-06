#!/usr/bin/env python3
"""Cache each KV word in dead H lanes and accumulate V in dead FF bytes.

Frozen numerical operators and each lane's sum order remain unchanged.
This is lifetime/port accounting, not an integrated hardware controller.
"""
import argparse
import ctypes as ct
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import time

from continuous_model import variant as continuous_variant,observe,GOLDEN,MODEL
from ring_model import ROOT,sha,load,forward

HERE=Path(__file__).resolve().parent


def variant(source):
    _,reference=continuous_variant(source);text=reference
    a=text.index('static int32_t ring_dequant(');b=text.index('uint64_t ring_count(',a)
    text=text[:a]+(HERE/'block_kv_access.c').read_text()+'\n'+text[b:]
    a=text.index('static void attention(');b=text.index('static uint64_t ff_evaluations',a)
    text=text[:a]+(HERE/'block_attention.c').read_text()+'\n'+text[b:]
    assert text.count('attention(a,p,h);')==1
    text=text.replace('attention(a,p,h);','attention(a,p,h,ff);')
    assert text.count('ring_begin();')==1
    text=text.replace('ring_begin();','ring_begin();cached_dequants=acc_byte_reads=acc_byte_writes=acc_rotations=0;acc_cursor=0;')
    return reference,text


def main():
    if os.getenv('GITHUB_ACTIONS')!='true':signal.alarm(55)
    begin=time.monotonic();ap=argparse.ArgumentParser();ap.add_argument('--context',type=int,choices=(16,32),required=True)
    context=ap.parse_args().context
    source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())==GOLDEN
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL
    reference,text=variant(source)
    payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(ROOT/'docs/index.html').read_text(),re.S)[1])
    rng=random.Random(260667+context);fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
    fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
    out=ROOT/'build/integer_opt/block_kv_model';out.mkdir(parents=True,exist_ok=True)
    (out/'block.c').write_text(text);(out/'reference.c').write_text(reference)
    fp=out/f'fixtures_c{context}.json';fp.write_text(json.dumps(fixtures,indent=2)+'\n')
    profiles=[('frozen',source,[]),('reference',reference,['-DCLOCK_GAPS=1']),
        ('no_gaps',text,[]),('gaps',text,['-DCLOCK_GAPS=1']),
        ('bad_cache',text,['-DCLOCK_GAPS=1','-DBAD_BLOCK_CACHE']),
        ('bad_acc',text,['-DCLOCK_GAPS=1','-DBAD_ACCUM_STORE'])]
    results=[];rejected={'bad_cache':0,'bad_acc':0}
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);libs={}
        for name,c,flags in profiles:
            p=tmp/(name+'.c');p.write_text(c);so=tmp/(name+'.so')
            subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',
                f'-DBANK_ROWS={2*context}',f'-DPREFIX_ROWS={4*context}']+flags+[str(p),'-o',str(so)],check=True,timeout=30)
            lib=load(so,blob)
            if hasattr(lib,'block_count'):lib.block_count.argtypes=[ct.c_uint];lib.block_count.restype=ct.c_uint64
            libs[name]=lib
        for f in fixtures:
            ids=f['ids'];n=len(ids);gold=forward(libs['frozen'],ids);old=observe(libs['reference'],ids)
            assert all(old[k]==gold[k] for k in ('logits','trace'))
            row=dict(name=f['name'],length=n,profiles={},negative={})
            for name in ('no_gaps','gaps'):
                lib=libs[name];got=observe(lib,ids)
                for k in ('logits','trace','ff_row_evaluations','ff_code_maximum_digest'):assert got[k]==old[k],(f['name'],name,k)
                reads=got.pop('scalar_reads');got['kv_word_reads']=reads
                counts=dict(zip(('dequantizations','ff_byte_reads','ff_byte_writes','ff_word_rotations'),(lib.block_count(i) for i in range(4))))
                assert reads==20*n*(n+1) and counts['dequantizations']==old['scalar_reads']==32*reads
                assert got['word_writes']==old['word_writes']==40*n
                assert all(got['prefix'][k]==old['prefix'][k] for k in ('word_reads','word_writes'))
                pairs=n*(n+1)//2
                assert counts['ff_byte_reads']==counts['ff_byte_writes']==20*256*(pairs+n)
                assert counts['ff_word_rotations']==20*21*(pairs+2*n)
                clocks=got['traffic_clocks'];extra=sum(counts[k] for k in ('ff_byte_reads','ff_byte_writes','ff_word_rotations'))
                assert clocks['total']==1+sum(clocks[k] for k in ('gaps','alignment','access','serial_residual'))+extra
                assert clocks['access']==reads+got['word_writes']+got['prefix']['word_reads']+got['prefix']['word_writes']
                assert clocks['alignment']<=(reads+got['word_writes'])*(2*context-1)+(got['prefix']['word_reads']+got['prefix']['word_writes'])*(4*context-1)
                assert got['rotations']==4*clocks['total'] and got['prefix']['rotations']==clocks['total']
                row['profiles'][name]=dict(**got,block=counts)
            for name in rejected:
                bad=observe(libs[name],ids);changed=[k for k in ('logits','trace','ff_code_maximum_digest') if bad[k]!=old[k]]
                assert changed,(name,f['name']);rejected[name]+=1
                row['negative'][name]=dict(differing=changed,logits=bad['logits'],trace=bad['trace'])
            results.append(row)
    sources={Path(__file__).resolve(),HERE/'block_kv_access.c',HERE/'block_attention.c'}
    for name in ('continuous_model.py','circulate_access.c','prefix_circulate_access.c','ff_recompute.py',
        'prefix_model.py','prefix_access.c','ring_model.py','ring_access.c','workspace_model.py',
        'prefix_packed_model.py','prefix_codec.py','prefix_codec12.py'):
        sources.add(ROOT/'integer_opt'/name)
    sources.update((ROOT/'integer/int_model.c',ROOT/'physical/model.bin',ROOT/'docs/index.html'))
    report=dict(status='all frozen C logits/traces/FF digests pass; both actual cache/accumulator storage faults rejected',
        context=context,cases=results,actual_mutations_rejected=rejected,seconds=time.monotonic()-begin,
        numerical_contract_changed=False,adopted=False,frozen_c_sha256=GOLDEN,model_sha256=MODEL,
        reference_c_sha256=sha(reference.encode()),block_c_sha256=sha(text.encode()),fixtures_sha256=sha(fp.read_bytes()),
        storage=dict(H_live_cache_bits=276,H_head_slice_bits=640,FF_accumulator_bits=32*64,
                     FF_existing_code_bits=336*8,held_FF_rows=21,FF_word_bits=128,logical_FF_cursor_bits=5,
                     extra_vector_bits=0,cursor_scope='requires control state/routing; covered only conditionally by retained control reserves, not integrated gate proof'),
        order='V loops interchange independent output lanes only; each lane still sums s=0..pos in order, with exactly the same dequantization and final RNE/saturation',
        port_accounting='Charge8 byte-read and8 byte-write clocks for each64-bit accumulator update, plus exact21-word held-ring rotations; homed after every head',
        scope='Full C lifetime/values and explicit synthetic traffic accounting; no new gate savings, full-controller timing or power claim',
        sources={str(p.relative_to(ROOT)) if ROOT in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(sources)})
    (out/f'c{context}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','sources')},indent=2))
    print(json.dumps(results[-1]['profiles']['gaps'],indent=2))


if __name__=='__main__':main()
