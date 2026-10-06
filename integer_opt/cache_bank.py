#!/usr/bin/env python3
"""Byte-serial circulating A8 prefix cache; no local large-gate simulation.

Each position is128 signed8 codes followed by the little-endian20-bit scale
in3 bytes. Four padding bits per position are counted, not hidden.
"""
from pathlib import Path
from collections import deque
import os,sys,json,hashlib,random,signal,argparse,subprocess,ctypes as ct,shutil
R=Path(os.environ.get('H3_CACHE_BANK_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache
from ring_model import load
from nand import Builder,metrics,with_state,verify_state,flip_output
from gate_check import Snapshot
from export import rtl
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_CACHE_BANK_OUT',str(R/'build/integer_opt/cache_bank')))
SLOTS=16*131;CW=12;NS=SLOTS*8+CW;NI=8+CW+2;NO=9


def control(b,cursor,address,we,reset,slots):
 eq=lambda bits,n:b.reduce([v if n>>i&1 else b.inv(v) for i,v in enumerate(bits)],b.land,1)
 ready=b.land(b.inv(reset),b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cursor,address)],b.land,1))
 keep=b.inv(b.lor(reset,eq(cursor,slots-1)))
 nxt=[b.land(keep,x) for x in b.add(cursor,[0]*len(cursor),1)[0]]
 return nxt,ready,b.land(ready,we)


def make(slots=SLOTS):
 a=(slots-1).bit_length();ns=8*slots+a;b=Builder(ns+8+a+2)
 old=list(range(2,ns+2));p=list(range(ns+2,ns+2+8+a+2))
 cursor=old[8*slots:];nxt,ready,write=control(b,cursor,p[8:8+a],p[-2],p[-1],slots)
 tail=[b.mux(write,x,y) for x,y in zip(old[:8],p[:8])]
 comb=b.finish(old[8:8*slots]+tail+nxt+old[:8]+[ready])
 return with_state(comb,ns),comb


class Model:
 def __init__(self,slots):
  self.slots=slots;self.a=(slots-1).bit_length();self.memory=deque([0]*slots);self.known=deque([False]*slots);self.cursor=0
 def state(self):
  return sum(v<<(8*i) for i,v in enumerate(self.memory))+(self.cursor<<(8*self.slots))
 def tick(self,x):
  data=x&255;addr=x>>8&((1<<self.a)-1);we=x>>(8+self.a)&1;reset=x>>(9+self.a)&1
  ready=not reset and self.cursor==addr;y=self.memory[0]+(int(ready)<<8)
  mask=256+(255 if ready and self.known[0] else 0)
  head=self.memory.popleft();known=self.known.popleft()
  self.memory.append(data if we and ready else head);self.known.append(True if we and ready else known)
  self.cursor=0 if reset or self.cursor==self.slots-1 else (self.cursor+1)&((1<<self.a)-1)
  if reset:self.known=deque([False]*self.slots)
  return y,mask


def arbitrary(slots,count=512):
 m=Model(slots);a=m.a;ns=slots*8+a;rng=random.Random(260776+slots);xs=[];ys=[]
 for i in range(count):
  m.memory=deque(rng.randrange(256) for _ in range(slots));m.known=deque([True]*slots)
  m.cursor=rng.randrange(1<<a);x=rng.getrandbits(8+a+2)
  if i%4==0:m.cursor=slots-1
  if i%4==1:x=(x&~(((1<<a)-1)<<8))+(m.cursor<<8)
  old=m.state();y,_=m.tick(x);xs.append(old+(x<<ns));ys.append(m.state()+(y<<ns))
 return xs,ys


def small():
 net,comb=make(14);assert metrics(net)['nNand']+net.n_state<=4000
 xs,ys=arbitrary(14);v=verify_state(comb,xs,ys,net.n_state);v.pop('nl_hex')
 # Real12-bit modulo2096/address control is independently small.
 b=Builder(26);d,ready,write=control(b,list(range(2,14)),list(range(14,26)),26,27,SLOTS)
 ctl=b.finish(d+[ready,write]);rng=random.Random(260777);xx=[];yy=[]
 for i in range(4096):
  cursor=i;address=i if i%3 else rng.randrange(4096);we=i>>1&1;reset=i&1
  nc=0 if reset or cursor==SLOTS-1 else (cursor+1)&4095
  r=not reset and cursor==address
  xx.append(cursor+(address<<12)+(we<<24)+(reset<<25));yy.append(nc+(int(r)<<12)+(int(r and we)<<13))
 c=verify_state(ctl,xx,yy,12);c.pop('nl_hex')
 return dict(small_bank=v,actual_modulo_control=c)


