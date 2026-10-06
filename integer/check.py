#!/usr/bin/env python3
"""C99 golden and real mutation checks, using the self-contained demo payload."""
import base64
import ctypes as ct
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

signal.alarm(55)
ROOT=Path(__file__).resolve().parents[1]
text=(ROOT/'docs/index.html').read_text()
data=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',text,re.S)[1])
blob=base64.b64decode(data['model']);sha=lambda b:hashlib.sha256(b).hexdigest()
assert sha(blob)==data['sha256']
source=(ROOT/'integer/int_model.c').read_text()
without_comments=re.sub(r'/\*.*?\*/|//[^\n]*','',source,flags=re.S)
assert not re.search(r'\b(float|double|sqrt|exp|sin|cos|pow)\b',without_comments)
start=time.monotonic();checks=[]
with tempfile.TemporaryDirectory() as td:
    for bad in (False,True):
        path=Path(td)/('bad.so' if bad else 'model.so')
        cmd=['cc','-O3','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared',str(ROOT/'integer/int_model.c'),'-o',str(path)]
        if bad:cmd.insert(1,'-DINT_BAD_ROUND')
        subprocess.run(cmd,check=True,timeout=30)
        lib=ct.CDLL(str(path));p=ct.POINTER(ct.c_int32)
        lib.int_init.argtypes=[ct.c_void_p,ct.c_int];assert lib.int_init(blob,len(blob))==0
        lib.int_run.argtypes=[p,ct.c_int,p,p]
        lib.int_mutate.argtypes=[ct.c_int,ct.c_int,ct.c_uint32];lib.int_mutate.restype=ct.c_uint32
        def run(f):
            n=len(f['ids']);ids=(ct.c_int32*n)(*f['ids']);out=(ct.c_int32*(n*192))();trace=(ct.c_int32*(n*128*6))()
            assert lib.int_run(ids,n,out,trace)==0
            # Explicit LE serialization, independent of host endian order.
            encode=lambda values:b''.join(int(x).to_bytes(4,'little',signed=True) for x in values)
            return sha(encode(out)),sha(encode(trace))
        if bad:
            assert run(data['fixtures'][3])[0]!=data['fixtures'][3]['logit_sha256'];continue
        for f in data['fixtures']:
            a,b=run(f);assert a==f['logit_sha256'] and b==f['trace_sha256'],f['name']
            checks.append(dict(name=f['name'],logit_sha256=a,trace_sha256=b))
        original=lib.int_mutate(0,0,32768)
        assert run(data['fixtures'][3])[0]!=data['fixtures'][3]['logit_sha256']
        lib.int_mutate(0,0,original)
receipt=dict(status='pass',model_sha256=data['sha256'],cases=checks,
             c_sha256=sha(source.encode()),actual_lut_mutation_rejected=True,actual_rounding_mutation_rejected=True,
             seconds=time.monotonic()-start)
Path('build').mkdir(exist_ok=True)
Path('build/integer_c.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('C99',len(checks),'cases and both negative controls PASS',receipt['seconds'])
