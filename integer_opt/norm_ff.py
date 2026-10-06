#!/usr/bin/env python3
"""X replay through true norm1 and FFN, reusing the original H work slot.

Only the final norm-word rotation installs a complete H row for the FF
controller. X preservation, residual addition and full-model control are
external. Norm multiplication/division are still separate in this round.
"""
from pathlib import Path
import sys,json,hashlib,signal,ctypes as ct,random,subprocess,os,argparse
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_stream
import ff_acc_shared
from ff_input import controller as h_control
from prefix_store import stage
from export import import_net,rtl
from nand import Builder,with_state,metrics,verify_state
sha=lambda x:hashlib.sha256(x).hexdigest()
NI,NO=26,678

def control(b,old,ins):
 phase=old[:3];pending,complete=old[3:];reset,start,nvalid,nlast,wordlast,ffdone,we=ins
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 p=[eq(phase,j) for j in range(5)];keep=b.inv(reset)
 begin=b.reduce([keep,p[0],start],b.land,1);ack=b.reduce([keep,p[1],nvalid,we],b.land,1)
 nfinish=b.land(ack,nlast);fstart=b.land(keep,p[3]);finish=b.reduce([keep,p[4],ffdone],b.land,1)
 pn=phase[:]
 for act,target in ((begin,1),(nfinish,2),(p[2],3),(p[3],4),(finish,0)):
  pn=[b.mux(act,v,target>>j&1) for j,v in enumerate(pn)]
 done=b.land(keep,b.lor(finish,b.land(complete,b.inv(begin))))
 available=b.reduce([keep,p[0],complete,b.inv(start)],b.land,1)
 return [b.land(keep,v) for v in pn]+[b.land(keep,b.land(ack,wordlast)),done],[begin,ack,fstart,available,b.inv(p[0]),b.land(keep,p[2])]

def make():
 ff=ff_acc_shared.make()[0];assert sha(ff.encode())=='da94fd0aea6585dc1ce0b287f980f87f01dc4bdb7243736a68b9b0f201057309'
 norm=norm_stream.make()[0];_,work=stage();ns=ff.n_state+norm.n_state+5
 b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
 fs=old[:5939];rs=old[5939:6451];cs=old[6451:];reset,start=ins[:2];x=ins[2:22];xvalid,enable,we,read=ins[22:]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 keep=b.inv(reset);normalizing=eq(cs[:3],1)
 _,no=import_net(b,norm,[reset]+[0]*23,rs)
 _,fo=import_net(b,ff,[reset]+[0]*649,fs)
 cd,co=control(b,cs,[reset,start,no[53],b.reduce(no[20:27],b.land,1),b.reduce(no[20:25],b.land,1),fo[1035],we])
 begin,nack,fstart,available,busy,install=co
 f_read=b.land(available,read)
 fd,fo=import_net(b,ff,[reset,fstart,0]+[0]*640+[enable,we]+[0]*4+[f_read],fs)
 nd,no=import_net(b,norm,[reset,begin]+x+[b.land(normalizing,xvalid),b.land(normalizing,we)],rs)
 # Same H scalar-write/rotate port: normalize first, then original FF
 # quantization/down/read actions. This does not instantiate another H bank.
 fp=fs[5935:5937];factive=eq(fp,3);ff_start=b.land(fstart,eq(fp,0))
 hphase=fs[3339:3342];hidx=fs[3330:3339];hcode=fs[3347:3355];hs=fs[3355:5915]
 qr=b.land(keep,b.lor(eq(hphase,1),eq(hphase,2)));qv=b.land(keep,eq(hphase,4))
 replay=b.lor(eq(hphase,2),b.lor(eq(hphase,3),eq(hphase,4)))
 fr=b.land(keep,b.lor(eq(fs[534:538],1),eq(fs[534:538],4)))
 fdone=b.reduce([keep,fs[3280],eq(fs[576:579],0)],b.land,1)
 _,hc=h_control(b,fs[5915:5921],[reset,ff_start,0,enable,qr,qv,replay,b.reduce(hidx[:5],b.land,1),eq(hidx,127),fr,fdone])
 dack=b.reduce([keep,factive,fo[388],we],b.land,1)
 fa=b.land(keep,b.lor(fs[5937],b.land(fo[1035],f_read)))
 fv=[b.mux(dack,a,v) for a,v in zip(hcode+[hcode[-1]]*12,fo[356:376])]
 fi=[b.mux(dack,a,v) for a,v in zip(hidx[:5],fo[376:381])]
 value=[b.mux(nack,a,v) for a,v in zip(fv,no[:20])]
 index=[b.mux(nack,a,v) for a,v in zip(fi,no[20:25])]
 # Every32 outputs rotate, not only the final128th output.
 npending=cs[3];advance=b.reduce([hc[6],fa,b.land(keep,npending)],b.lor,0)
 hd,_=import_net(b,work,[0]*640+value+index+[0,advance,b.lor(hc[7],b.lor(dack,nack))],hs)
 fd[3355:5915]=hd
 for j in range(3):fd[5917+j]=b.mux(install,fd[5917+j],4>>j&1)
 public=hs[:640]+no[20:27]+[b.land(normalizing,no[51]),no[52],b.land(normalizing,no[53])]+no[:20]+[busy,available]+cs+[install]
 assert len(public)==NO
 net=with_state(b.finish(fd+nd+cd+public),ns)
 return net,dict(before_ff=metrics(ff),norm=metrics(norm),h_slot_bits=2560,ff_code_bits=2688,nonvector_bits=ns-5248,
  two_mul_instances=2,two_div_instances=2,sqrt_instances=1,dot_instances=1,scope='X replay -> norm1 -> same H slot -> true FFN -> H result; residual and whole transformer excluded; norm core not shared yet')