def golden():
 source=(R/'integer/int_model.c').read_text();blob=(R/'physical/model.bin').read_bytes()
 assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
 s=norm_cache.variant(source)
 s=s.replace('static int8_t cached_codes[32][128];','static uint8_t observed_cache[5][32][131];\nstatic int8_t cached_codes[32][128];',1)
 marker='        cached_epoch[p]=layer;reverse_stats[9]++;'
 assert s.count(marker)==1
 s=s.replace(marker,marker+'''
        for(int j=0;j<128;j++)observed_cache[layer][p][j]=(uint8_t)cached_codes[p][j];
        for(int j=0;j<3;j++)observed_cache[layer][p][128+j]=(uint32_t)cached_max[p]>>(8*j);
''',1)
 s+='\nuint32_t cache_observe(int l,int p,int j){return observed_cache[l][p][j];}\n'
 (OUT/'cache_golden.c').write_text(s);(OUT/'frozen.c').write_text(source)
 libs=[]
 for name in ['frozen','cache_golden']:
  subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(OUT/(name+'.c')),'-o',str(OUT/(name+'.so'))],check=True,timeout=30)
  libs.append(load(OUT/(name+'.so'),blob))
 ids=[random.Random(260778+i).randrange(192) for i in range(16)]
 want=norm_cache.forward_bytes(libs[0],ids);got=norm_cache.forward_bytes(libs[1],ids);assert got==want
 g=libs[1];g.cache_observe.argtypes=[ct.c_int]*3;g.cache_observe.restype=ct.c_uint32
 layers=[[[int(g.cache_observe(l,p,j)) for j in range(131)] for p in range(16)] for l in range(5)]
 assert all(v[-1]<16 and 1<=sum(v[128+j]<<(8*j) for j in range(3))<=524288 for layer in layers for v in layer)
 result=dict(ids=ids,logits_sha256=sha(got[0]),trace_sha256=sha(got[1]),compared_int32_words=sum(len(x)//4 for x in got),layers=layers,
  packing='128 signed8 two-complement bytes then u20 maximum little-endian3 bytes; upper4 padding bits zero')
 (OUT/'cases.json').write_text(json.dumps(result,indent=2)+'\n');return result


def vectors(cases):
 m=Model(SLOTS);rows=[];logical=[None]*SLOTS;rng=random.Random(260779)
 counts=dict(read_bytes=0,written_bytes=0,wait_clocks=0,unrelated_clocks=0,resets=0,invalid_request_clocks=0)
 def tick(addr=4095,data=0,we=0,reset=0):
  x=data+(addr<<8)+(we<<20)+(reset<<21);y,mask=m.tick(x);rows.append((x,y,mask))
  if reset:logical[:]=[None]*SLOTS;counts['resets']+=1
  elif y>>8:
   if logical[addr] is not None:assert y&255==logical[addr]
   if we:logical[addr]=data;counts['written_bytes']+=1
   else:counts['read_bytes']+=1
  return y
 def request(addr,data=None):
  for _ in range(SLOTS+1):
   y=tick(addr,0 if data is None else data,int(data is not None))
   if y>>8:return y&255
   counts['wait_clocks']+=1
  raise AssertionError('valid cache request did not become ready')
 def read_vector(p,want):
  # Brief unrelated arithmetic intervals explicitly advance physical storage.
  for _ in range(rng.randrange(7)):
   tick();counts['unrelated_clocks']+=1
  got=[request(p*131+j) for j in range(131)];assert got==want
 tick(reset=1)
 for layer in cases['layers']:
  for p,v in enumerate(layer):
   for j,byte in enumerate(v):request(p*131+j,byte)
  # One head for every causal query position; normalized inputs are head-shared.
  for p in range(15,-1,-1):
   read_vector(p,layer[p])
   for _ in range(2):
    for s in range(p+1):read_vector(s,layer[s])
 # Invalid addresses never write or become ready in reachable counter states.
 for _ in range(SLOTS):
  assert not tick(4095,rng.randrange(256),1)>>8;counts['invalid_request_clocks']+=1
 for p in range(16):read_vector(p,cases['layers'][-1][p])
 # Reset during partial overwrite invalidates all data; only refill is legal.
 for j in range(37):request(j,255-j)
 tick(reset=1,we=1,data=255,addr=0)
 boundary=[i&255 for i in range(SLOTS)]
 for i,byte in enumerate(boundary):request(i,byte)
 for i in range(SLOTS):assert request(i)==boundary[i]
 return rows,dict(clocks=len(rows),counts=counts,real_layer_vectors=80,real_query_positions=80,
  scope='all frozen-C norm cache bytes,one head per query position of5layers,invalid requests,unrelated cycles,reset/refill; full4head token/arithmetic controller absent')


def reference(slots=SLOTS):
 a=(slots-1).bit_length();bits=slots*8;ns=bits+a;ni=ns+8+a+2
 return f'''module top(input [{ni-1}:0] din,output [{ns+8}:0] dout);
wire [{bits-1}:0] memory=din[{bits-1}:0];
wire [{a-1}:0] cursor=din[{ns-1}:{bits}],address=din[{ns+8+a-1}:{ns+8}];
wire [7:0] data=din[{ns+7}:{ns}];
wire we=din[{ni-2}],reset=din[{ni-1}];
wire ready=!reset && cursor==address;
wire [{a-1}:0] next_cursor=(reset || cursor=={a}'d{slots-1}) ? {a}'d0 : cursor+{a}'d1;
assign dout[{bits-1}:0]={{(we && ready)?data:memory[7:0],memory[{bits-1}:8]}};
assign dout[{ns-1}:{bits}]=next_cursor;
assign dout[{ns+8}:{ns}]={{ready,memory[7:0]}};
endmodule
'''


def cloud_check(net,comb,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from nand import blif,from_yosys
 from ci import cec
 import verify as checks
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 xs,ys=arbitrary(SLOTS,64);arb=verify_state(comb,xs,ys,NS);arb.pop('nl_hex')
 (OUT/'row.blif').write_text(blif(comb))
 ysfile=OUT/'reference.ys'
 ysfile.write_text(f'read_verilog {OUT}/row.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ysfile)],check=True,stdout=subprocess.DEVNULL,timeout=240)
 ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out)
 (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
 good=cec(abc,OUT/'row.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 fault=flip_output(net);wrong=checks.check_nand(rows,fault.encode());assert wrong>0
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'cache_bank',str(OUT/'vectors.txt')))
 (OUT/'negative.v').write_text(rtl(fault,'cache_bank'))
 checks.run([checks.compile_rtl('source',OUT/'row.v')],300)
 exe=checks.compile_rtl('negative',OUT/'negative.v')
 failed=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
 assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
 return dict(status='pass',arbitrary_state=arb,all_state_output_cec=good,actual_D_mutation=bad,clocks=len(rows),
  nand_mismatches=0,rtl_clocks=len(rows),actual_data_gate_mismatches=wrong,actual_RTL_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();cases=golden();rows,expected=vectors(cases);net,comb=make()
 assert (net.n_state,net.n_in,net.n_out)==(NS,NI,NO)
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'cache_bank'))
 (OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','bench.py','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c','integer/int_model.c','physical/model.bin']:paths.add(R/name)
 report=dict(status='small bank/control checks and full C-data schedule pass; actual-size gate proof pending',metrics=metrics(net),small=sm,expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  capacity=dict(code_bits=16384,scale_bits=320,padding_bits=64,cursor_bits=12,total_latch=NS),
  contract='data8,address12,we,reset; output pre-edge byte8+ready; every edge rotates; ready only address==cursor and !reset; reset discards old validity, full refill required',
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  scope='actual serial cache and arbitrary address handshake; norm/A8 producers,FF packing,wholemodel controller and physical timing remain separate',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['verification']=cloud_check(net,comb,rows);report['status']='all cache D/output CEC and actual NAND/RTL/C-data replay pass with faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ('status','metrics','small','expected','capacity')},indent=2))


if __name__=='__main__':main()
