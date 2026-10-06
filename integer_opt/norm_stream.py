#!/usr/bin/env python3
"""Exact norm[1] over128 streamed signed20 values, replayed after RMS scan.

One serial MUL is reused for squares and learned coefficients; one SQRT and
one RNE DIV complete the rule. The caller preserves/replays X; no vector RAM.
"""
import argparse
import ctypes as ct
import hashlib
import json
import math
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,with_state,metrics,verify_state,flip_output
from export import import_net,load_unit,MODEL_SHA,rtl
from golden import Netlist
from bench import lookup,verify
NI,NO=24,55
OUT=R/'build/integer_opt/norm_stream'
sha=lambda data:hashlib.sha256(data).hexdigest()

def control(b,phase,index,count,inputs):
 reset,start,xvalid,yready=inputs
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 p=[eq(phase,j) for j in range(10)];keep=b.inv(reset);last=b.reduce(index,b.land,1)
 begin=b.reduce([keep,p[0],start],b.land,1)
 ready=b.land(keep,b.lor(p[1],p[5]));take=b.land(ready,xvalid)
 sq_take=b.land(take,p[1]);norm_take=b.land(take,p[5])
 sq_end=b.land(p[2],eq(count,20));root_end=b.land(p[4],eq(count,24))
 mul_end=b.land(p[6],eq(count,16));div_end=b.land(p[8],eq(count,38))
 valid=b.land(keep,p[9]);ack=b.land(valid,yready)
 pn=phase[:]
 for enabled,target in ((begin,1),(sq_take,2),(sq_end,1),(b.land(sq_end,last),3),(p[3],4),
                        (root_end,5),(norm_take,6),(mul_end,7),(p[7],8),(div_end,9),(ack,5),(b.land(ack,last),0)):
  pn=[b.mux(enabled,v,target>>j&1) for j,v in enumerate(pn)]
 running=b.reduce([p[2],p[4],p[6],p[8]],b.lor,0)
 cc=b.reduce([reset,begin,take,p[3],p[7],sq_end,root_end,mul_end,div_end],b.lor,0)
 cn=[b.land(b.inv(cc),v) for v in b.add(count,[0]*6,running)[0]]
 ic=b.reduce([reset,begin,root_end],b.lor,0)
 advance=b.lor(sq_end,ack)
 ix=[b.land(b.inv(ic),v) for v in b.add(index,[0]*7,advance)[0]]
 return [b.land(keep,v) for v in pn],ix,cn,[begin,sq_take,norm_take,sq_end,root_end,mul_end,div_end,p[3],p[7],ready,valid,b.inv(p[0]),b.reduce(p[5:],b.lor,0)]

