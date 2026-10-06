#!/usr/bin/env python3
"""Route norm and FF loads into one physical MUL and DIV.

A307-bit conditional next-state comparison and full C-observed sequences
check the state identification. Source down-load priority is retained even
for arbitrary old control states; no lifetime invariant is assumed proved.
"""
from pathlib import Path
import sys,json,hashlib,signal,os,shutil,argparse
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_ff as source
from nand import Builder,with_state,metrics,blif,flip_output
from export import import_net,load_unit,rtl
from golden import Netlist
from prefix_store import select
from silu_pipeline import shifted_sat,front
sha=lambda data:hashlib.sha256(data).hexdigest()

def guard(b,owner,fmul,fdiv,nmul,ndiv):
 return b.inv(b.lor(b.land(owner,b.lor(fmul,fdiv)),b.land(b.inv(owner),b.lor(nmul,ndiv))))

def make():
 net=source.make()[0];assert sha(net.encode())=='e3c05aa0d042d55ab389a599a12e8d7313d67a4d516a389791881ddf61fa39ea'
 removed=set(range(5939,6246));kept=[i for i in range(6456) if i not in removed];slots={old:new for new,old in enumerate(kept)}
 def primary(i):return i-5939 if i in removed else i
 b=Builder(len(kept)+net.n_in);old=[2+slots[primary(i)] for i in range(6456)];ins=list(range(2+len(kept),2+len(kept)+net.n_in))
 ds,out=import_net(b,net,ins,old)
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 reset=ins[0];keep=b.inv(reset);owner=eq(old[6451:6454],1)
 rp=old[534:538];sp=old[395:397];sc=old[389:395];dp=old[5932:5935];down=old[5936]
 ff_launch=b.lor(eq(rp,2),eq(rp,5));down_launch=b.land(down,eq(dp,2))
 scale_begin=b.reduce([keep,b.lor(ff_launch,down_launch),eq(sp,0)],b.land,1)
 scale_second=b.land(eq(sp,2),eq(sc,0));scale_load=b.lor(scale_begin,scale_second)
 silu_begin=b.reduce([keep,eq(rp,7),eq(old[443:445],0)],b.land,1)
 silu_second=b.land(eq(old[443:445],1),eq(old[438:443],17));silu_load=b.lor(silu_begin,silu_second)
 temp=old[307:371];alpha=old[371:389];dot=old[466:483];maximum=old[483:503]
 sx=[b.mux(scale_begin,temp[j],dot[j] if j<17 else dot[-1]) for j in range(64)]
 sy=[b.mux(scale_begin,alpha[j] if j<18 else 0,maximum[j] if j<20 else 0) for j in range(64)]
 u=old[418:438];t=shifted_sat(b,old[:37],16);signed_t=t+[t[-1]]
 adjusted=b.add([b.xor(v,u[-1]) for v in signed_t],[0]*21,u[-1])[0]
 mag=b.add([b.xor(v,u[-1]) for v in u],[0]*20,u[-1])[0]
 gate=old[514:534];_,sig=import_net(b,front(),gate)
 tx=[b.mux(silu_begin,adjusted[j] if j<21 else adjusted[-1],gate[j] if j<20 else gate[-1]) for j in range(64)]
 ty=[b.mux(silu_begin,mag[j] if j<20 else 0,sig[j] if j<17 else 0) for j in range(64)]
 # Preserve the source's down-load priority even on arbitrary old states.
 down_begin=b.reduce([keep,down_launch,eq(sp,0)],b.land,1)
 choose_silu=b.land(silu_load,b.inv(down_begin))
 fx=[b.mux(choose_silu,a,v) for a,v in zip(sx,tx)];fy=[b.mux(choose_silu,a,v) for a,v in zip(sy,ty)];fmul=b.lor(scale_load,silu_load)
 scale_div=b.land(eq(sp,3),eq(sc,0));ffquant=b.reduce([keep,eq(old[576:579],2),eq(rp,9)],b.land,1)
 hquant=b.reduce([keep,eq(old[3339:3342],2),eq(old[5915:5917],1),b.inv(old[5920]),ins[23]],b.land,1)
 ffraw=old[445:465];fn=b.add([0]*7+ffraw,[b.inv(v) for v in ffraw+[ffraw[-1]]*7],1)[0]
 head=old[3355:3995];hx=select(b,[head[j*20:(j+1)*20] for j in range(32)],old[3330:3335])
 hn=b.add([0]*7+hx,[b.inv(v) for v in hx+[hx[-1]]*7],1)[0]
 fnum=[b.mux(hquant,b.mux(ffquant,a,f),h) for a,f,h in zip([0]*9+temp[:55],[0]*37+fn,[0]*37+hn)]
 fden=[b.mux(hquant,b.mux(ffquant,33292288>>j&1,old[538+j] if j<20 else 0),old[3301+j] if j<20 else 0) for j in range(25)]
 fdiv=b.reduce([scale_div,ffquant,hquant],b.lor,0)
 # Norm load operands are independent of the old unshared source D equations.
 ni=old[6414:6421];np=old[6427:6431];nc=old[6421:6427];x=ins[2:22]
 take=b.reduce([keep,owner,ins[22]],b.land,1);nt=b.land(take,eq(np,5));st=b.land(take,eq(np,1));nmul=b.lor(nt,st)
 weights=source.norm_stream.make()[1];_,weight=import_net(b,weights,ni)
 mag=b.add([b.xor(v,x[-1]) for v in x],[0]*20,x[-1])[0]
 adjusted=b.add([b.xor(v,weight[-1]) for v in x+[x[-1]]],[0]*21,weight[-1])[0]
 wmag=b.add([b.xor(v,weight[-1]) for v in weight],[0]*16,weight[-1])[0]
 nx=[b.mux(nt,mag[j] if j<20 else 0,adjusted[j] if j<21 else adjusted[-1]) for j in range(64)]
 ny=[b.mux(nt,mag[j] if j<20 else 0,wmag[j] if j<16 else 0) for j in range(64)]
 mx=[b.mux(nmul,a,v) for a,v in zip(fx,nx)];my=[b.mux(nmul,a,v) for a,v in zip(fy,ny)]
 md,_=import_net(b,load_unit('serial_mul'),[b.lor(fmul,nmul)]+mx+my,old[:192])
 ndiv=eq(np,7);num=[b.mux(ndiv,a,v) for a,v in zip(fnum,[0]*28+old[:36])]
 den=[b.mux(ndiv,a,v) for a,v in zip(fden,old[6390:6414]+[0])]
 meta=json.loads((R/'integer_opt/pilot_units/manifest.json').read_text())['serial_div'];raw=(R/'integer_opt/pilot_units/serial_div.nl').read_bytes();assert sha(raw)==meta['sha256']
 div=Netlist.decode(raw,meta['nIn'],meta['nOut']);dd,_=import_net(b,div,[b.lor(fdiv,ndiv)]+num+den,old[192:307])
 ok=guard(b,owner,fmul,fdiv,nmul,ndiv)
 nxt=[md[i] if i<192 else dd[i-192] if i<307 else ds[i] for i in kept]
 candidate=with_state(b.finish(nxt+out),len(kept));monitor=with_state(b.finish(nxt+out+[ok]),len(kept))
 reference=b.finish([b.land(ok,b.mux(owner,ds[i],ds[5939+i])) for i in range(307)])
 proof=b.finish([b.land(ok,v) for v in md+dd])
 return candidate,monitor,reference,proof,dict(before=metrics(net),after=metrics(candidate),removed_mul_div_state_bits=307,
  h_and_ff_storage_bits=5248,nonvector_bits=len(kept)-5248,single_mul_instances=1,single_div_instances=1,single_sqrt_instances=1,single_dot_instances=1,
  proof_scope='307 arithmetic next-state bits under explicit norm/FF load ownership after old-state identification; not unbounded lifetime proof')


