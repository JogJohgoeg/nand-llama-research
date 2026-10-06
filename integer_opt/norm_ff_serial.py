#!/usr/bin/env python3
"""Scalar result interface for the true norm/FF shared core.

Accept128 s20 result reads; rotate the original H word only after lane31.
No extra result buffer. This prepares a small pin interface for later layout
and does not launch EDA or change the active physical-design queue.
"""
from pathlib import Path
import sys,json,hashlib,signal,random,os,subprocess,shutil,argparse
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_ff_shared as source
from nand import Builder,with_state,metrics,verify_state,blif,flip_output,from_yosys
from export import import_net,rtl
from prefix_store import select
NI,NO=26,32
sha=lambda data:hashlib.sha256(data).hexdigest()

def port(b,index,head,pins):
 reset,start,busy,available,read=pins;keep=b.inv(reset)
 begin=b.reduce([keep,start,b.inv(busy)],b.land,1);take=b.reduce([keep,available,read],b.land,1)
 clear=b.lor(reset,begin);nxt=[b.land(b.inv(clear),v) for v in b.add(index,[0]*7,take)[0]]
 advance=b.land(take,b.reduce(index[:5],b.land,1));last=b.reduce([keep,available,b.reduce(index,b.land,1)],b.land,1)
 data=select(b,[head[j*20:(j+1)*20] for j in range(32)],index[:5])
 return nxt,data+[advance,last]

def small_check():
 b=Builder(652);old=list(range(2,9));head=list(range(9,649));pins=list(range(649,654))
 ds,out=port(b,old,head,pins);comb=b.finish(ds+out);assert metrics(comb)['nNand']<=4000
 rng=random.Random(260694);xs=[];ys=[]
 for index in range(128):
  for flags in range(32):
   word=rng.getrandbits(640);reset,start,busy,available,read=[flags>>j&1 for j in range(5)]
   begin=int(not reset and start and not busy);take=int(not reset and available and read)
   nxt=0 if reset or begin else (index+take)&127
   value=word>>(20*(index%32))&1048575
   advance=int(take and index%32==31);last=int(not reset and available and index==127)
   xs.append(index+(word<<7)+(flags<<647));ys.append(nxt+(value<<7)+(advance<<27)+(last<<28))
 result=verify_state(comb,xs,ys,7);result.pop('nl_hex');return result

def make():
 core=source.make()[0];assert sha(core.encode())=='5c22cdf28262b759ad03dfc36fef7f28c93841273a29b5f14393afc832414b0d'
 ns=core.n_state+7;b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI));cs=old[:core.n_state];index=old[core.n_state:]
 _,probe=import_net(b,core,ins[:25]+[0],cs)
 pd,po=port(b,index,probe[:640],[ins[0],ins[1],probe[670],probe[671],ins[25]])
 ds,out=import_net(b,core,ins[:25]+[po[20]],cs)
 public=po[:20]+out[640:647]+[out[647],out[648],out[670],out[671],po[21]]
 net=with_state(b.finish(ds+pd+public),ns);assert net.n_in==NI and net.n_out==NO
 return net,core,dict(core=metrics(core),extra_scalar_result_vector_bits=0,read_index_bits=7,
  original_signal_pins=core.n_in+core.n_out+1,serial_signal_pins=NI+NO+1,
  scope='exact norm1/FFN with scalar20 result stream; X externally replayed; no residual/attention; layout preparation only')




def serialized_vectors(rows):
 """Expand each accepted640-bit result word into32 scalar handshakes.

 An independent integer control recurrence predicts norm ready/replay/index;
 C owns every numeric H value. Original complete fixture rows are retained.
 """
 np=0;ni=0;nc=0;index=0;initialized=False
 for source_value,want,mask in rows:
  available=want>>671&1;busy=want>>670&1;rootphase=want>>672&7
  repeat=32 if source_value>>25&1 and available else 1
  for _ in range(repeat):
   value=source_value;reset=value&1;start=value>>1&1;read=value>>25&1
   ready=int(not reset and rootphase==1 and np in (1,5));replay=int(5<=np<=9)
   if mask>>640&127:assert (want>>640&127)==ni
   last=int(not reset and available and index==127)
   expected=(ni<<20)+(ready<<27)+(replay<<28)+(busy<<29)+(available<<30)+(last<<31)
   observed_mask=(1<<27)|(1<<31)
   if initialized:observed_mask|=(127<<20)|(1<<28)
   if mask>>670&1:observed_mask|=1<<29
   if mask>>671&1:observed_mask|=1<<30
   if available:
    assert mask&((1<<640)-1)==(1<<640)-1
    expected|=want>>(20*(index%32))&1048575;observed_mask|=1048575
   yield value,expected,observed_mask
   begin=bool(not reset and start and not busy)
   take=bool(not reset and available and read)
   index=0 if reset or begin else (index+take)&127
   # Independent recurrence for the already specified norm handshake timing.
   nb=bool(not reset and rootphase==0 and start and np==0)
   nt=bool(ready and value>>22&1);ack=bool(not reset and rootphase==1 and np==9 and value>>24&1)
   se=np==2 and nc==20;re=np==4 and nc==24;me=np==6 and nc==16;de=np==8 and nc==38
   pp=np
   if nb:pp=1
   elif np==1 and nt:pp=2
   elif se:pp=3 if ni==127 else 1
   elif np==3:pp=4
   elif re:pp=5
   elif np==5 and nt:pp=6
   elif me:pp=7
   elif np==7:pp=8
   elif de:pp=9
   elif ack:pp=0 if ni==127 else 5
   ni=0 if reset or nb or re else (ni+int(se or ack))&127
   nc=0 if reset or nb or nt or np in (3,7) or se or re or me or de else (nc+int(np in (2,4,6,8)))&63
   np=0 if reset else pp
   if reset:initialized=True