OUT=R/'build/integer_opt/norm_ff'

def small_control():
 b=Builder(12);ds,out=control(b,list(range(2,7)),list(range(7,14)));net=b.finish(ds+out);xs=list(range(1<<12));ys=[]
 for x in xs:
  p=x&7;pending=x>>3&1;complete=x>>4&1
  reset,start,valid,last,wordlast,ffdone,we=[x>>(5+j)&1 for j in range(7)]
  begin=int(not reset and p==0 and start);ack=int(not reset and p==1 and valid and we)
  fstart=int(not reset and p==3);finish=int(not reset and p==4 and ffdone)
  pn=p
  if begin:pn=1
  if ack and last:pn=2
  if p==2:pn=3
  if p==3:pn=4
  if finish:pn=0
  dn=int(not reset and (finish or complete and not begin));available=int(not reset and p==0 and complete and not start)
  nxt=(0 if reset else pn)+(int(not reset and ack and wordlast)<<3)+(dn<<4)
  obs=[begin,ack,fstart,available,int(p!=0),int(not reset and p==2)]
  ys.append(nxt+(sum(v<<j for j,v in enumerate(obs))<<5))
 result=verify_state(net,xs,ys,5);result.pop('nl_hex');return result


def reference():
 assert sha((R/'integer/int_model.c').read_bytes())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
 path=OUT/'reference.c'
 path.write_text('#include '+json.dumps(str(R/'integer/int_model.c'))+'\n'+r'''
void norm_ff_reference(const int32_t *x,int32_t *h,int32_t *out) {
    int32_t g[336],u[336],ff[336];
    norm(x,1,h);linear(h,0,4,g);linear(h,0,5,u);
    for(int i=0;i<336;i++) {
        int64_t j=int_rne(g[i]<0?-(int64_t)g[i]:g[i],64);
        if(j>1024)j=1024;
        uint32_t s=g[i]<0?65536-sigtab[j]:sigtab[j];
        ff[i]=sat(int_rne((int64_t)sat(int_rne((int64_t)g[i]*s,65536))*u[i],4096));
    }
    linear(ff,0,6,out);
}
''')
 lib=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared',str(path),'-o',str(lib)],check=True,timeout=30)
 c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int];blob=(R/'physical/model.bin').read_bytes()
 assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1';assert c.int_init(blob,len(blob))==0
 c.norm_ff_reference.argtypes=[ct.POINTER(ct.c_int32)]*3
 return c


