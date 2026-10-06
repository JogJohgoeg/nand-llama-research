#!/usr/bin/env python3
"""Actions-only complete source NAND / Verilog / mapped-cell checks vs C99."""
import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

from export import ROOT,HERE,OUT,NI,NO,rtl
from golden import Netlist


def run(cmd,seconds=1200):
    print('RUN',*[str(x) for x in cmd],flush=True);begin=time.monotonic()
    result=subprocess.run([str(x) for x in cmd],check=True,timeout=seconds)
    print('OK',cmd[0],round(time.monotonic()-begin,3),'seconds',flush=True)
    return result


def golden():
    run(['cc','-O2','-std=c99','-fPIC','-shared',HERE/'golden_slice.c','-o',OUT/'golden.so'],60)
    g=ct.CDLL(str(OUT/'golden.so'));g.int_init.argtypes=[ct.c_void_p,ct.c_int]
    blob=(HERE/'model.bin').read_bytes();assert g.int_init(blob,len(blob))==0
    g.slice_word.argtypes=[ct.c_uint];g.slice_word.restype=ct.c_uint64
    g.slice_dot.argtypes=[ct.c_uint,ct.POINTER(ct.c_int8)];g.slice_dot.restype=ct.c_int32
    g.int_rne.argtypes=[ct.c_int64,ct.c_int64];g.int_rne.restype=ct.c_int64
    g.int_sqrt.argtypes=[ct.c_uint64];g.int_sqrt.restype=ct.c_uint32
    g.slice_sat.argtypes=[ct.c_int64];g.slice_sat.restype=ct.c_int32
    g.slice_exp.argtypes=[ct.c_int32,ct.c_int32];g.slice_exp.restype=ct.c_uint32
    return g


def vectors(g):
    rng=random.Random(260639);rows=[];counts={};all_bits=(1<<NO)-1
    def emit(kind,view,data=0,addr=0,we=0,load=0,expected=0,mask=all_bits):
        value=addr+(data<<13)+(we<<289)+(load<<290)+(view<<291)
        assert value<1<<NI and expected>=0 and expected<1<<NO
        rows.append((value,expected,mask));counts[kind]=counts.get(kind,0)+1
    # All valid words and every padded/invalid address.
    for addr in range(8192):emit('weights',1,addr=addr,expected=g.slice_word(addr))
    for case in range(640):
        addr=(case*137)%8192
        q=[rng.randint(-128,127) for _ in range(32)]
        if case<4:q=[[-128,-127,0,127][case]]*32
        data=sum((x&255)<<(8*i) for i,x in enumerate(q))
        want=g.slice_dot(addr,(ct.c_int8*32)(*q))&0xffffffff
        emit('dot',0,data,addr,expected=want)
    bank=[0]*32
    for epoch in range(3):
        for addr in range(32):
            data=sum((rng.randint(-127,127)&255)<<(8*i) for i in range(32))
            data|=rng.randint(1,524288)<<256
            # Initialize every real FF before expecting a state-dependent output.
            emit('kv_write',2,data,addr,1,expected=bank[addr],mask=0 if epoch==0 else all_bits)
            bank[addr]=data
        order=list(range(32));rng.shuffle(order)
        for addr in order:emit('kv_read',2,addr=addr,expected=bank[addr])
    for view,steps in ((3,64),(4,64),(5,24)):
        for case in range(24):
            x=rng.randint(-(1<<26),(1<<26)-1);y=rng.randint(1,(1<<25)-1)
            if case<4:x,y=[(-5,2),(5,2),(7,2),(-7,2)][case]
            data=(x&((1<<64)-1))+(y<<64)
            emit('scalar_load',view,data,load=1,mask=0)
            for _ in range(steps):emit('scalar_step',view,rng.getrandbits(276),mask=0)
            if view==3:
                q=int(g.int_rne(x,y));expected=(q&((1<<64)-1))+((g.slice_sat(q)&1048575)<<64)
            elif view==4:expected=(x*y)&((1<<64)-1)
            else:expected=g.int_sqrt(x&((1<<48)-1))
            emit('scalar_result',view,expected=expected)
    for case in range(512):
        a=rng.randint(-524288,524287);b=rng.randint(-524288,524287)
        if case<4:a,b=[(524287,1),(-524288,-1),(-524288,524287),(1,-1)][case]
        emit('residual',6,(a&1048575)+((b&1048575)<<20),expected=g.slice_sat(a+b)&1048575)
        maximum=rng.randint(-(1<<31),(1<<31)-1)
        score=max(-(1<<31),maximum-rng.choice([0,31,32,33,64,65536,65568,2**31]))
        emit('exp',7,(maximum&0xffffffff)+((score&0xffffffff)<<32),expected=g.slice_exp(maximum,score))
    return rows,counts


