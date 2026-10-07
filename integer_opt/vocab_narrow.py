#!/usr/bin/env python3
"""Exact vocabulary scaling with bounded MUL and constant127 DIV state."""
from pathlib import Path
import argparse,hashlib,json,os,random,signal,sys,time
R=Path(os.environ.get('H3_VOCAB_NARROW_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_VOCAB_NARROW_OUT',str(R/'build/integer_opt/vocab_narrow')))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import vocab_scale as base
from nand import Builder,metrics,with_state,flip_output
from export import import_net
from gate_check import Snapshot
sha=lambda b:hashlib.sha256(b).hexdigest()
NS=291


def mul():
 b=Builder(128+75);q=list(range(2,130));p=list(range(130,205))
 acc=q[:54];a=q[54:108];y=q[108:];load=p[0];x=p[1:55];right=p[55:]
 product=b.add(acc,[b.land(y[0],v) for v in a])[0]
 nxt=[b.mux(load,v,0) for v in product]
 nxt += [b.mux(load,v,w) for v,w in zip([0]+a[:-1],x)]
 nxt += [b.mux(load,v,w) for v,w in zip(y[1:]+[0],right)]
 return with_state(b.finish(nxt+acc),128)


def div():
 b=Builder(50+43);q=list(range(2,52));p=list(range(52,95))
 A=q[:42];rem=q[42:49];sign=q[49];load=p[0];n=p[1:]
 neg=[b.xor(v,n[-1]) for v in n];magnitude=b.add(neg,[0]*42,n[-1])[0]
 low=[A[-1]]+rem[:6];ge=b.lor(rem[6],b.reduce(low,b.land,1))
 incremented=b.add(low,[0]*7,1)[0]
 nr=[b.mux(ge,v,w) for v,w in zip(low,incremented)]
 nxt=[b.mux(load,v,w) for v,w in zip([ge]+A[:-1],magnitude)]
 nxt += [b.mux(load,v,0) for v in nr]+[b.mux(load,sign,n[-1])]
 result=b.add([b.xor(v,sign) for v in A],[0]*42,b.xor(rem[6],sign))[0]
 return with_state(b.finish(nxt+result),50)


def make():
 m=mul();d=div();b=Builder(NS+base.NI);q=list(range(2,NS+2));p=list(range(NS+2,NS+2+base.NI))
 ms=q[:128];ds=q[128:178];temp=q[178:232];factor=q[232:250]
 count=q[250:256];phase=q[256:258];result=q[258:290];valid=q[290]
 reset,start=p[:2];dot=p[2:24];maximum=p[24:44];scale=p[44:62];keep=b.inv(reset)
 _,ctl=import_net(b,base.control(),count+phase+[valid,reset,start]);begin,first,ld,second,lm,finish,busy=ctl[9:]
 mx=[b.mux(begin,t,dot[i] if i<22 else dot[-1]) for i,t in enumerate(temp)]
 my=[b.mux(begin,factor[i] if i<18 else 0,maximum[i]) for i in range(20)]
 md,product=import_net(b,m,[b.lor(begin,lm)]+mx+my,ms)
 dd,quotient=import_net(b,d,[ld]+temp[:42],ds)
 _,rounded=import_net(b,base.tail(),product+[product[-1]]*10)
 td=[b.mux(first,t,v) for t,v in zip(temp,product)]
 td=[b.land(keep,b.mux(second,t,v)) for t,v in zip(td,quotient+[quotient[-1]]*12)]
 fd=[b.land(keep,b.mux(begin,f,g)) for f,g in zip(factor,scale)]
 rd=[b.land(keep,b.mux(finish,r,v)) for r,v in zip(result,rounded)]
 nxt=md+dd+td+fd+ctl[:8]+rd+[ctl[8]];assert len(nxt)==NS
 comb=b.finish(nxt+result+[valid,busy]);return with_state(comb,NS),comb


