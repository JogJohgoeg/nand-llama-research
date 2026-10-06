#!/usr/bin/env python3
"""Keep exact H storage, but rotate16 s20 lanes instead of32.

Only construction and <=4k gate checks run locally. Full H storage and FFN
verification stay on Actions. A second halfword move stalls gate/up DOT.
"""
from pathlib import Path
import sys,json,hashlib,signal,random,os,argparse,ctypes as ct,subprocess,shutil
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import ff_halfword as source
from nand import Builder,metrics,with_state,verify_state,blif,flip_output,from_yosys
from prefix_store import select
NI,NO=26,32
sha=lambda x:hashlib.sha256(x).hexdigest()


def bank(b,memory,value,index,write,rotate):
 lanes=1<<len(index);width=20*lanes;head=[]
 for lane in range(lanes):
  match=b.reduce([v if lane>>j&1 else b.inv(v) for j,v in enumerate(index)],b.land,1)
  accept=b.land(write,match)
  head += [b.mux(accept,a,v) for a,v in zip(memory[20*lane:20*(lane+1)],value)]
 changed=head+memory[width:];shifted=memory[width:]+memory[:width]
 return [b.mux(rotate,a,v) for a,v in zip(changed,shifted)]


def small_bank():
 ns=240;b=Builder(ns+24);old=list(range(2,ns+2));pins=list(range(ns+2,ns+26))
 ds=bank(b,old,pins[:20],pins[20:22],*pins[22:]);net=b.finish(ds+old[:20]);rng=random.Random(260706);xs=[];ys=[]
 for _ in range(1024):
  memory=rng.getrandbits(ns);value=rng.getrandbits(20);index=rng.randrange(4);write=rng.randrange(2);rotate=rng.randrange(2)
  nxt=(memory>>80)+((memory&((1<<80)-1))<<(ns-80)) if rotate else ((memory&~(1048575<<(20*index)))+(value<<(20*index)) if write else memory)
  inp=value+(index<<20)+(write<<22)+(rotate<<23)
  xs.append(memory+(inp<<ns));ys.append(nxt+((memory&1048575)<<ns))
 result=verify_state(net,xs,ys,ns);result.pop('nl_hex');return result


def make():
 net=source.make()[0];assert sha(net.encode())=='8745c4d80e797a16ae4dc38e4d2e3e95997916a5465c8c3310d2d5d4c88959b3'
 ns=net.n_state
 a=Builder(ns+NI);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+NI))
 _,_,wires,_=source.import_with_wires(a,net,pins,old);head=old[3355:3995]
 roots=select(a,[head[j*20:(j+1)*20] for j in range(32)],old[3330:3335])
 roots+=select(a,[head[j*20:(j+1)*20] for j in range(32)],old[8711:8716])
 lookup={}
 for i,w in enumerate(wires):lookup.setdefault(w,[]).append(i)
 found=[lookup.get(w,[]) for w in roots];assert all(found),[j for j,x in enumerate(found) if not x]
 b=Builder(ns+NI);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+NI));pending=old[5920]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def allof(*bits):return b.reduce(list(bits),b.land,1)
 def anyof(*bits):return b.reduce(list(bits),b.lor,0)
 keep=b.inv(pins[0]);modified=pins[:];modified[23]=b.land(pins[23],b.inv(pending))
 enabled=b.land(modified[23],b.inv(old[8718]));we=pins[24]
 hp=old[3339:3342];ho=old[5915:5917];fp=old[5935:5937]
 ni=old[6107:6114];hi=old[3330:3339];di=old[5921:5928]
 np=old[6144:6147];norm_phase=old[6120:6124]
 nack=allof(keep,eq(np,1),eq(norm_phase,9),we)
 dack=allof(keep,eq(fp,3),eq(old[5932:5935],4),we)
 hupdate=allof(keep,eq(ho,1),eq(hp,4),enabled)
 hscan=allof(keep,eq(ho,1),eq(hp,1),enabled,b.inv(old[5920]),b.reduce(hi[:4],b.land,1))
 take=allof(keep,eq(ho,3),anyof(eq(old[534:538],1),eq(old[534:538],4)),enabled)
 residual=allof(keep,eq(old[8709:8711],2),we,b.reduce(old[8711:8715],b.land,1))
 rotate=allof(keep,anyof(old[6147],old[5937],old[5920],hscan,take,pending,residual))
 hcode=old[3347:3355];hv=hcode+[hcode[-1]]*12
 value=[b.mux(nack,b.mux(dack,a,d),n) for a,d,n in zip(hv,old[397:417],old[6124:6144])]
 index=[b.mux(nack,b.mux(dack,h,d),n) for h,d,n in zip(hi[:4],di[:4],ni[:4])]
 hd=bank(b,old[3355:5915],value,index,anyof(nack,dack,hupdate),rotate)
 half=old[3355:3675]
 read=select(b,[half[j*20:(j+1)*20] for j in range(16)],hi[:4])
 read+=select(b,[half[j*20:(j+1)*20] for j in range(16)],old[8711:8715])
 replacements={wire:read[j] for j,indices in enumerate(found) for wire in indices}
 ds,out,_,changed=source.import_with_wires(b,net,modified,old,replacements)
 ds[3355:5915]=hd
 ds[6147]=allof(nack,b.reduce(ni[:4],b.land,1))
 ds[5937]=allof(dack,b.reduce(di[:4],b.land,1))
 ds[5920]=anyof(allof(hupdate,b.reduce(hi[:4],b.land,1)),take)
 result=with_state(b.finish(ds+out),ns)
 control=b.finish([nack,dack,hupdate,hscan,take,residual,rotate,ds[6147],ds[5937],ds[5920]])
 return result,control,dict(before=metrics(net),read_root_wires=found,replaced_wires=changed,
  h_bits=2560,h_halfword_bits=320,extra_pending_bits=0,reused_h_quant_pending_bit=5920,
  scope='exact H raw/code/result slot; two320-bit moves per gate/up DOT; scalar phases write/read16-lane head')


