#!/usr/bin/env python3
"""Keep the exact first norm root for the second normalization pass.

Only norm's restarted phase bit changes (1 -> 5). Its existing start still
clears the index/count and preserves the root. No arithmetic/precision change.
"""
from pathlib import Path
import os,sys,json,hashlib,signal,argparse,random,shutil,subprocess
R=Path(os.environ.get('H3_NORM_ROOT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_cache_recompute as prior
from nand import Builder,metrics,with_state,flip_output,blif,from_yosys
from export import import_net,rtl
from gate_check import verify
from state_projection import prune,structural_identity
base=prior.base
OUT=Path(os.environ.get('H3_NORM_ROOT_OUT',str(R/'build/integer_opt/norm_cache_root')))
sha=lambda b:hashlib.sha256(b).hexdigest()
NI,NO=40,40


def restart_bit(b,phase,owner,reset,original):
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 reuse=b.reduce([b.inv(reset),eq(owner[:3],2),eq(phase,0)],b.land,1)
 return b.lor(original,reuse)


def restart(net,wrong_phase=False):
 b=Builder(net.n_state+NI);old=list(range(2,net.n_state+2));p=list(range(net.n_state+2,net.n_state+NI+2))
 d,o=import_net(b,net,p,old);target=base.cache.NS+488+(1 if wrong_phase else 2)
 d[target]=restart_bit(b,old[base.cache.NS+488:base.cache.NS+492],old[-7:],p[0],d[target])
 if not wrong_phase:
  ns=old[base.cache.NS:base.cache.NS+512];qs=old[base.cache.NS+512:-7]
  nn,_,_=base.norm0();qn=base.quant.make()
  _,no=import_net(b,nn,[p[0]]+[0]*23,ns);_,qo=import_net(b,qn,[p[0]]+[0]*32,qs)
  ni=prior.connect(b,old[-7:],p,no,qo,0,[0]*640,[0]*12)[1]
  _,out=import_net(b,control_cut(),ns[488:492]+ns[475:482]+ns[482:488]+ni[:2]+ni[22:24]+old[-7:])
  assert out[:17]==d[base.cache.NS+488:base.cache.NS+492]+d[base.cache.NS+475:base.cache.NS+488], 'actual complete norm-control D binding'
 return with_state(b.finish(d+o),net.n_state)


def make(bad_rotation=False,bad_owner=False,wrong_phase=False):
 old,parts,weights,words=prior.make_unshared(bad_rotation)
 raw=restart(old,wrong_phase);shared,dl,dr,binding=prior.sharing(raw,bad_owner)
 net,kept=prune(shared)
 return net,raw,shared,dl,dr,kept,binding,weights,words


def control_cut():
 # Four norm phase, seven index, six count, four pins, seven producer-owner.
 b=Builder(28);p=list(range(2,30));phase,index,count,pins,owner=p[:4],p[4:11],p[11:17],p[17:21],p[21:]
 pd,ix,cn,acts=base.norm.control(b,phase,index,count,pins)
 pd[2]=restart_bit(b,phase,owner,pins[0],pd[2])
 return b.finish(pd+ix+cn+acts)


def control_ref():
 # Arithmetic leaf remains the same. This independent RTL describes only
 # the norm control plus the second-pass restart, including arbitrary state.
 return '''module top(input [27:0] din,output [29:0] dout);
wire [3:0] ph=din[3:0];wire [6:0] idx=din[10:4];wire [5:0] count=din[16:11];
wire reset=din[17],start=din[18],xvalid=din[19],yready=din[20];wire [6:0] owner=din[27:21];
wire begin_n=!reset && ph==0 && start,ready=!reset && (ph==1 || ph==5),take=ready && xvalid;
wire sq_take=take && ph==1,norm_take=take && ph==5,sq_end=ph==2 && count==20,root_end=ph==4 && count==24;
wire mul_end=ph==6 && count==16,div_end=ph==8 && count==38,valid=!reset && ph==9,ack=valid && yready;
wire run=ph==2 || ph==4 || ph==6 || ph==8;
reg [3:0] pn;reg [6:0] ix;reg [5:0] cn;
always @* begin
 pn=ph;
 if(begin_n)pn=1;if(sq_take)pn=2;if(sq_end)pn=idx==127?3:1;
 if(ph==3)pn=4;if(root_end)pn=5;if(norm_take)pn=6;if(mul_end)pn=7;
 if(ph==7)pn=8;if(div_end)pn=9;if(ack)pn=idx==127?0:5;
 if(reset)pn=0;
 if(!reset && owner[2:0]==2 && ph==0)pn[2]=1;
 cn=count+run;
 if(reset || begin_n || take || ph==3 || ph==7 || sq_end || root_end || mul_end || div_end)cn=0;
 ix=idx+(sq_end || ack);if(reset || begin_n || root_end)ix=0;
end
wire [12:0] acts={ph>=5 && ph<=9,ph!=0,valid,ready,ph==7,ph==3,div_end,mul_end,root_end,sq_end,norm_take,sq_take,begin_n};
assign dout={acts,cn,ix,pn};
endmodule
'''


def control_step(x):
 ph=x&15;idx=x>>4&127;count=x>>11&63;reset,start,xv,yr=[x>>i&1 for i in range(17,21)];owner=x>>21&127
 begin=not reset and ph==0 and start;ready=not reset and ph in (1,5);take=ready and xv
 sqtake=take and ph==1;ntake=take and ph==5;sqe=ph==2 and count==20;re=ph==4 and count==24
 me=ph==6 and count==16;de=ph==8 and count==38;valid=not reset and ph==9;ack=valid and yr
 pn=ph
 for cond,target in [(begin,1),(sqtake,2),(sqe,1),(sqe and idx==127,3),(ph==3,4),(re,5),(ntake,6),(me,7),(ph==7,8),(de,9),(ack,5),(ack and idx==127,0)]:
  if cond:pn=target
 if reset:pn=0
 if not reset and owner&7==2 and ph==0:pn|=4
 cn=0 if reset or begin or take or ph in (3,7) or sqe or re or me or de else (count+int(ph in (2,4,6,8)))&63
 ix=0 if reset or begin or re else (idx+int(sqe or ack))&127
 acts=[begin,sqtake,ntake,sqe,re,me,de,ph==3,ph==7,ready,valid,ph!=0,5<=ph<=9]
 return pn+(ix<<4)+(cn<<11)+(sum(int(v)<<j for j,v in enumerate(acts))<<17)


def small(cut):
 assert metrics(cut)['nNand']<=4000
 rng=random.Random(260796)
 xs=[ph+(idx<<4)+(count<<11)+(pins<<17)+(owner<<21) for ph in range(16) for idx in (0,127) for count in (0,16,20,24,38,63) for pins in range(16) for owner in (0,2,3,6)]
 xs += [rng.getrandbits(28) for _ in range(2048)]
 return verify(cut,xs,[control_step(x) for x in xs])


class Model(prior.Model):
 def tick(self,p,case=None):
  again=not(p&1) and self.owner&7==2 and self.n['p']==0
  root=self.n['root'];y=super().tick(p,case)
  if again:
   assert self.n['p']==1 and self.n['idx']==0 and self.n['root']==root==self.case['root']
   self.n['p']=5
  return y


def vectors(cases):
 m=Model();rows=[];calls=[];stored={}
 def tick(reset=0,start=0,x=0,xv=0,en=1,pos=0,address=4095,case=None):
  p=reset+(start<<1)+((x&1048575)<<2)+(xv<<22)+(en<<23)+(pos<<24)+(address<<28)
  y,mask=m.tick(p,case);rows.append((p,y,mask if len(rows) else 0));return y
 def reset():tick(reset=1,start=1,x=-524288,xv=1);stored.clear();tick()
 def read(pos,want):
  for j,byte in enumerate(want):
   for _ in range(base.cache.bank.SLOTS+1):
    y=tick(address=pos*131+j)
    if y>>19&1:assert y>>11&255==byte;break
   else:raise AssertionError('cache read never ready')
 def transact(case,pos,stall=False,abort=None):
  tick(start=1,pos=pos,case=case);assert m.owner&7==1
  rotations=m.counts['slot_rotations'];acks=m.cache.counts['cache_bytes'];disabled=set();before=m.counts.copy()
  for clock in range(100000):
   phase=m.owner&7
   if abort and abort(m):reset();return False
   if phase==6:break
   np=m.n['p'];idx=m.n['idx'];x=case['x'][idx] if np in (1,5) else -524288
   en=int(not stall or clock%11!=2);xv=int(not stall or clock%7!=3)
   if stall and phase==5 and m.cache.bank.cursor==((m.cache.ctl>>6&15)*131+(m.cache.ctl>>10&255)):
    ci=m.cache.ctl>>10&255
    if ci in (31,127,130) and ci not in disabled:en=0;disabled.add(ci)
   tick(start=int(clock%97==0),x=x,xv=xv,en=en,pos=(pos+1)%16)
  else:raise AssertionError('producer timeout')
  y=tick();assert y>>10&1 and y>>20==case['maximum'];assert m.cache.counts['cache_bytes']-acks==131
  assert m.counts['slot_rotations']-rotations==4
  for k,count in [('norm_scan',128),('norm_replay',256),('norm_outputs',256),('quant_scan',128),('quant_replay',128),('quant_writes',128)]:assert m.counts[k]-before[k]==count,k
  stored[pos]=case['packed'];read(pos,case['packed'])
  calls.append(dict(position=pos,clocks_to_observed_done=clock+2,source=case['source'],stalled=stall,
   disabled_cache_positions=sorted(disabled),cache_bytes=131,producer_rotations=4,full_norm_passes=1,normalization_passes=2,external_X_scalars=384))
  return True
 reset()
 for i,c in enumerate(cases):assert transact(c,i%16,stall=i in (1,17))
 for p,v in sorted(stored.items()):read(p,v)
 aborts=[lambda x:x.n['p']==2 and x.n['idx']==3,
  lambda x:x.owner&7==1 and x.n['p']==9 and x.n['idx']==31,
  lambda x:x.owner&7==3 and x.n['p']==6 and x.n['idx']==31,
  lambda x:x.owner&7==3 and x.q['p']==3 and x.q['idx']==31,
  lambda x:x.owner&7==3 and x.q['p']==4 and x.q['idx']==127,
  lambda x:x.owner&7==5 and x.cache.ctl>>10&255==64]
 for abort in aborts:
  assert not transact(cases[-1],0,abort=abort);assert transact(cases[2],0)
 assert m.counts['completed']==len(cases)+len(aborts) and m.counts['aborted']==len(aborts)
 return rows,dict(clocks=len(rows),calls=calls,counts=m.counts,cache_counts=m.cache.counts,complete_c_cases=len(cases),
  scope='one square/root pass, twice true normalized output, first max scan then A8 into same pruned slot,131byte cache and actual readback; external repeated X, no matrix/whole-token controller')



def prove_control(cut):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 ys=OUT/'root_control.ys'
 ys.write_text(f'read_verilog {OUT}/root_control.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/root_control.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'root_control.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
 ref=from_yosys(json.loads((OUT/'root_control.ref.json').read_text()),28,30)
 for n,g in [('root_control',cut),('root_control.reference',ref),('root_control.negative',flip_output(cut))]:(OUT/(n+'.blif')).write_text(blif(g))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 good=cec(abc,OUT/'root_control.blif',OUT/'root_control.reference.blif',OUT/'root_control.cec.log');assert good['verdict']=='equivalent'
 bad=cec(abc,OUT/'root_control.negative.blif',OUT/'root_control.reference.blif',OUT/'root_control.negative.log');assert bad['verdict']=='different'
 return dict(proof=good,negative=bad,arbitrary_control_inputs=True,actual_norm_control_D_binding=True,
  scope='norm17 controlD and13 action bits plus second-pass restart; unchanged arithmetic, full protocol separately replayed')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
 ap.add_argument('--references',type=Path,default=R/'build/integer_opt/norm_cache_live');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT;prior.OUT=OUT
 cut=control_cut();checked=small(cut)
 net,raw,shared,dl,dr,kept,binding,weights,words=make()
 before=prior.make()[0];assert sha(before.encode())=='fd1b75e9530f10136270f2ecde6c717455acf150fb52ff969d00f4e66485e2b5'
 pl,pr,same=structural_identity(shared,net,kept);assert same and pl.encode()==pr.encode()
 assert not structural_identity(shared,flip_output(net),kept)[2]
 naive=restart(prior.make_unshared(merged=False)[0]);slot_left,slot_right=base.port_identity(naive,raw)
 print('before/new',metrics(before),metrics(net),flush=True)
 cases=prior.reference_data(args.cloud,args.references);assert words==cases['weights']
 rows,expected=vectors(cases['cases'])
 (OUT/'fill.nl').write_bytes(net.encode());(OUT/'fill.v').write_text(rtl(net,'norm_cache_fill'))
 (OUT/'connector.ref.v').write_text(prior.connector_ref());(OUT/'root_control.ref.v').write_text(control_ref())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer_opt/weights.py','physical/verify.py','physical/nl_sim.c','integer/int_model.c','physical/model.bin',
  'integer_opt/reverse_attention.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl']:paths.add(R/n)
 report=dict(status='small actual root-restart control and C-data protocol pass; full gates await Actions',
  before=metrics(before),metrics=metrics(net),unpruned_shared=metrics(shared),kept=kept,small=checked,binding=binding,
  actual_norm_control_17D_binding=True,other_original_D_and_outputs_unchanged_except_restart_bit=True,
  control_metrics=metrics(cut),projection_metrics=metrics(pl),same_canonical_D_output=True,actual_mutation_breaks_identity=True,
  expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  contract='same public40/40; exact root held between norm passes, first square+norm then only norm;384 X scalar accepts per vector',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['root_control_proof']=prove_control(cut)
  original=prior.make
  def rooted(*a,**kw):
   n,_,s,l,r,k,bind,w,words_=make(*a,**kw);return n,s,l,r,k,bind,w,words_
  prior.make=rooted
  try:report['divider_proof'],report['projection_proof'],report['verification']=prior.cloud_check(net,raw,prior.connector(),weights,words,rows,dl,dr,pl,pr,slot_left,slot_right)
  finally:prior.make=original
  import verify as checks
  bad=make(wrong_phase=True)[0];assert sha(bad.encode())!=sha(net.encode())
  wrong=checks.check_nand(rows[:report['verification']['negative_prefix_clocks']],bad.encode());assert wrong>0
  (OUT/'wrong_restart_phase.nl').write_bytes(bad.encode());report['verification']['actual_wrong_restart_phase_mismatches']=wrong
  report['status']='actual root control/slot/connector/weight/DIV/projection proofs and complete NAND/RTL/C/faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('clocks',expected['clocks'],json.dumps(expected['counts']),flush=True)


if __name__=='__main__':main()