def cases(c):
 import ff_stream,down_engine
 norm_stream.OUT=OUT/'norm_reference';ff_stream.OUT=OUT/'ff_reference';down_engine.OUT=OUT/'down_reference'
 for p in (norm_stream.OUT,ff_stream.OUT,down_engine.OUT):p.mkdir(exist_ok=True)
 nc=norm_stream.reference();fc=ff_stream.reference();dc=down_engine.reference()
 rng=random.Random(260690)
 values=[[0]*128,[524287]+[0]*127,[0]*127+[-524288],[rng.randrange(-4096,4097) for _ in range(128)]]
 result=[]
 for x in values:
  h=(ct.c_int32*128)();out=(ct.c_int32*128)();c.norm_ff_reference((ct.c_int32*128)(*x),h,out)
  independent=(ct.c_int32*128)();nc.norm_reference((ct.c_int32*128)(*x),independent);assert list(h)==list(independent)
  hq=(ct.c_int8*128)();raw=(ct.c_int32*336)();fq=(ct.c_int8*336)();hm=ct.c_int32()
  fm=fc.ff_reference(h,hq,raw,fq,ct.byref(hm));dq=(ct.c_int8*336)();dy=(ct.c_int32*128)()
  assert dc.down_reference(raw,dq,dy)==fm and list(dq)==list(fq) and list(dy)==list(out)
  result.append(dict(x=x,h=list(h),out=list(out),h_maximum=hm.value,ff_maximum=fm))
 return result