OUT=R/'build/integer_opt/h_halfword'


def small_control(net):
 # Check the actual controls extracted from the constructed full graph;
 # only these <=4k gates are evaluated, with arbitrary state encodings.
 from bench import verify
 assert metrics(net)['nNand']<=4000
 rng=random.Random(260708);xs=[];ys=[];ns=8719
 for j in range(2048):
  x=rng.getrandbits(net.n_in)
  # Bias each independently decoded phase toward its live states as well
  # as retaining arbitrary/illegal encodings on half the trials.
  if j%2:
   for off,width,value in ((3339,3,rng.randrange(5)),(5915,2,rng.randrange(4)),
      (5935,2,rng.choice([0,3])),(5932,3,rng.choice([0,4])),
      (6144,3,rng.choice([0,1])),(6120,4,rng.choice([0,9])),
      (534,4,rng.choice([0,1,4])),(8709,2,rng.randrange(4))):
    mask=((1<<width)-1)<<off;x=(x&~mask)|(value<<off)
  def field(off,width=1):return x>>off&((1<<width)-1)
  reset=field(ns);keep=not reset;we=field(ns+24)
  enabled=field(ns+23) and not field(8718) and not field(5920)
  nack=bool(keep and field(6144,3)==1 and field(6120,4)==9 and we)
  dack=bool(keep and field(5935,2)==3 and field(5932,3)==4 and we)
  update=bool(keep and field(5915,2)==1 and field(3339,3)==4 and enabled)
  scan=bool(keep and field(5915,2)==1 and field(3339,3)==1 and enabled and not field(5920) and field(3330,4)==15)
  take=bool(keep and field(5915,2)==3 and field(534,4) in (1,4) and enabled)
  residual=bool(keep and field(8709,2)==2 and we and field(8711,4)==15)
  rotate=bool(keep and (field(6147) or field(5937) or field(5920) or scan or take or field(5920) or residual))
  bits=[nack,dack,update,scan,take,residual,rotate,nack and field(6107,4)==15,dack and field(5921,4)==15,(update and field(3330,4)==15) or take]
  xs.append(x);ys.append(sum(int(v)<<k for k,v in enumerate(bits)))
 return dict(metrics=metrics(net),verification=verify(net,xs,ys))


