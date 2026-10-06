#!/usr/bin/env python3
"""Experimental prefix storage: s20 -> RNE /16 -> sat16; decode by <<4.

This changes model outputs and is not the frozen INT-C16 contract. Numerical
adoption requires the same 512-story quality evaluation. One serial write
codec can serve the existing prefix bank; no change to arithmetic widths.
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

OUT=ROOT/'build/integer_opt/prefix_codec'
GOLDEN_SHA='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
sha=lambda value:hashlib.sha256(value).hexdigest()


def make():
    b=Builder(20);x=list(range(2,22));q=x[4:]
    increment=b.land(x[3],b.lor(q[0],b.reduce(x[:3],b.lor,0)))
    rounded=b.add(q,[0]*16,increment)[0]
    overflow=b.land(b.inv(x[-1]),rounded[-1])
    result=[b.lor(v,overflow) for v in rounded[:-1]]+[b.land(rounded[-1],b.inv(overflow))]
    return b.finish(result)


def variant(source):
    assert sha(source.encode())==GOLDEN_SHA
    helper='''
/* Experimental storage codec, not part of the frozen golden. */
static uint64_t prefix_changed,prefix_clipped,prefix_delta;
static int32_t prefix_store(int64_t value) {
    int32_t v=sat(value);
    int64_t q=int_rne(v,16);
    if(q>32767){q=32767;prefix_clipped++;}
    if(q< -32768){q= -32768;prefix_clipped++;}
    int32_t decoded=(int32_t)(q*16);
    uint64_t delta=(uint64_t)(decoded>v?decoded-v:v-decoded);
    prefix_changed+=(decoded!=v);
    if(delta>prefix_delta)prefix_delta=delta;
    return decoded;
}
uint64_t prefix_precision_count(unsigned which) {
    return which==0?prefix_changed:which==1?prefix_clipped:prefix_delta;
}
'''
    marker='int int_run(';assert source.count(marker)==1
    source=source.replace(marker,helper+'\n'+marker)
    marker='x[p][i]=sat(';assert source.count(marker)==3
    source=source.replace(marker,'x[p][i]=prefix_store(')
    marker='saturated=0;largest=0;';assert source.count(marker)==1
    return source.replace(marker,marker+'prefix_changed=prefix_clipped=prefix_delta=0;')


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
        int64_t q=int_rne(x,16);
        if(q>32767)q=32767;
        if(q< -32768)q= -32768;
        uint8_t input[3]={(uint8_t)u,(uint8_t)(u>>8),(uint8_t)(u>>16)},output[2];
        nl_step(input,output);
        uint16_t got=(uint16_t)(output[0]|((uint16_t)output[1]<<8));
        wrong+=(got!=(uint16_t)q);
    }
    return wrong;
}
''')
    lib=OUT/'check.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(adapter),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));c.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];c.codec_check.restype=ct.c_uint32
    raw=net.encode();assert c.nl_init(raw,len(raw),20,16)==0;assert c.codec_check()==0
    bad=flip_output(net).encode();assert c.nl_init(bad,len(bad),20,16)==0;negative=c.codec_check();assert negative>0
    report=dict(status='experimental codec full-domain actual NAND/C RNE match; model quality pending',metrics=metrics(net),
                inputs=1048576,mismatches=0,actual_gate_mutation_mismatches=negative,
                max_abs_storage_error=15,normal_abs_error_bound=8,positive_top_clip='x>=524280 encodes32767 and decodes524272',
                frozen_c_sha256=GOLDEN_SHA,candidate_c_sha256=sha(candidate.encode()),source_sha256=sha(Path(__file__).read_bytes()),
                numerical_contract_changed=True,adopted=False)
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
