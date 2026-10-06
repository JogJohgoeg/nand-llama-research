#!/usr/bin/env python3
"""Exact SwiGLU row tail: one shared MUL, bounded iterations, RNE wiring.

SIG index and both power-of-two divides preserve the frozen C rules. The
core accepts an external sigmoid value for a small local arithmetic check;
the complete module includes the actual immutable sigmoid NAND table.
"""
import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'));sys.path.insert(0,str(ROOT))
from export import import_net,load_unit,rtl,MODEL_SHA
from nand import Builder,metrics,with_state,flip_output,blif,from_yosys
from bench import lookup,verify
from ci import cec
from prefix_codec import GOLDEN_SHA

OUT=ROOT/'build/integer_opt/silu_pipeline'
LATENCY=40
sha=lambda data:hashlib.sha256(data).hexdigest()


def rounded_shift(b,bits,k):
    # Signed floor quotient plus the tie-to-even increment also works for
    # negative operands. A sign guard permits rounding across the top bit.
    increment=b.land(bits[k-1],b.reduce(bits[:k-1]+[bits[k]],b.lor,0))
    quotient=bits[k:]+[bits[-1]]
    return b.add(quotient,[0]*len(quotient),increment)[0]


def shifted_sat(b,bits,k):
    q=rounded_shift(b,bits,k);sign=q[-1]
    overflow=b.reduce([b.xor(v,sign) for v in q[19:-1]],b.lor,0)
    return [b.mux(overflow,v,sign if i==19 else b.inv(sign)) for i,v in enumerate(q[:20])]


def front():
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    table=[int.from_bytes(blob[i:i+4],'little') for i in range(len(blob)-4100,len(blob),4)]
    assert len(table)==1025 and min(table)==32768 and max(table)==65536
    lut=lookup(table,17,'shannon');b=Builder(20);g=list(range(2,22));sign=g[-1]
    magnitude=b.add([b.xor(v,sign) for v in g],[0]*20,sign)[0]
    # magnitude is unsigned, so supply a0 sign bit to the signed helper.
    j=rounded_shift(b,magnitude+[0],6)
    clamp=b.reduce(j[10:],b.lor,0)
    address=[b.land(b.inv(clamp),v) for v in j[:10]]+[clamp]
    _,positive=import_net(b,lut,address)
    negative=b.add([b.inv(v) for v in positive],[0]*16+[1],1)[0]
    s=[b.mux(sign,p,n) for p,n in zip(positive,negative)]
    return b.finish(s)