OUT=R/'build/integer_opt/norm_ff_shared'

def small_check():
 from bench import verify
 b=Builder(5);g=guard(b,*range(2,7));net=b.finish([g]);xs=list(range(32));ys=[]
 for value in xs:
  owner,fmul,fdiv,nmul,ndiv=[value>>j&1 for j in range(5)]
  ys.append(int(not(owner and (fmul or fdiv) or not owner and (nmul or ndiv))))
 return dict(metrics=metrics(net),verification=verify(net,xs,ys))


def prove(reference,candidate):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 for name,net in [('reference',reference),('candidate',candidate),('negative',flip_output(candidate))]:
  (OUT/(name+'.blif')).write_text(blif(net))
 positive=cec(abc,OUT/'reference.blif',OUT/'candidate.blif',OUT/'cec.log');assert positive['verdict']=='equivalent'
 negative=cec(abc,OUT/'reference.blif',OUT/'negative.blif',OUT/'negative.cec.log');assert negative['verdict']=='different'
 return dict(scope='307 shared arithmetic next-state bits, source states identified, explicit load-owner guard',proof=positive,negative=negative,unbounded_ownership_invariant_proved=False)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);source.OUT=OUT
 small=small_check();net,monitor,reference,candidate,parts=make();fixtures=source.cases(source.reference())
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'norm_ff'));(OUT/'ownership.nl').write_bytes(monitor.encode())
 (OUT/'cases.json').write_text(json.dumps(fixtures,indent=2)+'\n')
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
 report=dict(status='small ownership and unchanged C composition pass; large shared graph constructed only',metrics=metrics(net),parts=parts,
  small_ownership=small,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),golden_cases=len(fixtures),
  numerical_contract_changed=False,scope='true norm1 and layer0 complete FFN, same H slot, one MUL/DIV/SQRT/DOT; X retention/residual/attention outside',
  conditional_proof_metrics=dict(reference=metrics(reference),candidate=metrics(candidate)),
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)})
 if args.cloud:
  report['refinement_proof']=prove(reference,candidate);report['verification']=source.check(net,fixtures)
  import verify as checks
  checks.OUT=OUT;checks.NI=source.NI;checks.NO=source.NO+1
  def observed():
   with (OUT/'vectors.txt').open() as f:
    for line in f:
     x,y,mask=[int(v,16) for v in line.split()];yield x,y+(1<<source.NO),mask+(1<<source.NO)
  assert checks.check_nand(observed(),monitor.encode())==0
  report['ownership_sequence']=dict(clocks=report['verification']['observed']['clocks'],load_owner_violations=0,scope='actual guarded sequence, not unbounded proof')
  report['status']='actual NAND/RTL/C and307-bit conditional arithmetic refinement pass; observed ownership holds'
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))


if __name__=='__main__':main()
