#!/usr/bin/env python3
"""Reduce X ports by rotating one scalar only on accepted operations.

This keeps all20 bits and the original2560-bit X capacity. Rotation count
rises32x versus the four-word ring; no measured power improvement is claimed.
"""
from pathlib import Path
import sys,json,hashlib,signal,random,os,subprocess,argparse,shutil
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import ff_sublayer as source
from nand import Builder,with_state,metrics,verify_state,blif,flip_output,from_yosys
from export import import_net,load_unit,rtl
from prefix_store import select
NI,NO=26,32
sha=lambda data:hashlib.sha256(data).hexdigest()


def control(b,old,pins):
 phase=old[:2];index=old[2:9]
 reset,start,core_available,scan,replay,we,read=pins;keep=b.inv(reset)
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 p=[eq(phase,j) for j in range(4)]
 begin=b.reduce([keep,start,b.lor(p[0],p[3])],b.land,1)
 residual_begin=b.reduce([keep,p[1],core_available],b.land,1)
 write=b.reduce([keep,p[2],we],b.land,1)
 finish=b.land(write,b.reduce(index,b.land,1))
 available=b.reduce([keep,p[3],b.inv(start)],b.land,1);take=b.land(available,read)
 pp=phase[:]
 for action,target in ((begin,1),(residual_begin,2),(finish,3)):
  pp=[b.mux(action,v,target>>j&1) for j,v in enumerate(pp)]
 clear=b.reduce([reset,begin,residual_begin],b.lor,0)
 ni=[b.land(b.inv(clear),v) for v in b.add(index,[0]*7,b.lor(write,take))[0]]
 xadvance=b.land(keep,b.reduce([b.land(p[1],b.lor(scan,replay)),write,take],b.lor,0))
 hadvance=b.land(write,b.reduce(index[:5],b.land,1))
 return [b.land(keep,v) for v in pp]+ni,[begin,write,xadvance,hadvance,b.lor(p[1],p[2]),available,b.land(available,b.reduce(index,b.land,1))]


def small_control():
 b=Builder(16);ds,out=control(b,list(range(2,11)),list(range(11,18)));net=b.finish(ds+out);xs=[];ys=[]
 for old in range(512):
  p=old&3;idx=old>>2
  for flags in range(128):
   reset,start,ready,scan,replay,we,read=[flags>>j&1 for j in range(7)]
   begin=int(not reset and start and p in (0,3));rb=int(not reset and p==1 and ready)
   write=int(not reset and p==2 and we);finish=int(write and idx==127)
   available=int(not reset and p==3 and not start);take=available*read;pp=p
   if begin:pp=1
   if rb:pp=2
   if finish:pp=3
   nx=0 if reset or begin or rb else (idx+int(write or take))&127
   xa=int(not reset and (p==1 and (scan or replay) or write or take))
   ha=int(write and idx%32==31);last=int(available and idx==127)
   nxt=(0 if reset else pp)+(nx<<2)
   ys.append(nxt+(sum(v<<j for j,v in enumerate([begin,write,xa,ha,int(p in (1,2)),available,last]))<<9))
   xs.append(old+(flags<<9))
 result=verify_state(net,xs,ys,9);result.pop('nl_hex');return result


def rotate_store(b,old,value,load,advance):
 shifted=old[20:]+[b.mux(load,a,v) for a,v in zip(old[:20],value)]
 return [b.mux(advance,a,v) for a,v in zip(old,shifted)]


def small_store():
 ns=160;b=Builder(ns+22);old=list(range(2,ns+2));ins=list(range(ns+2,ns+24))
 ds=rotate_store(b,old,ins[:20],*ins[20:]);net=b.finish(ds+old[:20]);rng=random.Random(260700);xs=[];ys=[]
 for _ in range(512):
  state=rng.getrandbits(ns);value=rng.getrandbits(20);load=rng.randrange(2);advance=rng.randrange(2)
  nxt=state if not advance else (state>>20)+((value if load else state&1048575)<<(ns-20))
  xs.append(state+(value<<ns)+(load<<(ns+20))+(advance<<(ns+21)))
  ys.append(nxt+((state&1048575)<<ns))
 result=verify_state(net,xs,ys,ns);result.pop('nl_hex');return result


def xport(b,xstate,hhead,index,value,scan,write,advance):
 x=xstate[:20];h=select(b,[hhead[j*20:(j+1)*20] for j in range(32)],index[:5])
 _,summed=import_net(b,load_unit('resid'),x+h)
 stored=[b.mux(write,a,v) for a,v in zip(value,summed)]
 return rotate_store(b,xstate,stored,b.lor(scan,write),advance),x


