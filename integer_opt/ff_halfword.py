#!/usr/bin/env python3
"""Exact FFN with a held21-by128-bit FF-code ring and one pending move.

The numerical C contract and all336 A8 codes are retained. Source root
replacement is structural; complete graph evaluation is Actions-only.
"""
from pathlib import Path
import sys,json,hashlib,signal,random,ctypes as ct,os,subprocess,argparse,shutil
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import ff_sublayer_ring as source
from export import import_net
from nand import Builder,metrics,with_state,verify_state,blif,flip_output,from_yosys
from prefix_store import select
NI,NO=26,32
sha=lambda x:hashlib.sha256(x).hexdigest()


def bank(b,memory,code,index,write,rotate,tail):
 head=[]
 for lane in range(16):
  match=b.reduce([v if lane>>j&1 else b.inv(v) for j,v in enumerate(index)],b.land,1)
  accept=b.land(write,match)
  head += [b.mux(accept,a,v) for a,v in zip(memory[8*lane:8*(lane+1)],code)]
 updated=head+memory[128:];shifted=memory[128:]+memory[:128]
 ds=[b.mux(rotate,a,v) for a,v in zip(updated,shifted)]
 word=memory[:128]+[b.land(b.inv(tail),v) for v in memory[128:256]]
 return ds,word


def bank_control(b,pending,reset,write,last,take,tail):
 keep=b.inv(reset)
 pn=b.land(keep,b.lor(b.land(write,last),b.land(take,b.inv(tail))))
 rotate=b.land(keep,b.lor(pending,take))
 return pn,rotate


def small_control():
 b=Builder(6);pn,rotate=bank_control(b,*range(2,8));net=b.finish([pn,rotate]);xs=list(range(64));ys=[]
 for value in xs:
  pending,reset,write,last,take,tail=[value>>j&1 for j in range(6)]
  pn=int(not reset and (write and last or take and not tail))
  rotate=int(not reset and (pending or take))
  ys.append(pn+(rotate<<1))
 result=verify_state(net,xs,ys,1);result.pop('nl_hex');return result


def import_with_wires(b,net,inputs,state,replacements=None):
 wires=[0,1]+inputs;di=[];si=0;changes=0
 for record in net.records:
  wire=len(wires)
  if record[0]==1:
   wires.append(state[si]);si+=1;di.append(record[1])
  elif replacements is not None and wire in replacements:
   wires.append(replacements[wire]);changes+=1
  else:wires.append(b.nand(wires[record[1]],wires[record[2]]))
 return [wires[i] for i in di],wires[-net.n_out:],wires,changes


def small_bank():
 ns=384;b=Builder(ns+15);old=list(range(2,ns+2));ins=list(range(ns+2,ns+17))
 ds,word=bank(b,old,ins[:8],ins[8:12],*ins[12:]);net=b.finish(ds+word);xs=[];ys=[];rng=random.Random(260703)
 for _ in range(512):
  memory=rng.getrandbits(ns);code=rng.randrange(256);index=rng.randrange(16);write=rng.randrange(2);rotate=rng.randrange(2);tail=rng.randrange(2)
  value=code+(index<<8)+(write<<12)+(rotate<<13)+(tail<<14)
  nxt=(memory>>128)+((memory&((1<<128)-1))<<(ns-128)) if rotate else ((memory&~(255<<(8*index)))+(code<<(8*index)) if write else memory)
  read=memory&((1<<(128 if tail else 256))-1)
  xs.append(memory+(value<<ns));ys.append(nxt+(read<<ns))
 result=verify_state(net,xs,ys,ns);result.pop('nl_hex');return result


def make():
 net=source.make()[0];assert sha(net.encode())=='c635361870dbad3abd01c8bebf6f14e58bb691978c4cafd155a2a2561df675a9'
 ns=net.n_state+1
 def eq(b,bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 # Find the original read roots structurally, without simulating the graph.
 a=Builder(ns+NI);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+NI))
 _,_,wires,_=import_with_wires(a,net,pins,old[:-1])
 down=eq(a,old[5935:5937],3);address=[a.mux(down,0,v) for v in old[5928:5932]]
 words=[old[592+j*256:592+(j+1)*256] for j in range(10)]+[old[3152:3280]+[0]*128]+[[0]*256 for _ in range(5)]
 roots=select(a,words,address);lookup={}
 for i,wire in enumerate(wires):lookup.setdefault(wire,[]).append(i)
 found=[lookup.get(root,[]) for root in roots]
 assert all(found),[i for i,x in enumerate(found) if not x]
 # Read replacement and the new bank use the same physical state slots.
 b=Builder(ns+NI);old=list(range(2,ns+2));pins=list(range(ns+2,ns+2+NI));pending=old[-1]
 keep=b.inv(pins[0]);down=eq(b,old[5935:5937],3);group=old[5928:5932]
 enabled=b.land(pins[23],b.inv(pending));modified=pins[:];modified[23]=enabled
 filled=b.reduce([old[3280],eq(b,old[576:579],0),eq(b,old[5915:5917],0)],b.land,1)
 legal=b.reduce([eq(b,group,j) for j in range(11)],b.lor,0)
 take=b.reduce([keep,down,eq(b,old[5932:5935],1),enabled,filled,legal],b.land,1)
 write=b.reduce([keep,eq(b,old[576:579],4),pins[24]],b.land,1)
 tail=b.land(down,eq(b,group,10))
 pn,rotate=bank_control(b,pending,pins[0],write,b.reduce(old[567:571],b.land,1),take,tail)
 bd,word=bank(b,old[592:3280],old[584:592],old[567:571],write,rotate,tail)
 replacements={wire:word[j] for j,indices in enumerate(found) for wire in indices}
 ds,out,_,changed=import_with_wires(b,net,modified,old[:-1],replacements)
 ds[592:3280]=bd
 result=with_state(b.finish(ds+[pn]+out),ns)
 return result,dict(before=metrics(net),read_root_wires=found,replaced_wires=changed,extra_pending_bits=1,
  scope='actual FF code bank becomes21x128 held ring; code writes rotate after16 accepts, down reads rotate twice except16-lane tail')


