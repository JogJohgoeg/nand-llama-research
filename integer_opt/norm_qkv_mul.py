#!/usr/bin/env python3
"""Share the serial MUL: norm's live144 bits alias the QKV full192-bit unit."""
from pathlib import Path
import os,sys,json,hashlib,signal,random,argparse,shutil
R=Path(os.environ.get('H3_NORM_QKV_MUL_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_qkv_div as prior
base=prior.base;ports=prior.ports;cache=base.cache;pmod=base.producer;qmod=base.consumer
from nand import Builder,metrics,with_state
from export import import_net,load_unit,rtl
from gate_check import verify,simulate
NI,NO=base.NI,base.NO
PM=cache.NS;QM=base.PN+32
LIVE=list(range(40))+list(range(64,104))+list(range(128,192))
REMOVED=list(range(PM,PM+144))
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=Path(os.environ.get('H3_NORM_QKV_MUL_OUT',str(R/'build/integer_opt/norm_qkv_mul')))


def expand_div_state(old):
 kept=[i for i in range(base.NS) if i not in prior.REMOVED];idx={v:i for i,v in enumerate(kept)}
 return [old[idx[prior.PD+i-prior.QD if i in prior.REMOVED else i]] for i in range(base.NS)]


def norm_command(b,old,p,out):
 # Restore original producer state coordinates, retaining its exact projected
 # semantics. The48 absent high MUL bits are never read by these commands.
 live=[j for j in range(512) if j not in list(range(40,64))+list(range(104,128))]
 ix={j:i for i,j in enumerate(live)}
 ns=[old[cache.NS+ix[j]] if j in ix else 0 for j in range(512)]
 qs=old[prior.PD:prior.PD+115]+old[cache.NS+464:base.PN-7]
 _,pp,_,_=base.connect(b,old[-1],p,out[9],out[70],out[72])
 nn,weights,_=pmod.norm0();qn=pmod.quant.make()
 _,no=import_net(b,nn,[p[0]]+[0]*23,ns);_,qo=import_net(b,qn,[p[0]]+[0]*32,qs)
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 done=b.land(b.inv(p[0]),eq(old[cache.NS-38:cache.NS-36],2))
 _,ni,_,_,_,_,_=pmod.connect(b,old[base.PN-7:base.PN],pp,no,qo,done,old[cache.bank.NS:cache.bank.NS+640],old[cache.bank.NS-12:cache.bank.NS])
 _,_,_,acts=pmod.norm.control(b,ns[488:492],ns[475:482],ns[482:488],[ni[0],ni[1],ni[22],ni[23]])
 sq_take,norm_take=acts[1:3];x=ni[2:22];_,weight=import_net(b,weights,ns[475:482])
 mag=b.add([b.xor(v,x[-1]) for v in x],[0]*20,x[-1])[0]
 adjusted=b.add([b.xor(v,weight[-1]) for v in x+[x[-1]]],[0]*21,weight[-1])[0]
 wmag=b.add([b.xor(v,weight[-1]) for v in weight],[0]*16,weight[-1])[0]
 mx=[b.mux(norm_take,mag[j] if j<20 else 0,adjusted[j] if j<21 else adjusted[-1]) for j in range(64)]
 my=[b.mux(norm_take,mag[j] if j<20 else 0,wmag[j] if j<16 else 0) for j in range(64)]
 return [b.lor(sq_take,norm_take)]+mx+my


def qkv_command(b,old,p):
 es=old[base.PN:base.PN+503];ss=es[32:450]
 temp=ss[307:371];alpha=ss[371:389];count=ss[389:395];phase=ss[395:397]
 dot=es[450:467];maximum=es[467:487];engine_phase=es[500:503]
 eq=lambda bits,k:b.reduce([v if k>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 launch=eq(engine_phase,2);busy=b.lor(*phase);begin=b.land(b.land(launch,b.inv(busy)),b.inv(p[0]))
 load2=b.land(eq(phase,2),eq(count,0))
 mx=[b.mux(begin,t,dot[j] if j<17 else dot[-1]) for j,t in enumerate(temp)]
 my=[b.mux(begin,alpha[j] if j<18 else 0,maximum[j] if j<20 else 0) for j in range(64)]
 return [b.lor(begin,load2)]+mx+my


def sharing(net,bad_mul_owner=False):
 assert net.n_in==NI and net.n_out==NO and net.n_state==20296
 kept=[i for i in range(net.n_state) if i not in REMOVED];idx={v:i for i,v in enumerate(kept)}
 def primary(i):return QM+LIVE[i-PM] if i in REMOVED else i
 b=Builder(len(kept)+NI);old=[2+idx[primary(i)] for i in range(net.n_state)]
 p=list(range(2+len(kept),2+len(kept)+NI));ds,out=import_net(b,net,p,old)
 old86=expand_div_state(old);np=norm_command(b,old86,p,out);qp=qkv_command(b,old86,p)
 assert len(np)==len(qp)==129
 mul=load_unit('serial_mul');assert mul.n_state==192
 ns,_=import_net(b,mul,np,old[QM:QM+192]);qs,_=import_net(b,mul,qp,old[QM:QM+192])
 assert [ns[j] for j in LIVE]==ds[PM:PM+144],'actual144 retained norm MUL D binding'
 assert qs==ds[QM:QM+192],'actual192 QKV MUL D binding'
 selected=b.inv(out[-1]) if bad_mul_owner else out[-1]
 command=[b.mux(selected,x,y) for x,y in zip(np,qp)]
 md,_=import_net(b,mul,command,old[QM:QM+192])
 reference=b.finish([b.mux(out[-1],x,y) for x,y in zip(ns,qs)]);proof=b.finish(md)
 nxt=[md[i-QM] if QM<=i<QM+192 else ds[i] for i in kept]
 result=with_state(b.finish(nxt+out),len(kept))
 return result,reference,proof,dict(kept=kept,removed=REMOVED,norm_to_full_MUL_bits=LIVE,
  old_norm_MUL_start=PM,old_QKV_MUL_start=QM,shared_MUL_bits=192,removed_MUL_bits=144,
  exact_norm_retained_D_binding=True,exact_QKV_all_D_binding=True,
  norm_unobserved_extended_bits=48,conditional_owner_selected_D=True,unbounded_lifetime_proof=False,
  scalar_results_and_control_retained=True,extra_vector_bits=0)


def make(bad_owner=False,bad_div_owner=False,bad_mul_owner=False):
 old=prior.make(bad_owner,bad_div_owner)[1]
 if not bad_owner and not bad_div_owner:assert sha(old.encode())=='5e4bd90e16c559fcc004f34a0494063734ebae51d22530838aa5948037bb143e'
 net,left,right,binding=sharing(old,bad_mul_owner)
 return old,net,left,right,binding


def small():
 b=Builder(259);l=list(range(2,131));r=list(range(131,260));select=260
 g=b.finish([b.mux(select,x,y) for x,y in zip(l,r)]);assert metrics(g)['nNand']<=4000
 rng=random.Random(260797);xs=[];ys=[]
 for i in range(512):
  a=rng.getrandbits(129);c=rng.getrandbits(129);s=i&1
  xs.append(a+(c<<129)+(s<<258));ys.append(c if s else a)
 return dict(metrics=metrics(g),verification=verify(g,xs,ys))


def small_pair(left,right):
 assert max(metrics(g)['nNand']+g.n_state for g in (left,right))<=4000
 rng=random.Random(260798);values=[rng.getrandbits(left.n_in) for _ in range(64)]
 expected=simulate(left,values)
 return dict(reference=verify(left,values,expected),candidate=verify(right,values,expected),
  scope='differential arbitrary-state checks of actual selected next-state cut; not full datapath C replay')


VECTOR_SHA='af895b6adc136ab74e3a96ee9a1a7316ab4549b715fd83200e89f8c127298e6c'
CASES_SHA='7dfdb7a79473de65e3d9e32be782279212ef1d8e64353351a9d792a79e61c974'
EXPECTED_SHA='ea06e3735c75ba35b2b70578949d9b3fd902e058ac58c292c122d9f123c26de1'


def reference_data(cloud,reference_dir):
 if cloud:
  cases,refs=base.fixtures();rows,expected=base.vectors(cases['cases'],refs)
  (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 else:
  receipt=json.loads((reference_dir/'receipt.json').read_text())
  for n,h in receipt['sources'].items():assert sha((R/n).read_bytes())==h,n
  assert receipt['metrics']['sha256']=='5e4bd90e16c559fcc004f34a0494063734ebae51d22530838aa5948037bb143e'
  expected=receipt['expected'];rows=None
  for p in reference_dir.rglob('*'):
   if p.is_file() and (p.name in ('vectors.txt','cases.json','reference.c','matrix_golden.c') or
                      p.parent.name=='c_vectors' and p.suffix in ('.json','.c')):
    dest=OUT/p.relative_to(reference_dir);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 assert expected['clocks']==1503175
 assert sha((OUT/'vectors.txt').read_bytes())==VECTOR_SHA
 assert sha((OUT/'cases.json').read_bytes())==CASES_SHA
 assert sha(json.dumps(expected,sort_keys=True,separators=(',',':')).encode())==EXPECTED_SHA
 return rows,expected


def cloud_check(net,rows,pl,pr,dl,dr,ml,mr,step,reset):
 assert os.getenv('GITHUB_ACTIONS')=='true';base.OUT=OUT
 inherited_ports=base.prove(pl,pr,'inherited_ports');inherited_div=base.prove(dl,dr,'inherited_div')
 proof=base.prove(ml,mr,'shared_mul')
 proof.update(state_identification=True,conditional_owner_selected_D=True,shared_D_bits=192,
  original_norm_retained_bits=144,norm_unobserved_extended_bits=48,unbounded_lifetime_proof=False)
 original=base.make
 def optimized(*args,**kw):
  _,candidate,_,_,binding=make(*args,**kw);return candidate,binding
 base.make=optimized
 try:checked=base.cloud_check(net,rows,step,reset)
 finally:base.make=original
 import verify as checks
 prefix=rows[:checked['negative_prefix_clocks']]
 for kind in ['div','mul']:
  bad=make(**{'bad_'+kind+'_owner':True})[1];assert sha(bad.encode())!=sha(net.encode())
  wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
  (OUT/('wrong_'+kind+'_owner.nl')).write_bytes(bad.encode());checked['actual_wrong_'+kind.upper()+'_owner_mismatches']=wrong
 return inherited_ports,inherited_div,proof,checked


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
 ap.add_argument('--references',type=Path,default=R/'build/integer_opt/norm_qkv_div');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 sm=small();step,reset,ownership=base.ownership()
 _,pnet,pl,pr,port_binding=ports.make();before,dl,dr,div_binding=prior.sharing(pnet)
 net,ml,mr,binding=sharing(before);pair=small_pair(ml,mr)
 assert sha(before.encode())=='5e4bd90e16c559fcc004f34a0494063734ebae51d22530838aa5948037bb143e'
 print('before/after',metrics(before),metrics(net),flush=True)
 rows,expected=reference_data(args.cloud,args.references)
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
              'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl']:
  paths.add(R/name)
 report=dict(status='actual retained MUL D bindings and small differential cuts pass; full shared gate replay awaits Actions',
  before=metrics(before),metrics=metrics(net),binding=binding,small=sm,small_actual_pair=pair,
  ownership_checks=ownership,proof_metrics={'reference':metrics(ml),'candidate':metrics(mr)},expected=expected,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  reference_policy='local reuses source-checked R88 frozen C data; Actions regenerates all1503175 clocks; fixed expected/cases/vector digests',
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  report['inherited_ports_proof'],report['inherited_DIV_proof'],report['shared_MUL_proof'],report['verification']=cloud_check(net,rows,pl,pr,dl,dr,ml,mr,step,reset)
  report['status']='conditional192-bit shared MUL and inherited proofs pass; complete actual shared NAND/RTL/C and faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print('reference clocks',expected['clocks'])


if __name__=='__main__':main()
