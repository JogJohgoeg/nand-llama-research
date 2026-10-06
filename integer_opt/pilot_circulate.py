#!/usr/bin/env python3
"""Integrate the always-rotating KV candidate with the real integer pilot.

Values are unchanged but requests must wait for the circulating address.
RAM rotates even during unrelated arithmetic and on reset. Local checks
only simulate a74-NAND/34-state-bit bank; full checks remain on Actions.
"""
from pathlib import Path
import sys,signal,json,random,hashlib,os,argparse
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import pilot_bank as source
from nand import Builder,metrics,with_state,verify_state,blif
sha=lambda b:hashlib.sha256(b).hexdigest()
NI,NO=source.NI,source.NO
LOW=(1<<276)-1
OUT=R/'build/integer_opt/pilot_circulate'


def bank(rows,width):
 a=(rows-1).bit_length();s=rows*width;ns=s+a
 assert rows>1 and rows&(rows-1)==0
 b=Builder(ns+width+a+3);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+width+a+3))
 data=pins[:width];addr=pins[width:width+a];request,reset,we=pins[-3:];cursor=old[s:]
 match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cursor,addr)],b.land,1)
 active=b.land(request,b.inv(reset));write=b.land(active,b.land(match,we))
 tail=[b.mux(write,x,y) for x,y in zip(old[:width],data)]
 nxt=old[width:s]+tail+[b.land(b.inv(reset),v) for v in b.add(cursor,[0]*a,1)[0]]
 ready=b.lor(b.inv(request),b.land(b.inv(reset),match))
 comb=b.finish(nxt+old[:width]+[ready])
 return comb,with_state(comb,ns)


def reference(rows,width):
 a=(rows-1).bit_length();s=rows*width;ns=s+a;ni=ns+width+a+3
 return f'''module top(input [{ni-1}:0] din,output [{ns+width}:0] dout);
wire [{s-1}:0] old=din[{s-1}:0];
wire [{a-1}:0] cursor=din[{ns-1}:{s}],addr=din[{ns+width+a-1}:{ns+width}];
wire [{width-1}:0] data=din[{ns+width-1}:{ns}];
wire request=din[{ni-3}],reset=din[{ni-2}],we=din[{ni-1}];
wire accept=request && !reset && cursor==addr && we;
assign dout[{s-width-1}:0]=old[{s-1}:{width}];
assign dout[{s-1}:{s-width}]=accept ? data : old[{width-1}:0];
assign dout[{ns-1}:{s}]=reset ? {a}'d0 : cursor+{a}'d1;
assign dout[{ns+width-1}:{ns}]=old[{width-1}:0];
assign dout[{ns+width}]=!request || (!reset && cursor==addr);
endmodule
'''


def small():
 rows=4;width=8;a=2;s=rows*width;ns=s+a;comb,net=bank(rows,width)
 assert metrics(net)['nNand']+metrics(net)['nLatch']<=4000
 rng=random.Random(260710);xs=[];ys=[]
 for j in range(2048):
  old=[rng.getrandbits(width) for _ in range(rows)];cursor=j&3;addr=j>>2&3
  req=j>>4&1;reset=j>>5&1;we=j>>6&1;data=rng.getrandbits(width)
  x=sum(v<<(width*k) for k,v in enumerate(old))+(cursor<<s)
  x+=(data+(addr<<width)+(req<<(width+a))+(reset<<(width+a+1))+(we<<(width+a+2)))<<ns
  nxt=old[1:]+[data if req and not reset and addr==cursor and we else old[0]]
  c=0 if reset else (cursor+1)%rows;ready=int(not req or not reset and addr==cursor)
  y=sum(v<<(width*k) for k,v in enumerate(nxt))+(c<<s)+(old[0]<<ns)+(ready<<(ns+width))
  xs.append(x);ys.append(y)
 result=verify_state(comb,xs,ys,ns);result.pop('nl_hex');return result


def make():
 # Use the existing composition unchanged; replace only its bank factory.
 # Restore it even if construction fails. No other process/module is edited.
 old_bank=source.bank
 try:
  before=source.make()[0];source.bank=bank
  net,comb,storage,manifest=source.make()
 finally:source.bank=old_bank
 return net,comb,storage,manifest,metrics(before)