OUT=R/'build/integer_opt/ff_halfword'


def c_bank_cases(fixtures):
 import ff_stream
 ff_stream.OUT=OUT/'ff_codes';ff_stream.OUT.mkdir(parents=True,exist_ok=True)
 c=ff_stream.reference();rows=[]
 for case in fixtures:
  qh=(ct.c_int8*128)();raw=(ct.c_int32*336)();q=(ct.c_int8*336)();hm=ct.c_int32()
  maximum=c.ff_reference((ct.c_int32*128)(*case['h']),qh,raw,q,ct.byref(hm))
  assert maximum==max(1,max(abs(v) for v in raw))
  rows.append(dict(q=list(q),maximum=maximum,hm=hm.value))
 return rows


def abstract_permutation():
 # Unique labels prove the schedule/order independently of8-bit coincidences.
 memory=[None]*336;rotations=0
 for i in range(336):
  memory[i%16]=i
  if i%16==15:memory=memory[16:]+memory[:16];rotations+=1
 assert memory==list(range(336)) and rotations==21
 for row in range(128):
  for group in range(11):
   observed=memory[:32] if group<10 else memory[:16]
   assert observed==list(range(32*group,min(336,32*group+32)))
   memory=memory[16:]+memory[:16];rotations+=1
   if group<10:memory=memory[16:]+memory[:16];rotations+=1
  assert memory==list(range(336))
 assert rotations==2709
 return dict(unique_labels=336,rows=128,groups=1408,write_rotations=21,total_rotations=2709,
  scope='abstract permutation/schedule check, not large gate simulation; stalls hold except one committed pending rotation')


def prove_bank():
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 b=Builder(2703);bits=list(range(2,2705))
 ds,word=bank(b,bits[:2688],bits[2688:2696],bits[2696:2700],*bits[2700:])
 net=b.finish(ds+word)
 (OUT/'bank.blif').write_text(blif(net));(OUT/'bank.negative.blif').write_text(blif(flip_output(net)))
 (OUT/'bank.ref.v').write_text("""module top(input [2702:0] din,output [2943:0] dout);
wire [2687:0] old=din[2687:0];wire [7:0] code=din[2695:2688];
wire [3:0] index=din[2699:2696];wire wr=din[2700],rotate=din[2701],tail=din[2702];
reg [2687:0] next;
always @* begin
 next=old;
 if(rotate)next={old[127:0],old[2687:128]};
 else if(wr)next[index*8+:8]=code;
end
assign dout={tail?128'd0:old[255:128],old[127:0],next};
endmodule
""")
 script=OUT/'bank.ys';script.write_text(f'read_verilog {OUT}/bank.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/bank.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'bank.yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=120)
 ref=from_yosys(json.loads((OUT/'bank.ref.json').read_text()),2703,2944);(OUT/'bank.ref.blif').write_text(blif(ref))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'bank.blif',OUT/'bank.ref.blif',OUT/'bank.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'bank.negative.blif',OUT/'bank.ref.blif',OUT/'bank.negative.log');assert bad['verdict']=='different'
 return dict(scope='all2688 memory bits,code,index,write,rotate,tail; old-state rotate priority and16-code tail padding',
  metrics=metrics(net),proof=good,negative=bad)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);source.source.OUT=OUT
 small=small_bank();ctl=small_control();permutation=abstract_permutation();net,parts=make()
 c=source.source.reference();fixtures=source.source.cases(c);codes=c_bank_cases(fixtures)
 from export import rtl
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_sublayer'))
 (OUT/'cases.json').write_text(json.dumps(fixtures,indent=2)+'\n');(OUT/'bank_cases.json').write_text(json.dumps(codes,indent=2)+'\n')
 assert sha((OUT/'cases.json').read_bytes())=='e586e22a4dd3a2501781d75b3f790dff4454ec07b0e16a959525150eaa424074'
 report=dict(status='small gates, abstract ring order and C fixtures pass; full graph constructed only',metrics=metrics(net),parts=parts,
  small_bank=small,small_control=ctl,permutation=permutation,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),
  bank_cases_sha256=sha((OUT/'bank_cases.json').read_bytes()),numerical_contract_changed=False,
  tradeoff='one extra pending LATCH;21 producer rotations and2688 down rotations per complete FFN;10 extra wait clocks per128 down rows, actual total cadence verified on Actions; not a measured power reduction')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  path=getattr(module,'__file__',None)
  if path:
   path=Path(path).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/weights_golden.c',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl','physical/units/resid.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/name)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else str(p):sha(p.read_bytes()) for p in sorted(paths)})
 if args.cloud:
  report['bank_proof']=prove_bank();report['verification']=source.source.check(net,fixtures,scalar_x=True,ff_codes=codes)
  report['status']=report['verification']['status']
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','parts')},indent=2))


if __name__=='__main__':main()