def convert_file(path,target,fixtures=None):
 digest=hashlib.sha256();stats=dict(source_clocks=0,serial_clocks=0,result_items=0,last_items=0)
 def read():
  with Path(path).open('rb') as f:
   for line in f:
    digest.update(line);stats['source_clocks']+=1;yield tuple(int(v,16) for v in line.split())
 vd=hashlib.sha256();results=[];current=[]
 with Path(target).open('wb') as f:
  for x,y,mask in serialized_vectors(read()):
   line=f'{x:x} {y:x} {mask:x}\n'.encode();f.write(line);vd.update(line);stats['serial_clocks']+=1
   if x>>25&1 and y>>30&1:
    stats['result_items']+=1;stats['last_items']+=y>>31&1
    q=y&1048575;current.append(q-1048576 if q>=524288 else q)
    if y>>31&1:results.append(current);current=[]
 stats.update(source_vector_sha256=digest.hexdigest(),vector_sha256=vd.hexdigest())
 assert stats['source_vector_sha256']=='02d9bae976528ba19227777232139485fc230ac64ddd1de05c4307d054f4d80b'
 assert stats['serial_clocks']==stats['source_clocks']+620 and stats['result_items']==640 and stats['last_items']==5
 if fixtures is not None:assert results==[fixtures[j]['out'] for j in (0,1,2,3,2)]
 return stats

OUT=R/'build/integer_opt/norm_ff_serial'

def prove_port():
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 b=Builder(652);ds,out=port(b,list(range(2,9)),list(range(9,649)),list(range(649,654)));net=b.finish(ds+out)
 (OUT/'port.blif').write_text(blif(net));(OUT/'port.negative.blif').write_text(blif(flip_output(net)))
 (OUT/'port.ref.v').write_text('''module top(input [651:0] din,output [28:0] dout);
wire [6:0] index=din[6:0];wire [639:0] head=din[646:7];
wire reset=din[647],start=din[648],busy=din[649],available=din[650],rd=din[651];
wire begin_row=!reset && start && !busy;
wire take=!reset && available && rd;
assign dout[6:0]=(reset || begin_row)?7'b0:index+take;
assign dout[26:7]=head[index[4:0]*20+:20];
assign dout[27]=take && (&index[4:0]);
assign dout[28]=!reset && available && (&index);
endmodule
''')
 script=OUT/'port.ys';script.write_text(f'read_verilog {OUT}/port.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/port.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'port.yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=120)
 ref=from_yosys(json.loads((OUT/'port.ref.json').read_text()),652,29);(OUT/'port.ref.blif').write_text(blif(ref))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'port.blif',OUT/'port.ref.blif',OUT/'port.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'port.negative.blif',OUT/'port.ref.blif',OUT/'port.negative.log');assert bad['verdict']=='different'
 return dict(scope='all640 H bits,all128 old read indices,all32 control inputs',proof=good,negative=bad)


def check(net):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from golden import Netlist
 import verify as checks
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
 def read(prefix=False):
  with (OUT/'vectors.txt').open() as f:
   for line in f:
    row=tuple(int(v,16) for v in line.split());yield row
    if prefix and row[1]>>30&1:return
 assert checks.check_nand(read(),net.encode())==0
 gates=net.records.copy();target=len(gates)-NO;inverse=gates[target][1]
 op,a,b=gates[inverse-NI-2];assert op==0 and a==b;gates[target]=(0,a,a)
 bad=Netlist(NI,NO,gates);wrong=checks.check_nand(read(True),bad.encode());assert wrong>0
 (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,'norm_ff_serial'))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'norm_ff_serial',str(OUT/'vectors.txt')))
 normal=checks.compile_rtl('source',OUT/'stream.v');checks.run([normal],600)
 mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
 assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
 return dict(status='actual scalar-interface NAND/RTL/C and output mutation pass',actual_output_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--reference-vectors',type=Path);args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true' and args.reference_vectors is None
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base=source.source;base.OUT=OUT/'source';base.OUT.mkdir(exist_ok=True)
 small=small_check();net,core,parts=make();fixtures=base.cases(base.reference())
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'norm_ff_serial'))
 (OUT/'cases.json').write_text(json.dumps(fixtures,indent=2)+'\n')
 report=dict(status='small serial port/C fixtures/large graph construction only',metrics=metrics(net),parts=parts,small_port=small,
  numerical_contract_changed=False,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),
  contract='din26 unchanged except read bit25 accepts one s20 result; dout:Y20,norm_index7,norm_ready,norm_replay,busy,available,last_result',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'))
 if args.cloud:
  report['port_proof']=prove_port();rows,observed=base.cloud_vectors(core,fixtures)
  original=base.OUT/'vectors.txt';original.write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows));del rows
  report['source_sequence']=observed;report['expected']=convert_file(original,OUT/'vectors.txt',fixtures)
  report['verification']=check(net);report['status']=report['verification']['status']
 elif args.reference_vectors:
  report['expected']=convert_file(args.reference_vectors,OUT/'vectors.txt',fixtures)
  report['status']='small port and complete C-derived scalar fixture pass; large graph construction only'
 paths=set()
 for module in list(sys.modules.values()):
  path=getattr(module,'__file__',None)
  if path:
   path=Path(path).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for name in ['integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/weights_golden.c',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/name)
 report['sources']={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)}
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))


if __name__=='__main__':main()