def core():
    mul=load_unit('serial_mul');assert sha(mul.encode())=='289c2d58fe80cc42b366fc882de87e451df5d698efa91a1f405fe6f54c86d7eb'
    ns=192+20+5+2+20+1;b=Builder(ns+59)
    old=list(range(2,ns+2));ins=list(range(ns+2,ns+61))
    ms=old[:192];u=old[192:212];count=old[212:217];phase=old[217:219];result=old[219:239];valid=old[239]
    reset,start=ins[:2];g0=ins[2:22];u0=ins[22:42];s0=ins[42:]
    def eq(bits,value):return b.reduce([v if value>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    keep=b.inv(reset);busy=b.lor(*phase);begin=b.land(keep,b.land(start,b.inv(busy)))
    second=b.land(eq(phase,1),eq(count,17));finish=b.land(eq(phase,2),eq(count,20))
    # The pinned MUL state's first64 bits are its accumulator. Feeding the
    # rounded old product into the second load adds no intermediate buffer.
    t=shifted_sat(b,ms[:37],16)
    signed_t=t+[t[-1]]
    adjusted=b.add([b.xor(v,u[-1]) for v in signed_t],[0]*21,u[-1])[0]
    magnitude=b.add([b.xor(v,u[-1]) for v in u],[0]*20,u[-1])[0]
    mx=[b.mux(begin,adjusted[i] if i<21 else adjusted[-1],g0[i] if i<20 else g0[-1]) for i in range(64)]
    my=[b.mux(begin,magnitude[i] if i<20 else 0,s0[i] if i<17 else 0) for i in range(64)]
    md,_=import_net(b,mul,[b.lor(begin,second)]+mx+my,ms)
    value=shifted_sat(b,ms[:40],12)
    clear=b.lor(reset,b.lor(begin,b.lor(second,finish)))
    phase_next=phase[:]
    for enable,target in ((begin,1),(second,2),(finish,0)):
        phase_next=[b.mux(enable,v,target>>j&1) for j,v in enumerate(phase_next)]
    nxt=md+[b.land(keep,b.mux(begin,a,v)) for a,v in zip(u,u0)]
    nxt += [b.land(b.inv(clear),v) for v in b.add(count,[0]*5,busy)[0]]
    nxt += [b.land(keep,v) for v in phase_next]
    nxt += [b.land(keep,b.mux(finish,r,v)) for r,v in zip(result,value)]
    nxt += [b.land(keep,b.land(b.inv(begin),b.lor(valid,finish)))]
    assert len(nxt)==ns
    return with_state(b.finish(nxt+result+[valid,busy]),ns)


def make():
    arithmetic=core();sigmoid=front();b=Builder(arithmetic.n_state+42)
    old=list(range(2,arithmetic.n_state+2));ins=list(range(arithmetic.n_state+2,arithmetic.n_state+44))
    _,s=import_net(b,sigmoid,ins[2:22]);nxt,out=import_net(b,arithmetic,ins+s,old)
    return with_state(b.finish(nxt+out),arithmetic.n_state)


def transition_reference(core_only):
    # Independent behavioral RTL for all240 old state bits, including states
    # unreachable after reset. Arithmetic uses magnitude RNE, not gate logic.
    ni=299 if core_only else 282
    text=f'module top(input [{ni-1}:0] din,output [261:0] dout);\n'+'''
wire [63:0] acc=din[63:0], a=din[127:64], y=din[191:128];
wire [19:0] u=din[211:192], old_result=din[238:219];
wire [4:0] count=din[216:212];
wire [1:0] phase=din[218:217];
wire old_valid=din[239], reset=din[240], start=din[241];
wire [19:0] g=din[261:242], u0=din[281:262];
wire busy=(phase!=0), begin_op=!reset && start && !busy;
wire second=(phase==1 && count==17), finish=(phase==2 && count==20);
function [19:0] rounded_sat;
 input signed [63:0] value;
 input integer k;
 reg [63:0] magnitude, quotient, remainder, half;
 reg signed [63:0] result;
 begin
  magnitude=value<0 ? -value : value;
  quotient=magnitude>>k;
  remainder=magnitude & ((64'd1<<k)-1);
  half=64'd1<<(k-1);
  if(remainder>half || (remainder==half && quotient[0])) quotient=quotient+1;
  result=value<0 ? -$signed(quotient) : $signed(quotient);
  if(result>524287) rounded_sat=20'h7ffff;
  else if(result< -524288) rounded_sat=20'h80000;
  else rounded_sat=result[19:0];
 end
endfunction
'''
    if core_only:text+='wire [16:0] s=din[298:282];\n'
    else:
        blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
        table=[int.from_bytes(blob[i:i+4],'little') for i in range(len(blob)-4100,len(blob),4)]
        text+='function [16:0] sigmoid; input [10:0] address; begin case(address)\n'
        text+=''.join(f"11'd{i}: sigmoid=17'd{v};\n" for i,v in enumerate(table))
        text+='default: sigmoid=0; endcase end endfunction\n'+'''
wire [19:0] gm=g[19] ? -g : g;
wire [13:0] floor_index=gm[19:6];
wire [14:0] rounded_index={1'b0,floor_index}+((gm[5:0]>32) || (gm[5:0]==32 && floor_index[0]));
wire [10:0] address=rounded_index>1024 ? 11'd1024 : rounded_index[10:0];
wire [16:0] positive= sigmoid(address);
wire [16:0] s=g[19] ? 17'd65536-positive : positive;
'''
    return text+'''
wire [19:0] t=rounded_sat({{27{acc[36]}},acc[36:0]},16);
wire [20:0] adjusted=u[19] ? -{t[19],t} : {t[19],t};
wire [19:0] um=u[19] ? -u : u;
wire [63:0] operand_x=begin_op ? {{44{g[19]}},g} : {{43{adjusted[20]}},adjusted};
wire [63:0] operand_y=begin_op ? {47'd0,s} : {44'd0,um};
wire load=begin_op || second;
assign dout[63:0]=load ? 64'd0 : acc+(y[0]?a:64'd0);
assign dout[127:64]=load ? operand_x : (a<<1);
assign dout[191:128]=load ? operand_y : (y>>1);
assign dout[211:192]=reset ? 20'd0 : begin_op ? u0 : u;
assign dout[216:212]=(reset || begin_op || second || finish) ? 5'd0 : count+busy;
assign dout[218:217]=reset ? 2'd0 : finish ? 2'd0 : second ? 2'd2 : begin_op ? 2'd1 : phase;
assign dout[238:219]=reset ? 20'd0 : finish ? rounded_sat({{24{acc[39]}},acc[39:0]},12) : old_result;
assign dout[239]=!reset && !begin_op && (old_valid || finish);
assign dout[261:240]={busy,old_valid,old_result};
endmodule
'''


def prove_transition(net,core_only):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    ns=net.n_state;b=Builder(ns+net.n_in)
    nxt,out=import_net(b,net,list(range(ns+2,ns+net.n_in+2)),list(range(2,ns+2)))
    comb=b.finish(nxt+out);(OUT/'transition.blif').write_text(blif(comb))
    (OUT/'transition.ref.v').write_text(transition_reference(core_only))
    script=OUT/'transition.ys';script.write_text(f'read_verilog {OUT}/transition.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/transition.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'transition.yosys.log'),'-s',str(script)],stdout=subprocess.DEVNULL,check=True,timeout=240)
    ref=from_yosys(json.loads((OUT/'transition.ref.json').read_text()),comb.n_in,comb.n_out)
    (OUT/'transition.ref.blif').write_text(blif(ref));(OUT/'transition.negative.blif').write_text(blif(flip_output(comb)))
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    proof=cec(abc,OUT/'transition.blif',OUT/'transition.ref.blif',OUT/'transition.cec.log');assert proof['verdict']=='equivalent'
    negative=cec(abc,OUT/'transition.negative.blif',OUT/'transition.ref.blif',OUT/'transition.negative.log');assert negative['verdict']=='different'
    return dict(scope='all old states and inputs against independent arithmetic/table RTL',proof=proof,negative=negative)


def reference():
    p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(ROOT/'integer/int_model.c'))+'\n'+'''
uint32_t sigmoid_reference(int32_t g) {
    int64_t j=int_rne(g<0?-(int64_t)g:g,64);if(j>1024)j=1024;
    return g<0?65536-sigtab[j]:sigtab[j];
}
int32_t silu_core_reference(int32_t g,int32_t u,uint32_t s) {
    return sat(int_rne((int64_t)sat(int_rne((int64_t)g*s,65536))*u,4096));
}
''')
    lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(lib)],check=True,timeout=30)
    c=ct.CDLL(str(lib));blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    c.int_init.argtypes=[ct.c_void_p,ct.c_int];assert c.int_init(blob,len(blob))==0
    c.sigmoid_reference.argtypes=[ct.c_int32];c.sigmoid_reference.restype=ct.c_uint32
    c.silu_core_reference.argtypes=[ct.c_int32,ct.c_int32,ct.c_uint32];c.silu_core_reference.restype=ct.c_int32
    return c