def cloud_vectors(net,fixtures):
 """Handshake-driven inputs; all numeric expectations come from frozen C.

 The DUT chooses when it is ready. Independent counters require exactly128
 values in each pass,128 norm results, one installation, four final H words,
 ordered legal phase transitions and completion within a fixed bound.
 This is a bounded functional test, not an unbounded sequential proof.
 """
 assert os.getenv('GITHUB_ACTIONS')=='true'
 subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
 sim=ct.CDLL(str(OUT/'sim.so'));sim.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32]
 sim.nl_step.argtypes=[ct.c_void_p,ct.c_void_p];raw=net.encode();assert sim.nl_init(raw,len(raw),NI,NO)==0
 buf=ct.create_string_buffer((NO+7)//8);rows=[]
 counts=dict(completed=0,aborted=0,scan_inputs=0,replay_inputs=0,norm_outputs=0,installations=0,result_words=0,busy_starts=0)
 def step(value):
  sim.nl_step(value.to_bytes((NI+7)//8,'little'),buf);return int.from_bytes(buf.raw,'little')
 def record(value,want,mask,got):
  assert ((want^got)&mask)==0,(len(rows),hex(value),hex(got),hex(want),hex(mask))
  rows.append((value,want,mask))
 def reset():
  value=1+(1<<22)+(1<<23)+(1<<24)+(1<<25)
  record(value,0,(1<<647)|(1<<649)|(1<<671)|(1<<677),step(value))
  record(0,0,((1<<8)-1)<<670,step(0))
 def pack(values):return sum((v&1048575)<<(20*j) for j,v in enumerate(values))
 def transaction(case,stall=False,abort=None):
  got=step(2);record(2,0,(1<<670)|(1<<671),got)
  accepted=[0,0];written=0;installed=0;reads=0;previous=got;lastphase=0;last_pending=0
  phase4_clocks=0;first_publish=None
  for clock in range(260000):
   pp=(previous>>672)&7;bucket=(previous>>648)&1
   valid=int(bool(previous>>647&1) and accepted[bucket]<128 and (not stall or clock%7!=1))
   data=case['x'][accepted[bucket]] if accepted[bucket]<128 else 0
   enable=int(not stall or clock%11!=3);we=int(not stall or clock%13!=5)
   early=int((pp in (1,2,3) or pp==4 and phase4_clocks<150000) and clock%97==0)
   read=int(bool(previous>>671&1) and reads<4)
   value=(early<<1)+((data&1048575)<<2)+(valid<<22)+(enable<<23)+(we<<24)+(read<<25)
   got=step(value);p=(got>>672)&7;pending=(got>>675)&1;complete=(got>>676)&1
   assert p in range(5) and p in (lastphase,(lastphase+1)%5),(clock,lastphase,p)
   assert pending==last_pending
   ready=got>>647&1;replay=got>>648&1;nvalid=got>>649&1;idx=got>>640&127
   available=got>>671&1;install=got>>677&1
   assert (got>>670&1)==int(p!=0)
   assert available==int(p==0 and complete and not early)
   assert install==int(p==2) and not (p!=1 and (ready or nvalid))
   want=(int(p!=0)<<670)+(available<<671)+(p<<672)+(pending<<675)+(complete<<676)+(install<<677)
   mask=((1<<8)-1)<<670
   if ready:
    assert replay==int(accepted[0]==128),(clock,'pass',accepted,replay)
    assert idx==accepted[replay],(clock,'input index',idx,accepted)
    want|=accepted[replay]<<640;mask|=127<<640
    if valid:
     assert bucket==replay and data==case['x'][accepted[replay]]
     accepted[replay]+=1;counts['replay_inputs' if replay else 'scan_inputs']+=1
   if nvalid:
    assert written<128 and idx==written
    want|=(case['h'][written]&1048575)<<650;mask|=1048575<<650
    want|=written<<640;mask|=127<<640
    if we:written+=1;counts['norm_outputs']+=1
   if install:
    assert written==128 and accepted==[128,128]
    installed+=1;counts['installations']+=1;assert installed==1
    want|=pack(case['h'][96:128]);mask|=(1<<640)-1
   elif p==3:
    want|=pack(case['h'][:32]);mask|=(1<<640)-1
   if available:
    if first_publish is None:first_publish=clock+2
    assert installed==1 and written==128 and accepted==[128,128]
    want|=pack(case['out'][32*reads:32*(reads+1)]);mask|=(1<<640)-1
   record(value,want,mask,got)
   counts['busy_starts']+=int(bool(early and p!=0))
   lastphase=p;last_pending=int(bool(nvalid and we and idx%32==31));previous=got
   phase4_clocks+=int(p==4)
   if available and read:
    reads+=1;counts['result_words']+=1
    if reads==4:
     counts['completed']+=1;return dict(clocks=clock+2,first_publish_clock=first_publish,stalled=stall,abort=None)
   abort_now=(abort=='norm_word_pending' and p==1 and last_pending and written==32)
   abort_now|=(abort=='ff_h_quant' and phase4_clocks==3000)
   abort_now|=(abort=='down_writeback' and phase4_clocks==185000)
   if abort_now:
    counts['aborted']+=1;reset();return dict(clocks=clock+2,stalled=stall,abort=abort)
  raise AssertionError('norm/FF transaction did not complete before260000 clocks')
 reset();runs=[]
 for j,case in enumerate(fixtures):runs.append(transaction(case,stall=bool(j%2)))
 for kind in ('norm_word_pending','ff_h_quant','down_writeback'):
  runs.append(transaction(fixtures[3],abort=kind))
 # Reset during a partially written down result, then fully overwrite H/FF.
 runs.append(transaction(fixtures[2],stall=True))
 assert counts['completed']==5 and counts['aborted']==3 and counts['result_words']==20
 return rows,dict(clocks=len(rows),counts=counts,transactions=runs,
  scope='reactive handshake fixture, independent C norm and FF output, ordered counters and bounded completion; no whole-transformer proof')



def check(net,fixtures):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 import verify as checks
 from golden import Netlist
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 rows,observed=cloud_vectors(net,fixtures)
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {mask:x}\n' for x,y,mask in rows))
 (OUT/'observed.json').write_text(json.dumps(observed,indent=2)+'\n')
 prefix=rows[:next(j for j,(_,y,mask) in enumerate(rows) if y>>671&1)+1]
 gates=net.records.copy();target=len(gates)-NO;inverse=gates[target][1]
 op,a,b=gates[inverse-NI-2];assert op==0 and a==b;gates[target]=(0,a,a)
 bad=Netlist(NI,NO,gates);wrong=checks.check_nand(prefix,bad.encode());assert wrong>0
 (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,'norm_ff'))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'norm_ff',str(OUT/'vectors.txt')))
 normal=checks.compile_rtl('source',OUT/'stream.v');checks.run([normal],600)
 mutant=checks.compile_rtl('negative',OUT/'bad.v')
 result=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(result.stdout+result.stderr)
 assert result.returncode!=0 and 'C99 comparison failed' in result.stdout+result.stderr
 return dict(status='actual NAND/RTL end-to-end C comparison and H-output mutation pass',observed=observed,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),negative_prefix_clocks=len(prefix),
  actual_H_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True)
 small=small_control();net,parts=make();fixtures=cases(reference())
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'norm_ff'))
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
 report=dict(status='small controller and independent C composition pass; complete graph constructed only',metrics=metrics(net),parts=parts,
  small_control=small,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),golden_cases=len(fixtures),
  numerical_contract_changed=False,scope=parts['scope'],
  contract='din:reset,start,X20,xvalid,FF access_enable,write_enable,read_advance; dout:H640,norm index7,ready,replay,valid,result20,busy,available,parent5,install',
  proof_limit='complete nonlinear numeric outputs use C; readiness is handshake-driven with ordered counters/deadline; not unbounded sequential proof',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(path.relative_to(R)):sha(path.read_bytes()) for path in sorted(paths)})
 if args.cloud:report['verification']=check(net,fixtures);report['status']=report['verification']['status']
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))


if __name__=='__main__':main()