def permutation():
 memory=[None]*128;rot=0
 def move():
  nonlocal memory,rot
  memory=memory[16:]+memory[:16];rot+=1
 for i in range(128):
  memory[i%16]=i
  if i%16==15:move()
 assert memory==list(range(128))
 for i in range(128):
  assert memory[i%16]==i
  if i%16==15:move()
 for i in range(128):
  assert memory[i%16]==i;memory[i%16]=128+i
  if i%16==15:move()
 for row in range(2*336):
  for matrix in range(2):
   for group in range(4):
    assert memory[:32]==list(range(128+32*group,128+32*group+32))
    move();move()
 assert memory==list(range(128,256))
 for i in range(128):
  memory[i%16]=256+i
  if i%16==15:move()
 assert rot==10784 and memory==list(range(256,384))
 for i in range(128):
  assert memory[i%16]==256+i
  if i%16==15:move()
 assert rot==10792 and memory==list(range(256,384))
 return dict(unique_labels=384,complete_h_rotations=rot,gate_up_groups=5376,two_raw_ff_passes=True,
  gate_up_rotations=10752,scalar_phase_rotations=40,
  scope='abstract exact-position schedule only, not large NAND simulation')


def h_cases(fixtures):
 import ff_stream
 ff_stream.OUT=OUT/'h_codes';ff_stream.OUT.mkdir(parents=True,exist_ok=True);c=ff_stream.reference();cases=[]
 for fixture in fixtures:
  hq=(ct.c_int8*128)();raw=(ct.c_int32*336)();fq=(ct.c_int8*336)();hm=ct.c_int32()
  fm=c.ff_reference((ct.c_int32*128)(*fixture['h']),hq,raw,fq,ct.byref(hm))
  cases.append(dict(q=list(hq),maximum=hm.value,ff_maximum=fm))
 return cases


def prove_bank():
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 b=Builder(2586);bits=list(range(2,2588));ds=bank(b,bits[:2560],bits[2560:2580],bits[2580:2584],*bits[2584:])
 net=b.finish(ds);(OUT/'bank.blif').write_text(blif(net));(OUT/'bank.negative.blif').write_text(blif(flip_output(net)))
 (OUT/'bank.ref.v').write_text("""module top(input [2585:0] din,output [2559:0] dout);
wire [2559:0] old=din[2559:0];wire [19:0] value=din[2579:2560];
wire [3:0] index=din[2583:2580];wire wr=din[2584],rotate=din[2585];
reg [2559:0] next;
always @* begin
 next=old;
 if(rotate)next={old[319:0],old[2559:320]};
 else if(wr)next[index*20+:20]=value;
end
assign dout=next;
endmodule
""")
 script=OUT/'bank.ys';script.write_text(f'read_verilog {OUT}/bank.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/bank.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'bank.yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=120)
 ref=from_yosys(json.loads((OUT/'bank.ref.json').read_text()),2586,2560);(OUT/'bank.ref.blif').write_text(blif(ref))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'bank.blif',OUT/'bank.ref.blif',OUT/'bank.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'bank.negative.blif',OUT/'bank.ref.blif',OUT/'bank.negative.log');assert bad['verdict']=='different'
 return dict(scope='all2560 old bits,value,index,write,rotate; old-state rotate priority',metrics=metrics(net),proof=good,negative=bad)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);small=small_bank();net,control,parts=make();ctl=small_control(control);order=permutation()
 common=source.source.source;common.OUT=OUT;c=common.reference();fixtures=common.cases(c)
 source.OUT=OUT;ffcodes=source.c_bank_cases(fixtures);hcodes=h_cases(fixtures)
 from export import rtl
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_sublayer'))
 for name,data in [('cases',fixtures),('bank_cases',ffcodes),('h_cases',hcodes)]:
  (OUT/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
 assert sha((OUT/'cases.json').read_bytes())=='e586e22a4dd3a2501781d75b3f790dff4454ec07b0e16a959525150eaa424074'
 report=dict(status='small gates, abstract H schedule and independent C cases pass; full graph constructed only',metrics=metrics(net),parts=parts,
  small_bank=small,small_control=ctl,permutation=order,numerical_contract_changed=False,
  fixtures_sha256=sha((OUT/'cases.json').read_bytes()),bank_cases_sha256=sha((OUT/'bank_cases.json').read_bytes()),h_cases_sha256=sha((OUT/'h_cases.json').read_bytes()),
  tradeoff='existing H quantization pending bit reused; two halfword moves per32-code gate/up DOT; scalar phase moves double; cadence and power not yet measured')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   path=Path(name).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/weights_golden.c',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl','physical/units/resid.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/name)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)) if R in p.parents else str(p):sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['bank_proof']=prove_bank();report['verification']=common.check(net,fixtures,scalar_x=True,ff_codes=ffcodes,h_codes=hcodes)
  report['status']=report['verification']['status'];(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','parts')},indent=2))


if __name__=='__main__':main()