def make():
 core=source.source.make()[0];assert sha(core.encode())=='5c22cdf28262b759ad03dfc36fef7f28c93841273a29b5f14393afc832414b0d'
 ns=core.n_state+2560+9;b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
 cs=old[:6149];xs=old[6149:8709];ps=old[-9:];reset,start=ins[:2];value=ins[2:22];xvalid,enable,we,read=ins[22:]
 active=b.land(ps[0],b.inv(ps[1]));keep=b.inv(reset);index=ps[2:9]
 _,probe=import_net(b,core,[reset]+[0]*25,cs);norm_index=probe[640:647];replaying=probe[648]
 input_ready=b.reduce([keep,active,probe[647],b.inv(replaying)],b.land,1);scan=b.land(input_ready,xvalid)
 replay=b.reduce([keep,active,probe[647],replaying,enable],b.land,1)
 pd,po=control(b,ps,[reset,start,probe[671],scan,replay,we,read]);begin,write,xadvance,hadvance,busy,available,last=po
 xd,x=xport(b,xs,probe[:640],index,value,scan,write,xadvance)
 feed=[b.mux(replaying,a,v) for a,v in zip(value,x)]
 source_valid=b.reduce([keep,active,b.mux(replaying,xvalid,enable)],b.land,1)
 cd,out=import_net(b,core,[reset,begin]+feed+[source_valid,enable,we,hadvance],cs)
 public=x+norm_index+[input_ready,replaying,busy,available,last]
 net=with_state(b.finish(cd+xd+pd+public),ns)
 return net,dict(core=metrics(core),original_X_bits=2560,parent_bits=9,other_bits=ns-7808,
  one_scalar_per_accepted_X_rotation=True,scope='same exact layer0 FFN sublayer; held scalar X ring replaces held four-word X ring')


OUT=R/'build/integer_opt/ff_sublayer_ring'


def prove_xport():
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 b=Builder(3230);bits=list(range(2,3232))
 ds,x=xport(b,bits[:2560],bits[2560:3200],bits[3200:3207],bits[3207:3227],*bits[3227:])
 net=b.finish(ds+x)
 (OUT/'xport.blif').write_text(blif(net));(OUT/'xport.negative.blif').write_text(blif(flip_output(net)))
 (OUT/'xport.ref.v').write_text('''module top(input [3229:0] din,output [2579:0] dout);
wire [2559:0] old=din[2559:0];wire [639:0] h=din[3199:2560];
wire [6:0] ix=din[3206:3200];wire [19:0] external_x=din[3226:3207];
wire scan=din[3227],wr=din[3228],advance=din[3229];
wire signed [19:0] x=old[19:0],delta=h[ix[4:0]*20+:20];
wire signed [20:0] sum={x[19],x}+{delta[19],delta};
wire [19:0] clipped=(sum>21'sd524287)?20'd524287:(sum< -21'sd524288)?20'h80000:sum[19:0];
wire [19:0] tail=(scan || wr)?(wr?clipped:external_x):x;
assign dout={x,advance?{tail,old[2559:20]}:old};
endmodule
''')
 script=OUT/'xport.ys';script.write_text(f'read_verilog {OUT}/xport.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/xport.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'xport.yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=120)
 ref=from_yosys(json.loads((OUT/'xport.ref.json').read_text()),3230,2580);(OUT/'xport.ref.blif').write_text(blif(ref))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 positive=cec(abc,OUT/'xport.blif',OUT/'xport.ref.blif',OUT/'xport.cec.log');assert positive['verdict']=='equivalent'
 negative=cec(abc,OUT/'xport.negative.blif',OUT/'xport.ref.blif',OUT/'xport.negative.log');assert negative['verdict']=='different'
 return dict(scope='all X2560/H640 states,read index,external value and scan/write/advance combinations; exact scalar X rotation and saturated tail insertion',
  metrics=metrics(net),proof=positive,negative=negative)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);source.OUT=OUT
 small=small_control();store=small_store();net,parts=make();c=source.reference();fixtures=source.cases(c)
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_sublayer'))
 (OUT/'cases.json').write_text(json.dumps(fixtures,indent=2)+'\n')
 assert sha((OUT/'cases.json').read_bytes())=='e586e22a4dd3a2501781d75b3f790dff4454ec07b0e16a959525150eaa424074'
 paths=set()
 for module in list(sys.modules.values()):
  path=getattr(module,'__file__',None)
  if path:
   path=Path(path).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for name in ['integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/weights_golden.c',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl','physical/units/resid.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/name)
 before=dict(nNand=123793,nLatch=8719,sha256='d94cc98221afae350cd0aa7326198e754105aa4231864d0e6de18634aa54a5fc')
 report=dict(status='small control/scalar bank and unchanged C cases pass; complete graph construction only',metrics=metrics(net),parts=parts,before=before,
  small_control=small,small_store=store,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),golden_cases=len(fixtures),numerical_contract_changed=False,
  transfer_counts=dict(scope='one completed vector including all128 result reads; excludes pauses and aborted rows',
   X_word_rotations_before=16,X_scalar_rotations_after=512,ring_bits=2560,rotation_count_ratio=32,
   residual_phase_cycles_before=132,residual_phase_cycles_after=128,power_measured=False),
  contract='same26-input/32-output scalar interface; X shifts by20 only on accepted scan/replay/residual/read',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)})
 if args.cloud:
  report['xport_proof']=prove_xport();report['verification']=source.check(net,fixtures,scalar_x=True);report['status']=report['verification']['status']
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))


if __name__=='__main__':main()
