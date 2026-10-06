#!/usr/bin/env python3
"""Check streamed top-40 and cache-free sampling against the unchanged C golden."""
import ctypes as ct
import json
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import time

from ring_model import ROOT,load,sha


def bind(path,blob):
    lib=load(path,blob)
    lib.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
    lib.stream_logit.argtypes=[ct.c_uint,ct.c_int32]
    lib.stream_pick.argtypes=[ct.c_uint32,ct.c_int]
    lib.stream_count.argtypes=[ct.c_uint];lib.stream_count.restype=ct.c_uint64
    lib.stream_order.argtypes=[ct.c_uint];lib.stream_order.restype=ct.c_uint
    return lib


def main():
    signal.alarm(55);start=time.monotonic();source=ROOT/'integer_opt/sample_stream.c'
    blob=(ROOT/'physical/model.bin').read_bytes()
    assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
    assert sha((ROOT/'integer/int_model.c').read_bytes())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    html=(ROOT/'docs/index.html').read_text()
    payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',html,re.S)[1])
    rng=random.Random(260648);rows=[];negative=[0,0];maxima=[0,0,0];picks=0
    with tempfile.TemporaryDirectory() as temp:
        libraries=[]
        for macro in ('','BAD_STREAM_TIE','BAD_REPLAY_WEIGHT'):
            so=Path(temp)/(macro+'.so')
            cmd=['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC']
            if macro:cmd.append('-D'+macro)
            subprocess.run(cmd+[str(source),'-o',str(so)],check=True,timeout=30)
            libraries.append(bind(so,blob))
        good=libraries[0]
        vectors=[('equal',[0]*192),('ascending',list(range(192))),('descending',list(range(191,-1,-1))),
                 ('extreme',[(-2147483648,2147483647,0)[i%3] for i in range(192)]),
                 ('exp_boundary',[-i*64 for i in range(192)])]
        for i in range(128):
            limit=(4,1000,100000,2147483647)[i%4]
            vectors.append((f'random{i}',[rng.randint(-limit,limit) for _ in range(192)]))
        for f in payload['fixtures']:
            ids=f['ids'];tokens=(ct.c_int32*len(ids))(*ids);out=(ct.c_int32*(len(ids)*192))()
            trace=(ct.c_int32*(len(ids)*128*6))()
            assert good.int_run(tokens,len(ids),out,trace)==0
            assert sha(bytes(out))==f['logit_sha256'] and sha(bytes(trace))==f['trace_sha256']
            vectors.append((f['name'],list(out)[-192:]))
        for name,logits in vectors:
            arr=(ct.c_int32*192)(*logits);order=sorted(range(192),key=lambda i:(-logits[i],i))[:40]
            for lib in libraries:
                lib.stream_begin()
                for i,score in enumerate(logits):lib.stream_logit(i,score)
            assert [good.stream_order(i) for i in range(40)]==order,name
            comparisons=good.stream_count(0);moves=good.stream_count(1)
            assert comparisons<=192*40 and moves<=192*40
            maxima[0]=max(maxima[0],comparisons);maxima[1]=max(maxima[1],moves)
            randoms=[0,1,0xffffffff,0x80000000]+[rng.getrandbits(32) for _ in range(96)]
            chosen=[]
            for sample in (0,1):
                for value in randoms:
                    before=good.stream_count(2);want=good.int_pick(arr,value,sample)
                    actual=good.stream_pick(value,sample);assert actual==want,(name,value,sample)
                    calls=good.stream_count(2)-before;assert calls<=80
                    maxima[2]=max(maxima[2],calls);picks+=1;chosen.append(actual)
                    for j,lib in enumerate(libraries[1:]):negative[j]+=lib.stream_pick(value,sample)!=want
            rows.append(dict(name=name,logits_sha256=sha(bytes(arr)),top40=order,
                             chosen_sha256=sha(bytes(chosen)),comparisons=comparisons,moves=moves))
        assert all(negative),negative
    report=dict(status='pass',scope='C storage/sampling refinement, not controller gate proof',
                numerical_contract_changed=False,logit_vectors=len(rows),pick_cases=picks,
                actual_mutations_rejected=dict(reverse_tie=negative[0],wrong_second_pass_weight=negative[1]),
                maximum_observed=dict(zip(('comparisons','entry_moves','exp_evaluations_per_pick'),maxima)),
                logical_list_bits=40*(32+8),cached_weight_bits=0,
                schedule='At most 40 extra EXP evaluations per sample; +40*(69+2*69+2)=8360 clocks under existing serial operator budget',
                rows=rows,seconds=time.monotonic()-start,
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),source,ROOT/'integer/int_model.c',ROOT/'integer_opt/ring_model.py']},
                model_sha256=sha(blob))
    out=ROOT/'build/integer_opt/sample_stream.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))


if __name__=='__main__':main()
