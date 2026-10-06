#!/usr/bin/env python3
"""True norm1 written directly into the original four-word H work slot.

Scalar writes and next-cycle word rotations share the existing port. Publish
only after the final rotation. X remains externally preserved and replayed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_stream as source
base=source
from prefix_store import stage
from nand import Builder,with_state,metrics,verify_state
from export import import_net,rtl
from golden import Netlist
NI,NO=25,699
OUT=R/'build/integer_opt/norm_store'
sha=lambda data:hashlib.sha256(data).hexdigest()

def controller(b,old,inputs):
 pending,complete=old;reset,start,busy,valid,last,we,rd=inputs;keep=b.inv(reset)
 begin=b.reduce([keep,start,b.inv(busy),b.inv(pending)],b.land,1)
 ack=b.reduce([keep,valid,we],b.land,1)
 finish=b.reduce([keep,pending,b.inv(busy)],b.land,1)
 available=b.reduce([keep,complete,b.inv(busy),b.inv(start)],b.land,1)
 advance=b.land(keep,b.lor(pending,b.land(available,rd)))
 nxt=[b.land(keep,b.land(ack,last)),b.land(keep,b.lor(finish,b.land(complete,b.inv(begin))))]
 return nxt,[begin,ack,advance,available]

def make():
 norm=source.make()[0];assert sha(norm.encode())=='dd7e4fa2a97a0242ca8ee2d91139fac07c1dd00387157b0f4add8cbe4dcc9cfd'
 _,work=stage();ns=norm.n_state+work.n_state+2;b=Builder(ns+NI)
 old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI));rs=old[:512];hs=old[512:3072];cs=old[3072:]
 reset,start=ins[:2];x=ins[2:22];xvalid,we,rd=ins[22:]
 _,no=import_net(b,norm,[reset]+[0]*23,rs)
 cd,co=controller(b,cs,[reset,start,no[54],no[53],b.reduce(no[20:25],b.land,1),we,rd])
 begin,ack,advance,available=co
 nd,no=import_net(b,norm,[reset,begin]+x+[xvalid,we],rs)
 hd,_=import_net(b,work,[0]*640+no[:20]+no[20:25]+[0,advance,ack],hs)
 net=with_state(b.finish(nd+hd+cd+no+hs[:640]+cs+[available,b.lor(no[54],cs[0])]),ns)
 assert net.n_in==NI and net.n_out==NO
 return net,dict(norm=metrics(norm),h_slot_bits=2560,extra_result_vector_bits=0,control_bits=2,scope='norm1 into original four-word H work slot; FF connection still outside')


def small_control():
 b=Builder(9);ds,out=controller(b,[2,3],list(range(4,11)));net=b.finish(ds+out);xs=list(range(512));ys=[]
 for x in xs:
  pending=x&1;complete=x>>1&1
  reset,start,busy,valid,last,we,rd=[x>>(2+j)&1 for j in range(7)]
  begin=int(not reset and start and not busy and not pending);ack=int(not reset and valid and we)
  finish=int(not reset and pending and not busy);available=int(not reset and complete and not busy and not start)
  advance=int(not reset and (pending or available and rd))
  nxt=int(not reset and ack and last)+(int(not reset and (finish or complete and not begin))<<1)
  ys.append(nxt+((begin+(ack<<1)+(advance<<2)+(available<<3))<<2))
 result=verify_state(net,xs,ys,2);result.pop('nl_hex');return result

def vectors(c):
 original,expected=base.vectors(c);rows=[];memory=[None]*128
 state=dict(pending=0,complete=0);counts=dict(writes=0,write_rotations=0,read_words=0,completed=0,aborts=0,ignored_pending_starts=0)
 first_latency=None;started=None
 def tick(inp,no,nmask,rd=0,inject=False):
  nonlocal first_latency,started
  reset=inp&1;start=inp>>1&1;we=inp>>23&1;busy=no>>54&1;valid=no>>53&1;idx=no>>20&127
  pending=state['pending'];complete=state['complete']
  begin=int(not reset and start and not busy and not pending);ack=int(not reset and valid and we)
  finish=int(not reset and pending and not busy)
  available=int(not reset and complete and not busy and not start)
  if inject:
   assert pending and not busy and not start;inp|=2;start=1;available=0;counts['ignored_pending_starts']+=1
  advance=int(not reset and (pending or available and rd))
  word=0
  if available:
   assert all(v is not None for v in memory)
   word=sum((v&1048575)<<(20*j) for j,v in enumerate(memory[:32]))
  want=no+(word<<55)+(pending<<695)+(complete<<696)+(available<<697)+(int(busy or pending)<<698)
  mask=nmask+((((1<<640)-1)<<55) if available else 0)+(15<<695)
  rows.append((inp+(rd<<24),want,0 if nmask==0 else mask))
  if reset:
   counts['aborts']+=int(bool(busy or pending));state.update(pending=0,complete=0);return
  if begin:started=len(rows)-1
  if ack:
   raw=no&1048575;memory[idx%32]=raw-1048576 if raw>=524288 else raw;counts['writes']+=1
  if advance:
   assert not ack;memory[:]=memory[32:]+memory[:32]
   if pending:counts['write_rotations']+=1
   else:counts['read_words']+=1
  if finish:
   counts['completed']+=1
   if first_latency is None:first_latency=len(rows)-started
  state['pending']=int(ack and idx%32==31)
  state['complete']=int(finish or complete and not begin)
 def append_fixture(frames):
  for inp,no,mask in frames:
   finish=bool(state['pending'] and not (no>>54&1) and not inp&1)
   tick(inp,no,mask,inject=finish)
   if finish:
    idle=no & ~((1<<51)|(1<<52)|(1<<53)|(1<<54))
    for _ in range(4):tick(0,idle,mask,rd=1)
    tick(0,idle,mask)
 append_fixture(original)
 # A new composition-specific reset exactly between last-lane write and
 # rotation, followed by a full row rewrite. No RAM-clear assumption.
 boundary=next(i for i,(inp,no,mask) in enumerate(original) if (no>>20&127)==31 and no>>53&1 and inp>>23&1)
 append_fixture(original[:boundary+1])
 nxt=original[boundary+1][1]&~((1<<51)|(1<<53))
 tick(1+2+(1<<22)+(1<<23),nxt,original[boundary+1][2])
 first_end=next(i for i,(inp,no,mask) in enumerate(original) if (no>>20&127)==127 and no>>53&1 and inp>>23&1)
 append_fixture(original[:first_end+3])
 assert counts['completed']==19 and counts['read_words']==76
 assert counts['writes']==expected['counts']['outputs']+32+128 and counts['aborts']==16
 return rows,dict(clocks=len(rows),counts=counts,norm_fixture=expected,no_stall_to_publication_clocks=first_latency,
  no_stall_formula='10395 norm clocks +1 deferred last-word rotation; external reads separate')


def check(net,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    assert checks.check_nand(rows,net.encode())==0
    prefix=rows[:next(i for i,(_,_,mask) in enumerate(rows) if mask>>55&1)+1]
    gates=net.records.copy();target=len(gates)-net.n_out+55;inverse=gates[target][1]
    op,a,b=gates[inverse-net.n_in-2];assert op==0 and a==b;gates[target]=(0,a,a)
    bad=Netlist(net.n_in,net.n_out,gates);wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
    (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,'norm_store'))
    (OUT/'tb.v').write_text(checks.testbench(NI,NO,'norm_store',str(OUT/'vectors.txt')))
    normal=checks.compile_rtl('source',OUT/'norm.v');checks.run([normal],300)
    mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
    assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
    return dict(status='actual norm-to-H NAND/RTL/C pass',clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
                negative_prefix_clocks=len(prefix),actual_H_readout_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
    small=small_control();net,parts=make();rows,expected=vectors(base.reference())
    assert expected['no_stall_to_publication_clocks']==10396
    (OUT/'norm.nl').write_bytes(net.encode());(OUT/'norm.v').write_text(rtl(net,'norm_store'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
    names=['norm_store','norm_stream','prefix_store','prefix_writer','prefix_codec','pilot_bank','pilot_v2','weight_cursor','ports','weights','gate_check']
    paths=[R/'integer_opt'/f'{name}.py' for name in names]
    paths += [R/p for p in ['integer/int_model.c','physical/model.bin','physical/export.py','physical/verify.py','physical/nl_sim.c',
      'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl','physical/units/manifest.json',
      'physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','bench.py','nand.py','golden.py','ci.py']]
    report=dict(status='small port control and frozen norm C fixture pass; full norm/H graph constructed only',
      metrics=metrics(net),parts=parts,small_control=small,expected=expected,numerical_contract_changed=False,
      scope='norm1 and original H work slot; external X replay,FF/residual connection and arithmetic sharing remain outside',
      contract='din norm24 plus read_advance; dout norm55,H_head640,pending,complete,available,busy',
      vector_sha256=sha((OUT/'vectors.txt').read_bytes()),run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
      sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in paths})
    if args.cloud:report['verification']=check(net,rows);report['status']=report['verification']['status']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected')},indent=2))
    print(json.dumps({k:v for k,v in expected.items() if k!='norm_fixture'},indent=2))


if __name__=='__main__':main()
