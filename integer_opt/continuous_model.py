#!/usr/bin/env python3
"""Frozen full-model values with always-moving KV/prefix banks.

Synthetic gaps exercise liveness, not hardware arithmetic cycle counts.
The C cyclic-array representation models rotations; it adds no silicon RAM.
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

from ring_model import ROOT, HERE, sha, load, forward, variant as kv_variant, schedule as kv_schedule
from workspace_model import variant as workspace_variant
from prefix_model import variant as prefix_variant, schedule as prefix_schedule
from ff_recompute import variant as ff_variant

LOCAL=Path(__file__).resolve().parent
GOLDEN='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
MODEL='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'


def variant(source):
    _, reference=ff_variant(prefix_variant(kv_variant(workspace_variant(source))))
    modified=reference
    for old_name,new_name in [('ring_access.c','circulate_access.c'),('prefix_access.c','prefix_circulate_access.c')]:
        old=(HERE/old_name).read_text();assert modified.count(old)==1
        modified=modified.replace(old,(LOCAL/new_name).read_text())
    for old,new in [('prefix_residual(p,a);','prefix_residual(p,a,h);'),('prefix_residual(p,h);','prefix_residual(p,h,a);')]:
        assert modified.count(old)==1;modified=modified.replace(old,new)
    return reference,modified


def observe(lib,ids):
    result=forward(lib,ids)
    lib.prefix_count.argtypes=[ct.c_uint];lib.prefix_count.restype=ct.c_uint64
    result['prefix']=dict(zip(('rotations','word_reads','word_writes','homing'),(lib.prefix_count(i) for i in range(4))))
    lib.ff_count.argtypes=[ct.c_uint];lib.ff_count.restype=ct.c_uint64
    result.update(ff_row_evaluations=lib.ff_count(0),ff_code_maximum_digest=f'{lib.ff_count(1):016x}')
    if hasattr(lib,'circulate_count'):
        lib.circulate_count.argtypes=[ct.c_uint];lib.circulate_count.restype=ct.c_uint64
        result['traffic_clocks']=dict(zip(('total','gaps','alignment','access','serial_residual'),(lib.circulate_count(i) for i in range(5))))
    return result


def main():
    if os.getenv('GITHUB_ACTIONS')!='true':signal.alarm(55)
    begin=time.monotonic()
    ap=argparse.ArgumentParser();ap.add_argument('--context',type=int,choices=(16,32),required=True)
    args=ap.parse_args();context=args.context
    source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())==GOLDEN
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL
    reference,modified=variant(source)
    payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(ROOT/'docs/index.html').read_text(),re.S)[1])
    rng=random.Random(260667+context)
    fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
    fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
    out=ROOT/'build/integer_opt/continuous_model';out.mkdir(parents=True,exist_ok=True)
    fixture_path=out/f'fixtures_c{context}.json';fixture_path.write_text(json.dumps(fixtures,indent=2)+'\n')
    (out/'continuous.c').write_text(modified);(out/'reference.c').write_text(reference)
    profiles=[('reference',reference,[]),('no_gaps',modified,[]),('gaps',modified,['-DCLOCK_GAPS=1']),
              ('bad_kv',modified,['-DCLOCK_GAPS=1','-DBAD_CIRCULATE_KV']),
              ('bad_prefix',modified,['-DCLOCK_GAPS=1','-DBAD_CIRCULATE_PREFIX'])]
    cases=[];rejected={'bad_kv':0,'bad_prefix':0}
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);libs={}
        for name,text,flags in [('frozen',source,[])]+profiles:
            c=tmp/(name+'.c');c.write_text(text);so=tmp/(name+'.so')
            subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',
                f'-DBANK_ROWS={2*context}',f'-DPREFIX_ROWS={4*context}']+flags+[str(c),'-o',str(so)],check=True,timeout=30)
            libs[name]=load(so,blob)
        for f in fixtures:
            ids=f['ids'];n=len(ids);frozen=forward(libs['frozen'],ids)
            ref=observe(libs['reference'],ids)
            assert all(ref[k]==frozen[k] for k in ('logits','trace')),f['name']
            kv=kv_schedule(context,n);prefix=prefix_schedule(context,n);row=dict(name=f['name'],length=n,profiles={})
            for name in ('no_gaps','gaps'):
                got=observe(libs[name],ids)
                for k in ('logits','trace','ff_row_evaluations','ff_code_maximum_digest'):assert got[k]==ref[k],(f['name'],name,k)
                assert got['ff_row_evaluations']==2*5*n*336
                assert all(got[k]==kv[k] for k in ('scalar_reads','word_writes'))
                assert all(got['prefix'][k]==prefix[k] for k in ('word_reads','word_writes'))
                clocks=got['traffic_clocks'];access=kv['scalar_reads']+kv['word_writes']+prefix['word_reads']+prefix['word_writes']
                assert clocks['access']==access and clocks['serial_residual']==2*5*n*128
                assert clocks['total']==1+sum(clocks[k] for k in ('gaps','alignment','access','serial_residual'))
                assert got['rotations']==4*clocks['total'] and got['prefix']['rotations']==clocks['total']
                assert got['homing']==got['prefix']['homing']==0
                max_align=(2*context-1)*(kv['scalar_reads']+kv['word_writes'])+(4*context-1)*(prefix['word_reads']+prefix['word_writes'])
                assert clocks['alignment']<=max_align
                assert clocks['gaps']<=127*access and (clocks['gaps']==0)==(name=='no_gaps')
                row['profiles'][name]=got
            row['negative']={}
            for name in rejected:
                bad=observe(libs[name],ids)
                differing=[k for k in ('logits','trace','ff_code_maximum_digest') if bad[k]!=ref[k]]
                assert differing,(name,f['name']);rejected[name]+=1
                row['negative'][name]=dict(differing=differing,logits=bad['logits'],trace=bad['trace'])
            cases.append(row)
    files=[Path(__file__),LOCAL/'circulate_access.c',LOCAL/'prefix_circulate_access.c']
    files += [HERE/x for x in ('ring_model.py','ring_access.c','workspace_model.py','prefix_model.py','prefix_access.c',
                              'ff_recompute.py','prefix_packed_model.py','prefix_codec.py','prefix_codec12.py')]
    files += [ROOT/'integer/int_model.c',ROOT/'physical/model.bin',ROOT/'docs/index.html']
    report=dict(status='all complete C values/traces and FF codes identical to frozen v1.1; both actual memory-read negatives rejected',
        context=context,numerical_contract_changed=False,adopted=False,frozen_c_sha256=GOLDEN,model_sha256=MODEL,
        reference_c_sha256=sha(reference.encode()),continuous_c_sha256=sha(modified.encode()),
        fixtures_sha256=sha(fixture_path.read_bytes()),actual_mutations_rejected=rejected,cases=cases,
        seconds=time.monotonic()-begin,
        storage='Same two H/A slots: first residual captures old prefix into dead H; second uses dead A. No extra row buffer. Existing arithmetic operand registers capture KV q8/m20 before rotation.',
        traffic_scope='0..127 deterministic gap clocks per request are adversarial fixtures, not arithmetic latency or a silicon PRNG. Each serial residual add advances all banks. All other arithmetic timing is unspecified.',
        proof_scope='Complete C scheduling/lifetime evidence, not whole-model NAND/RTL or timing/power proof. R48 full640-bit H/A ports retained; R47/R49 narrower H ports are not assumed.',
        source_hashes={str(p.relative_to(ROOT)) if ROOT in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in files})
    (out/f'c{context}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','source_hashes')},indent=2))
    print(json.dumps(cases[-1],indent=2))


if __name__=='__main__':main()