def make():
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
 words=[int.from_bytes(blob[268692+2*(128+i):268694+2*(128+i)],'little') for i in range(128)]
 weights=lookup(words,16,'shannon')
 mul=load_unit('serial_mul');sqrt=load_unit('serial_sqrt')
 meta=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
 raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256'];div=Netlist.decode(raw,90,199)
 ns=512;b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
 ms=old[:192];ds=old[192:307];rs=old[307:405];total=old[405:451];root=old[451:475];index=old[475:482];count=old[482:488];phase=old[488:492];result=old[492:]
 reset,start=ins[:2];x=ins[2:22];xvalid,yready=ins[22:]
 pd,ix,cn,acts=control(b,phase,index,count,[reset,start,xvalid,yready])
 begin,sq_take,norm_take,sq_end,root_end,mul_end,div_end,sqrt_load,div_load,ready,valid,busy,replay=acts
 _,weight=import_net(b,weights,index)
 mag=b.add([b.xor(v,x[-1]) for v in x],[0]*20,x[-1])[0]
 adjusted=b.add([b.xor(v,weight[-1]) for v in x+[x[-1]]],[0]*21,weight[-1])[0]
 wmag=b.add([b.xor(v,weight[-1]) for v in weight],[0]*16,weight[-1])[0]
 mx=[b.mux(norm_take,mag[j] if j<20 else 0,adjusted[j] if j<21 else adjusted[-1]) for j in range(64)]
 my=[b.mux(norm_take,mag[j] if j<20 else 0,wmag[j] if j<16 else 0) for j in range(64)]
 md,mo=import_net(b,mul,[b.lor(sq_take,norm_take)]+mx+my,ms)
 summed=b.add(total,mo[192:232]+[0]*6)[0]
 epsilon=[4295>>j&1 for j in range(48)]
 radicand=b.add([0]+total+[0],epsilon)[0]
 rd,ro=import_net(b,sqrt,[sqrt_load]+radicand,rs)
 dd,do=import_net(b,div,[div_load]+[0]*28+mo[192:228]+root+[0],ds)
 clear=b.lor(reset,begin);keep=b.inv(reset)
 nxt=md+dd+rd
 nxt += [b.land(b.inv(clear),b.mux(sq_end,a,v)) for a,v in zip(total,summed)]
 nxt += [b.land(keep,b.mux(root_end,a,v)) for a,v in zip(root,ro[98:122])]
 nxt += ix+cn+pd
 nxt += [b.land(keep,b.mux(div_end,a,v)) for a,v in zip(result,do[179:199])]
 assert len(nxt)==ns
 net=with_state(b.finish(nxt+result+index+root+[ready,replay,valid,busy]),ns)
 return net,weights,words,dict(weights=metrics(weights),state_layout=dict(mul=[0,192],div=[192,307],sqrt=[307,405],sum=[405,451],root=[451,475],index=[475,482],count=[482,488],phase=[488,492],result=[492,512]),logical_shape='fixed norm[1],128 signed20 inputs, replayed after the square-sum pass')


def small_control():
 b=Builder(21);pd,ix,cn,acts=control(b,list(range(2,6)),list(range(6,13)),list(range(13,19)),list(range(19,23)))
 net=b.finish(pd+ix+cn+acts);rng=random.Random(260686)
 xs=[p+(idx<<4)+(cnt<<11)+(pins<<17) for p in range(16) for idx in (0,1,126,127) for cnt in range(64) for pins in range(16)]
 xs += [rng.randrange(1<<21) for _ in range(1024)];ys=[]
 for value in xs:
  p=value&15;idx=value>>4&127;cnt=value>>11&63
  reset,start,xvalid,yready=[value>>(17+j)&1 for j in range(4)]
  begin=int(not reset and p==0 and start);ready=int(not reset and p in (1,5));take=ready*xvalid
  st=int(take and p==1);nt=int(take and p==5)
  se=int(p==2 and cnt==20);re=int(p==4 and cnt==24);me=int(p==6 and cnt==16);de=int(p==8 and cnt==38)
  valid=int(not reset and p==9);ack=valid*yready
  pn=p
  if reset:pn=0
  elif p==0 and begin:pn=1
  elif p==1 and st:pn=2
  elif p==2 and se:pn=3 if idx==127 else 1
  elif p==3:pn=4
  elif p==4 and re:pn=5
  elif p==5 and nt:pn=6
  elif p==6 and me:pn=7
  elif p==7:pn=8
  elif p==8 and de:pn=9
  elif p==9 and ack:pn=0 if idx==127 else 5
  ni=0 if reset or begin or re else (idx+se+ack)&127
  nc=0 if reset or begin or take or p in (3,7) or se or re or me or de else (cnt+int(p in (2,4,6,8)))&63
  observed=[begin,st,nt,se,re,me,de,int(p==3),int(p==7),ready,valid,int(p!=0),int(5<=p<=9)]
  ys.append(pn+(ni<<4)+(nc<<11)+(sum(v<<j for j,v in enumerate(observed))<<17))
 result=verify_state(net,xs,ys,17);result.pop('nl_hex');return result