def vectors(g):
 # Values come from the fixed C arithmetic/weight reference or this independent
 # logical bank. No NAND output supplies an expected value or a seek decision.
 baseline,groups=source.checks.vectors(g);rows=[];cursor=0;logical=[0]*32;known=[False]*32
 counts=dict(reset=0,seek=0,read=0,write=0,other=0,bank_rotations=0,unrelated_rotations=0,accepted_random_writes=0)
 rng=random.Random(260711)
 def tick(value,want=0,mask=LOW):
  nonlocal cursor,logical,known
  addr=value&31;data=value>>13&LOW;we=value>>289&1;reset=value>>290&1;view=value>>291
  ready=int(view!=2 or not reset and cursor==addr)
  if view==2:
   want=logical[cursor];mask=LOW if ready and known[cursor] else 0
  rows.append((value,want+(ready<<276),mask|(1<<276)))
  if view==2 and reset:
   # Physical data also rotates on this edge. The logical cursor alone resets.
   offset=(cursor+1)%32;logical=logical[offset:]+logical[:offset];known=[False]*32;cursor=0
   counts['reset']+=1
  else:
   if view==2:
    if cursor!=addr:counts['seek']+=1
    elif we:logical[cursor]=data;known[cursor]=True;counts['write']+=1
    else:counts['read']+=1
   else:counts['other']+=1;counts['unrelated_rotations']+=1
   cursor=(cursor+1)%32
  counts['bank_rotations']+=1
 def request(addr,data=0,we=0):
  value=addr+(data<<13)+(we<<289)+(2<<291)
  while cursor!=addr:tick(value)
  tick(value)
 tick((2<<291)+(1<<290))
 for value,want,mask in baseline:
  if value>>291==2:
   while cursor!=(value&31):tick(value)
   if mask:assert logical[value&31]==want
  tick(value,want,mask)
 # Prove data survives every unrelated scalar/weight operation above.
 for addr in rng.sample(list(range(32)),32):request(addr)
 # Distracting write/reset pins outside bank mode must not overwrite RAM.
 for j in range(256):
  addr=rng.randrange(8192);value=addr+(rng.getrandbits(276)<<13)+(rng.randrange(2)<<289)+(rng.randrange(2)<<290)+(1<<291)
  tick(value,g.slice_word(addr))
  if j%8==0:request(rng.randrange(32))
 # Requests may change while waiting; any actually ready write is accepted.
 for _ in range(256):
  addr=rng.randrange(32);we=rng.randrange(2)
  counts['accepted_random_writes']+=int(we and cursor==addr)
  tick(addr+(rng.getrandbits(276)<<13)+(we<<289)+(2<<291))
  gap=rng.randrange(8192);tick(gap+(1<<291),g.slice_word(gap))
 for addr in rng.sample(list(range(32)),32):request(addr)
 tick(((cursor+7)%32)+(1<<289)+(1<<290)+(2<<291))
 for addr in range(32):request(addr,rng.getrandbits(276),1)
 for addr in rng.sample(list(range(32)),32):request(addr)
 assert counts['bank_rotations']==len(rows) and counts['unrelated_rotations']>10000
 return rows,dict(baseline_clocks=len(baseline),baseline_groups=groups,clocks=len(rows),operations=counts,
  protocol='pre-edge ready; every clock rotates, including accepted reads and all unrelated computation; matching writes alone replace departing head',
  reset='bank rotates while cursor resets; old logical validity discarded and all rows reinitialized',
  power_scope='all8832 data LATCH input positions move each clock; no power or complete-token cycle claim')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);source.OUT=OUT;source.checks.OUT=OUT
 assert sha((R/'physical/model.bin').read_bytes())==source.physical.MODEL_SHA
 small_result=small();net,comb,storage,manifest,before=make()
 assert before['sha256']=='ebed6952838d5e9ca05a3c3a8f87d3a27bc5854f2176fa0248aee8af2bfab02f'
 rows,expected=vectors(source.checks.golden())
 from export import rtl
 (OUT/'slice.nl').write_bytes(net.encode());(OUT/'slice.v').write_text(rtl(net,'int_c16_ring_slice'))
 (OUT/'vectors.txt').write_text(''.join(f'{a:x} {b:x} {m:x}\n' for a,b,m in rows))
 (OUT/'bank.ref.v').write_text(reference(32,276));(OUT/'bank.blif').write_text(blif(comb))
 assert sha(net.encode())=='dbcceb5d45129b1bd28c14d42d9b4eacc0600ed996a1f73783e8c33b574afc68'
 assert sha((OUT/'vectors.txt').read_bytes())=='49f580a7808873520b66b9f828389596b05bc8be33c5f682d62e5d2aa1318b88'
 report=dict(status='small bank pass; complete pilot and C vectors constructed only',
  metrics=metrics(net),before=before,bank=metrics(storage),small=small_result,expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),weight_sources=manifest,numerical_contract_changed=False,
  adopted=False,scope='R10 continuous-rotation bank candidate integrated with real L0 weights/integer arithmetic; not full-model clock scheduling',
  tradeoff='same9242 state bits;8832 RAM data positions rotate every clock including unrelated operations; no physical power or complete-token timing claim')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['physical/golden_slice.c','integer/int_model.c','physical/nl_sim.c','physical/model.bin',
              'physical/units/manifest.json','integer_opt/pilot_units/manifest.json']:
  paths.add(R/name)
 for name in ['serial_mul','serial_sqrt','resid','exp','dot32']:paths.add(R/'physical/units'/(name+'.nl'))
 paths.update((R/'integer_opt/pilot_units').glob('*.nl'))
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),model_sha256=source.physical.MODEL_SHA,
  sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['verification']=source.cloud_check(net,comb,rows)
  report['status']='bank all-input CEC and complete actual NAND/RTL/C pilot pass, with real mutations'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','weight_sources')},indent=2))


if __name__=='__main__':main()
