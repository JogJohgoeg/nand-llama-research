#!/usr/bin/env python3
"""Actions-only input scheduling, DOT32 and serial-state diagnostics.

Use the production RTL exporter and testbench on small independent cases
before the full slice. Preserve the old direct-$fscanf result as a diagnostic,
without requiring a simulator-specific bug to remain reproducible forever.
"""
import ctypes as ct
import hashlib
import json
import os
import random
import subprocess

from export import ROOT,OUT,NI,NO,load_unit,rtl
from nand import Builder
from verify import compile_rtl,golden,run,testbench


def gate_rows(net,inputs):
    state=bytes(net.n_state);rows=[]
    for value in inputs:
        state,out=net.step(state,bytes(value>>i&1 for i in range(net.n_in)))
        rows.append((value,sum(x<<i for i,x in enumerate(out)),(1<<net.n_out)-1))
    return rows


def cases(g):
    rng=random.Random(260639)
    b=Builder(NI);echo=b.finish(list(range(2,NO+2)))
    echo_rows=[(x,x&((1<<NO)-1),(1<<NO)-1) for x in [0,1,2,3,1<<275,(1<<NI)-1,0]]
    dot=load_unit('dot32');xs=[];ys=[]
    for i in range(64):
        addr=(i*137)%8192;q=[rng.randint(-128,127) for _ in range(32)]
        if i<4:q=[[-128,-127,0,127][i]]*32
        xs.append(sum((x&255)<<(8*j) for j,x in enumerate(q))+(int(g.slice_word(addr))<<256))
        ys.append(int(g.slice_dot(addr,(ct.c_int8*32)(*q)))&0xffffffff)
    dot_rows=gate_rows(dot,xs);assert [r[1] for r in dot_rows]==ys
    div=load_unit('serial_div');xs=[];checkpoints=[]
    for x,y in [(-5,2),(5,2),(7,2),(-7,2),(0,1),(524288,1),(-524289,1),(12345678,127)]:
        xs.append(1+((x&((1<<64)-1))<<1)+(y<<65))
        xs.extend(rng.getrandbits(89)<<1 for _ in range(64))
        checkpoints.append((len(xs),int(g.int_rne(x,y))))
        xs.append(0)
    div_rows=gate_rows(div,xs)
    for index,q in checkpoints:
        want=(q&((1<<64)-1))+((int(g.slice_sat(q))&1048575)<<64)
        assert div_rows[index][1]>>div.n_state==want
    return [('input_echo',echo,echo_rows),('dot32',dot,dot_rows),('serial_div',div,div_rows)]


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true','RTL simulation only on Actions'
    os.chdir(ROOT);OUT.mkdir(parents=True,exist_ok=True);g=golden();results=[]
    for label,net,rows in cases(g):
        source=OUT/(label+'.v');tb=OUT/(label+'_tb.v');vectors=OUT/(label+'_vectors.txt')
        source.write_text(rtl(net));vectors.write_text(''.join(f'{a:x} {b:x} {m:x}\n' for a,b,m in rows))
        text=testbench(net.n_in,net.n_out,vectors_path=str(vectors.relative_to(ROOT)))
        tb.write_text(text)
        if label=='input_echo':
            legacy=OUT/'input_echo_legacy_tb.v'
            legacy.write_text(text.replace('stimulus,expected,mask','din,expected,mask').replace('   din=stimulus;\n',''))
            exe=compile_rtl('echo_legacy',source,legacy)
            old=subprocess.run([str(exe)],capture_output=True,text=True,timeout=60)
            (OUT/'input_echo_legacy.log').write_text(old.stdout+old.stderr)
            results.append(dict(name='legacy_direct_fscanf',returncode=old.returncode,output=old.stdout+old.stderr))
        exe=compile_rtl(label,source,tb);run([exe],60)
        results.append(dict(name=label,status='pass',clocks=len(rows),nNand=sum(r[0]==0 for r in net.records),
                            nLatch=net.n_state,netlist_sha256=hashlib.sha256(net.encode()).hexdigest()))
    report=dict(status='pass',source_simulator=subprocess.check_output(['verilator','--version'],text=True).strip(),
                results=results,serial_initial_state='Verilator two-state zero initialization; full slice masks physical uninitialized state')
    (OUT/'smoke.json').write_text(json.dumps(report,indent=2)+'\n');print(report)


if __name__=='__main__':main()