def front_check(c):
    net=front();assert metrics(net)['nNand']<=4000
    rng=random.Random(260671)
    values=set(rng.randrange(-524288,524288) for _ in range(2048))
    for j in range(1026):
        for offset in (-1,0,1,31,32,33):values.update((64*j+offset,-64*j-offset))
    values.update((-524288,524287));values=sorted(v for v in values if -524288<=v<524288)
    checked=verify(net,[v&1048575 for v in values],[c.sigmoid_reference(v) for v in values]);assert checked['status']=='pass'
    return dict(metrics=metrics(net),verification=checked)


def vectors(c,core_only):
    def rne(x,d):
        q,r=divmod(abs(x),d);q+=int(2*r>d or 2*r==d and q%2);return -q if x<0 else q
    def sat(x):return max(-524288,min(524287,x))
    rng=random.Random(260672);edges=(-524288,-65568,-65536,-4096,-33,-32,-31,-1,0,1,31,32,33,4096,65536,65568,524287)
    cases=[(g,u) for g in edges for u in (-524288,-4096,-1,0,1,4096,524287)]
    cases += [(rng.randrange(-524288,524288),rng.randrange(-524288,524288)) for _ in range(256)]
    triples=[(g,u,c.sigmoid_reference(g)) for g,u in cases]
    if core_only:triples += [(g,u,s) for g,u in cases[:119] for s in (0,1,32768,65535,65536)]
    clocks=[];remaining=0;result=valid=pending=0;counts=dict(complete=0,aborts=0,busy_starts=0)
    def tick(g=0,u=0,s=0,start=0,reset=0,first=False):
        nonlocal remaining,result,valid,pending
        x=reset+(start<<1)+((g&1048575)<<2)+((u&1048575)<<22)
        if core_only:x+=s<<42
        clocks.append((x,result+(valid<<20)+(int(remaining>0)<<21),0 if first else (1<<22)-1))
        if reset:counts['aborts']+=int(remaining>0);remaining=result=valid=0
        elif not remaining and start:
            maximum=s if core_only else c.sigmoid_reference(g)
            wanted=c.silu_core_reference(g,u,maximum);assert wanted==sat(rne(sat(rne(g*maximum,65536))*u,4096))
            pending=wanted&1048575;remaining=LATENCY-1;valid=0
        elif remaining:
            counts['busy_starts']+=int(bool(start));remaining-=1
            if not remaining:result=pending;valid=1;counts['complete']+=1
    def garbage():tick(rng.randrange(-524288,524288),rng.randrange(-524288,524288),rng.randrange(65537),start=int(rng.randrange(11)==0))
    tick(reset=1,first=True)
    for g,u,s in triples:
        tick(g,u,s,start=1)
        for _ in range(LATENCY-1):garbage()
        assert not remaining and valid
        for _ in range(2):tick()
    for elapsed in (1,16,17,18,19,37,38):
        tick(524287,-524288,65536,start=1)
        for _ in range(elapsed-1):garbage()
        tick(reset=1,start=1);tick()
    tick(-33,524287,c.sigmoid_reference(-33),start=1)
    for _ in range(LATENCY-1):garbage()
    tick();assert valid
    return clocks,dict(clocks=len(clocks),cases=len(triples)+1,latency=LATENCY,mul_iterations=[17,20],counts=counts,
                       domain='g/u signed20; core-only s unsigned in0..65536; full s from immutable sigmoid NAND table')


