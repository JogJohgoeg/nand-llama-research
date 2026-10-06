#!/usr/bin/env python3
"""Prove two dead-buffer reuses in a temporary C model, keeping golden intact.

A8 codes overwrite their dead H/FF input after the maximum scan; K and V share
the same quantized H. Attention exponentials overwrite scores after score-max.
This validates lifetimes and values, not a complete hardware controller.
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

from ring_model import ROOT,sha,load,forward


def variant(source):
    helper='''static int32_t quant_in_place(int32_t *in,int n) {
#ifdef BAD_WORKSPACE_OVERWRITE
    in[0]=0; /* Actual premature overwrite of a live source element. */
#endif
    int32_t m=1;for(int i=0;i<n;i++){int32_t v=in[i]<0?-in[i]:in[i];if(v>m)m=v;}
    for(int i=0;i<n;i++)in[i]=(int32_t)int_rne((int64_t)in[i]*127,m);
    return m;
}
'''
    replacements={
        'static void norm(':helper+'\nstatic void norm(',
        'static void linear(const int32_t *in,int layer,int matrix,int32_t *out)':
            'static void linear_q(const int32_t *q,int32_t m,int layer,int matrix,int32_t *out)',
        '    int8_t q[336];int32_t m=quant(in,n,q);\n':'',
        'int32_t scores[32];uint32_t w[32];':'int32_t scores[32];',
        'w[s]=exp_weight((int64_t)maximum-scores[s]);denominator+=w[s];':
            'scores[s]=(int32_t)exp_weight((int64_t)maximum-scores[s]);denominator+=(uint32_t)scores[s];',
        '(int64_t)w[s]*kv_dequant(values[s][h][i],vm[s][h])':
            '(int64_t)scores[s]*kv_dequant(values[s][h][i],vm[s][h])',
        'norm(x[p],2*layer,h);linear(h,layer,1,a);rope(a,p);':
            'norm(x[p],2*layer,h);int32_t m1=quant_in_place(h,128);linear_q(h,m1,layer,1,a);rope(a,p);',
        'linear(h,layer,2,a);':'linear_q(h,m1,layer,2,a);',
        'norm(x[p],2*layer,h);linear(h,layer,0,a);rope(a,p);':
            'norm(x[p],2*layer,h);int32_t m1=quant_in_place(h,128);linear_q(h,m1,layer,0,a);rope(a,p);',
        'attention(a,p,h);linear(h,layer,3,a);':
            'attention(a,p,h);m1=quant_in_place(h,128);linear_q(h,m1,layer,3,a);',
        'int8_t qh[128];int32_t m=quant(h,128,qh);':'int32_t m=quant_in_place(h,128);',
        'dg+=(int32_t)qh[j]*wg[j];du+=(int32_t)qh[j]*wu[j];':'dg+=h[j]*wg[j];du+=h[j]*wu[j];',
        'linear(ff,layer,6,h);':'m=quant_in_place(ff,336);linear_q(ff,m,layer,6,h);',
        'int32_t h[128];int8_t q[128];norm(x[p],10,h);int32_t m=quant(h,128,q);':
            'int32_t h[128];norm(x[p],10,h);int32_t m=quant_in_place(h,128);',
        '(int32_t)q[i]*embedding[j*128+i]':'h[i]*embedding[j*128+i]',
    }
    for old,new in replacements.items():assert source.count(old)==1,old;source=source.replace(old,new)
    assert 'linear(' not in source and 'qh[' not in source
    return source


def quant_probe(lib,inputs):
    f=lib.workspace_quant;ptr=ct.POINTER(ct.c_int32);f.argtypes=[ptr,ct.c_int,ptr];f.restype=ct.c_int32
    out=(ct.c_int32*len(inputs))();m=f((ct.c_int32*len(inputs))(*inputs),len(inputs),out)
    return m,list(out)


def main():
    signal.alarm(55);begin=time.monotonic()
    source=(ROOT/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    html=(ROOT/'docs/index.html').read_text();payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',html,re.S)[1])
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==payload['sha256']
    modified=variant(source)
    normal_probe='''\nint32_t workspace_quant(const int32_t *in,int n,int32_t *out) {
        for(int i=0;i<n;i++)out[i]=in[i];return quant_in_place(out,n);
    }\n'''
    ref_probe='''\nint32_t workspace_quant(const int32_t *in,int n,int32_t *out) {
        int8_t q[336];int32_t m=quant(in,n,q);for(int i=0;i<n;i++)out[i]=q[i];return m;
    }\n'''
    rows=[]
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);libraries=[]
        for name,text in [('golden',source+ref_probe),('reuse',modified+normal_probe),('negative',modified+normal_probe)]:
            c=tmp/(name+'.c');c.write_text(text);so=tmp/(name+'.so')
            cmd=['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC']
            if name=='negative':cmd.append('-DBAD_WORKSPACE_OVERWRITE')
            subprocess.run(cmd+[str(c),'-o',str(so)],check=True,timeout=30);libraries.append(load(so,blob))
        reference,reuse,negative=libraries;rng=random.Random(260642);quant_cases=0;quant_rejected=0
        for n in (1,32,128,336):
            vectors=[[0]*n,[524287]*n,[-524288]*n,[-1 if i%2 else 1 for i in range(n)]]
            vectors += [[rng.randint(-524288,524287) for _ in range(n)] for _ in range(32)]
            for values in vectors:
                want=quant_probe(reference,values);assert quant_probe(reuse,values)==want
                quant_rejected+=int(quant_probe(negative,values)!=want);quant_cases+=1
        assert quant_rejected>0
        for context in (16,32):
            cases=[];rejected=0
            fixtures=[dict(f,ids=f['ids'][-context:]) for f in payload['fixtures']]
            fixtures += [dict(name=f'length{n}',ids=[rng.randrange(192) for _ in range(n)]) for n in range(1,context+1)]
            for f in fixtures:
                want=forward(reference,f['ids']);got=forward(reuse,f['ids']);assert got==want,f['name']
                if any(f['name']==p['name'] and f['ids']==p['ids'] for p in payload['fixtures']):
                    assert got['logits']==f['logit_sha256'] and got['trace']==f['trace_sha256']
                rejected+=int(forward(negative,f['ids'])!=want);cases.append(dict(name=f['name'],length=len(f['ids']),**got))
            assert rejected>0
            rows.append(dict(context=context,cases=cases,premature_overwrite_cases_rejected=rejected,
                             eliminated_latch_bits=336*8+context*17,
                             removed_allocations=dict(a8_codes=336*8,attention_exponentials=context*17)))
    receipt=dict(status='pass',scope='complete C model lifetime/value refinement; no whole-gate or physical claim',
                 numerical_contract_changed=False,c_sha256=sha(source.encode()),variant_sha256=sha(modified.encode()),
                 model_sha256=sha(blob),quantization=dict(cases=quant_cases,negative_rejected=quant_rejected),results=rows,
                 seconds=time.monotonic()-begin,
                 source_hashes={p.name:sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/ring_model.py']})
    out=ROOT/'build/integer_opt/workspace_model.json';out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='results'},indent=2))
    for row in rows:print(row['context'],len(row['cases']),row['eliminated_latch_bits'],row['premature_overwrite_cases_rejected'])


if __name__=='__main__':main()
