#!/usr/bin/env python3
"""Exact root-reusing norm/A8 cache producer integrated with true Q/K/V.

Reuse the verified shared command, DIV and MUL transformations with explicit
bindings to the changed producer. Project the complete resulting state graph.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse,shutil,pickle,time
R=Path(os.environ.get('H3_NORM_QKV_RECOMPUTE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache_root as root
import norm_qkv_mul as mul
from nand import Builder,metrics,with_state,flip_output
from export import import_net,rtl
from golden import Netlist
from state_projection import prune,structural_identity
from gate_check import verify
base=mul.base;ports=mul.ports;div=mul.prior;cache=base.cache
NI,NO,NS=base.NI,base.NO,base.NS
PD,QD,REMOVED=div.PD,div.QD,div.REMOVED
OUT=Path(os.environ.get('H3_NORM_QKV_RECOMPUTE_OUT',str(R/'build/integer_opt/norm_qkv_recompute')))
sha=lambda b:hashlib.sha256(b).hexdigest()


def producer19(wrong_restart=False):
 # Preserve R86 coordinates through integration. Only norm's48 already-dead
 # MUL bits are omitted here; dead A high bits are removed from the full graph.
 raw=root.restart(root.prior.make_unshared()[0],wrong_restart)
 shared,dl,dr,bind=root.prior.sharing(raw)
 removed=set(range(cache.NS+40,cache.NS+64))|set(range(cache.NS+104,cache.NS+128))
 kept=[i for i in range(shared.n_state) if i not in removed];idx={i:j for j,i in enumerate(kept)}
 b=Builder(len(kept)+40);old=[2+idx[i] if i in idx else 0 for i in range(shared.n_state)]
 p=list(range(2+len(kept),2+len(kept)+40));d,o=import_net(b,shared,p,old)
 net=with_state(b.finish([d[i] for i in kept]+o),len(kept))
 pl,pr,same=structural_identity(shared,net,kept);assert same and pl.encode()==pr.encode()
 assert net.n_state==base.PN==19903
 aliases=[i for i in range(20066) if i not in base.shared.REMOVED]
 return net,{'kept':aliases},kept,(dl,dr,pl,pr)


def port_graph(bad_owner=False,wrong_restart=False):
 pn,sp,kept,producer_proofs=producer19(wrong_restart)
 original,_,_,table,_,_,_=base.qkv.candidates();qn,_,_=base.qkv.prior.make(table,original)
 assert sha(qn.encode())=='51ec0ff2deb2bed8b308b92d921e318db4c89ac26f930ee6611909ba40acf9c9'
 b=Builder(NS+NI);old=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
 ps=old[:base.PN];qs=old[:base.COMMON]+old[base.PN:-1]
 _,po=import_net(b,pn,[p[0]]+[0]*39,ps);_,qo=import_net(b,qn,[p[0]]+[0]*675,qs)
 md,pp,qp,public=base.connect(b,old[-1],p,po[9],qo[30],qo[32])
 pd,po=import_net(b,pn,pp,ps);qd,qo=import_net(b,qn,qp,qs)
 saved=ports.pmod.connect;ports.pmod.connect=root.prior.connect
 try:cp,wp,(install,owns)=ports.producer_commands(b,ps,pp,sp,kept)
 finally:ports.pmod.connect=saved
 cq=ports.consumer_commands(b,qs,qp);cn=cache.make()[0]
 assert ports.common_port(b,cn,ps[:cache.NS],cp,wp,owns,install)==pd[:cache.NS]
 assert import_net(b,cn,cq,qs[:cache.NS])[0]==qd[:cache.NS]
 select=b.inv(public[1]) if bad_owner else public[1];active=b.inv(select)
 command=[b.mux(select,x,y) for x,y in zip(cp,cq)]
 cd=ports.common_port(b,cn,ps[:cache.NS],command,wp,b.land(active,owns),b.land(active,install))
 reference=[b.mux(public[1],x,y) for x,y in zip(pd[:cache.NS],qd[:cache.NS])]
 rest=pd[cache.NS:]+qd[cache.NS:]+md+po+qo+public
 candidate=with_state(b.finish(cd+rest),NS)
 return candidate,b.finish(reference),b.finish(cd),producer_proofs,dict(
  producer=metrics(pn),consumer=metrics(qn),shared_D_bits=cache.NS,actual_both_command_D_bindings=True,
  arbitrary_shared_old_state=True,private_D_and_outputs_unchanged_by_port_merge=True)


def producer_div_command(b,old,p):
 live=[i for i in range(512) if i not in list(range(40,64))+list(range(104,128))];mi={v:i for i,v in enumerate(live)}
 ns=[old[cache.NS+mi[j]] if j in mi else 0 for j in range(512)]
 qs=old[PD:PD+115]+old[cache.NS+464:base.PN-7];owner=old[base.PN-7:base.PN]
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 AND=lambda *xs:b.reduce(xs,b.land,1)
 keep=b.inv(p[0]);first=AND(keep,eq(owner[:3],1));second=AND(keep,eq(owner[:3],3));running=b.lor(first,second)
 valid=AND(keep,eq(ns[488:492],9));choose_quant=AND(second,valid)
 np=[eq(ns[488:492],7)]+[0]*28+ns[:36]+ns[451:475]+[0]
 receive=eq(qs[153:156],2);ready=AND(keep,b.lor(eq(qs[153:156],1),receive))
 qload=b.land(b.land(ready,AND(running,valid,p[23])),receive)
 x=ns[492:512];product=b.add([0]*7+x,[b.inv(v) for v in x+[x[-1]]*7],1)[0]
 qp=[qload]+[0]*37+product+qs[115:135]+[0]*5
 return [b.mux(choose_quant,n,q) for n,q in zip(np,qp)]


def share_div(net,bad_div_owner=False):
 assert net.n_in==NI and net.n_out==NO and net.n_state==base.NS
 kept=[i for i in range(net.n_state) if i not in REMOVED];index={v:i for i,v in enumerate(kept)}
 def primary(i):return PD+i-QD if i in REMOVED else i
 b=Builder(len(kept)+NI);old=[2+index[primary(i)] for i in range(net.n_state)]
 p=list(range(2+len(kept),2+len(kept)+NI));ds,out=import_net(b,net,p,old)
 producer=producer_div_command(b,old,p)
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 scale=old[base.PN+32:base.PN+32+418];assert len(scale)==418
 temp=scale[307:371];count=scale[389:395];phase=scale[395:397]
 consumer=[b.land(eq(phase,3),eq(count,0))]+[0]*9+temp[:55]+[(33292288>>j)&1 for j in range(25)]
 assert len(producer)==len(consumer)==90
 meta=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div']
 raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
 div=Netlist.decode(raw,meta['nIn'],meta['nOut']);assert div.n_state==115
 pd,_=import_net(b,div,producer,old[PD:PD+115]);qd,_=import_net(b,div,consumer,old[QD:QD+115])
 assert pd==ds[PD:PD+115],'actual producer DIV command binding'
 assert qd==ds[QD:QD+115],'actual QKV DIV command binding'
 selected=b.inv(out[-1]) if bad_div_owner else out[-1]
 command=[b.mux(selected,x,y) for x,y in zip(producer,consumer)]
 dd,_=import_net(b,div,command,old[PD:PD+115])
 reference=b.finish([b.mux(out[-1],x,y) for x,y in zip(pd,qd)]);proof=b.finish(dd)
 nxt=[dd[i-PD] if PD<=i<PD+115 else ds[i] for i in kept]
 new=with_state(b.finish(nxt+out),len(kept))
 return new,reference,proof,dict(kept=kept,removed=REMOVED,producer_DIV_start=PD,consumer_DIV_start=QD,
  shared_DIV_bits=115,actual_producer_operand_D_binding=True,actual_consumer_operand_D_binding=True,
  selected_next_state_after_state_identification=True,unbounded_lifetime_proof=False,
  scalar_result_registers_retained=True,extra_vector_bits=0)



def make(bad_owner=False,bad_div_owner=False,bad_mul_owner=False,wrong_restart=False):
 pn,pl,pr,producer_proofs,pbind=port_graph(bad_owner,wrong_restart)
 dn,dl,dr,dbind=share_div(pn,bad_div_owner)
 saved=mul.pmod.connect;mul.pmod.connect=root.prior.connect
 try:mn,ml,mr,mbind=mul.sharing(dn,bad_mul_owner)
 finally:mul.pmod.connect=saved
 net,kept=prune(mn);left,right,same=structural_identity(mn,net,kept)
 assert same and left.encode()==right.encode()
 assert not structural_identity(mn,flip_output(net),kept)[2]
 return net,dict(ports=(pl,pr),producer_div=producer_proofs[:2],producer_projection=producer_proofs[2:],
  shared_div=(dl,dr),shared_mul=(ml,mr),projection=(left,right)),dict(ports=pbind,DIV=dbind,MUL=mbind,
  before_projection=metrics(mn),kept=kept,removed=sorted(set(range(mn.n_state))-set(kept)),
  all_retained_D_outputs_identical=True,actual_output_mutation_breaks_projection=True)


producer=root
consumer=base.consumer
connect_int=base.connect_int

class Model:
 def __init__(self):
  self.p=producer.Model();self.e=consumer.Engine();self.qowner=0;self.mode=0;self.context=None
  self.counts=dict(clocks=0,producer_starts=0,matrix_starts=0,results=0,groups=0,aborts=0,busy_commands=0,invalid_matrices=0)

 def tick(self,p,pcase=None,qcase=None):
  reset=p&1;c=self.p.cache;was_busy=self.p.owner&7 not in (0,6) or self.qowner&3!=0
  pb=int(not reset and self.p.owner&7 not in (0,6));qb=int(not reset and self.qowner&3!=0);cb=int(not reset and c.ctl&3==1)
  md,pp,qp,flags=connect_int(self.mode,p,pb,qb,cb);selected=flags>>1
  # Observe both state-derived interfaces before the selected owner advances.
  n=self.p.n;normal=not reset and self.p.owner&7 in (1,3);manual=not reset and self.p.owner&7 in (0,6) and not (pp>>1&1)
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



def references(cloud,directory):
 base.OUT=OUT;root.prior.OUT=OUT;root.base.OUT=OUT;base.consumer.OUT=OUT
 cases=root.prior.reference_data(cloud,directory)
 refs=base.consumer.golden({'layers':[[c['packed'] for c in cases['cases']]]})
 return cases,refs


def vector_chunk(cases,refs,state=None,seconds=None):
 started=time.monotonic()
 if state is None:state=dict(model=Model(),calls=[],stored={},op=0)
 m=state['model'];rows=[];calls=state['calls'];stored=state['stored']
 def tick(reset=0,ps=0,ms=0,x=0,xv=0,en=1,pos=0,mat=0,yr=1,address=4095,pcase=None):
  p=reset+(ps<<1)+((x&1048575)<<2)+(xv<<22)+(en<<23)+(pos<<24)+(ms<<28)+(mat<<29)+(yr<<31)+(address<<32)
  qcase=refs[stored[pos],mat] if pos in stored and mat<3 else None
  y,mask=m.tick(p,pcase,qcase);rows.append((p,y,mask if m.counts['clocks']>1 else 0));return y
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
 operations=[('reset',),('invalid',)]
 operations += [('produce',i,i,i==0,None) for i in range(16)]
 operations += [('matrix',pos,mat,pos==15,None) for pos in range(15,-1,-1) for mat in range(3)]
 for i in (16,17,18,19,20,21):operations += [('produce',i,0,False,None),('matrix',0,i%3,False,None)]
 operations += [('produce',2,0,False,200),('produce',2,0,False,None),('matrix',0,0,False,2200),('produce',3,0,False,None),('matrix',0,1,True,None)]
 while state['op']<len(operations) and (seconds is None or time.monotonic()-started<seconds):
  item=operations[state['op']]
  if item[0]=='reset':reset()
  elif item[0]=='invalid':tick(ms=1,mat=3);assert m.mode==0
  else:
   result=(produce if item[0]=='produce' else matrix)(*item[1:]);assert result==(item[-1] is None)
  state['op']+=1
 done=state['op']==len(operations)
 expected=dict(clocks=m.counts['clocks'],counts=m.counts,calls=calls,cache=m.p.cache.counts,
  scope='exact root-reusing norm/A8 -> same actual cache/A8 slot -> true QKV, shared DIV/MUL; no external A fill; X replay remains external; no full transformer') if done else None
 return rows,state,expected


def cloud_check(net,rows,proofs,step,reset):
 assert os.getenv('GITHUB_ACTIONS')=='true';base.OUT=OUT
 checked={k:base.prove(l,r,k) for k,(l,r) in proofs.items()}
 # Reprove the changed leaf controls in a separate namespace; no duplicate
 # full leaf replay is needed because all actual integrated gates are replayed.
 directory=OUT/'producer';directory.mkdir(exist_ok=True);root.OUT=directory;root.base.OUT=directory
 (directory/'root_control.ref.v').write_text(root.control_ref())
 (directory/'connector.ref.v').write_text(root.prior.connector_ref())
 checked['root_control']=root.prove_control(root.control_cut())
 checked['producer_connector']=root.base.prove_connector(root.prior.connector())
 original=base.make
 def integrated(bad_owner=False):return make(bad_owner)[0],{}
 base.make=integrated
 try:full=base.cloud_check(net,rows,step,reset)
 finally:base.make=original
 import verify as checks
 prefix=rows[:full['negative_prefix_clocks']]
 for name,flag in [('DIV_owner','bad_div_owner'),('MUL_owner','bad_mul_owner'),('restart_phase','wrong_restart')]:
  bad=make(**{flag:True})[0];assert sha(bad.encode())!=sha(net.encode())
  wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
  (OUT/('wrong_'+name+'.nl')).write_bytes(bad.encode());full['actual_wrong_'+name+'_mismatches']=wrong
 return checked,full


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
 ap.add_argument('--references',type=Path,default=R/'build/integer_opt/norm_cache_live');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 checkpoint=OUT/'protocol.pickle';receipt_file=OUT/'receipt.json';script_sha=sha(Path(__file__).read_bytes())
 if not args.cloud and checkpoint.exists():
  # Only locally generated checkpoints are read, and all producing code is
  # matched before continuing the same in-memory state trajectory.
  report=json.loads(receipt_file.read_text())
  for n,h in report['sources'].items():
   p=Path(__file__) if n=='integer_opt/'+Path(__file__).name else R/n
   assert sha(p.read_bytes())==h,n
  if report.get('expected') is not None:
   assert sha((OUT/'vectors.txt').read_bytes())==report['vector_sha256'];print('prepared unit already complete');return
  assert sha(checkpoint.read_bytes())==report['checkpoint_sha256']
  progress=pickle.loads(checkpoint.read_bytes());assert progress['script_sha']==script_sha
  cases,refs,state,part=progress['cases'],progress['refs'],progress['state'],progress['part']
 else:
  connector=base.small();step,reset,ownership=base.ownership()
  net,proofs,binding=make();pair=mul.small_pair(*proofs['shared_mul'])
  print('net',metrics(net),flush=True)
  cases,refs=references(args.cloud,args.references);state=None;part=0
  (OUT/'core.nl').write_bytes(net.encode());(OUT/'core.v').write_text(rtl(net,'norm_qkv'))
  (OUT/'connector.ref.v').write_text(base.connector_ref())
  paths={Path(__file__).resolve()}
  for module in list(sys.modules.values()):
   name=getattr(module,'__file__',None)
   if name:
    p=Path(name).resolve()
    if R in p.parents and p.suffix=='.py':paths.add(p)
  for name in ['ci.py','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c',
   'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl',
   'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl']:paths.add(R/name)
  report=dict(status='prepared graph; C protocol incomplete',metrics=metrics(net),binding=binding,small_connector=connector,
   ownership_checks=ownership,small_actual_MUL_pair=pair,proof_metrics={k:[metrics(x) for x in pair] for k,pair in proofs.items()},
   proof_scope='arbitrary-state shared ports/projections; identified DIV/MUL selected-D conditional proofs, not whole-transformer or resource-lifetime theorem',
   cases_sha256=sha((OUT/'cases.json').read_bytes()),numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
   run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
   sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 rows,state,expected=vector_chunk(cases['cases'],refs,state,None if args.cloud else 35)
 fragment=OUT/('vectors.%03d.part'%part);fragment.write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows));part+=1
 if not args.cloud:
  progress=dict(script_sha=script_sha,cases=cases,refs=refs,state=state,part=part)
  checkpoint.write_bytes(pickle.dumps(progress,protocol=4));report['checkpoint_sha256']=sha(checkpoint.read_bytes())
  report['local_protocol_chunks']=part
 if expected is None:
  receipt_file.write_text(json.dumps(report,indent=2)+'\n')
  print('saved protocol',state['op'],'commands',state['model'].counts['clocks'],'clocks; invoke unchanged command to continue',flush=True);return
 assert expected['counts']['results']==7041 and expected['counts']['groups']==28164 and expected['counts']['aborts']==2
 with (OUT/'vectors.txt').open('wb') as f:
  for i in range(part):f.write((OUT/('vectors.%03d.part'%i)).read_bytes())
 # Checkpoints are local execution mechanics, not a claim that cloud must use
 # the same chunk sizes or unsafe deserialization of an external artifact.
 report.pop('checkpoint_sha256',None);report.pop('local_protocol_chunks',None)
 report.update(status='actual command/projection bindings, small cuts and complete C-data protocol pass; full gates await Actions',
  expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
  protocol_policy='local command-boundary checkpoints with source/SHA validation under55s; Actions regenerates the uninterrupted trajectory from frozen C')
 receipt_file.write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  assert part==1
  report['transform_proofs'],report['verification']=cloud_check(net,rows,proofs,step,reset)
  report['status']='all control/shared-port/conditional-arithmetic/projection proofs and complete actual NAND/RTL/C with real faults pass'
  receipt_file.write_text(json.dumps(report,indent=2)+'\n')
 print('clocks',expected['clocks'],json.dumps(expected['counts']),flush=True)


if __name__=='__main__':main()