def reference():
 assert sha((R/'integer/int_model.c').read_bytes())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(R/'integer/int_model.c'))+'\n'+'''
uint32_t norm_reference(const int32_t *in,int32_t *out) {
    uint64_t sum=0;for(int i=0;i<128;i++)sum+=(uint64_t)((int64_t)in[i]*in[i]);
    norm(in,1,out);return int_sqrt(2*sum+4295);
}
uint16_t norm_weight(unsigned i) { return (uint16_t)norms[128+i]; }
''')
 lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(p),'-o',str(lib)],check=True,timeout=30)
 c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int]
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1';assert c.int_init(blob,len(blob))==0
 c.norm_weight.argtypes=[ct.c_uint];c.norm_weight.restype=ct.c_uint16
 c.norm_reference.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32)];c.norm_reference.restype=ct.c_uint32
 return c

def vectors(c):
 rng=random.Random(260687)
 weights=[c.norm_weight(i) for i in range(128)];weights=[x-65536 if x>=32768 else x for x in weights]
 values=[[0]*128,[-524288]*128,[524287]*128,[(-524288,524287)[i%2] for i in range(128)],
         [0]*127+[-524288],[524287]+[0]*127,[1]*128,[-1]*128,list(range(-64,64))]
 values += [[rng.randrange(-524288,524288)>>scale for _ in range(128)] for scale in (0,0,1,3,5,8,12,16)]
 def rne(x,d):
  q,r=divmod(abs(x),d);q+=int(2*r>d or (2*r==d and q%2));return -q if x<0 else q
 cases=[]
 for x in values:
  out=(ct.c_int32*128)();root=c.norm_reference((ct.c_int32*128)(*x),out)
  assert root==math.isqrt(2*sum(v*v for v in x)+4295)>=65
  assert list(out)==[max(-524288,min(524287,rne(4*v*w,root))) for v,w in zip(x,weights)]
  cases.append(dict(x=x,out=list(out),root=root))
 state=dict(phase=0,index=0,remaining=0,root=0,result=0,sum=0,case=None)
 counts=dict(completed=0,scan_inputs=0,replay_inputs=0,outputs=0,input_stalls=0,output_stalls=0,busy_starts=0,resets=0,aborts=0)
 rows=[];latency=None;started=None
 def tick(reset=0,start=0,x=0,xvalid=0,yready=0,case=None,first=False):
  nonlocal latency,started
  p=state['phase'];idx=state['index'];ready=int(not reset and p in (1,5));valid=int(not reset and p==9)
  want=(state['result']&1048575)+(idx<<20)+(state['root']<<27)+(ready<<51)+(int(p>=5)<<52)+(valid<<53)+(int(p!=0)<<54)
  mask=(1<<55)-1 if valid else ((1<<55)-1)^1048575
  inp=reset+(start<<1)+((x&1048575)<<2)+(xvalid<<22)+(yready<<23)
  rows.append((inp,want,0 if first else mask))
  if reset:
   counts['resets']+=1;counts['aborts']+=int(p!=0);state.update(phase=0,index=0,remaining=0,root=0,result=0,sum=0,case=None);return
  if p==0 and start:
   assert case is not None;state.update(phase=1,index=0,sum=0,remaining=0,case=case);started=len(rows)-1;return
  if p:counts['busy_starts']+=int(bool(start))
  if p in (1,5):
   if not xvalid:counts['input_stalls']+=1;return
   assert x==state['case']['x'][idx]
   if p==1:counts['scan_inputs']+=1;state.update(phase=2,remaining=20);state['sum']+=x*x
   else:counts['replay_inputs']+=1;state.update(phase=6,remaining=16)
  elif p in (2,4,6,8):
   if state['remaining']:state['remaining']-=1;return
   if p==2:state.update(phase=3 if idx==127 else 1,index=(idx+1)&127)
   elif p==4:
    assert math.isqrt(2*state['sum']+4295)==state['case']['root'];state.update(phase=5,index=0,root=state['case']['root'])
   elif p==6:state['phase']=7
   else:state.update(phase=9,result=state['case']['out'][idx])
  elif p==3:state.update(phase=4,remaining=24)
  elif p==7:state.update(phase=8,remaining=38)
  elif p==9:
   if yready:
    counts['outputs']+=1;state.update(phase=0 if idx==127 else 5,index=(idx+1)&127)
    if idx==127:
     counts['completed']+=1
     if latency is None:latency=len(rows)-started
   else:counts['output_stalls']+=1
 def active(stalls=True,hold=False):
  p=state['phase'];x=state['case']['x'][state['index']] if p in (1,5) else rng.randrange(-524288,524288)
  tick(start=int(stalls and rng.randrange(43)==0),x=x,xvalid=int(not stalls or rng.randrange(5)!=0),yready=int(not hold and (not stalls or rng.randrange(5)!=0)))
 tick(reset=1,first=True);tick()
 for i,case in enumerate(cases):
  tick(start=1,case=case)
  while state['phase']:active(stalls=i!=0)
  tick()
 for target,remaining in ((1,0),(2,20),(2,1),(2,0),(3,0),(4,24),(4,1),(4,0),(5,0),(6,16),(6,0),(7,0),(8,38),(8,0),(9,0)):
  tick(start=1,case=cases[-1]);limit=len(rows)+20000
  while state['phase']!=target or state['remaining']!=remaining:
   active(hold=True);assert len(rows)<limit,(target,remaining,state)
  tick(reset=1,start=1,x=-524288,xvalid=1,yready=1);tick()
 tick(start=1,case=cases[3])
 while state['phase']:active()
 tick()
 assert counts['completed']==len(cases)+1 and counts['aborts']==15
 return rows,dict(clocks=len(rows),counts=counts,no_stall_clocks=latency,no_stall_formula='1+128*22+26+128*59',
   cases=[dict(root=c['root'],input_sha256=sha(b''.join(x.to_bytes(4,'little',signed=True) for x in c['x'])),output_sha256=sha(b''.join(x.to_bytes(4,'little',signed=True) for x in c['out']))) for c in cases])