def check_nand(rows,raw):
    lib=ct.CDLL(str(OUT/'sim.so'));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32]
    assert lib.nl_init(raw,len(raw),NI,NO)==0
    lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    out=ct.create_string_buffer((NO+7)//8);mismatches=0
    for value,want,mask in rows:
        lib.nl_step(value.to_bytes((NI+7)//8,'little'),out)
        mismatches+=int(bool((int.from_bytes(out.raw,'little')^want)&mask))
    return mismatches


def testbench(ni=NI,no=NO,module='int_c16_slice',vectors_path='build/physical/vectors.txt'):
    return f'''module tb;
reg clk=0;reg [{ni-1}:0] din,stimulus;wire [{no-1}:0] dout;
reg [{no-1}:0] expected,mask;integer file,ret,line;
{module} dut(.clk(clk),.din(din),.dout(dout));
initial begin
 file=$fopen("{vectors_path}","r");if(file==0)$fatal(1,"vectors missing");line=0;
 while(!$feof(file))begin
  ret=$fscanf(file,"%h %h %h\\n",stimulus,expected,mask);
  if(ret==3)begin
   // An explicit HDL assignment makes the input update visible to scheduling.
   din=stimulus;
   #250;
   if((dout&mask)!==(expected&mask))begin
    $display("mismatch line %0d: input %h got %h expected %h mask %h",line,din,dout,expected,mask);
    $fatal(1,"C99 comparison failed");
   end
   clk=1;#250;clk=0;line=line+1;
  end else if(!$feof(file))$fatal(1,"bad vector");
 end
 $display("PASS %0d C99 vector clocks",line);$finish;
end
endmodule
'''


def compile_rtl(label,source,tb=None):
    directory=OUT/('obj_'+label)
    run(['verilator','--binary','--timing','--top-module','tb','-j','4',
         '--output-split','10000','--output-split-cfuncs','1000','-Wno-fatal',
         '--Mdir',directory,source,tb or OUT/'tb.v'],900)
    return directory/'Vtb'


def source_check():
    start=time.monotonic();g=golden();rows,counts=vectors(g)
    run(['cc','-O3','-std=c99','-fPIC','-shared',HERE/'nl_sim.c','-o',OUT/'sim.so'],60)
    raw=(OUT/'slice.nl').read_bytes();print('Checking canonical NAND/LATCH bytes',flush=True)
    assert check_nand(rows,raw)==0
    net=Netlist.decode(raw,NI,NO);idx=len(net.records)-NO
    inv=net.records[idx][1];before=net.records[inv-(NI+2)]
    assert before[0]==0 and before[1]==before[2]
    net.records[idx]=(0,before[1],before[1])
    negative=check_nand(rows,net.encode());assert negative>0
    report=dict(status='NAND pass; source RTL pending',source_nand_clocks=len(rows),groups=counts,
                negative_nand_mismatching_clocks=negative,
                netlist_sha256=hashlib.sha256(raw).hexdigest(),
                c_sha256=hashlib.sha256((ROOT/'integer/int_model.c').read_bytes()).hexdigest())
    (OUT/'source_nand.json').write_text(json.dumps(report,indent=2)+'\n');print(report,flush=True)
    (OUT/'bad.v').write_text(rtl(net))
    (OUT/'vectors.txt').write_text(''.join(f'{a:x} {b:x} {m:x}\n' for a,b,m in rows))
    (OUT/'tb.v').write_text(testbench())
    executable=compile_rtl('source',OUT/'slice.v');run([executable],300)
    executable=compile_rtl('negative',OUT/'bad.v')
    bad=subprocess.run([str(executable)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(bad.stdout+bad.stderr)
    assert bad.returncode!=0 and 'C99 comparison failed' in bad.stdout+bad.stderr
    report.update(status='pass',source_verilog_clocks=len(rows),negative_verilog_rejected=True,
                  source_simulator=subprocess.check_output(['verilator','--version'],text=True).strip(),
                  rtl_sha256=hashlib.sha256((OUT/'slice.v').read_bytes()).hexdigest(),
                  seconds=time.monotonic()-start)
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(report)


def mapped_check():
    cfg=json.loads((OUT/'run/resolved.json').read_text())
    netlist=OUT/'run/final/nl/int_c16_slice.nl.v'
    if not netlist.exists():
        candidates=list((OUT/'run/final/nl').glob('*.v'));assert len(candidates)==1;netlist=candidates[0]
    lib=Path(cfg['PDK_ROOT'])/cfg['PDK']/'libs.ref/sky130_fd_sc_hd/verilog'
    sources=[lib/'primitives.v',lib/'sky130_fd_sc_hd.v'];assert all(p.exists() for p in sources)
    run(['iverilog','-g2012','-DFUNCTIONAL','-DUNIT_DELAY=#0','-I',lib,'-s','tb','-o',OUT/'mapped.vvp',
         *sources,netlist,OUT/'tb.v'])
    run(['vvp',OUT/'mapped.vvp'])
    (OUT/'mapped_verification.json').write_text(json.dumps(dict(status='pass',
        vectors_sha256=hashlib.sha256((OUT/'vectors.txt').read_bytes()).hexdigest(),
        netlist_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(),
        method='post-route functional standard-cell simulation, zero delay, same C99 vectors'),indent=2)+'\n')


if __name__=='__main__':
    assert os.getenv('GITHUB_ACTIONS')=='true','Large gate simulation is restricted to GitHub Actions'
    ap=argparse.ArgumentParser();ap.add_argument('kind',choices=('source','mapped'));arg=ap.parse_args()
    os.chdir(ROOT)
    source_check() if arg.kind=='source' else mapped_check()
