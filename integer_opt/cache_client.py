#!/usr/bin/env python3
"""Actual A-slot <-> normalized byte-cache transfer controller, no new vector.

128 A8 codes plus3 scale bytes per transfer. Quant/norm production and full
model ownership remain outside. Large proof and simulation are Actions-only.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,subprocess,shutil
R=Path(os.environ.get('H3_CACHE_CLIENT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import cache_bank as bank
import stage_fused as stage
from nand import Builder,metrics,with_state,verify_state,flip_output
from gate_check import verify
from export import import_net,rtl
OUT=Path(os.environ.get('H3_CACHE_CLIENT_OUT',str(R/'build/integer_opt/cache_client')))
sha=lambda b:hashlib.sha256(b).hexdigest()
NI,NO,NC=670,280,38
NS=bank.NS+2560+NC


def address(b,pos,index):
 three=b.add(pos+[0,0],[0]+pos+[0])[0]
 return b.add(three+[0]+pos+[0],index+[0]*4)[0]


def controller(b,old,p):
 phase=old[:2];fills=old[2:5];mode=old[5];pos=old[6:10];index=old[10:18];maximum=old[18:]
 reset,start,write=p[:3];asked=p[3:7];mx=p[7:27];fill,enable,advance,ready=p[27:31];byte=p[31:39]
 eq=lambda xs,n:b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(xs)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 keep=b.inv(reset);idle=b.lor(eq(phase,0),eq(phase,2));active=AND(keep,eq(phase,1))
 begin=AND(keep,idle,start,b.lor(b.inv(write),eq(fills,4)))
 fr=AND(keep,idle,b.inv(start));fa=b.land(fr,fill)
 ack=AND(active,enable,ready);data=b.inv(index[7]);last=b.reduce(index[:5],b.land,1);finish=AND(ack,eq(index,130))
 view=AND(keep,idle,eq(fills,4),b.inv(begin))
 rotate=b.lor(AND(ack,data,last),AND(view,b.inv(start),b.inv(fill),advance))
 update=AND(ack,data,b.inv(mode));we=AND(active,enable,mode)
 ph=phase[:]
 for event,value in ((begin,[1,0]),(finish,[0,1])):ph=[b.mux(event,x,y) for x,y in zip(ph,value)]
 nf=b.add(fills,[0]*3,1)[0];nf=[b.mux(eq(fills,4),v,(1>>j)&1) for j,v in enumerate(nf)]
 nf=[b.mux(fa,x,y) for x,y in zip(fills,nf)]
 nf=[b.mux(finish,x,(4>>j)&1) for j,x in enumerate(nf)]
 nf=[AND(b.inv(begin),x) for x in nf]
 ix=[b.mux(ack,x,y) for x,y in zip(index,b.add(index,[0]*8,1)[0])]
 ix=[AND(b.inv(begin),x) for x in ix]
 nm=[b.mux(begin,x,b.land(write,y)) for x,y in zip(maximum,mx)]
 for j in range(20):
  capture=AND(ack,b.inv(mode),eq(index,128+j//8))
  nm[j]=b.mux(capture,nm[j],byte[j%8])
 d=ph+nf+[b.mux(begin,mode,write)]+[b.mux(begin,x,y) for x,y in zip(pos,asked)]+ix+nm
 d=[AND(keep,x) for x in d]
 return d,[fa,rotate,update,we,view,fr,active,AND(keep,eq(phase,2),b.inv(begin))]


def ctl_step(old,p):
 ph=old&3;nf=old>>2&7;mode=old>>5&1;pos=old>>6&15;idx=old>>10&255;mx=old>>18&1048575
 reset=p&1;start=p>>1&1;write=p>>2&1;asked=p>>3&15;maximum=p>>7&1048575
 fill,enable,advance,ready=[p>>j&1 for j in range(27,31)];byte=p>>31&255
 idle=ph in (0,2);active=not reset and ph==1;begin=not reset and idle and start and (not write or nf==4)
 fr=not reset and idle and not start;fa=fr and fill;ack=active and enable and ready
 finish=ack and idx==130;view=not reset and idle and nf==4 and not begin
 rotate=ack and idx<128 and idx%32==31 or view and not start and not fill and advance
 update=ack and idx<128 and not mode;we=active and enable and mode
 out=sum(int(x)<<j for j,x in enumerate([fa,rotate,update,we,view,fr,active,not reset and ph==2 and not begin]))
 np=ph
 if begin:np=1
 if finish:np=2
 ff=nf
 if fa:ff=1 if nf==4 else (nf+1)&7
 if finish:ff=4
 if begin:ff=0
 ni=(idx+int(bool(ack)))&255
 if begin:ni=0;mode=write;pos=asked;mx=maximum if write else 0
 if ack and not (old>>5&1) and idx in (128,129,130):
  shift=8*(idx-128);mask=255 if idx<130 else 15;mx=(mx&~(mask<<shift))|((byte&mask)<<shift)
 result=np+(ff<<2)+(mode<<5)+(pos<<6)+(ni<<10)+(mx<<18)
 return (0 if reset else result),out


def small():
 b=Builder(NC+39);d,a=controller(b,list(range(2,2+NC)),list(range(2+NC,2+NC+39)))
 comb=b.finish(d+a);rng=random.Random(260781);xs=[];ys=[]
 for i in range(4096):
  old=rng.getrandbits(NC);p=rng.getrandbits(39)
  if i%3==0:old=(old&~(255<<10))+(rng.choice([0,31,63,95,127,128,129,130])<<10)
  n,o=ctl_step(old,p);xs.append(old+(p<<NC));ys.append(n+(o<<NC))
 assert metrics(with_state(comb,NC))['nNand']+NC<=4000
 report=verify_state(comb,xs,ys,NC);report.pop('nl_hex')
 b=Builder(12);g=b.finish(address(b,list(range(2,6)),list(range(6,14))))
 assert metrics(g)['nNand']<=4000
 addr=verify(g,list(range(4096)),[(x&15)*131+(x>>4) for x in range(4096)])
 return dict(control=report,address=addr)


def make():
 bn,_=bank.make();an,_=stage.make()
 assert sha(bn.encode())=='62ec88a20c596f5d28b9280fdf2581186720ee52e9b28a5f74d578d19ae7e2dd'
 assert sha(an.encode())=='befa2233b83206c6faf33a48094a723e3eb14923d72b750cd56fb77c5ba10495'
 b=Builder(NS+NI);old=list(range(2,2+NS));p=list(range(2+NS,2+NS+NI))
 bs=old[:bank.NS];ws=old[bank.NS:bank.NS+2560];cs=old[-NC:]
 reset,start,write=p[:3];pos=p[3:7];maximum=p[7:27];fill=p[27];incoming=p[28:668];enable,advance=p[668:]
 ix=cs[10:18];addr=address(b,cs[6:10],ix)
 ready=b.land(b.inv(reset),b.reduce([b.inv(b.xor(x,y)) for x,y in zip(bs[-12:],addr)],b.land,1))
 cd,act=controller(b,cs,[reset,start,write]+pos+maximum+[fill,enable,advance,ready]+bs[:8])
 ad,ao=import_net(b,an,incoming+bs[:8]+[bs[7]]*12+ix[:5]+act[:3],ws)
 words=[cs[18:26],cs[26:34],cs[34:38]+[0]*4,[0]*8]
 meta=stage.old.select(b,words,ix[:2]);code=ao[640:648]
 byte=[b.mux(ix[7],x,y) for x,y in zip(code,meta)]
 bd,bo=import_net(b,bn,byte+addr+[act[3],reset],bs)
 head=[ws[lane*20+j] for lane in range(32) for j in range(8)]
 comb=b.finish(bd+ad+cd+head+cs[18:]+act[4:])
 return with_state(comb,NS),comb,dict(cache=metrics(bn),existing_A_slot=metrics(an),controller_latch=NC,extra_vector_bits=0)


class Model:
 def __init__(self):
  self.bank=bank.Model(bank.SLOTS);self.work=[[0]*32 for _ in range(4)];self.known=[[False]*32 for _ in range(4)];self.ctl=0
  self.counts=dict(clocks=0,commands=0,completed=0,aborts=0,cache_bytes=0,fill_words=0,rotations=0,consumer_word_reads=0,disabled_ready=0,ignored_busy_starts=0,ignored_busy_fills=0)
 def tick(self,p):
  reset=p&1;start=p>>1&1;write=p>>2&1;pos=p>>3&15;mx=p>>7&1048575;fill=p>>27&1;enable=p>>668&1;adv=p>>669&1
  incoming=[p>>(28+20*j)&1048575 for j in range(32)]
  old=self.ctl;ph=old&3;mode=old>>5&1;idx=old>>10&255;maximum=old>>18&1048575;addr=(old>>6&15)*131+idx
  byte=self.bank.memory[0];ready=not reset and self.bank.cursor==addr
  cp=(p&((1<<28)-1))+(enable<<28)+(adv<<29)+(int(ready)<<30)+(byte<<31)
  nxt,action=ctl_step(old,cp);fa,rot,upd,we,view,fr,busy,done=[action>>i&1 for i in range(8)]
  head=sum((v&255)<<(8*j) for j,v in enumerate(self.work[0]));out=head+(maximum<<256)+((action>>4)<<276)
  mask=15<<276
  if view:assert all(sum(self.known,[]));mask|=(1<<276)-1
  ack=bool(busy and enable and ready);begin=(nxt&3)==1 and ph in (0,2)
  if ack and not mode:assert self.bank.known[0]
  value=(byte-256 if byte>=128 else byte)&1048575
  written=self.work[0][idx%32]&255 if idx<128 else (maximum>>(8*(idx%4)))&255
  if idx==130:written&=15
  if idx>=128 and idx%4==3:written=0
  ko,_=self.bank.tick(written+(addr<<8)+(we<<20)+(reset<<21));assert bool(ko>>8)==bool(ready)
  self.work=stage.transition(self.work,value,idx%32,fa,rot,upd,incoming)
  self.known=stage.transition(self.known,True,idx%32,fa,rot,upd,[True]*32)
  c=self.counts;c['clocks']+=1;c['commands']+=int(begin);c['completed']+=int(ph==1 and nxt&3==2)
  c['cache_bytes']+=int(ack);c['fill_words']+=fa;c['rotations']+=rot;c['consumer_word_reads']+=int(rot and not busy)
  c['disabled_ready']+=int(busy and ready and not enable);c['ignored_busy_starts']+=int(busy and start);c['ignored_busy_fills']+=int(busy and fill)
  if reset:c['aborts']+=int(ph==1);self.known=[[False]*32 for _ in range(4)]
  self.ctl=nxt
  return out,mask,ack


def vectors(cases):
 m=Model();rows=[];calls=[];rng=random.Random(260782)
 def tick(reset=0,start=0,write=0,pos=0,maximum=0,fill=0,data=None,enable=1,advance=0):
  x=reset+(start<<1)+(write<<2)+(pos<<3)+(maximum<<7)+(fill<<27)+(stage.pack(data or [0]*32)<<28)+(enable<<668)+(advance<<669)
  y,mask,ack=m.tick(x);rows.append((x,y,mask));return y,ack
 def load(v):
  q=[x-256 if x>=128 else x for x in v[:128]]
  for j in range(4):tick(fill=1,data=q[j*32:j*32+32])
  assert sum(m.work,[])==[x&1048575 for x in q]
 def command(write,pos,v,stall=False,abort=None):
  assert m.ctl&3 in (0,2);begin=len(rows);maximum=sum(v[128+j]<<(8*j) for j in range(3))
  tick(start=1,write=write,pos=pos,maximum=maximum)
  assert m.ctl&3==1;acks=[];disabled=set()
  for step in range(bank.SLOTS*16):
   idx=m.ctl>>10&255
   if abort is not None and idx==abort:tick(reset=1,start=1,fill=1);return False
   if m.ctl&3==2:break
   targeted=m.bank.cursor==pos*131+idx
   en=1
   if stall and targeted and idx in (31,63,95,127,128,130) and idx not in disabled:en=0;disabled.add(idx)
   y,ack=tick(start=int(step%29==0),write=not write,pos=rng.randrange(16),maximum=1,
    fill=int(step%37==0),data=[-1]*32,enable=en,advance=1)
   if ack:acks.append(len(rows)-1)
  else:raise AssertionError(('cache client timeout',write,pos,m.ctl,m.bank.cursor))
  assert len(acks)==131
  if stall:assert disabled=={31,63,95,127,128,130}
  gaps=[y-x for x,y in zip(acks,acks[1:])]
  if not stall:
   assert gaps==[1]*130
   assert acks[-1]-begin+2<=2095+131+2
  y,_=tick();assert y>>279&1 and y>>276&1
  assert m.ctl>>18==maximum
  assert [x&255 for x in sum(m.work,[])]==v[:128]
  for j in range(4):
   y,_=tick(advance=1);assert y&((1<<256)-1)==sum(v[32*j+k]<<(8*k) for k in range(32))
  calls.append(dict(write=bool(write),position=pos,clocks_until_observed_done=acks[-1]-begin+2,
   accepted_bytes=131,burst_span_clocks=acks[-1]-acks[0]+1,disabled_byte_positions=sorted(disabled),inter_ack_gaps=sorted(set(gaps))))
  return True
 def write_layer(layer):
  for p,v in enumerate(layer):load(v);assert command(True,p,v)
 tick(reset=1)
 # Incomplete stage cannot launch a cache write.
 tick(start=1,write=1);assert m.ctl&3==0
 for _ in range(3):
  tick(fill=1,data=[-1]*32);tick(start=1,write=1);assert m.ctl&3==0
 tick(reset=1)
 for li,layer in enumerate(cases['layers']):
  write_layer(layer)
  for p in range(15,-1,-1):assert command(False,p,layer[p],stall=li==0 and p==15)
 layer=cases['layers'][-1]
 for writing,stop in ((True,64),(False,129)):
  if writing:load(layer[0])
  assert not command(writing,0,layer[0],abort=stop)
  write_layer(layer);assert command(False,0,layer[0])
 return rows,dict(clocks=len(rows),counts=m.counts,calls=calls,
  scope='all80 true norm/A8 vectors roundtrip A/cache,actual131-byte bursts,consumer word reads,stalls,busy starts/fills and reset/refill; norm/quantizer production and full model absent')


def control_reference():
 return '''module ctl_ref(input [76:0] din,output [45:0] dout);
wire [37:0] cs=din[37:0];wire [38:0] p=din[76:38];
wire [1:0] phase=cs[1:0];wire [2:0] fills=cs[4:2];wire mode=cs[5];wire [3:0] pos=cs[9:6];wire [7:0] index=cs[17:10];wire [19:0] maximum=cs[37:18];
wire reset=p[0],start=p[1],writing=p[2],fill=p[27],enable=p[28],advance=p[29],ready=p[30];
wire [3:0] asked=p[6:3];wire [19:0] mx=p[26:7];wire [7:0] byte_value=p[38:31];
wire idle=phase==0 || phase==2,active=!reset && phase==1;
wire begin_copy=!reset && idle && start && (!writing || fills==4);
wire fill_ready=!reset && idle && !start,fill_ack=fill_ready && fill;
wire ack=active && enable && ready,finish=ack && index==130;
wire view_valid=!reset && idle && fills==4 && !begin_copy;
wire rotate=(ack && !index[7] && index[4:0]==31) || (view_valid && !start && !fill && advance);
reg [1:0] np;reg [2:0] nf;reg nm;reg [3:0] npos;reg [7:0] ni;reg [19:0] nmax;
always @* begin
 np=phase;nf=fills;nm=mode;npos=pos;ni=index+{7'd0,ack};nmax=maximum;
 if(begin_copy)begin np=1;nf=0;nm=writing;npos=asked;ni=0;nmax=writing?mx:20'd0;end
 if(fill_ack)nf=fills==4 ? 3'd1 : fills+3'd1;
 if(finish)begin np=2;nf=4;end
 if(begin_copy)nf=0;
 if(ack && !mode)begin
  case(index)128:nmax[7:0]=byte_value;129:nmax[15:8]=byte_value;130:nmax[19:16]=byte_value[3:0];default:begin end endcase
 end
 if(reset)begin np=0;nf=0;nm=0;npos=0;ni=0;nmax=0;end
end
assign dout[37:0]={nmax,ni,npos,nm,nf,np};
assign dout[45:38]={!reset && phase==2 && !begin_copy,active,fill_ready,view_valid,active && enable && mode,ack && !index[7] && !mode,rotate,fill_ack};
endmodule
'''


def reference():
 k=bank.reference().replace('module top(','module cache_ref(',1)
 a=stage.reference().replace('module top(','module stage_ref(',1)
 s=bank.NS;ws=s+2560
 t=[f'module top(input [{NS+NI-1}:0] din,output [{NS+NO-1}:0] dout);',
  f'wire [{s-1}:0] ks=din[{s-1}:0];wire [2559:0] astate=din[{ws-1}:{s}];wire [37:0] cs=din[{NS-1}:{ws}];wire [669:0] pins=din[{NS+NI-1}:{NS}];',
  'wire reset=pins[0];wire [7:0] index=cs[17:10];wire [3:0] position=cs[9:6];wire [19:0] maximum=cs[37:18];',
  "wire [11:0] address={8'd0,position}*12'd131+{4'd0,index};",
  f'wire bank_ready=!reset && ks[{s-1}:{s-12}]==address;',
  'wire [38:0] cp={ks[7:0],bank_ready,pins[669:668],pins[27:0]};wire [45:0] ctl;ctl_ref c({cp,cs},ctl);wire [7:0] act=ctl[45:38];',
  'reg [7:0] code,meta;always @* begin code=0;case(index[4:0])']
 for i in range(32):t.append(f"5'd{i}:code=astate[{i*20} +: 8];")
 t += ['endcase','case(index[1:0])',"0:meta=maximum[7:0];1:meta=maximum[15:8];2:meta={4'd0,maximum[19:16]};3:meta=0;endcase end",
  'wire [7:0] value=index[7]?meta:code;',f'wire [{s+8}:0] ko;cache_ref k({{reset,act[3],address,value,ks}},ko);',
  'wire [3219:0] ao;stage_ref a({act[2:0],index[4:0],{{12{ks[7]}},ks[7:0]},pins[667:28],astate},ao);',
  f'assign dout[{NS-1}:0]={{ctl[37:0],ao[2559:0],ko[{s-1}:0]}};']
 for i in range(32):t.append(f'assign dout[{NS+8*i} +: 8]=astate[{20*i} +: 8];')
 t += [f'assign dout[{NS+256} +: 24]={{act[7:4],maximum}};','endmodule','']
 return k+a+control_reference()+'\n'.join(t)


def cloud_check(net,comb,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from nand import blif,from_yosys
 from ci import cec
 import verify as checks
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 (OUT/'client.blif').write_text(blif(comb));ys=OUT/'reference.ys'
 ys.write_text(f'read_verilog {OUT}/client.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=300)
 ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out)
 (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
 good=cec(abc,OUT/'client.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 wrong=checks.check_nand(rows,flip_output(net).encode());assert wrong>0
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'cache_client',str(OUT/'vectors.txt')))
 (OUT/'negative.v').write_text(rtl(flip_output(net),'cache_client'))
 checks.run([checks.compile_rtl('source',OUT/'client.v')],300)
 failed=subprocess.run([str(checks.compile_rtl('negative',OUT/'negative.v'))],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
 assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
 return dict(status='pass',all_state_output_cec=good,actual_D_mutation=bad,clocks=len(rows),rtl_clocks=len(rows),nand_mismatches=0,
  actual_data_gate_mismatches=wrong,actual_RTL_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);sm=small();bank.OUT=OUT/'c_vectors';bank.OUT.mkdir(parents=True,exist_ok=True)
 cases=bank.golden();rows,expected=vectors(cases);net,comb,parts=make()
 (OUT/'client.nl').write_bytes(net.encode());(OUT/'client.v').write_text(rtl(net,'cache_client'));(OUT/'client.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','bench.py','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c','integer/int_model.c','physical/model.bin']:paths.add(R/n)
 report=dict(status='small control/address and full C-data transfer model pass; full gates pending',metrics=metrics(net),parts=parts,small=sm,expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'c_vectors/cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  scope='A8 vector/maximum copy between original A stage and actual cache,includesbyteaddress,FSM,sign extension and metadata; norm/quantizer/fullmodel ownership absent',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['verification']=cloud_check(net,comb,rows);report['status']='all D/output CEC and actual NAND/RTL/C-data client transfers pass with faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','parts','small']},indent=2))
 print(json.dumps({k:v for k,v in expected.items() if k!='calls'},indent=2))


if __name__=='__main__':main()
