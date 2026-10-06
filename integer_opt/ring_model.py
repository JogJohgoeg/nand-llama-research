#!/usr/bin/env python3
"""Execute the integer model with physical ring access order, no gate/EDA run.

Golden arithmetic remains byte-for-byte unchanged. Only KV storage/accesses
are replaced in a temporary C translation unit, with explicit match counts.
"""
import ctypes as ct
import hashlib
import json
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve().parent
sha=lambda b:hashlib.sha256(b).hexdigest()


def variant(source):
    replacements={
        'static int8_t keys[32][4][32], values[32][4][32];':'',
        'static int32_t km[32][4], vm[32][4];':'',
        'static void attention(':(HERE/'ring_access.c').read_text()+'\nstatic void attention(',
        'kv_dequant(keys[s][h][i],km[s][h])':'ring_dequant(h,s,0,i)',
        'kv_dequant(values[s][h][i],vm[s][h])':'ring_dequant(h,s,1,i)',
        'km[p][j]=quant(a+j*32,32,keys[p][j]);':'ring_store(j,p,0,a+j*32);',
        'vm[p][j]=quant(a+j*32,32,values[p][j]);':'ring_store(j,p,1,a+j*32);',
        'if(!loaded || length<1 || length>32)return -1;':'if(!loaded || length<1 || length>BANK_ROWS/2)return -1;\n    ring_begin();'
    }
    for old,new in replacements.items():assert source.count(old)==1,old;source=source.replace(old,new)
    return source


def load(path,blob):
    lib=ct.CDLL(str(path));ptr=ct.POINTER(ct.c_int32)
    lib.int_init.argtypes=[ct.c_void_p,ct.c_int];assert lib.int_init(blob,len(blob))==0
    lib.int_run.argtypes=[ptr,ct.c_int,ptr,ptr]
    if hasattr(lib,'ring_count'):lib.ring_count.argtypes=[ct.c_uint];lib.ring_count.restype=ct.c_uint64
    return lib


def forward(lib,ids):
    n=len(ids);out=(ct.c_int32*(n*192))();trace=(ct.c_int32*(n*128*6))();tokens=(ct.c_int32*n)(*ids)
    assert lib.int_run(tokens,n,out,trace)==0
    encode=lambda values:b''.join(int(x).to_bytes(4,'little',signed=True) for x in values)
    result=dict(logits=sha(encode(out)),trace=sha(encode(trace)))
    if hasattr(lib,'ring_count'):
        result.update(rotations=lib.ring_count(0),scalar_reads=lib.ring_count(1),
                      word_writes=lib.ring_count(2),homing=lib.ring_count(3))
    return result


def schedule(context,length):
    # Independent address-only accounting. Four heads execute the same order.
    rows=context*2;cursor=0;rotations=0;reads=0;writes=0
    def seek(addr,write=False):
        nonlocal cursor,rotations,writes
        rotations+=(addr-cursor)%rows;cursor=addr
        if write:rotations+=1;cursor=(cursor+1)%rows;writes+=1
    for _ in range(5):
        for pos in range(length):seek(pos,True);seek(context+pos,True)
        for pos in range(length):
            for s in range(pos+1):seek(s);reads+=32
            for _ in range(32):
                for s in range(pos+1):seek(context+s);reads+=1
    return dict(rotations=4*rotations,scalar_reads=4*reads,word_writes=4*writes)


def main():
    signal.alarm(55);begin=time.monotonic()
    source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    html=(ROOT/'docs/index.html').read_text();payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',html,re.S)[1])
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==payload['sha256']
    rows=[]
    with tempfile.TemporaryDirectory() as td:
        td=Path(td);(td/'ring.c').write_text(variant(source))
        subprocess.run(['cc','-O2','-std=c99','-shared','-fPIC',str(ROOT/'integer/int_model.c'),'-o',str(td/'golden.so')],check=True,timeout=30)
        reference=load(td/'golden.so',blob)
        for context in (16,32):
            libraries=[]
            for bad in (False,True):
                lib=td/f'ring_{context}_{int(bad)}.so';cmd=['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',f'-DBANK_ROWS={context*2}']
                if bad:cmd.append('-DBAD_RING_READ')
                subprocess.run(cmd+[str(td/'ring.c'),'-o',str(lib)],check=True,timeout=30);libraries.append(load(lib,blob))
            normal,negative=libraries;cases=[];caught=0
            fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
            rng=random.Random(260641+context)
            fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
            for f in fixtures:
                want=forward(reference,f['ids']);got=forward(normal,f['ids'])
                assert got['logits']==want['logits'] and got['trace']==want['trace'],f['name']
                if len(f['ids'])==len(next((x['ids'] for x in payload['fixtures'] if x['name']==f['name']),[])):
                    assert want['logits']==f['logit_sha256'] and want['trace']==f['trace_sha256']
                planned=schedule(context,len(f['ids']))
                assert got['rotations']-got['homing']==planned['rotations']
                assert got['scalar_reads']==planned['scalar_reads'] and got['word_writes']==planned['word_writes']
                bad=forward(negative,f['ids']);caught+=int(bad['logits']!=want['logits'])
                cases.append(dict(name=f['name'],length=len(f['ids']),**got))
            assert caught>0
            rows.append(dict(context=context,cases=cases,actual_address_mutation_cases_rejected=caught,
                             cycles_by_length={str(n):schedule(context,n) for n in range(1,context+1)},
                             max_entry_homing_rotations=4*(context*2-1)))
    receipt=dict(status='pass',scope='complete model with C ring state/access simulation; not a whole-gate or controller proof',
                 c_sha256=sha(source.encode()),model_sha256=sha(blob),results=rows,seconds=time.monotonic()-begin,
                 source_hashes={p.name:sha(p.read_bytes()) for p in (Path(__file__),HERE/'ring_access.c')})
    out=ROOT/'build/integer_opt/ring_model.json';out.parent.mkdir(exist_ok=True,parents=True);out.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='results'},indent=2))
    for row in rows:print(row['context'],len(row['cases']),row['cycles_by_length'][str(row['context'])])


if __name__=='__main__':main()
