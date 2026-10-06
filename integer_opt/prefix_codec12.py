#!/usr/bin/env python3
"""One narrower candidate: sat12(RNE(S20(x)/256)), decoded by <<8.

P16 remains unchanged. This candidate needs its own full512-story quality
measurement; construction savings alone do not authorize adoption.
"""
import ctypes as ct
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nand import Builder,metrics,flip_output
import prefix_codec as p16

GOLDEN_SHA=p16.GOLDEN_SHA
OUT=ROOT/'build/integer_opt/prefix_codec12'
sha=lambda value:hashlib.sha256(value).hexdigest()


def make():
    b=Builder(20);x=list(range(2,22));q=x[8:]
    increment=b.land(x[7],b.lor(q[0],b.reduce(x[:7],b.lor,0)))
    rounded=b.add(q,[0]*12,increment)[0]
    overflow=b.land(b.inv(x[-1]),rounded[-1])
    return b.finish([b.lor(v,overflow) for v in rounded[:-1]]+[b.land(rounded[-1],b.inv(overflow))])


def variant(source):
    text=p16.variant(source)
    start=text.index('/* Experimental storage codec,');stop=text.index('int int_run(',start)
    before,helper,after=text[:start],text[start:stop],text[stop:]
    for old,new in [('int_rne(v,16)','int_rne(v,256)'),('q>32767','q>2047'),('q=32767','q=2047'),
                    ('q< -32768','q< -2048'),('q= -32768','q= -2048'),('(q*16)','(q*256)')]:
        assert helper.count(old)==1,old
        helper=helper.replace(old,new)
    return before+helper+after


def main():
    signal.alarm(55);OUT.mkdir(parents=True,exist_ok=True);net=make();assert metrics(net)['nNand']<=4000
    source=(ROOT/'integer/int_model.c').read_text();candidate=variant(source)
    (OUT/'candidate.c').write_text(candidate);(OUT/'codec.nl').write_bytes(net.encode())
    adapter=OUT/'check.c'
    adapter.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+
                      '#include '+json.dumps(str(ROOT/'physical/nl_sim.c'))+'\n'+'''
uint32_t codec_check(void) {
    uint32_t wrong=0;
    for(uint32_t u=0;u<1048576;u++) {
        int64_t x=u<524288?(int64_t)u:(int64_t)u-1048576;
        int64_t q=int_rne(x,256);
        if(q>2047)q=2047;
        if(q< -2048)q= -2048;
        uint8_t input[3]={(uint8_t)u,(uint8_t)(u>>8),(uint8_t)(u>>16)},output[2];
        nl_step(input,output);
        uint16_t got=(uint16_t)(output[0]|((uint16_t)output[1]<<8));
        wrong+=(got!=((uint16_t)q&4095));
    }
    return wrong;
}
''')
    lib=OUT/'check.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];c.codec_check.restype=ct.c_uint32
    raw=net.encode();assert c.nl_init(raw,len(raw),20,12)==0;assert c.codec_check()==0
    bad=flip_output(net).encode();assert c.nl_init(bad,len(bad),20,12)==0;negative=c.codec_check();assert negative>0
    report=dict(status='P12 codec full-domain actual NAND/C RNE match; numerical quality pending',metrics=metrics(net),
                inputs=1048576,mismatches=0,actual_gate_mutation_mismatches=negative,normal_abs_error_bound=128,max_abs_storage_error=255,
                positive_top_clip='x>=524160 encodes2047 and decodes524032',candidate_c_sha256=sha(candidate.encode()),
                frozen_c_sha256=GOLDEN_SHA,numerical_contract_changed=True,adopted=False,
                sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer_opt/prefix_codec.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'integer/int_model.c',ROOT/'physical/nl_sim.c']})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
