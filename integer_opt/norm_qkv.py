#!/usr/bin/env python3
"""A real norm/cache producer and QKV consumer sharing the same physical state.

No external activation fill/write interface. X is replayed by the caller.
All large NAND/RTL checks stay on Actions; no complete transformer claim.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,random,ctypes as ct,subprocess,argparse,shutil
R=Path(os.environ.get('H3_NORM_QKV_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache_shared as shared
import qkv_range as qkv
import cache_matrix as consumer
from state_projection import prune
from nand import Builder,metrics,with_state,blif,from_yosys,flip_output
from export import import_net,rtl
from gate_check import verify
producer=shared.base
cache=producer.cache
OUT=Path(os.environ.get('H3_NORM_QKV_OUT',str(R/'build/integer_opt/norm_qkv')))
NI,NO,COMMON=44,76,cache.NS
PN,QN=19903,19885
NS=PN+QN-COMMON+1
sha=lambda b:hashlib.sha256(b).hexdigest()


def connect(b,mode,p,pbusy,qbusy,cbusy):
 AND=lambda *xs:b.reduce(xs,b.land,1)
 reset=p[0];ready=AND(b.inv(reset),b.inv(pbusy),b.inv(qbusy),b.inv(cbusy))
 ps=AND(ready,p[1]);qs=AND(ready,b.inv(p[1]),p[28],b.inv(b.land(p[29],p[30])))
 selected=b.mux(qs,b.mux(ps,mode,0),1)
 md=AND(b.inv(reset),selected)
 pp=[reset,ps]+p[2:28]+p[32:44]
 qp=[reset,0]+[0]*640+[0]+[0]*4+[0]*20+[qs]+p[24:28]+p[29:31]+[p[23],p[31]]
 assert len(pp)==40 and len(qp)==676
 return [md],pp,qp,[ready,selected]


def connect_int(mode,p,pbusy,qbusy,cbusy):
 reset=p&1;ready=int(not reset and not pbusy and not qbusy and not cbusy)
 ps=ready*(p>>1&1);qs=ready*int(not (p>>1&1))*int(bool(p>>28&1) and (p>>29&3)<3)
 selected=1 if qs else 0 if ps else mode
 pp=reset+(ps<<1)+(p&(((1<<28)-1)^3))+((p>>32)<<28)
 qp=reset+(qs<<667)+((p>>24&15)<<668)+((p>>29&3)<<672)+((p>>23&1)<<674)+((p>>31&1)<<675)
 return (0 if reset else selected),pp,qp,ready+(selected<<1)


def connector():
 b=Builder(48);md,pp,qp,o=connect(b,2,list(range(3,47)),47,48,49)
 return b.finish(md+pp+qp+o)


def small():
 cut=connector();assert metrics(cut)['nNand']<=4000
 rng=random.Random(260794);xs=[rng.getrandbits(48) for _ in range(1024)]
 # All command/reset/busy/mode combinations, all four matrix IDs.
 xs += [mode+(reset<<1)+(ps<<2)+(qs<<29)+(mat<<30)+(busy<<45)
        for mode in range(2) for reset in range(2) for ps in range(2) for qs in range(2)
        for mat in range(4) for busy in range(8)]
 ys=[]
 for x in xs:
  md,pp,qp,o=connect_int(x&1,x>>1&((1<<44)-1),x>>45&1,x>>46&1,x>>47&1)
  ys.append(md+(pp<<1)+(qp<<41)+(o<<717))
 return verify(cut,xs,ys)


def make(bad_owner=False):
 pn=prune(shared.sharing(producer.make()[0])[0])[0]
 original,_,_,table,_,_,_=qkv.candidates();qn,_,_=qkv.prior.make(table,original)
 assert sha(pn.encode())=='f3173a27342369a2d29ff516d13346da98af5d71fb4a1915b8144a822ab188a7'
 assert sha(qn.encode())=='51ec0ff2deb2bed8b308b92d921e318db4c89ac26f930ee6611909ba40acf9c9'
 assert (pn.n_state,qn.n_state)==(PN,QN)
 b=Builder(NS+NI);old=list(range(2,2+NS));p=list(range(2+NS,2+NS+NI))
 ps=old[:PN];qs=old[:COMMON]+old[PN:-1];assert len(qs)==QN
 _,po=import_net(b,pn,[p[0]]+[0]*39,ps)
 _,qo=import_net(b,qn,[p[0]]+[0]*675,qs)
 md,pp,qp,o=connect(b,old[-1],p,po[9],qo[30],qo[32])
 pd,po_actual=import_net(b,pn,pp,ps);qd,qo_actual=import_net(b,qn,qp,qs)
 assert po_actual[9]==po[9] and qo_actual[30]==qo[30] and qo_actual[32]==qo[32]
 select=b.inv(o[1]) if bad_owner else o[1]
 common=[b.mux(select,x,y) for x,y in zip(pd[:COMMON],qd[:COMMON])]
 net=with_state(b.finish(common+pd[COMMON:]+qd[COMMON:]+md+po_actual+qo_actual+o),NS)
 return net,dict(producer=metrics(pn),consumer=metrics(qn),common_state_bits=COMMON,
   cache_bits=cache.bank.NS,original_A20_bits=2560,cache_controller_bits=38,
   owner_bits=1,extra_vector_bits=0,actual_state_only_busy_binding=True)


def connector_ref():
 return '''module top(input [47:0] din,output [718:0] dout);
wire mode=din[0];wire [43:0] p=din[44:1];wire pb=din[45],qb=din[46],cb=din[47];
wire ready=!p[0] && !pb && !qb && !cb;
wire ps=ready && p[1],qs=ready && !p[1] && p[28] && p[30:29]!=3;
wire selected=qs?1'b1:(ps?1'b0:mode);
wire [39:0] pp={p[43:32],p[27:2],ps,p[0]};
wire [675:0] qp={p[31],p[23],p[30:29],p[27:24],qs,666'b0,p[0]};
assign dout={selected,ready,qp,pp,(!p[0] && selected)};
endmodule
'''


def ownership():
 def transition(b,s,ins):
  mode=s[0];ps=s[1:8];qs=s[8:12]
  reset,pstart,mstart=ins[:3];mat=ins[3:5];pos=ins[5:9]
  nf,qf,cd,cb,cv,valid,last,ready=ins[9:]
  eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
  pb=b.land(b.inv(reset),b.inv(b.lor(eq(ps[:3],0),eq(ps[:3],6))))
  qb=b.land(b.inv(reset),b.inv(eq(qs[:2],0)))
  pins=[0]*NI;pins[0]=reset;pins[1]=pstart;pins[28]=mstart;pins[29:31]=mat;pins[24:28]=pos
  md,pp,qp,_=connect(b,mode,pins,pb,qb,cb)
  pd,_=producer.owner(b,ps,[reset,pp[1]]+pos+[nf,qf,cd])
  qd,_=consumer.control(b,qs,[reset,qp[667]]+mat+[cb,cv,cd,valid,last,ready])
  return md+pd+qd
 def good(b,s):
  eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
  p_idle=b.lor(eq(s[1:4],0),eq(s[1:4],6));q_idle=eq(s[8:10],0)
  return b.reduce([b.inv(eq(s[1:4],7)),b.inv(b.land(s[10],s[11])),
                   b.mux(s[0],q_idle,p_idle)],b.land,1)
 b=Builder(29);s=list(range(2,14));p=list(range(14,31));ds=transition(b,s,p)
 step=b.finish([b.lor(b.inv(good(b,s)),good(b,ds))])
 b=Builder(28);s=list(range(2,14));p=[1]+list(range(14,30));ds=transition(b,s,p)
 reset=b.finish([b.inv(b.reduce(ds,b.lor,0))])
 assert max(metrics(g)['nNand'] for g in (step,reset))<=4000
 rng=random.Random(260795)
 checks={}
 for name,g in [('induction',step),('reset',reset)]:
  xs=[rng.getrandbits(g.n_in) for _ in range(2048)]
  checks[name]=verify(g,xs,[1]*len(xs))
 return step,reset,checks


class Model:
 def __init__(self):
  self.p=producer.Model();self.e=consumer.Engine();self.qowner=0;self.mode=0;self.context=None
  self.counts=dict(clocks=0,producer_starts=0,matrix_starts=0,results=0,groups=0,aborts=0,busy_commands=0,invalid_matrices=0)

 def tick(self,p,pcase=None,qcase=None):
  reset=p&1;c=self.p.cache;was_busy=self.p.owner&7 not in (0,6) or self.qowner&3!=0
  pb=int(not reset and self.p.owner&7 not in (0,6));qb=int(not reset and self.qowner&3!=0);cb=int(not reset and c.ctl&3==1)
  md,pp,qp,flags=connect_int(self.mode,p,pb,qb,cb);selected=flags>>1
  # Observe both state-derived interfaces before the selected owner advances.
  n=self.p.n;normal=not reset and self.p.owner&7==1;manual=not reset and self.p.owner&7 in (0,6) and not (pp>>1&1)
  rr=manual and c.bank.cursor==(pp>>28&4095)
  po=n['idx']+(int(normal and n['p'] in (1,5))<<7)+(int(normal and n['p']>=5)<<8)+(pb<<9)
  po+=(int(not reset and self.p.owner&7==6 and not (pp>>1&1))<<10)+(c.bank.memory[0]<<11)+(int(rr)<<19)+((c.ctl>>18)<<20)
  pmask=((1<<40)-1)^((255<<11) if not rr or not c.bank.known[0] else 0)
  olde=self.e.observe(reset);ph=c.ctl&3;cv=int(not reset and ph in (0,2) and c.ctl>>2&7==4);cd=int(not reset and ph==2)
  start=qp>>667&1;asked=qp>>672&3;enable=qp>>674&1;yready=qp>>675&1
  inp=reset+(start<<1)+(asked<<2)+(cb<<4)+(cv<<5)+(cd<<6)+((olde>>34&1)<<7)+(int((olde>>20&511)==127)<<8)+(yready<<9)
  qd,a=consumer.ctl_step(self.qowner,inp);begin,launch,run,manualq,busy=[a>>j&1 for j in range(5)]
  xf=run and enable and cv;take=xf and olde>>33&1
  qo=(olde&((1<<29)-1))+(int(run and olde>>34&1)<<29)+(busy<<30)+(int(manualq and not cb and not begin)<<31)+(cb<<32)+(int(cd and not begin)<<33)
  qmask=31<<29
  if run and olde>>34&1:qmask|=(1<<29)-1
  out=po+(qo<<40)+(flags<<74);mask=pmask+(qmask<<40)+(3<<74)
  # There is exactly one actual cache/slot object and one tick per clock.
  if selected==0:
   py,pm=self.p.tick(pp,pcase);assert py==po and pm==pmask
   # Inactive consumer control is idle; numeric leaf registers are not modeled
   # while invalid and are completely reloaded by each subsequent matrix.
   assert reset or self.qowner&3==0
   if reset:self.e.tick(1);self.qowner=0
  else:
   assert reset or self.p.owner&7 in (0,6)
   if begin:assert qcase is not None;self.context=qcase
   ci=reset+(begin<<1)+((qp>>668&15)<<3)+(enable<<668)+(int(take)<<669)
   head=sum((v&255)<<(8*j) for j,v in enumerate(c.work[0]));maximum=c.ctl>>18
   ei=reset+(launch<<1)+((self.qowner>>2)<<2)+(maximum<<5)+(head<<25)+(int(xf)<<281)+(int(run and yready)<<282)
   eo,accepted,result=self.e.tick(ei,self.context if launch else None);assert eo==olde
   c.tick(ci);self.qowner=qd
   self.counts['groups']+=int(accepted);self.counts['results']+=int(result)
   if reset:
    # Reset the producer's scalar state without advancing the common bank twice.
    fresh=producer.Model();fresh.cache=c;self.p=fresh
  self.counts['clocks']+=1;self.counts['producer_starts']+=pp>>1&1;self.counts['matrix_starts']+=start
  self.counts['aborts']+=int(reset and was_busy)
  self.counts['busy_commands']+=int(not(flags&1) and bool(p>>1&1 or p>>28&1))
  self.counts['invalid_matrices']+=int(flags&1 and p>>28&1 and p>>29&3==3 and not(p>>1&1))
  self.mode=md
  return out,mask


def fixtures():
 producer.OUT=OUT;consumer.OUT=OUT
 cases=producer.fixtures()
 # Reuse the independently checked frozen-C Q/K/V routine for every true
 # norm/quant output. It also decodes each real coefficient from the blob.
 refs=consumer.golden({'layers':[[c['packed'] for c in cases['cases']]]})
 return cases,refs


def vectors(cases,refs):
 m=Model();rows=[];calls=[];stored={}
 def tick(reset=0,ps=0,ms=0,x=0,xv=0,en=1,pos=0,mat=0,yr=1,address=4095,pcase=None):
  p=reset+(ps<<1)+((x&1048575)<<2)+(xv<<22)+(en<<23)+(pos<<24)+(ms<<28)+(mat<<29)+(yr<<31)+(address<<32)
  qcase=refs[stored[pos],mat] if pos in stored and mat<3 else None
  y,mask=m.tick(p,pcase,qcase);rows.append((p,y,mask if rows else 0));return y
 def reset():tick(reset=1,ps=1,ms=1);stored.clear();tick()
 def produce(index,pos,stall=False,abort=None):
  c=cases[index];begin=len(rows);tick(ps=1,ms=1,pos=pos,pcase=c);assert m.mode==0 and m.p.owner&7==1
  for k in range(100000):
   if m.p.owner&7==6:break
   if abort is not None and k==abort:reset();return False
   n=m.p.n;x=c['x'][n['idx']] if n['p'] in (1,5) else -524288
   tick(ms=int(k%31==0),ps=int(k%37==0),pos=(pos+1)%16,mat=2,x=x,xv=int(not stall or k%7!=3),en=int(not stall or k%11!=2))
  else:raise AssertionError('produce timeout')
  assert m.p.cache.ctl>>18==c['maximum'];stored[pos]=index
  calls.append(dict(kind='produce',case=index,position=pos,clocks=len(rows)-begin));tick();return True
 def matrix(pos,mat,stall=False,abort=None):
  assert pos in stored;before=m.counts['results'];begin=len(rows)
  tick(ms=1,pos=pos,mat=mat);assert m.mode==1 and m.qowner&3==1
  for k in range(100000):
   if m.qowner&3==0:break
   if abort is not None and k==abort:reset();return False
   tick(ps=int(k%41==0),ms=int(k%43==0),pos=(pos+1)%16,mat=3,x=-524288,xv=1,
        en=int(not stall or k%7!=3),yr=int(not stall or k%5!=2))
  else:raise AssertionError(('matrix timeout',pos,mat,m.qowner,m.p.cache.ctl,m.p.cache.bank.cursor,m.e.__dict__))
  assert m.counts['results']-before==128 and not m.e.busy
  assert [v&255 for v in sum(m.p.cache.work,[])]==cases[stored[pos]]['packed'][:128]
  calls.append(dict(kind='matrix',case=stored[pos],position=pos,matrix=mat,clocks=len(rows)-begin));tick();return True
 reset();tick(ms=1,mat=3);assert m.mode==0
 for i in range(16):assert produce(i,i,stall=i==0)
 for pos in range(15,-1,-1):
  for mat in range(3):assert matrix(pos,mat,stall=pos==15)
 # Switch repeatedly between raw norm and every matrix without duplicating A.
 for i in (16,17,18,19,20,21):
  assert produce(i,0)
  assert matrix(0,i%3)
 assert not produce(2,0,abort=200)
 assert produce(2,0)
 assert not matrix(0,0,abort=2200)
 assert produce(3,0)
 assert matrix(0,1,stall=True)
 return rows,dict(clocks=len(rows),counts=m.counts,calls=calls,cache=m.p.cache.counts,
   scope='actual norm/A8 producer -> same cache and A20 slot -> true QKV rows; all16 stored positions, switches/stalls/reset; no attention/whole transformer')


def prove(left,right,name):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 for label,g in [('left',left),('right',right),('negative',flip_output(left))]:
  (OUT/(name+'.'+label+'.blif')).write_text(blif(g))
 good=cec(abc,OUT/(name+'.left.blif'),OUT/(name+'.right.blif'),OUT/(name+'.cec.log'));assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/(name+'.negative.blif'),OUT/(name+'.right.blif'),OUT/(name+'.negative.log'));assert bad['verdict']=='different'
 return dict(proof=good,negative=bad)


def cloud_check(net,rows,step,reset):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 import verify as checks
 ys=OUT/'connector.ys';cut=connector()
 ys.write_text(f'read_verilog {OUT}/connector.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/connector.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'connector.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
 reference=from_yosys(json.loads((OUT/'connector.ref.json').read_text()),cut.n_in,cut.n_out)
 proofs={'connector':prove(cut,reference,'connector')}
 for name,g in [('ownership_induction',step),('ownership_reset',reset)]:
  proofs[name]=prove(g,Builder(g.n_in).finish([1]),name)
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
 assert checks.check_nand(rows,net.encode())==0
 # Flip the first actual QKV numeric result bit, never a handshake flag.
 from golden import Netlist
 records=net.records.copy();target=len(records)-NO+40;inv=records[target][1];op,a,b=records[inv-NI-2]
 assert op==0 and a==b;records[target]=(0,a,a);bad=Netlist(NI,NO,records)
 prefix=rows[:next(i for i,(_,_,m) in enumerate(rows) if m>>40&1)+513]
 wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
 bad_owner=make(True)[0];assert sha(bad_owner.encode())!=sha(net.encode())
 owner_wrong=checks.check_nand(prefix,bad_owner.encode());assert owner_wrong>0
 for name,g in [('negative',bad),('wrong_owner',bad_owner)]:
  (OUT/(name+'.nl')).write_bytes(g.encode())
 (OUT/'negative.v').write_text(rtl(bad,'norm_qkv'))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'norm_qkv',str(OUT/'vectors.txt')))
 checks.run([checks.compile_rtl('source',OUT/'core.v')],900)
 failed=subprocess.run([str(checks.compile_rtl('negative',OUT/'negative.v'))],capture_output=True,text=True,timeout=600)
 (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
 assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
 return dict(status='pass',proofs=proofs,clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),
  negative_prefix_clocks=len(prefix),actual_matrix_result_fault_mismatches=wrong,
  actual_wrong_shared_state_owner_mismatches=owner_wrong,actual_RTL_mutation_rejected=True,
  proof_scope='independent command/owner connector and child-owner reset/induction for arbitrary leaf status; inherits exact child arithmetic graphs, not independent all-state full arithmetic CEC')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True)
 sm=small();step,reset,oc=ownership();net,parts=make();print(metrics(net),flush=True)
 cases,refs=fixtures();rows,expected=vectors(cases['cases'],refs)
 assert expected['clocks']==1503175
 (OUT/'core.nl').write_bytes(net.encode());(OUT/'core.v').write_text(rtl(net,'norm_qkv'))
 (OUT/'connector.ref.v').write_text(connector_ref())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c',
              'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl',
              'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl']:
  paths.add(R/name)
 report=dict(status='small ownership/connection and full C-data protocol pass; full gates await Actions',metrics=metrics(net),parts=parts,
  small=sm,ownership_checks=oc,ownership_metrics={'induction':metrics(step),'reset':metrics(reset)},expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['verification']=cloud_check(net,rows,step,reset)
  report['status']='owner reset/induction and connector proved; all actual shared producer-consumer NAND/RTL/C clocks and real faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('clocks',len(rows),expected['counts'])


if __name__=='__main__':main()