def check(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask&1)+1]
    bad=flip_output(net);wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
    (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,'norm_stream'))
    (OUT/'tb.v').write_text(checks.testbench(NI,NO,'norm_stream',str(OUT/'vectors.txt')))
    normal=checks.compile_rtl('source',OUT/'norm.v');checks.run([normal],300)
    mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    return dict(status='actual NAND/RTL/C/Python pass',clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
                negative_prefix_clocks=len(prefix),actual_result_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True)
    small=small_control();net,weights,words,parts=make();c=reference()
    golden=[c.norm_weight(i) for i in range(128)];assert golden==words
    weight_check=verify(weights,list(range(128)),golden)
    rows,expected=vectors(c);assert expected['no_stall_clocks']==10395
    (OUT/'norm.nl').write_bytes(net.encode());(OUT/'norm.v').write_text(rtl(net,'norm_stream'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    wo=OUT/'weights';wo.mkdir(exist_ok=True);(wo/'source.nl').write_bytes(weights.encode());(wo/'golden.json').write_text(json.dumps(golden)+'\n')
    paths=[R/p for p in ['integer_opt/norm_stream.py','integer_opt/weights.py','integer_opt/gate_check.py',
      'integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py','physical/nl_sim.c',
      'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json',
      'physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small control,all true norm weights and independent C/Python pass; full graph constructed only',
      metrics=metrics(net),parts=parts,small_control=small,weight_verification=weight_check,expected=expected,
      numerical_contract_changed=False,scope='standalone RMSNorm1; X replay and H output storage supplied by caller; FF connection and whole-core sharing excluded',
      contract='din reset,start,x20,xvalid,yready; dout result20,index7,root24,xready,replay,yvalid,busy',
      vector_sha256=sha((OUT/'vectors.txt').read_bytes()),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
      sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in paths})
    if args.cloud:
        from weights import cloud_check
        report['weight_proof']=cloud_check(wo,{'norm_weights':weights},golden)
        report['verification']=check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected','weight_proof')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
