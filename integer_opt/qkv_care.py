#!/usr/bin/env python3
"""Care-aware Q/K/V weight decision tree, bound to the exact integrated path.

Only the already-unreachable fourth matrix is unspecified. Numerical values,
state coordinates, protocol and all reachable weight words stay unchanged.
"""
from pathlib import Path
import os,sys,json,hashlib,random,signal,argparse,shutil
R=Path(os.environ.get('H3_QKV_CARE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_qkv_recompute as prior
from nand import Builder,metrics,with_state,flip_output
from export import import_net,rtl,weight_words
from state_projection import prune,structural_identity
from gate_check import verify
import weight_cone
base,ports,root,mul,cache=prior.base,prior.ports,prior.root,prior.mul,prior.cache
NI,NO,NS=prior.NI,prior.NO,prior.NS
share_div=prior.share_div
OUT=Path(os.environ.get('H3_QKV_CARE_OUT',str(R/'build/integer_opt/qkv_care')))
sha=lambda b:hashlib.sha256(b).hexdigest()
OLD_SHA='7159b3c99583168915a8b4b190608514078e48e19a741b67aa30c3b63c16a077'
VECTOR_SHA='8d57dba20f45b05d366fcb214194dc72a2caaf4df7f64f17fe038562958ab88b'

def care_lookup(table,width,valid,order):
 n=len(order);assert sorted(order)==list(range(n)) and len(table)==1<<n
 b=Builder(n);memo={}
 seq=[sum(((i>>j)&1)<<k for j,k in enumerate(order)) for i in range(1<<n)]
 care=sum(int(valid[i])<<j for j,i in enumerate(seq))
 def node(value,mask,level):
  value&=mask
  if not value:return 0
  if value==mask:return 1
  if value>(value^mask):return b.inv(node(value^mask,mask,level))
  key=value,mask,level
  if key not in memo:
   half=1<<(level-1);lowmask=(1<<half)-1
   lo,hi=value&lowmask,value>>half;lc,hc=mask&lowmask,mask>>half
   if not ((lo^hi)&lc&hc):
    memo[key]=node(lo|hi,lc|hc,level-1)
   else:memo[key]=b.mux(2+order[level-1],node(lo,lc,level-1),node(hi,hc,level-1))
  return memo[key]
 out=[node(sum(((table[i]>>bit)&1)<<j for j,i in enumerate(seq)),care,n) for bit in range(width)]
 return b.finish(out)


def port_graph(bad_owner=False,wrong_restart=False):
 pn,sp,kept,producer_proofs=prior.producer19(wrong_restart)
 original=base.qkv.candidates()[0];table=weight_tables()[2]
 qn,_,_=base.qkv.prior.make(table,original)
 assert qn.n_state==19885 and qn.n_in==676 and qn.n_out==34
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


def weight_tables():
 # Four fixed radix orders, not an unbounded search. Choose once from recorded
 # source counts; large truth equivalence is exclusively checked on Actions.
 blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==base.consumer.engine.MODEL_SHA
 words=weight_words(blob)[:2048];valid=[True]*1536+[False]*512
 orders=dict(matrix_last=list(range(11)),matrix_first=[9,10]+list(range(9)),
  group_matrix_row=[0,1,9,10]+list(range(2,9)),matrix_row_group=[9,10]+list(range(2,9))+[0,1])
 choices={name:care_lookup(words,64,valid,order) for name,order in orders.items()}
 selected=min(choices,key=lambda name:(metrics(choices[name])['nNand'],metrics(choices[name])['nand_depth']))
 assert selected=='matrix_row_group'
 original=base.qkv.candidates()[4];candidate=choices[selected]
 b=Builder(13);_,y=import_net(b,candidate,list(range(2,13)));adapted=b.finish(y)
 return words,original,adapted,candidate,dict(selected=selected,orders=orders,choices={k:metrics(v) for k,v in choices.items()},before=metrics(original),after=metrics(candidate))


def small():
 # Exhaust all three-matrix Boolean columns, plus varied sparse care sets and
 # input permutations in small functions. No full selector evaluation locally.
 rng=random.Random(260806);reports=[]
 for n in range(2,7):
  for k in range(24):
   width=1 if n==2 else 4;table=[rng.getrandbits(width) for _ in range(1<<n)]
   if n==2:table=[(k>>j)&1 for j in range(3)]+[0]
   valid=[i<3 for i in range(4)] if n==2 else [bool(rng.randrange(3)) for _ in table]
   valid[0]=True;order=list(range(n));rng.shuffle(order)
   g=care_lookup(table,width,valid,order);assert metrics(g)['nNand']<=4000
   xs=[i for i,v in enumerate(valid) if v];result=verify(g,xs,[table[i] for i in xs])
   reports.append(dict(n=n,width=width,order=order,care_count=len(xs),metrics=metrics(g),verification=result))
 return dict(cases=len(reports),known_addresses=sum(x['care_count'] for x in reports),reports=reports)


def transition_pair(old,new,bind,oldbind):
 _,previous,_,candidate,_=weight_tables()
 left,right,record=weight_cone.pair(old,new,previous,candidate,bind,oldbind,base.PN)
 record['scope']='exact actual-cone recomposition and arbitrary common-body D/output proof; separately proved selector equality requires actual cursor matrix<3'
 return left,right,record


def references(cloud,directory):
 if cloud:
  prior.OUT=OUT;cases,refs=prior.references(True,None);rows,_,expected=prior.vector_chunk(cases['cases'],refs)
  (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 else:
  rec=json.loads((directory/'receipt.json').read_text());assert rec['metrics']['sha256']==OLD_SHA
  for n,h in rec['sources'].items():assert sha((R/n).read_bytes())==h,n
  expected=rec['expected'];rows=None
  for p in directory.rglob('*'):
   if p.is_file() and (p.name in ('vectors.txt','cases.json','reference.c','matrix_golden.c') or p.parent.name=='c_vectors' and p.suffix in ('.json','.c')):
    dest=OUT/p.relative_to(directory);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 assert expected['clocks']==1693911 and sha((OUT/'vectors.txt').read_bytes())==VECTOR_SHA
 return rows,expected


def cloud_check(net,old,words,previous,candidate,proofs,step,reset,rows):
 assert os.getenv('GITHUB_ACTIONS')=='true';base.OUT=OUT;base.qkv.prior.OUT=OUT
 q=base.qkv;scope={}
 for name,g in [('weight_owner_step',q.owner_graphs()[0]),('weight_owner_reset',q.owner_graphs()[1]),('weight_cursor_step',q.cursor_graph()[0]),('weight_cursor_reset',q.prior.invariant_graphs()[1])]:
  b=Builder(g.n_in);scope[name]=base.prove(g,b.finish([1]),name)
 left,right=q.masked(previous),q.masked(candidate)
 scope['selector_legal']=base.prove(left,right,'selector_legal')
 expected=words[:1536]+[0]*512
 scope['full_selector_truth']=dict(previous=verify(left,list(range(2048)),expected),candidate=verify(right,list(range(2048)),expected),legal_addresses=1536)
 prior.OUT=OUT;saved=prior.make;prior.make=make
 try:scope['transforms'],actual=prior.cloud_check(net,rows,proofs,step,reset)
 finally:prior.make=saved
 return scope,actual


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--references',type=Path,default=R/'build/integer_opt/norm_qkv_recompute');a=ap.parse_args()
 if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);prior.OUT=OUT;base.OUT=OUT;base.qkv.prior.OUT=OUT
 local=small();words,oldtable,table,newtable,selection=weight_tables();wc=base.qkv.prior.c_words(words)
 baseline,_,beforebind=prior.make();assert sha(baseline.encode())==OLD_SHA
 net,proofs,binding=make();left,right,transition=transition_pair(baseline,net,binding,beforebind)
 proofs['complete_transition_legal']=(left,right)
 assert metrics(net)['nNand']<metrics(baseline)['nNand']
 connector=base.small();step,reset,ownership=base.ownership()
 rows,expected=references(a.cloud,a.references)
 for name,g in [('core',net),('selector',newtable)]:(OUT/(name+'.nl')).write_bytes(g.encode())
 (OUT/'core.v').write_text(rtl(net,'norm_qkv'));(OUT/'connector.ref.v').write_text(base.connector_ref())
 (OUT/'selector_words.json').write_text(json.dumps(words[:1536])+'\n')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if R in p.parents and p.suffix=='.py':paths.add(p)
 for name in ['ci.py','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/reverse_attention.c',
   'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/weights_layer0.nl','integer_opt/pilot_units/serial_div.nl',
   'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl']:paths.add(R/name)
 report=dict(status='small care functions, C words and exact structural bindings pass; full selector/transition and gate replay await Actions',
  metrics=metrics(net),before=metrics(baseline),selection=selection,small_care=local,C_words=wc,small_connector=connector,ownership_checks=ownership,
  binding=binding,transition_domain=transition,proof_metrics={k:[metrics(g) for g in v] for k,v in proofs.items()},
  expected=expected,vector_sha256=VECTOR_SHA,cases_sha256=sha((OUT/'cases.json').read_bytes()),
  numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if a.cloud:
  report['verification_proofs'],report['verification']=cloud_check(net,baseline,words,oldtable,newtable,proofs,step,reset,rows)
  report['status']='range and full legal state-transition CEC, all selector words, full actual NAND/RTL/C and real faults pass'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:report[k] for k in ['status','metrics','before','selection','C_words','transition_domain']},indent=2))


if __name__=='__main__':main()
