#!/usr/bin/env python3
"""Recompute FFN rows to store only their final A8 codes, without new rounding.

First pass finds max(1,abs(FF)); second pass recomputes identical rows and
stores signed8 codes. Host int32 containers are not hardware bit counts.
Keep the input H codes and its maximum live through both passes.
"""
import argparse
import ctypes as ct
import json
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import time

from ring_model import ROOT,HERE,sha,load,forward
from prefix_packed_model import variant as packed_variant
from prefix_codec import GOLDEN_SHA


def instrument(source):
    helper='''
static uint64_t ff_evaluations,ff_digest;
static void ff_observe(const int32_t *q,int32_t m) {
    for(int i=0;i<336;i++) {
        ff_digest^=(uint8_t)q[i];ff_digest*=1099511628211ULL;
    }
    for(int j=0;j<4;j++) {ff_digest^=((uint32_t)m>>(8*j))&255;ff_digest*=1099511628211ULL;}
}
uint64_t ff_count(unsigned which) {return which?ff_digest:ff_evaluations;}
'''
    assert source.count('int int_run(')==1 and source.count('saturated=0;largest=0;')==1
    source=source.replace('int int_run(',helper+'\nint int_run(')
    return source.replace('saturated=0;largest=0;','saturated=0;largest=0;ff_evaluations=0;ff_digest=14695981039346656037ULL;')


def variant(source):
    marker='            for(int i=0;i<336;i++) {'
    end='            m=quant_in_place(ff,336);linear_q(ff,m,layer,6,h);'
    assert source.count(marker)==source.count(end)==1
    first=source.index(marker);last=source.index(end,first);body=source[first:last]
    assignment='ff[i]=sat(int_rne((int64_t)sat(int_rne((int64_t)g*s,65536))*u,4096));'
    assert body.count(assignment)==1
    prefix='''            int32_t ffmax=1;
            for(int pass=0;pass<2;pass++) {
'''
    replacement=prefix+body.replace(marker,marker+'\n                ff_evaluations++;').replace(assignment,'''
                int32_t value=sat(int_rne((int64_t)sat(int_rne((int64_t)g*s,65536))*u,4096));
                if(pass==0) {
                    int32_t magnitude=value<0?-value:value;
                    if(magnitude>ffmax)ffmax=magnitude;
                } else {
                    ff[i]=(int32_t)int_rne((int64_t)value*127,ffmax);
#ifdef BAD_FF_CODE
                    if(i==0)ff[i]^=1; /* Real stored-code bit error, without overflow. */
#endif
                }''')+'            }\n            m=ffmax;ff_observe(ff,m);linear_q(ff,m,layer,6,h);'
    modified=source[:first]+replacement+source[last+len(end):]
    reference=source.replace(marker,marker+'\n                ff_evaluations++;').replace(end,'            m=quant_in_place(ff,336);ff_observe(ff,m);linear_q(ff,m,layer,6,h);')
    return instrument(reference),instrument(modified)


def main():
    signal.alarm(55);begin=time.monotonic()
    ap=argparse.ArgumentParser();ap.add_argument('--storage-bits',type=int,choices=(16,12),default=12);args=ap.parse_args()
    source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())==GOLDEN_SHA
    numerical,packed=packed_variant(source,args.storage_bits);reference,modified=variant(packed)
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
    payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(ROOT/'docs/index.html').read_text(),re.S)[1])
    results=[]
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);paths={}
        for name,text in [('reference',reference),('recompute',modified)]:
            p=tmp/(name+'.c');p.write_text(text);paths[name]=p
        for context in (16,32):
            libraries=[]
            for name,negative in [('reference',False),('recompute',False),('recompute',True)]:
                so=tmp/f'{name}_{context}_{int(negative)}.so'
                cmd=['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',f'-DPREFIX_ROWS={4*context}',f'-DBANK_ROWS={2*context}']
                if negative:cmd.append('-DBAD_FF_CODE')
                subprocess.run(cmd+[str(paths[name]),'-o',str(so)],check=True,timeout=30)
                lib=load(so,blob);lib.ff_count.argtypes=[ct.c_uint];lib.ff_count.restype=ct.c_uint64;libraries.append(lib)
            old,new,bad=libraries;rng=random.Random(260667+context)
            fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
            fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
            cases=[];negative_cases=0
            for f in fixtures:
                want=forward(old,f['ids']);got=forward(new,f['ids']);assert want==got,f['name']
                old_evals=old.ff_count(0);new_evals=new.ff_count(0)
                assert old_evals==5*len(f['ids'])*336 and new_evals==2*old_evals
                assert old.ff_count(1)==new.ff_count(1)
                rejected=forward(bad,f['ids'])!=want;assert rejected;negative_cases+=int(rejected)
                assert bad.ff_count(1)!=old.ff_count(1)
                cases.append(dict(name=f['name'],length=len(f['ids']),**got,ff_code_maximum_digest=f'{new.ff_count(1):016x}',
                                  original_row_evaluations=old_evals,recomputed_row_evaluations=new_evals))
            results.append(dict(context=context,cases=cases,actual_code_mutations_rejected=negative_cases,
                                original_ff_bits=336*20,recomputed_ff_bits=336*8,extra_maximum_and_pass_bits=21))
    out=ROOT/'build/integer_opt';out.mkdir(parents=True,exist_ok=True)
    report=dict(status='all complete C values, traces, storage accesses and FF code/max digests identical; real FF bit negatives rejected',
        numerical_profile=f'TC16-P{args.storage_bits}-v1',numerical_contract_changed=False,adopted=False,
        numerical_reference_sha256=sha(numerical.encode()),packed_base_sha256=sha(packed.encode()),
        instrumented_reference_sha256=sha(reference.encode()),recomputed_c_sha256=sha(modified.encode()),
        frozen_c_sha256=GOLDEN_SHA,model_sha256=sha(blob),results=results,seconds=time.monotonic()-begin,
        scope='C value/lifetime proof; evaluate FFN rows twice, retain input H and maximum; physical FF bank/control and cycle tradeoff need separate evidence',
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),HERE/'prefix_packed_model.py',HERE/'prefix_codec.py',HERE/'prefix_codec12.py',HERE/'prefix_model.py',HERE/'prefix_access.c',HERE/'workspace_model.py',HERE/'ring_model.py',HERE/'ring_access.c',ROOT/'integer/int_model.c']})
    (out/f'ff_recompute{args.storage_bits}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    print([(x['context'],len(x['cases']),x['actual_code_mutations_rejected']) for x in results])


if __name__=='__main__':main()