def full_small(net,cases):
 assert len(net.records)<=4000
 rng=random.Random(26100317);g=Snapshot.decode(net.encode(),base.NI,base.NO)
 frozen_expected=cases['expected'];cases=cases['tests'];count=len(cases);states=[bytes(rng.getrandbits(1) for _ in range(NS)) for _ in cases]
 bits=lambda x:bytes(x>>i&1 for i in range(base.NI))
 states=[s for s,_ in g.step_simd(states,[bits(rng.getrandbits(base.NI)|1) for _ in cases])]
 inputs=[bits(2+((a&((1<<22)-1))<<2)+(m<<24)+(f<<44)) for a,m,f in cases]
 step=g.step_simd(states,inputs);assert all(not any(y) for _,y in step);states=[s for s,_ in step]
 checks=0
 for tick in range(1,base.LATENCY):
  inputs=[bits(rng.getrandbits(base.NI)&~1) for _ in cases]
  step=g.step_simd(states,inputs)
  assert all(sum(v<<i for i,v in enumerate(y))==1<<33 for _,y in step),(tick,'busy/result')
  states=[s for s,_ in step]
  if tick in (21,65):
   for st,(a,m,f) in zip(states,cases):
    actual=sum(v<<i for i,v in enumerate(st[178:232]));expected=a*m if tick==21 else base.rne(a*m,127)
    assert actual==expected&((1<<54)-1),(tick,actual,expected)
    checks+=1
 inputs=[bits(0)]*count;step=g.step_simd(states,inputs)
 assert [base.rne(base.rne(a*m,127)*f,1<<24) for a,m,f in cases]==frozen_expected
 expected=[(v&0xffffffff)+(1<<32) for v in frozen_expected]
 assert [sum(v<<i for i,v in enumerate(y)) for _,y in step]==expected
 bad=Snapshot.decode(flip_output(net).encode(),base.NI,base.NO)
 assert [sum(v<<i for i,v in enumerate(y)) for _,y in bad.step_simd(states,inputs)]==[y^1 for y in expected]
 return dict(status='pass',cases=count,clocks_per_case=base.LATENCY+2,actual_parallel_clocks=count*(base.LATENCY+2),
             random_old_states_reset=True,busy_external_inputs_random_every_clock=True,first_product_and_division_checks=checks,
             actual_valid_output_gate_faults_rejected=count)