def check(net,rows,cloud):
    import verify as checks
    if cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:assert metrics(net)['nNand']<=4000
    checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(ROOT/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=30)
    assert checks.check_nand(rows,net.encode())==0
    bad=flip_output(net);wrong=checks.check_nand(rows,bad.encode());assert wrong>0
    result=dict(status='actual NAND/C/Python pass',clocks=len(rows),nand_mismatches=0,actual_result_gate_mutation_mismatches=wrong)
    if cloud:
        (OUT/'bad.v').write_text(rtl(bad,'silu'));(OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'silu',str(OUT/'vectors.txt')))
        normal=checks.compile_rtl('source',OUT/'silu.v');checks.run([normal],300)
        mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
        (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
        assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
        result.update(status='actual NAND/RTL/C/Python pass',rtl_clocks=len(rows),actual_rtl_mutation_rejected=True)
    return result


def main():
    global OUT
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--core',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    if args.core:OUT=ROOT/'build/integer_opt/silu_core'
    OUT.mkdir(parents=True,exist_ok=True);assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    c=reference();front_result=front_check(c);net=core() if args.core else make();rows,expected=vectors(c,args.core)
    (OUT/'silu.nl').write_bytes(net.encode());(OUT/'silu.v').write_text(rtl(net,'silu'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    report=dict(status='construction and independent C/Python vectors only',core_only=args.core,metrics=metrics(net),front=front_result,expected=expected,
        contract='din reset0,start1,g[21:2],u[41:22], core-only s[58:42]; dout value20,valid,busy. Busy starts ignored; reset aborts; result holds.',
        scope='exact FFN nonlinear scalar tail; external BitLinear gate/up producer and shared-MUL arbitration not integrated',numerical_contract_changed=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'integer/int_model.c',ROOT/'physical/model.bin',
          ROOT/'physical/export.py',ROOT/'physical/verify.py',ROOT/'physical/nl_sim.c',ROOT/'physical/units/manifest.json',ROOT/'physical/units/serial_mul.nl',
          ROOT/'integer_opt/prefix_codec.py',ROOT/'bench.py',ROOT/'nand.py',ROOT/'golden.py',ROOT/'ci.py']})
    if args.cloud or metrics(net)['nNand']<=4000:report['verification']=check(net,rows,args.cloud);report['status']=report['verification']['status']
    if args.cloud:report['transition_proof']=prove_transition(net,args.core)
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','front')},indent=2));print('front',json.dumps(front_result))


if __name__=='__main__':main()
