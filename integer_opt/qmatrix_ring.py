#!/usr/bin/env python3
"""Real Q128x128 with a continuously rotating A8 bank and ready handshakes.

No numeric change. Extra alignment waits are measured, not hidden. The original
held bank/layout remains frozen. Full graph simulation/formal are Actions-only.
"""
from pathlib import Path
from collections import deque
import os,sys,json,hashlib,signal,argparse,random,importlib.util,subprocess
R=Path(os.environ.get('H3_QMATRIX_RING_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
spec=importlib.util.spec_from_file_location('qmatrix_previous',R/'matrix_physical/prepare.py')
base=importlib.util.module_from_spec(spec);sys.modules[spec.name]=base;spec.loader.exec_module(base)
from nand import Builder,metrics,with_state,verify_state,flip_output
from gate_check import simulate
from export import import_net,rtl
OUT=Path(os.environ.get('H3_QMATRIX_RING_OUT',str(R/'build/integer_opt/qmatrix_ring')))
NI,NO,NS=33,32,1505
STRIDE='word'
sha=lambda b:hashlib.sha256(b).hexdigest()


def bank(b,old,p,bad_rotation=False,slots=128):
 bits=8*slots;a=(slots-1).bit_length();mem=old[:bits];cursor=old[bits:]
 code=p[:8];address=p[8:8+a];we,reset,loading=p[8+a:]
 ready=b.land(b.inv(reset),b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cursor,address)],b.land,1))
 write=b.land(ready,we);head=[b.mux(write,x,y) for x,y in zip(mem[:8],code)];updated=head+mem[8:]
 byte=updated[8:]+updated[:8]
 nxtbyte=b.add(cursor,[0]*a,1)[0]
 if STRIDE=='word':
  word=updated[256:]+updated[:256]
  memory=[b.mux(loading,x,y) for x,y in zip(word,byte)]
  nxtword=cursor[:5]+b.add(cursor[5:],[0]*(a-5),1)[0]
  nxt=[b.mux(loading,x,y) for x,y in zip(nxtword,nxtbyte)]
 else:memory=byte;nxt=nxtbyte
 if bad_rotation:memory=updated
 nxt=[b.land(b.inv(reset),x) for x in nxt]
 return memory+nxt,mem[:256]+[ready]


def bank_graph(slots=128):
 ns=slots*8+(slots-1).bit_length();ni=8+(slots-1).bit_length()+3
 b=Builder(ns+ni);bits=list(range(2,2+ns+ni));d,o=bank(b,bits[:ns],bits[ns:],slots=slots);comb=b.finish(d+o)
 return with_state(comb,ns),comb


def small():
 slots=64 if STRIDE=='word' else 128;bits=slots*8;a=(slots-1).bit_length();ns=bits+a
 net,comb=bank_graph(slots);assert metrics(net)['nNand']+net.n_state<=4000 and metrics(comb)['nNand']<=4000
 rng=random.Random(260789);xs=[];ys=[]
 for i in range(512):
  memory=deque(rng.randrange(256) for _ in range(slots));cursor=rng.randrange(slots);code=rng.randrange(256)
  address=cursor if i%3==0 else rng.randrange(slots);we=rng.randrange(2);reset=rng.randrange(2);loading=rng.randrange(2)
  old=sum(v<<(8*j) for j,v in enumerate(memory))+(cursor<<bits);ready=not reset and cursor==address
  o=sum(v<<(8*j) for j,v in enumerate(list(memory)[:32]))+(int(ready)<<256)
  stride=1 if STRIDE=='byte' or loading else 32
  if ready and we:memory[0]=code
  memory.rotate(-stride);nc=0 if reset else (cursor+stride)%slots
  d=sum(v<<(8*j) for j,v in enumerate(memory))+(nc<<bits)
  p=code+(address<<8)+(we<<(8+a))+(reset<<(9+a))+(loading<<(10+a))
  xs.append(old+(p<<ns));ys.append(d+(o<<ns))
 r=verify_state(comb,xs,ys,ns);r.pop('nl_hex')
 b=Builder(comb.n_in);pins=list(range(2,2+comb.n_in));d,o=bank(b,pins[:ns],pins[ns:],True,slots)
 wrong=sum(a!=z for a,z in zip(simulate(b.finish(d+o),xs),ys));assert wrong>0
 return dict(slots=slots,check=r,actual_missing_rotation_mismatches=wrong)