def reference():
 return '''module top(input [352:0] din,output [324:0] dout);
wire [290:0] q=din[290:0];wire [61:0] p=din[352:291];
wire [53:0] acc=q[53:0],a=q[107:54];wire [19:0] y=q[127:108];
wire [41:0] A=q[169:128];wire [6:0] rem=q[176:170];wire signbit=q[177];
wire [53:0] temp=q[231:178];wire [17:0] factor=q[249:232];
wire [5:0] count=q[255:250];wire [1:0] phase=q[257:256];wire [31:0] result=q[289:258];wire valid=q[290];
wire reset=p[0],start=p[1];wire signed [21:0] dot=p[23:2];wire [19:0] maximum=p[43:24];wire [17:0] scale=p[61:44];
wire busy=phase!=0,begin_op=!reset && start && !busy;
wire first=phase==1 && count==20,ld=phase==2 && count==0;
wire second=phase==2 && count==43,lm=phase==3 && count==0,finish_op=phase==3 && count==19;
wire mul_load=begin_op || lm;
wire [53:0] mx=begin_op?{{32{dot[21]}},dot}:temp;wire [19:0] my=begin_op?maximum:{2'b0,factor};
wire [53:0] next_acc=mul_load?54'b0:acc+(y[0]?a:54'b0);
wire [53:0] next_a=mul_load?mx:(a<<1);wire [19:0] next_y=mul_load?my:(y>>1);
wire [41:0] numerator=temp[41:0],magnitude=numerator[41]?-numerator:numerator;
wire [7:0] shifted_rem={rem,A[41]};wire ge=shifted_rem>=8'd127;wire [7:0] diff=shifted_rem-8'd127;
wire [6:0] next_rem=ld?7'b0:ge?diff[6:0]:shifted_rem[6:0];
wire [41:0] next_A=ld?magnitude:{A[40:0],ge};wire next_sign=ld?numerator[41]:signbit;
wire increment=({rem,1'b0}>8'd127);wire [41:0] quotient=(A^{42{signbit}})+{41'b0,(increment^signbit)};
wire signed [63:0] signed_product={{10{acc[53]}},acc};
wire signed [63:0] rounded=(signed_product>>>24)+((acc[23] && ((|acc[22:0]) || acc[24]))?64'sd1:64'sd0);
reg [5:0] nc;reg [1:0] np;reg nv;
always @* begin
 nc=count+(busy?6'd1:6'd0);np=phase;nv=valid;
 if(begin_op)begin nc=0;np=1;nv=0;end
 if(first)begin nc=0;np=2;end
 if(second)begin nc=0;np=3;end
 if(finish_op)begin nc=0;np=0;nv=1;end
 if(reset)begin nc=0;np=0;nv=0;end
end
wire [53:0] nt=reset?54'b0:second?{{12{quotient[41]}},quotient}:first?acc:temp;
wire [17:0] nf=reset?18'b0:begin_op?scale:factor;wire [31:0] nr=reset?32'b0:finish_op?rounded[31:0]:result;
assign dout={busy,valid,result,nv,nr,np,nc,nf,nt,next_sign,next_rem,next_A,next_y,next_a,next_acc};
endmodule
'''


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
 ap.add_argument('--reference-dir',type=Path,default=R/'build/integer_opt/vocab_scale');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 begin=time.monotonic();OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 if args.cloud:
  cases=base.cases();rows,expected=base.vectors(cases)
  (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 else:
  old=json.loads((args.reference_dir/'receipt.json').read_text())
  for n,h in old['sources'].items():assert sha((R/n).read_bytes())==h,n
  for name,key in [('cases.json','cases_sha256'),('vectors.txt','vector_sha256')]:
   raw=(args.reference_dir/name).read_bytes();assert sha(raw)==old[key];(OUT/name).write_bytes(raw)
  cases=json.loads((OUT/'cases.json').read_text());expected=old['expected'];rows=None
 assert sha((OUT/'cases.json').read_bytes())=='168ae96b2ffacc56808cc76d46aa0f0077f5afbf660ea7ea49b0164ab9a264dc'
 assert sha((OUT/'vectors.txt').read_bytes())=='d5b9b91605c98eeb9b427b3cb2cb6b551aaae5964d1437a35da887711eee38c6'
 before=base.make()[0];assert metrics(before)['sha256']=='84085cd2e1333385f3e84ce3a15bdbc7b97c1fb75ad3e4888afcefadb45fd149'
 net,comb=make();checked=full_small(net,cases)
 assert metrics(net)['sha256']=='2315bb01081f205f35c22d26c3bf48db056d6ca1aee60df119d8a020f11d4aee'
 for name,g in [('scale',net),('transition',comb),('mul',mul()),('div',div())]:(OUT/(name+'.nl')).write_bytes(g.encode())
 (OUT/'scale.v').write_text(base.rtl(net,'vocab_scale'));(OUT/'transition.ref.v').write_text(reference())
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ('integer_opt/vocab_scale_golden.c','integer_opt/vocab_units/manifest.json','integer_opt/vocab_units/head_scale.nl',
           'integer_opt/weights_golden.c','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c',
           'physical/units/manifest.json','physical/units/serial_mul.nl','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl'):
  paths.add(R/n)
 report=dict(status='complete small pipeline C replay passes locally; independent CEC and long protocol await Actions',
  before=metrics(before),metrics=metrics(net),comb_metrics=metrics(comb),mul=metrics(mul()),div=metrics(div()),small=checked,expected=expected,
  cases_sha256=sha((OUT/'cases.json').read_bytes()),vector_sha256=sha((OUT/'vectors.txt').read_bytes()),model_sha256=base.MODEL_SHA,
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  range_certificate='|dot*m|<2^41; quotient after RNE127 has magnitude<2^35; second product magnitude<2^53; signed54 MUL holds both. Constant127 remainder is0..126 from load; odd divisor excludes half ties, so increment iff remainder>=64.',
  contract='same62/34 pins,86clocks and all98456 C protocol vectors as R102; bounded internal arithmetic, no externally changed width or rounding',
  scope='standalone vocabulary scale, not complete vocabulary MAC/norm/sampler/model; state proof checks independent new transition, not arbitrary old-state projection from430 to291bits',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 receipt=OUT/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
 if args.cloud:
  previous=base.NS;base.NS=NS
  try:report['verification']=base.cloud(net,comb,rows,cases)
  finally:base.NS=previous
  report['status']='all291D/34outputs CEC and complete98456 actual NAND/RTL/C clocks pass; real faults rejected'
 report['seconds']=time.monotonic()-begin;save()
 print(json.dumps({k:v for k,v in report.items() if k in ('status','metrics','small','seconds')},indent=2))


if __name__=='__main__':main()
