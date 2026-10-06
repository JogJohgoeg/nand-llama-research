#!/usr/bin/env python3
"""Integrate experimental P16 storage with held rings and dead workspace reuse.

Host prefix rows hold actual int16 codes, not decoded int32 values. Residual
staging is rounded before n2 consumes A. The original C remains untouched.
"""
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
from prefix_model import variant as prefix_variant,schedule as prefix_schedule
from prefix_codec import variant as numeric_variant,GOLDEN_SHA


def variant(source):
    reference=numeric_variant(source)
    begin=reference.index('/* Experimental storage codec, not part of the frozen golden. */')
    helper=reference[begin:reference.index('int int_run(')]
    code=prefix_variant(kv_variant(workspace_variant(source)))
    replacements={
        'typedef struct {int32_t lane[32];} PrefixWord;':
            helper+'\n#ifdef BAD_PACKED_STAGE\n#define prefix_stage(v) sat(v)\n#else\n#define prefix_stage(v) prefix_store(v)\n#endif\n'+
            'typedef struct {int16_t lane[32];} PrefixWord;',
        'tail.lane[i]=replacement[i];':'tail.lane[i]=(int16_t)(prefix_store(replacement[i])/16);',
        'out[c*32+i]=prefix_words[0].lane[i];':'out[c*32+i]=16*(int32_t)prefix_words[0].lane[i];',
        'work[c*32+i]=sat((int64_t)prefix_words[0].lane[i]+work[c*32+i]);':
            'work[c*32+i]=prefix_stage(16*(int64_t)prefix_words[0].lane[i]+work[c*32+i]);',
        'return prefix_words[physical].lane[lane%32];':'return 16*(int32_t)prefix_words[physical].lane[lane%32];',
        'saturated=0;largest=0;':'saturated=0;largest=0;prefix_changed=prefix_clipped=prefix_delta=0;'
    }
    for old,new in replacements.items():assert code.count(old)==1,old;code=code.replace(old,new)
    return reference,code


def main():
    signal.alarm(55);begin=time.monotonic();source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())==GOLDEN_SHA
    reference,modified=variant(source);blob=(ROOT/'physical/model.bin').read_bytes()
    html=(ROOT/'docs/index.html').read_text();payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',html,re.S)[1]);assert sha(blob)==payload['sha256']
    results=[]
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp);paths=[]
        for name,text in (('reference',reference),('packed',modified)):
            p=temp/(name+'.c');p.write_text(text);paths.append(p)
        base=temp/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(paths[0]),'-o',str(base)],check=True,timeout=30)
        golden=load(base,blob)
        for context in (16,32):
            libraries=[]
            for mode,define in (('normal',None),('staging','BAD_PACKED_STAGE'),('address','BAD_PREFIX_READ')):
                so=temp/f'{mode}_{context}.so'
                cmd=['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',f'-DPREFIX_ROWS={4*context}',f'-DBANK_ROWS={2*context}']
                if define:cmd.append('-D'+define)
                subprocess.run(cmd+[str(paths[1]),'-o',str(so)],check=True,timeout=30);libraries.append(load(so,blob))
            normal,bad_stage,bad_address=libraries
            normal.prefix_count.argtypes=[ct.c_uint];normal.prefix_count.restype=ct.c_uint64
            rng=random.Random(260662+context);fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
            fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
            cases=[];stage_rejected=address_rejected=0
            for fixture in fixtures:
                ids=fixture['ids'];want=forward(golden,ids);got=forward(normal,ids)
                assert got['logits']==want['logits'] and got['trace']==want['trace'],fixture['name']
                prefix=dict(zip(('rotations','word_reads','word_writes','homing'),(normal.prefix_count(i) for i in range(4))))
                scheduled=prefix_schedule(context,len(ids));kv=kv_schedule(context,len(ids))
                assert prefix['rotations']-prefix['homing']==scheduled['rotations']
                assert all(prefix[k]==scheduled[k] for k in ('word_reads','word_writes'))
                assert got['rotations']-got['homing']==kv['rotations']
                stage_rejected+=int(forward(bad_stage,ids)['logits']!=want['logits'])
                address_rejected+=int(forward(bad_address,ids)['logits']!=want['logits'])
                cases.append(dict(name=fixture['name'],length=len(ids),prefix=prefix,**got))
            assert stage_rejected==address_rejected==len(fixtures)
            results.append(dict(context=context,cases=cases,staging_mutations_rejected=stage_rejected,address_mutations_rejected=address_rejected,
                                prefix_latch_bits=context*128*16,extra_vector_buffer_bits=0))
    result=dict(status='candidate P16 full model matches packed prefix + KV ring + workspace reuse; actual staging/address negatives rejected',
                numerical_contract_changed=True,adopted=False,scope='C value/lifetime/access proof; complete hardware controller still pending',
                reference_c_sha256=sha(reference.encode()),packed_c_sha256=sha(modified.encode()),frozen_c_sha256=GOLDEN_SHA,
                model_sha256=sha(blob),results=results,seconds=time.monotonic()-begin,
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),HERE/'prefix_codec.py',HERE/'prefix_model.py',HERE/'prefix_access.c',
                    HERE/'workspace_model.py',HERE/'ring_model.py',HERE/'ring_access.c',ROOT/'integer/int_model.c']})
    out=ROOT/'build/integer_opt';out.mkdir(parents=True,exist_ok=True)
    (out/'prefix_packed_model.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='results'},indent=2))
    for row in results:print(row['context'],len(row['cases']),row['staging_mutations_rejected'],row['address_mutations_rejected'])


if __name__=='__main__':main()