def make(weights,bad_rotation=False):
 scale=base.scale_net(True);dot=base.load_unit('dot32');assert scale.n_state==418
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==base.MODEL_SHA
 alpha=int.from_bytes(blob[243208:243212],'little');assert alpha==72581
 b=Builder(NS+NI);old=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
 bs=old[:1031];ss=old[1031:1449];acc=old[1449:1466];maximum=old[1466:1486]
 index=old[1486:1493];row=old[1493:1500];group=old[1500:1502];phase=old[1502:1505]
 reset,start=p[:2];q=p[2:10];m=p[10:30];qvalid,yready,enable=p[30:]
 _,so=import_net(b,scale,[0]*57,ss)
 eq=lambda bits,n:b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 loading=eq(phase,1);address=[b.mux(loading,x,y) for x,y in zip([0]*5+group,index)]
 at=b.land(b.inv(reset),b.reduce([b.inv(b.xor(x,y)) for x,y in zip(bs[1024:],address)],b.land,1))
 legal=b.land(b.reduce(m,b.lor,0),b.inv(b.land(m[19],b.reduce(m[:19],b.lor,0))))
 pd,acts=base.control(b,phase,[reset,start,legal,b.reduce(index,b.land,1),b.land(*group),b.reduce(row,b.land,1),
  b.land(qvalid,at),yready,b.land(enable,at),so[20]])
 begin,fill,take,launch,ack,phase_ready,valid,busy=acts
 bd,bo=bank(b,bs,q+address+[fill,reset,loading],bad_rotation);assert bo[-1]==at
 _,trits=import_net(b,weights,group+row);_,dotout=import_net(b,dot,bo[:256]+trits)
 summed=b.add(acc,dotout[:17])[0]
 sd,so=import_net(b,scale,[reset,launch]+acc+maximum+[alpha>>j&1 for j in range(18)],ss)
 keep=b.inv(reset);clear=b.lor(reset,b.lor(begin,ack));init=b.lor(reset,begin)
 d=bd+sd+[b.land(b.inv(clear),b.mux(take,a,s)) for a,s in zip(acc,summed)]
 d += [b.land(keep,b.mux(begin,a,s)) for a,s in zip(maximum,m)]
 d += [b.land(b.inv(init),v) for v in b.add(index,[0]*7,fill)[0]]
 d += [b.land(b.inv(init),v) for v in b.add(row,[0]*7,ack)[0]]
 d += [b.land(b.inv(clear),v) for v in b.add(group,[0]*2,take)[0]]+pd
 assert len(d)==NS
 return with_state(b.finish(d+so[:20]+row+group+[b.land(phase_ready,at),valid,busy]),NS)


def vectors(cases):
 rng=random.Random(260790);rows=[];memory=deque([0]*128);known=deque([False]*128)
 phase=index=row=group=wait=cursor=0;current=None
 counts=dict(completed_matrices=0,result_items=0,filled_codes=0,dot_groups=0,aborts=0,busy_starts=0,
  input_stalls=0,output_stalls=0,dot_stalls=0,fill_alignment_waits=0,dot_alignment_waits=0)
 def tick(reset=0,start=0,m=0,q=0,qvalid=0,yready=0,enable=1,context=None,first=False):
  nonlocal phase,index,row,group,wait,cursor,current,known
  address=index if phase==1 else 32*group;at=not reset and cursor==address
  ready=int(not reset and phase==1 and at);valid=int(not reset and phase==5)
  y=(current['result'][row]&1048575) if valid else 0
  out=y+(row<<20)+(group<<27)+(ready<<29)+(valid<<30)+(int(phase!=0)<<31)
  mask=0 if first else (((1<<NO)-1)^(0 if valid else 1048575))
  p=reset+(start<<1)+((q&255)<<2)+(m<<10)+(qvalid<<30)+(yready<<31)+(enable<<32)
  rows.append((p,out,mask));fill=bool(ready and qvalid);take=bool(not reset and phase==2 and enable and at)
  if fill:assert q==current['q'][index]
  if take:
   assert all(list(known)[:32])
   assert list(memory)[:32]==[v&255 for v in current['q'][32*group:32*group+32]],('group direction',len(rows),row,group,cursor)
  stride=1 if STRIDE=='byte' or phase==1 else 32
  if fill:memory[0]=q&255;known[0]=True
  memory.rotate(-stride);known.rotate(-stride)
  cursor=0 if reset else (cursor+stride)%128
  if reset:
   counts['aborts']+=int(phase!=0);phase=index=row=group=wait=0;current=None;known=deque([False]*128)
  elif phase==0:
   if start and 1<=m<=524288:
    assert context and context['m']==m;current=context;phase=1;index=row=group=0
  else:
   counts['busy_starts']+=int(bool(start))
   if phase==1:
    counts['fill_alignment_waits']+=int(not at);counts['input_stalls']+=int(at and not qvalid)
    if fill:
     counts['filled_codes']+=1;index=(index+1)%128
     if index==0:phase=2
   elif phase==2:
    counts['dot_alignment_waits']+=int(not at);counts['dot_stalls']+=int(at and not enable)
    if take:
     counts['dot_groups']+=1;group=(group+1)%4
     if group==0:phase=3
   elif phase==3:phase=4;wait=base.BOUNDED_LATENCY-1
   elif phase==4:
    if wait:wait-=1
    else:phase=5
   elif phase==5:
    if yready:
     counts['result_items']+=1;row=(row+1)%128;group=0
     if row==0:phase=0;counts['completed_matrices']+=1
     else:phase=2
    else:counts['output_stalls']+=1
 def active(stall):
  tick(start=int(rng.randrange(47)==0),m=rng.randrange(1048576),q=current['q'][index] if phase==1 else rng.randrange(-128,128),
   qvalid=int(not stall or rng.randrange(5)!=0),yready=int(not stall or rng.randrange(4)!=0),enable=int(not stall or rng.randrange(3)!=0))
 tick(reset=1,first=True)
 for m in [0,524289,1048575]:tick(start=1,m=m)
 completed=[]
 for j,case in enumerate(cases):
  before=len(rows);tick(start=1,m=case['m'],context=case)
  for _ in range(180000):
   if phase==0:break
   active(j%2==1)
  else:raise AssertionError('matrix timeout')
  completed.append(dict(clocks=len(rows)-before,stalled=bool(j%2)))
  tick()
 for elapsed in [1,31,127,129,132,255,270,300,350,399,450]:
  case=cases[6];tick(start=1,m=case['m'],context=case)
  for _ in range(elapsed):active(False)
  tick(reset=1,start=1);tick()
 case=cases[7];tick(start=1,m=case['m'],context=case)
 for _ in range(180000):
  if phase==0:break
  active(True)
 else:raise AssertionError('matrix timeout after reset')
 tick();assert counts['completed_matrices']==len(cases)+1
 return rows,dict(clocks=len(rows),counts=counts,completed=completed,
  unstalled_clock_values=sorted({x['clocks'] for x in completed if not x['stalled']}),
  scope='same17 frozen C/Python Q fixtures; actual rotating A8 bank modeled each clock; allgroup alignments checked; no full inference')


