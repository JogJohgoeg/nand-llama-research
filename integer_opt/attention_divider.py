#!/usr/bin/env python3
"""Exact saturated attention division: signed42 / nonzero unsigned22.

Only the already-proven accumulator domain is narrowed. No model rule changes.
One load plus 42 restoring steps; the output wrapper holds a result until read.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,ctypes as ct,subprocess,shutil
R=Path(os.environ.get('H3_ATTENTION_DIV_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,simulate,flip_output,blif,from_yosys
from golden import Netlist
from export import import_net,rtl
from gate_check import Snapshot
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_ATTENTION_DIV_OUT',str(R/'build/integer_opt/attention_divider')))
N,D,DS=42,22,87
NS,NI,NO=115,67,22


def division(b,old,pins):
 a=old[:N];rem=old[N:N+D];den=old[N+D:N+2*D];sign=old[-1]
 load=pins[0];n=pins[1:1+N];d=pins[1+N:]
 shifted=[a[-1]]+rem
 diff,ge=b.add(shifted,[b.inv(x) for x in den]+[1],1)
 mag=b.add([b.xor(x,n[-1]) for x in n],[0]*N,n[-1])[0]
 nxt=[b.mux(load,x,y) for x,y in zip([ge]+a[:-1],mag)]
 nxt += [b.mux(load,b.mux(ge,x,y),0) for x,y in zip(shifted[:D],diff[:D])]
 nxt += [b.mux(load,x,y) for x,y in zip(den,d)]+[b.mux(load,sign,n[-1])]
 # Strict comparison (2*rem + quotient_parity) > denominator implements RNE.
 _,up=b.add([a[0]]+rem,[b.inv(x) for x in den]+[1],0)
 res=b.add([b.xor(x,sign) for x in a],[0]*N,b.xor(up,sign))[0]
 top=res[19:];negative=res[-1]
 overflow=b.mux(negative,b.reduce(top,b.lor,0),b.inv(b.reduce(top,b.land,1)))
 sat=[b.mux(overflow,x,b.inv(negative)) for x in res[:19]]+[negative]
 return nxt,sat


def raw():
 b=Builder(DS+1+N+D);bits=list(range(2,2+b.n_in));ds,y=division(b,bits[:DS],bits[DS:])
 comb=b.finish(ds+y);return with_state(comb,DS),comb


def legacy_raw():
 directory=R/'integer_opt/pilot_units';meta=json.loads((directory/'manifest.json').read_text())['serial_div']
 data=(directory/'serial_div.nl').read_bytes();assert sha(data)==meta['sha256']
 net=Netlist.decode(data,meta['nIn'],meta['nOut']);b=Builder(net.n_state+65);bits=list(range(2,2+b.n_in))
 old=bits[:net.n_state];p=bits[net.n_state:];n=p[1:43]
 ds,out=import_net(b,net,p[:1]+n+[n[-1]]*22+p[43:]+[0]*3,old)
 comb=b.finish(ds+out[-20:]);return with_state(comb,net.n_state),comb


def make(legacy=False):
 div=(legacy_raw() if legacy else raw())[0];nd=div.n_state;cw=7 if legacy else 6;steps=64 if legacy else 42
 ns=nd+2+cw+20;b=Builder(ns+NI);s=list(range(2,2+ns));p=list(range(2+ns,2+ns+NI))
 old=s[:nd];phase=s[nd:nd+2];count=s[nd+2:nd+2+cw];result=s[-20:]
 reset,start,read=p[:3];n=p[3:45];den=p[45:]
 def eq(x,v):return b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(x)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);begin=AND(keep,eq(phase,0),start,b.reduce(den,b.lor,0))
 run=eq(phase,1);finish=AND(keep,run,eq(count,steps));valid=AND(keep,eq(phase,2));take=AND(valid,read)
 dd,y=import_net(b,div,[begin]+n+den,old)
 ph=phase[:]
 for event,value in ((begin,1),(finish,2),(take,0)):ph=[b.mux(event,x,value>>i&1) for i,x in enumerate(ph)]
 clear=b.lor(reset,b.lor(begin,finish));cn=b.add(count,[0]*cw,run)[0]
 ds=dd+[AND(keep,x) for x in ph]+[AND(b.inv(clear),x) for x in cn]
 ds += [AND(keep,b.mux(finish,x,v)) for x,v in zip(result,y)]
 comb=b.finish(ds+result+[valid,AND(keep,run)]);return with_state(comb,ns),comb


def step(state,pins):
 a=state&((1<<N)-1);rem=state>>N&((1<<D)-1);den=state>>(N+D)&((1<<D)-1);sign=state>>(N+2*D)&1
 load=pins&1;n=pins>>1&((1<<N)-1);d=pins>>(1+N)&((1<<D)-1)
 shift=2*rem+(a>>(N-1));ge=int(shift>=den)
 aa=((a<<1)+ge)&((1<<N)-1);rr=(shift-ge*den)&((1<<D)-1)
 if load:
  sn=n-(1<<N) if n>>(N-1) else n;aa=abs(sn);rr=0;den_next=d;sg_next=int(sn<0)
 else:den_next=den;sg_next=sign
 ds=aa+(rr<<N)+(den_next<<(N+D))+(sg_next<<(N+2*D))
 up=int(2*rem+(a&1)>den);v=(-a-up if sign else a+up)&((1<<N)-1)
 v=v-(1<<N) if v>>(N-1) else v;sat=max(-524288,min(524287,v))&1048575
 return ds,sat


def client_step(state,p):
 old=state&((1<<DS)-1);phase=state>>DS&3;count=state>>(DS+2)&63;result=state>>(DS+8)&1048575
 reset=p&1;start=p>>1&1;read=p>>2&1;den=p>>45
 begin=not reset and phase==0 and start and den!=0;finish=not reset and phase==1 and count==42
 valid=not reset and phase==2;take=valid and read
 dd,y=step(old,int(begin)+((p>>3)<<1))
 np=0 if reset or take else 1 if begin else 2 if finish else phase
 nc=0 if reset or begin or finish else (count+int(phase==1))&63
 nr=0 if reset else y if finish else result
 out=result+(int(valid)<<20)+(int(not reset and phase==1)<<21)
 return dd+(np<<DS)+(nc<<(DS+2))+(nr<<(DS+8)),out


def arbitrary():
 rng=random.Random(260776);report={}
 for name,(net,comb),fn in [('raw',raw(),step),('client',make(),client_step)]:
  xs=[rng.getrandbits(comb.n_in) for _ in range(2048)];ys=[]
  for x in xs:
   ds,y=fn(x&((1<<net.n_state)-1),x>>net.n_state);ys.append(ds+(y<<net.n_state))
  assert metrics(comb)['nNand']<=4000 and simulate(comb,xs)==ys
  bad=sum(a!=b for a,b in zip(simulate(flip_output(comb),xs),ys));assert bad==len(xs)
  report[name]=dict(cases=len(xs),actual_D_gate_mutation_mismatches=bad,metrics=metrics(net))
 return report


def cases():
 import value_normalize as norm
 norm.OUT=OUT;norm.base.OUT=OUT;g,c=norm.golden()
 real=[(n,sum(v['weights'])) for v in c['cases'] for partial in v['partials'] for n in partial];assert len(real)==2560
 pairs=real[:];lo=-(1<<41);hi=(1<<41)-1;rng=random.Random(260777)
 pairs += [(n,d) for n in (lo,lo+1,-524289,-524288,-3,-1,0,1,3,524287,524288,hi) for d in (1,2,3,65536,1048576,4194303)]
 for d in (2,4,6,65536,1048576,4194302):
  for q in (-524289,-524288,-9,-8,-3,-2,-1,0,1,2,3,8,9,524286,524287,524288):
   for delta in (-1,0,1):
    n=q*d+d//2+delta
    if lo<=n<=hi:pairs.append((n,d))
 pairs += [(rng.randint(lo,hi),rng.randrange(1,1<<22)) for _ in range(2048)]
 return g,pairs,dict(count=len(pairs),real_partial_numerators=len(real),pairs_sha256=sha(json.dumps(pairs).encode()),
  source_cases_sha256=sha((OUT/'cases.json').read_bytes()))


def vectors(g,pairs,legacy=False):
 steps=64 if legacy else 42;rng=random.Random(260778);rows=[];phase=count=result=pending=0
 stats=dict(started=0,completed=0,outputs=0,aborted=0,busy_starts=0,output_holds=0,zero_den_rejected=0)
 outputs=[]
 def tick(n=0,d=1,start=0,read=0,reset=0,first=False):
  nonlocal phase,count,result,pending
  x=reset+(start<<1)+(read<<2)+((n&((1<<N)-1))<<3)+(d<<45)
  y=(result&1048575)+(int(not reset and phase==2)<<20)+(int(not reset and phase==1)<<21)
  rows.append((x,y,0 if first else (1<<NO)-1))
  if reset:
   stats['aborted']+=int(phase!=0);phase=count=result=0
  elif phase==0:
   if start and d:
    q,r=divmod(abs(n),d);q+=int(2*r>d or 2*r==d and q&1)
    want=max(-524288,min(524287,-q if n<0 else q));pending=int(g.slice_sat(g.int_rne(n,d)));assert pending==want
    phase=1;count=0;stats['started']+=1
   elif start:stats['zero_den_rejected']+=1
  elif phase==1:
   stats['busy_starts']+=int(start)
   if count==steps:result=pending;phase=2;count=0;stats['completed']+=1
   else:count+=1
  elif phase==2:
   if read:phase=0;outputs.append(result);stats['outputs']+=1
   else:stats['output_holds']+=1
 def noise(read=0):tick(rng.randint(-(1<<41),(1<<41)-1),rng.randrange(1<<22),start=int(rng.randrange(7)==0),read=read)
 tick(reset=1,first=True)
 for _ in range(4):tick(start=1,d=0,read=1)
 for n,d in pairs:
  tick(n,d,start=1);accepted=len(rows)-1
  while phase!=2:noise(read=1)
  assert len(rows)-1-accepted==steps+1
  noise();noise();tick(read=1)
 for elapsed in (1,2,steps//2,steps,steps+1,steps+2):
  tick(-3,2,start=1)
  for _ in range(elapsed-1):noise()
  tick(reset=1,start=1,read=1);tick()
 tick(-3,2,start=1)
 while phase!=2:noise()
 tick(read=1);tick()
 assert outputs[-1]==-2 and stats['zero_den_rejected']==4
 return rows,dict(clocks=len(rows),counts=stats,steps_after_load=steps,load_to_observed_valid=steps+2,
  outputs_sha256=sha(b''.join(x.to_bytes(4,'little',signed=True) for x in outputs)))


def check(net,rows):
 import verify
 assert metrics(net)['nNand']+net.n_state<=4000
 verify.OUT=OUT;verify.NI=NI;verify.NO=NO
 lib=OUT/'sim.so'
 if not lib.exists():subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(lib)],check=True,timeout=30)
 assert verify.check_nand(rows,net.encode())==0
 end=next(i for i,(_,y,_) in enumerate(rows) if y>>20&1)+2
 bad=verify.check_nand(rows[:end],flip_output(net).encode());assert bad>0
 return dict(status='pass',clocks=len(rows),nand_mismatches=0,negative_prefix_clocks=end,actual_result_gate_mismatches=bad)


def reference(raw_only=False):
 core='''module divider_ref(input [151:0] din,output [106:0] dout);
wire [41:0] a=din[41:0];wire [21:0] rem=din[63:42],den=din[85:64];wire sign=din[86];
wire load=din[87];wire signed [41:0] n=din[129:88];wire [21:0] d=din[151:130];
wire [22:0] shift={rem,a[41]};wire ge=shift>={1'b0,den};
wire [21:0] r_step=ge?shift-{1'b0,den}:shift;
wire [41:0] next_a=load?(n<0?-n:n):{a[40:0],ge};
wire [21:0] next_rem=load?22'b0:r_step,next_den=load?d:den;
wire next_sign=load?n[41]:sign;
wire up=({rem,1'b0}>{1'b0,den}) || ({rem,1'b0}=={1'b0,den} && a[0]);
wire [41:0] magnitude=a+up;wire signed [41:0] result=sign?-magnitude:magnitude;
wire signed [19:0] sat=result>42'sd524287?20'sd524287:result< -42'sd524288?-20'sd524288:result[19:0];
assign dout={sat,next_sign,next_den,next_rem,next_a};
endmodule
'''
 if raw_only:return core.replace('divider_ref','top',1)
 return core+'''module top(input [181:0] din,output [136:0] dout);
wire [86:0] ds=din[86:0];wire [1:0] phase=din[88:87];wire [5:0] count=din[94:89];wire [19:0] result=din[114:95];
wire [66:0] p=din[181:115];wire reset=p[0],start=p[1],take_out=p[2];
wire [41:0] n=p[44:3];wire [21:0] den=p[66:45];
wire begin_op=!reset && phase==0 && start && den!=0;
wire finish=!reset && phase==1 && count==42,valid=!reset && phase==2,busy=!reset && phase==1;
wire take=valid && take_out;
wire [106:0] q;divider_ref divide({den,n,begin_op,ds},q);
reg [1:0] next_phase;reg [5:0] next_count;
always @* begin
 next_phase=phase;next_count=count+(phase==1);
 if(reset)begin next_phase=0;next_count=0;end
 else if(begin_op)begin next_phase=1;next_count=0;end
 else if(finish)begin next_phase=2;next_count=0;end
 else if(take)next_phase=0;
end
wire [19:0] next_result=reset?20'b0:finish?q[106:87]:result;
assign dout={busy,valid,result,next_result,next_count,next_phase,q[86:0]};
endmodule
'''


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);a=arbitrary();g,p,cr=cases();net,comb=make()
 rows,expected=vectors(g,p);checked=check(net,rows);print('compact',expected['clocks'],flush=True)
 old_rows,old_expected=vectors(g,p,True);old=make(True)[0];old_checked=check(old,old_rows);print('old',old_expected['clocks'],flush=True)
 assert expected['outputs_sha256']==old_expected['outputs_sha256']
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'));(OUT/'row.ref.v').write_text(reference())
 (OUT/'raw.nl').write_bytes(raw()[0].encode());(OUT/'legacy.nl').write_bytes(old.encode())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='complete local small-graph/C checks pass; cloud all-state CEC and RTL pending',metrics=metrics(net),previous=metrics(old),
  raw=metrics(raw()[0]),previous_raw=metrics(legacy_raw()[0]),arbitrary=a,cases=cr,expected=expected,previous_expected=old_expected,
  local_verification=checked,previous_verification=old_checked,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,
  scope='standalone s42/u22 attention divider and handshake, not a general s64/u25 replacement; R70 head integration pending')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   path=Path(name).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','physical/golden_slice.c',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  import value_row as base
  base.OUT=OUT;base.NI=NI;base.NO=NO;base.reference=reference
  report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='every D and output versus independent restoring-divider/control RTL for arbitrary state/input; 42-step numerical behavior tested against frozen C; no monolithic old-width equivalence or full-head reachability claim'
  report['status']='all-state/output CEC and complete NAND/RTL/C pass with actual faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2));print('source hashes',len(paths))


if __name__=='__main__':main()
