#!/usr/bin/env python3
"""A work-slot last-lane write and rotation on the same edge, no new state.

Old callers used separate write/rotate cycles. This extends that interface;
it deliberately differs only when update and advance coincide without fill.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,subprocess,shutil
R=Path(os.environ.get('H3_STAGE_FUSED_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import prefix_store as old
import cache_bank
from nand import Builder,metrics,verify_state,with_state,flip_output
from gate_check import Snapshot
from export import rtl
OUT=Path(os.environ.get('H3_STAGE_FUSED_OUT',str(R/'build/integer_opt/stage_fused')))
sha=lambda b:hashlib.sha256(b).hexdigest()


def make(lanes=32):
 width=lanes*20;a=(lanes-1).bit_length();ns=4*width
 b=Builder(ns+width+20+a+3);q=list(range(2,ns+2));p=list(range(ns+2,ns+2+width+20+a+3))
 incoming=p[:width];value=p[width:width+20];index=p[width+20:width+20+a]
 fill,advance,update=p[-3:];rotate=b.lor(fill,advance);head=q[:width];updated=[]
 for lane in range(lanes):
  match=b.reduce([x if lane>>j&1 else b.inv(x) for j,x in enumerate(index)],b.land,1)
  enable=b.land(update,match)
  updated += [b.mux(enable,x,y) for x,y in zip(head[lane*20:(lane+1)*20],value)]
 nxt=[]
 for row in range(4):
  held=updated if row==0 else q[row*width:(row+1)*width]
  shifted=q[(row+1)*width:(row+2)*width] if row<3 else [b.mux(fill,x,y) for x,y in zip(updated,incoming)]
  nxt += [b.mux(rotate,x,y) for x,y in zip(held,shifted)]
 selected=old.select(b,[head[j*20:(j+1)*20] for j in range(lanes)],index)
 comb=b.finish(nxt+head+selected);return with_state(comb,ns),comb


def pack(values):return sum((v&1048575)<<(20*j) for j,v in enumerate(values))


def transition(memory,value,index,fill,advance,update,incoming,fused=True):
 m=[row[:] for row in memory]
 if fill:return m[1:]+[incoming[:]]
 if update and (fused or not advance):m[0][index]=value
 return m[1:]+m[:1] if advance else m


def pins(lanes,value=0,index=0,fill=0,advance=0,update=0,incoming=None):
 w=20*lanes;a=(lanes-1).bit_length()
 return pack(incoming or [0]*lanes)+((value&1048575)<<w)+(index<<(w+20))+(fill<<(w+20+a))+(advance<<(w+21+a))+(update<<(w+22+a))


def small():
 lanes=4;ns=320;net,comb=make(lanes);before=old.stage(lanes)[1]
 assert max(metrics(n)['nNand']+n.n_state for n in (net,before))<=4000
 rng=random.Random(260780);xs=[];ys=[];before_ys=[];changed=0
 for i in range(512):
  memory=[[rng.getrandbits(20) for _ in range(lanes)] for _ in range(4)]
  value=rng.getrandbits(20);index=rng.randrange(lanes);fill=i&1;advance=i>>1&1;update=i>>2&1
  incoming=[rng.getrandbits(20) for _ in range(lanes)]
  x=pins(lanes,value,index,fill,advance,update,incoming);obs=pack(memory[0])+(memory[0][index]<<80)
  new=transition(memory,value,index,fill,advance,update,incoming)
  prior=transition(memory,value,index,fill,advance,update,incoming,False)
  n=pack(sum(new,[]))+(obs<<ns);o=pack(sum(prior,[]))+(obs<<ns)
  assert (n!=o)==bool(update and advance and not fill);changed+=n!=o
  xs.append(pack(sum(memory,[]))+(x<<ns));ys.append(n);before_ys.append(o)
 a=verify_state(comb,xs,ys,ns);a.pop('nl_hex')
 b=verify_state(old.stage(lanes)[0],xs,before_ys,ns);b.pop('nl_hex')
 return dict(new=a,previous=b,changed_only_in_new_overlap_mode=changed)


def fixtures():
 cache_bank.OUT=OUT/'c_vectors';cache_bank.OUT.mkdir(parents=True,exist_ok=True)
 return cache_bank.golden()


def vectors(cases,lanes=32,fused=True):
 ns=lanes*80;no=lanes*20+20;m=[[0]*lanes for _ in range(4)];known=False;rows=[];calls=[]
 def tick(value=0,index=0,fill=0,advance=0,update=0,incoming=None):
  nonlocal m
  y=pack(m[0])+((m[0][index]&1048575)<<(lanes*20))
  rows.append((pins(lanes,value,index,fill,advance,update,incoming),y,(1<<no)-1 if known else 0))
  m=transition(m,value&1048575,index,fill,advance,update,incoming or [0]*lanes,fused)
 for layer in cases['layers']:
  for codes in layer:
   values=[q-256 if q>=128 else q for q in codes[:lanes*4]];start=len(rows)
   for i,value in enumerate(values):
    last=i%lanes==lanes-1
    tick(value,i%lanes,advance=int(fused and last),update=1)
    if not fused and last:tick(advance=1)
   assert m==[[v&1048575 for v in values[j*lanes:(j+1)*lanes]] for j in range(4)]
   known=True;calls.append(len(rows)-start)
   for _ in range(4):tick(advance=1)
 # Full fills retain their original precedence, even with update/advance.
 for j in range(4):tick(-1,j%lanes,fill=1,advance=1,update=1,incoming=[j*lanes+i for i in range(lanes)])
 for _ in range(4):tick(advance=1)
 return rows,dict(clocks=len(rows),true_vectors=len(calls),load_cycles_per_vector=sorted(set(calls)),
  readback_clocks_per_vector=4,scope='all80 true C vectors,128 serial writes without stalls plus full readback; scalar codes already produced,not cache/quantizer controller integration')


def small_sequence(cases):
 net=make(4)[0];before=old.stage(4)[1];rows,_=vectors(cases,4,True);prior_rows,_=vectors(cases,4,False)
 def check(graph,stim):
  assert metrics(graph)['nNand']+graph.n_state<=4000
  dec=Snapshot.decode(graph.encode(),graph.n_in,graph.n_out);state=bytes(graph.n_state);wrong=0
  for x,y,mask in stim:
   state,out=dec.step(state,bytes(x>>j&1 for j in range(graph.n_in)))
   wrong+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&mask))
  return wrong
 assert check(net,rows)==0 and check(before,prior_rows)==0
 dropped=check(before,rows);flipped=check(flip_output(net),rows);assert dropped>0 and flipped>0
 return dict(new_clocks=len(rows),old_clocks=len(prior_rows),new_mismatches=0,old_mismatches=0,
  old_graph_with_fused_schedule_mismatches=dropped,actual_output_gate_mismatches=flipped)


def reference(lanes=32):
 w=20*lanes;a=(lanes-1).bit_length();ns=w*4;ni=ns+w+20+a+3;no=w+20
 lines=[f'module top(input [{ni-1}:0] din,output [{ns+no-1}:0] dout);',
  f'wire [{w-1}:0] incoming=din[{ns+w-1}:{ns}];',f'wire [19:0] value=din[{ns+w+19}:{ns+w}];',
  f'wire [{a-1}:0] index=din[{ns+w+20+a-1}:{ns+w+20}];',f'wire fill=din[{ni-3}],advance=din[{ni-2}],update=din[{ni-1}];',
  f'wire [{w-1}:0] updated;reg [19:0] selected;']
 for lane in range(lanes):lines.append(f"assign updated[{20*lane} +: 20]=(update && index=={a}'d{lane}) ? value : din[{20*lane} +: 20];")
 for row in range(4):
  shift=f'din[{(row+1)*w} +: {w}]' if row<3 else '(fill ? incoming : updated)'
  held='updated' if row==0 else f'din[{row*w} +: {w}]'
  lines.append(f'assign dout[{row*w} +: {w}]=(fill || advance) ? {shift} : {held};')
 lines += ['always @* begin','selected=0;case(index)']
 for lane in range(lanes):lines.append(f"{a}'d{lane}:selected=din[{20*lane} +: 20];")
 lines += ['endcase end',f'assign dout[{ns} +: {no}]={{selected,din[{w-1}:0]}};','endmodule','']
 return '\n'.join(lines)


def cloud_check(net,comb,rows,old_rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from nand import blif,from_yosys
 from ci import cec
 import verify as checks
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 (OUT/'stage.blif').write_text(blif(comb));ys=OUT/'reference.ys'
 ys.write_text(f'read_verilog {OUT}/stage.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
 ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out)
 (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
 good=cec(abc,OUT/'stage.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
 checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 prior=old.stage()[1];assert checks.check_nand(old_rows,prior.encode())==0
 dropped=checks.check_nand(rows,prior.encode());wrong=checks.check_nand(rows,flip_output(net).encode());assert dropped>0 and wrong>0
 (OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'fused_stage',str(OUT/'vectors.txt')))
 (OUT/'negative.v').write_text(rtl(flip_output(net),'fused_stage'))
 checks.run([checks.compile_rtl('source',OUT/'stage.v')],300)
 failed=subprocess.run([str(checks.compile_rtl('negative',OUT/'negative.v'))],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
 assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
 return dict(status='pass',all_state_output_cec=good,actual_D_mutation=bad,clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,
  old_schedule_clocks=len(old_rows),old_schedule_mismatches=0,old_graph_with_fused_schedule_mismatches=dropped,
  actual_data_gate_mismatches=wrong,actual_RTL_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();cases=fixtures();seq=small_sequence(cases)
 net,comb=make();before=old.stage()[1];rows,expected=vectors(cases);prior_rows,prior_expected=vectors(cases,fused=False)
 (OUT/'stage.nl').write_bytes(net.encode());(OUT/'stage.v').write_text(rtl(net,'fused_stage'));(OUT/'stage.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 (OUT/'prior_vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in prior_rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','bench.py','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c','integer/int_model.c','physical/model.bin']:paths.add(R/n)
 report=dict(status='small bank/control and C-data schedules pass; full-size gate proof pending',metrics=metrics(net),previous=metrics(before),
  small=sm,small_sequence=seq,expected=expected,previous_expected=prior_expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'c_vectors/cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  scope='existing A/H four-row s20 stage,updated head is rotated; producer/byte-cache client/wholemodel integration remain separate',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['verification']=cloud_check(net,comb,rows,prior_rows);report['status']='all D/output CEC and actual new/old NAND/C-data plus new RTL pass with faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','previous','small_sequence','expected','previous_expected']},indent=2))


if __name__=='__main__':main()