def bank_reference():
 stride="(loading?7'd1:7'd32)" if STRIDE=='word' else "7'd1"
 memory="(loading?{updated[7:0],updated[1023:8]}:{updated[255:0],updated[1023:256]})" if STRIDE=='word' else "{updated[7:0],updated[1023:8]}"
 return f'''module top(input [1048:0] din,output [1287:0] dout);
wire [1023:0] memory=din[1023:0];wire [6:0] cursor=din[1030:1024];
wire [7:0] code=din[1038:1031];wire [6:0] address=din[1045:1039];wire we=din[1046],reset=din[1047],loading=din[1048];
wire ready=!reset && cursor==address;wire [6:0] next_cursor=reset ? 7'd0 : cursor+{stride};
wire [1023:0] updated={{memory[1023:8],(ready && we)?code:memory[7:0]}};
assign dout={{ready,memory[255:0],next_cursor,{memory}}};
endmodule
'''


def cloud_check(net,weights,words,rows,comb):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 proof=base.prove('ring',comb,bank_reference())
 # All true weights and every source NAND/Verilator clock keep the independent
 # C checks from the proven fixed-Q harness. Its old held bank is still proven
 # there as a baseline; the actual new bank proof above is additional.
 verification=base.check_cloud(net,weights,words,rows)
 import verify as checks
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 first=next(i for i,(_,y,m) in enumerate(rows) if m&1 and y&1048575);prefix=rows[:first+257]
 mutant=make(weights,True);wrong=checks.check_nand(prefix,mutant.encode());assert wrong>0
 return dict(status='pass',bank_all_state_proof=proof,full=verification,
  actual_missing_rotation_mismatches=wrong,negative_prefix_clocks=len(prefix))


def main():
 global STRIDE,OUT
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--stride',choices=['byte','word'],default='word');a=ap.parse_args()
 STRIDE=a.stride;OUT=OUT/STRIDE
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 sm=small();weights,words=base.weight_table();old,parts=base.make(weights);net=make(weights)
 c=base.reference();cases=base.fixtures(c,words,parts['alpha']);rows,expected=vectors(cases);bn,comb=bank_graph()
 (OUT/'slice.nl').write_bytes(net.encode());(OUT/'slice.v').write_text(rtl(net,base.NAME))
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 (OUT/'cases.json').write_text(json.dumps(cases,indent=2)+'\n')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','ci.py','physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:paths.add(R/n)
 report=dict(status='small ring/C fixture pass; complete Q graph only constructed; cloud pending',stride=STRIDE,metrics=metrics(net),before=metrics(old),
  bank=metrics(bn),small=sm,expected=expected,baseline_unstalled_clocks=1+128+128*(4+1+base.BOUNDED_LATENCY+1),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,scope='fixed layer0 Q matrix; new ready timings and ring activity, same integer outputs; no norm/attention or wholemodel claim',
  cases_sha256=sha((OUT/'cases.json').read_bytes()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['verification']=cloud_check(net,weights,words,rows,comb);report['status']='actual ring allstate CEC and complete NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ('status','metrics','before','bank','baseline_unstalled_clocks')},indent=2))
 print(json.dumps({k:v for k,v in expected.items() if k!='completed'},indent=2))


if __name__=='__main__':main()
