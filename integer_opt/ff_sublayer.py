#!/usr/bin/env python3
"""Complete layer0 FFN sublayer with one external X scan and residual writeback.

Norm internally replays the original X slot. The final saturated residual
rewrites that same slot. Attention and full-transformer control are outside.
"""
from pathlib import Path
import sys,json,hashlib,signal,random,ctypes as ct,subprocess,os,argparse,shutil
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import norm_ff_shared as source
from nand import Builder,with_state,metrics,verify_state,blif,flip_output,from_yosys
from export import import_net,load_unit,rtl
from prefix_store import stage,select
NI,NO=26,32
sha=lambda data:hashlib.sha256(data).hexdigest()

def control(b,old,pins):
 phase=old[:2];index=old[2:9];pending=old[9]
 reset,start,core_available,scan,replay,input_last,we,read=pins;keep=b.inv(reset)
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 p=[eq(phase,j) for j in range(4)];last=b.reduce(index[:5],b.land,1)
 begin=b.reduce([keep,start,b.lor(p[0],p[3])],b.land,1)
 residual_begin=b.reduce([keep,p[1],core_available],b.land,1)
 write=b.reduce([keep,p[2],b.inv(pending),we],b.land,1)
 finish=b.reduce([keep,p[2],pending,eq(index,0)],b.land,1)
 available=b.reduce([keep,p[3],b.inv(start)],b.land,1)
 take=b.land(available,read)
 pp=phase[:]
 for action,target in ((begin,1),(residual_begin,2),(finish,3)):
  pp=[b.mux(action,v,target>>j&1) for j,v in enumerate(pp)]
 clear=b.reduce([reset,begin,residual_begin],b.lor,0)
 ni=[b.land(b.inv(clear),v) for v in b.add(index,[0]*7,b.lor(write,take))[0]]
 pn=b.land(keep,b.lor(b.reduce([p[1],scan,input_last],b.land,1),b.land(write,last)))
 xadvance=b.reduce([keep,b.reduce([pending,b.land(take,last),b.reduce([p[1],replay,input_last],b.land,1)],b.lor,0)],b.land,1)
 hadvance=b.reduce([keep,p[2],pending],b.land,1)
 done_last=b.land(available,b.reduce(index,b.land,1))
 return [b.land(keep,v) for v in pp]+ni+[pn],[begin,write,xadvance,hadvance,b.lor(p[1],p[2]),available,done_last]

def small_control():
 b=Builder(18);ds,out=control(b,list(range(2,12)),list(range(12,20)));net=b.finish(ds+out);xs=[];ys=[]
 for old in range(1024):
  p=old&3;idx=old>>2&127;pending=old>>9&1
  for flags in range(256):
   reset,start,ready,scan,replay,last,we,read=[flags>>j&1 for j in range(8)]
   begin=int(not reset and start and p in (0,3));rb=int(not reset and p==1 and ready)
   write=int(not reset and p==2 and not pending and we);finish=int(not reset and p==2 and pending and idx==0)
   available=int(not reset and p==3 and not start);take=available*read;pp=p
   if begin:pp=1
   if rb:pp=2
   if finish:pp=3
   nx=0 if reset or begin or rb else (idx+int(write or take))&127
   pn=int(not reset and (p==1 and scan and last or write and idx%32==31))
   xa=int(not reset and (pending or take and idx%32==31 or p==1 and replay and last))
   ha=int(not reset and p==2 and pending);last_result=int(available and idx==127)
   nxt=(0 if reset else pp)+(nx<<2)+(pn<<9)
   outputs=[begin,write,xa,ha,int(p in (1,2)),available,last_result]
   xs.append(old+(flags<<10));ys.append(nxt+(sum(v<<j for j,v in enumerate(outputs))<<10))
 result=verify_state(net,xs,ys,10);result.pop('nl_hex');return result

def xport(b,xstate,hhead,phase,norm_index,index,value,scan,write,advance):
 address=[b.mux(phase[1],a,v) for a,v in zip(norm_index[:5],index[:5])]
 x=select(b,[xstate[j*20:(j+1)*20] for j in range(32)],address)
 h=select(b,[hhead[j*20:(j+1)*20] for j in range(32)],index[:5])
 _,summed=import_net(b,load_unit('resid'),x+h)
 stored=[b.mux(write,a,v) for a,v in zip(value,summed)]
 _,work=stage()
 xd,_=import_net(b,work,[0]*640+stored+address+[0,advance,b.lor(scan,write)],xstate)
 return xd,x


def make():
 core=source.make()[0];assert sha(core.encode())=='5c22cdf28262b759ad03dfc36fef7f28c93841273a29b5f14393afc832414b0d'
 ns=core.n_state+2560+10;b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
 cs=old[:core.n_state];xs=old[core.n_state:core.n_state+2560];ps=old[-10:];reset,start=ins[:2];value=ins[2:22];xvalid,enable,we,read=ins[22:]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 keep=b.inv(reset);active=eq(ps[:2],1);pending=ps[9];index=ps[2:9]
 _,probe=import_net(b,core,[reset]+[0]*25,cs);norm_index=probe[640:647];replaying=probe[648]
 input_ready=b.reduce([keep,active,probe[647],b.inv(replaying),b.inv(pending)],b.land,1)
 scan=b.land(input_ready,xvalid)
 replay=b.reduce([keep,active,probe[647],replaying,b.inv(pending),enable],b.land,1)
 pd,po=control(b,ps,[reset,start,probe[671],scan,replay,b.reduce(norm_index[:5],b.land,1),we,read])
 begin,write,xadvance,hadvance,busy,available,last=po
 xd,x=xport(b,xs,probe[:640],ps[:2],norm_index,index,value,scan,write,xadvance)
 feed=[b.mux(replaying,a,v) for a,v in zip(value,x)]
 source_valid=b.reduce([keep,active,b.inv(pending),b.mux(replaying,xvalid,enable)],b.land,1)
 cd,out=import_net(b,core,[reset,begin]+feed+[source_valid,enable,we,hadvance],cs)
 public=x+norm_index+[input_ready,replaying,busy,available,last]
 net=with_state(b.finish(cd+xd+pd+public),ns);assert net.n_in==NI and net.n_out==NO
 return net,dict(core=metrics(core),original_X_bits=2560,parent_bits=10,other_bits=ns-7808,
  single_mul_instances=1,single_div_instances=1,single_sqrt_instances=1,single_dot_instances=1,
  scope='true layer0 FFN sublayer X -> S(X+FFN(norm1(X))), one external X scan and original X/H slots; attention/full model outside')

OUT=R/'build/integer_opt/ff_sublayer'


def reference():
 base=source.source;base.OUT=OUT;base.reference()
 p=OUT/'reference.c';p.write_text(p.read_text()+r"""
void ff_sublayer_reference(const int32_t *x,int32_t *h,int32_t *delta,int32_t *out) {
    norm_ff_reference(x,h,delta);
    for(int i=0;i<128;i++)out[i]=sat((int64_t)x[i]+delta[i]);
}
int32_t residual_reference(int32_t a,int32_t b) {return sat((int64_t)a+b);}
""")
 lib=OUT/'sublayer_reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared',str(p),'-o',str(lib)],check=True,timeout=30)
 c=ct.CDLL(str(lib));c.int_init.argtypes=[ct.c_void_p,ct.c_int];blob=(R/'physical/model.bin').read_bytes()
 assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1';assert c.int_init(blob,len(blob))==0
 c.ff_sublayer_reference.argtypes=[ct.POINTER(ct.c_int32)]*4
 c.residual_reference.argtypes=[ct.c_int32,ct.c_int32];c.residual_reference.restype=ct.c_int32
 return c


def cases(c):
 base=source.source;base.OUT=OUT/'base_reference';base.OUT.mkdir(exist_ok=True)
 items=base.cases(base.reference())
 values=[i['x'] for i in items]+[[524287]*128,[-524288]*128]
 result=[]
 for j,x in enumerate(values):
  h=(ct.c_int32*128)();delta=(ct.c_int32*128)();out=(ct.c_int32*128)()
  c.ff_sublayer_reference((ct.c_int32*128)(*x),h,delta,out)
  if j<4:assert list(h)==items[j]['h'] and list(delta)==items[j]['out']
  expected=[max(-524288,min(524287,a+b)) for a,b in zip(x,delta)];assert list(out)==expected
  result.append(dict(x=x,h=list(h),delta=list(delta),out=list(out),residual_saturations=sum(a+b!=v for a,b,v in zip(x,delta,out))))
 assert sum(item['residual_saturations'] for item in result[4:])>0
 return result


def small_residual(c):
 from bench import verify
 net=load_unit('resid');assert metrics(net)['nNand']<=4000
 rng=random.Random(260698);bounds=[-524288,-524287,-1,0,1,524286,524287]
 pairs=[(a,b) for a in bounds for b in bounds]+[(rng.randrange(-524288,524288),rng.randrange(-524288,524288)) for _ in range(2048)]
 xs=[(a&1048575)+((b&1048575)<<20) for a,b in pairs]
 ys=[c.residual_reference(a,b)&1048575 for a,b in pairs]
 return dict(metrics=metrics(net),verification=verify(net,xs,ys))


def cloud_vectors(net,fixtures,scalar_x=False,ff_codes=None,h_codes=None,shared_h_read=False):
 """NAND handshakes select timing; C alone supplies all numeric values.

 Internal state is read only for protocol assertions and precise reset points.
 Parent transitions, transaction counts and completion bounds are checked
 independently. No internal numeric value becomes a C expectation.
 """
 assert os.getenv('GITHUB_ACTIONS')=='true'
 wrapper=OUT/'sim_driver.c'
 wrapper.write_text(('#include '+json.dumps(str(R/'physical/nl_sim.c'))+'\n'+r'''
static uint64_t state_word(unsigned at,unsigned width) {
    uint64_t word=0;
    for(unsigned j=0;j<width;j++)word|=(uint64_t)state[at+j]<<j;
    return word;
}
uint64_t ff_sublayer_inspect(void) {
    return state_word(8709,10) | (state_word(6120,4)<<10)
        | (state_word(6144,5)<<14) | (state_word(6107,7)<<19)
        | (state_word(6124,20)<<26) | (state_word(3339,3)<<46)
        | (state_word(5932,3)<<49) | (state_word(5921,7)<<52);
}
''' + (r'''
uint64_t ff_storage_inspect(void) {
    return state_word(576,3) | (state_word(567,9)<<3) | (state_word(3280,1)<<12)
        | (state_word(5915,2)<<13) | (state_word(5935,2)<<15) | (state_word(5932,3)<<17)
        | (state_word(5928,4)<<20) | (state_word(8718,1)<<24) | (state_word(584,8)<<25);
}
void ff_storage_head(uint8_t *out) {
    for(unsigned j=0;j<32;j++)out[j]=(uint8_t)state_word(592+8*j,8);
}
''' if ff_codes is not None else '') + ('\nuint64_t h_storage_inspect(void) {\n    return state_word(3339,3) | (state_word(3330,9)<<3) | (state_word(5915,2)<<12)\n        | (state_word(534,4)<<14) | (state_word(503,9)<<18) | (state_word(512,2)<<27)\n        | (state_word(5920,1)<<29) | (state_word(6147,1)<<30) | (state_word(5937,1)<<31)\n        | (state_word(5920,1)<<32) | (state_word(3347,8)<<33) | (state_word(5935,2)<<41);\n}\nuint32_t h_storage_lane(unsigned index) {\n    return (uint32_t)state_word(3355+20*index,20);\n}\nuint32_t h_scale_result(void) { return (uint32_t)state_word(397,20); }\nvoid h_storage_head(uint32_t *out) {\n    for(unsigned i=0;i<32;i++)out[i]=h_storage_lane(i);\n}\n' if h_codes is not None else '')).replace('state_word(8709,10)',f'state_word(8709,{9 if scalar_x else 10})'))
 subprocess.run(['cc','-O3','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(wrapper),'-o',str(OUT/'sim.so')],check=True,timeout=60)
 sim=ct.CDLL(str(OUT/'sim.so'));sim.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32]
 sim.nl_step.argtypes=[ct.c_void_p,ct.c_void_p];sim.ff_sublayer_inspect.restype=ct.c_uint64
 raw=net.encode();assert net.n_state==8719-int(scalar_x)+int(ff_codes is not None) and sim.nl_init(raw,len(raw),NI,NO)==0
 buf=ct.create_string_buffer(4);parent=0;clocks=0;vd=hashlib.sha256()
 counts=dict(completed=0,aborted=0,partial_restarts=0,scan_inputs=0,replay_inputs=0,norm_outputs=0,
             residual_writes=0,result_items=0,last_items=0,busy_starts=0)
 if shared_h_read:
  assert h_codes is not None
  counts['h_read_owner_checks']=0
 bank_pending=bank_writes=bank_reads=bank_rotations=0;bank_case=None;bank_head=ct.create_string_buffer(32)
 if ff_codes is not None:
  assert scalar_x and len(ff_codes)==len(fixtures)
  bank_lookup={tuple(case['x']):codes for case,codes in zip(fixtures,ff_codes)}
  sim.ff_storage_inspect.restype=ct.c_uint64;sim.ff_storage_head.argtypes=[ct.c_void_p]
 h_pending=0;h_counts={};h_case=None;h_head=(ct.c_uint32*32)()
 if h_codes is not None:
  assert scalar_x and ff_codes is not None and len(h_codes)==len(fixtures)
  h_lookup={tuple(case['x']):codes for case,codes in zip(fixtures,h_codes)}
  sim.h_storage_inspect.restype=ct.c_uint64;sim.h_storage_head.argtypes=[ct.POINTER(ct.c_uint32)]
  sim.h_storage_lane.argtypes=[ct.c_uint];sim.h_storage_lane.restype=ct.c_uint32;sim.h_scale_result.restype=ct.c_uint32
 f=(OUT/'vectors.txt').open('wb')
 def step(value,case=None,tx=None):
  nonlocal parent,clocks,bank_pending,bank_writes,bank_reads,bank_rotations,bank_case,h_pending,h_counts,h_case
  debug=sim.ff_sublayer_inspect();old=debug&1023;assert old==parent,(clocks,'parent',old,parent)
  p=parent&3;index=parent>>2&127;pending=parent>>9
  np=debug>>10&15;cp=debug>>14&7;complete=debug>>18&1;ni=debug>>19&127;norm_value=debug>>26&1048575
  reset=value&1;start=value>>1&1;valid=value>>22&1;enable=value>>23&1;we=value>>24&1;read=value>>25&1
  begin=int(not reset and start and p in (0,3))
  core_ready=int(not reset and cp==0 and complete and not begin)
  ready=int(not reset and p==1 and cp==1 and np==1 and not pending)
  scan=ready*valid;replay=int(not reset and p==1 and cp==1 and np==5 and not pending and enable)
  write=int(not reset and p==2 and not pending and we)
  rb=int(not reset and p==1 and core_ready);finish=int(write and index==127) if scalar_x else int(not reset and p==2 and pending and index==0)
  available=int(not reset and p==3 and not start);take=available*read;last=int(available and index==127)
  want=(ni<<20)+(ready<<27)+(int(5<=np<=9)<<28)+(int(p in (1,2))<<29)+(available<<30)+(last<<31)
  mask=0xfff00000
  if tx is not None and not reset:
   if scan:
    assert tx['scan']<128 and ni==tx['scan'] and (value>>2&1048575)==(case['x'][tx['scan']]&1048575)
    tx['scan']+=1;counts['scan_inputs']+=1
   if replay:
    assert tx['scan']==128 and ni==tx['replay']<128
    tx['replay']+=1;counts['replay_inputs']+=1
   if p==1 and cp==1 and np==9:
    assert tx['norm']<128 and ni==tx['norm'] and norm_value==(case['h'][ni]&1048575)
    if we:tx['norm']+=1;counts['norm_outputs']+=1
   if rb:assert tx['scan']==tx['replay']==tx['norm']==128 and tx['writes']==0
   if write:
    assert tx['scan']==tx['replay']==tx['norm']==128 and index==tx['writes']<128
    tx['writes']+=1;counts['residual_writes']+=1
   if available:
    assert tx['scan']==tx['replay']==tx['norm']==tx['writes']==128 and index==tx['reads']<128
    want|=case['out'][index]&1048575;mask|=1048575
   if take:tx['reads']+=1;counts['result_items']+=1;counts['last_items']+=last
   counts['busy_starts']+=int(start and p in (1,2))
  if ff_codes is not None:
   probe=sim.ff_storage_inspect();qp=probe&7;qi=probe>>3&511;filled=probe>>12&1
   hp=probe>>13&3;fp=probe>>15&3;dp=probe>>17&7;dg=probe>>20&15
   assert probe>>24&1==bank_pending,(clocks,'FF rotation pending')
   if reset or begin:bank_writes=bank_reads=bank_rotations=0;bank_case=None
   if case is not None and bank_case is None:bank_case=bank_lookup[tuple(case['x'])]
   bw=int(not reset and qp==4 and we)
   bt=int(not reset and fp==3 and dp==1 and enable and not bank_pending and filled and qp==0 and hp==0 and dg<11)
   assert not (bw and (bank_pending or bt)),(clocks,'FF write lost to rotation')
   if bw:
    assert bank_case is not None and qi==bank_writes<336
    assert probe>>25&255==bank_case['q'][qi]&255,(clocks,'FF producer code',qi)
    bank_writes+=1
   if bt:
    assert bank_writes==336 and dg==bank_reads%11 and debug>>52&127==bank_reads//11
    if bank_reads==0:assert bank_rotations==21
    sim.ff_storage_head(bank_head)
    expected_head=bytes(bank_case['q'][(32*dg+j)%336]&255 for j in range(32))
    assert bank_head.raw==expected_head,(clocks,'FF head order',bank_reads)
    bank_reads+=1
   if not reset and (bank_pending or bt):bank_rotations+=1
   bank_pending=int(not reset and (bw and qi%16==15 or bt and dg!=10))
   if rb:
    assert bank_writes==336 and bank_reads==1408 and bank_rotations==2709 and not bank_pending
    sim.ff_storage_head(bank_head)
    assert bank_head.raw==bytes(v&255 for v in bank_case['q'][:32])
   if tx is not None:
    tx.update(ff_bank_writes=bank_writes,ff_bank_groups=bank_reads,ff_bank_rotations=bank_rotations)
  if h_codes is not None:
   hp=sim.h_storage_inspect();hphase=hp&7;hi=hp>>3&511;ho=hp>>12&3;rp=hp>>14&15
   hr=hp>>18&511;hg=hp>>27&3;hq_pending=hp>>29&1;hn_pending=hp>>30&1;hd_pending=hp>>31&1;fp=hp>>41&3
   if shared_h_read:
    assert not (p==2 and hphase in (1,2)),(clocks,'shared H read ownership',p,hphase)
    counts['h_read_owner_checks']+=1
   assert hp>>32&1==h_pending,(clocks,'H halfword pending')
   if reset or begin:
    h_counts=dict(norm=0,scan=0,replay=0,quant=0,groups=0,down=0,residual=0,rotations=0);h_case=None
   if case is not None and h_case is None:h_case=h_lookup[tuple(case['x'])]
   # bank_pending above now holds next state. Read the old FF bit directly.
   active_enable=bool(enable and not h_pending and not (probe>>24&1))
   nw=int(not reset and cp==1 and np==9 and we)
   dw=int(not reset and fp==3 and (debug>>49&7)==4 and we)
   qw=int(not reset and ho==1 and hphase==4 and active_enable)
   qr=int(not reset and ho==1 and hphase in (1,2) and active_enable and not hq_pending)
   gt=int(not reset and ho==3 and rp in (1,4) and active_enable)
   hs=int(qr and hphase==1 and hi%16==15)
   ra=int(write and index%16==15)
   move=int(not reset and (hn_pending or hd_pending or hq_pending or hs or gt or h_pending or ra))
   assert not ((nw or dw or qw) and move),(clocks,'H write lost to rotation')
   if nw:
    assert ni==h_counts['norm']<128;h_counts['norm']+=1
   if qr:
    key='scan' if hphase==1 else 'replay'
    assert h_counts['norm']==128 and hi==h_counts[key]<128
    assert sim.h_storage_lane(hi%16)==case['h'][hi]&1048575,(clocks,'H raw order',key,hi)
    h_counts[key]+=1
   if qw:
    assert hi==h_counts['quant']<128 and hp>>33&255==h_case['q'][hi]&255
    h_counts['quant']+=1
   if gt:
    g=h_counts['groups'];assert h_counts['quant']==128 and hr==(g//8)%336 and hg==g%4 and rp==(1 if g%8<4 else 4)
    sim.h_storage_head(h_head)
    assert list(h_head)==[v&1048575 for v in h_case['q'][hg*32:hg*32+32]],(clocks,'H DOT order',hr,rp,hg)
    h_counts['groups']+=1
   if dw:
    dr=debug>>52&127;assert h_counts['groups']==5376 and dr==h_counts['down']<128
    assert sim.h_scale_result()==case['delta'][dr]&1048575,(clocks,'H down value',dr)
    h_counts['down']+=1
   if rb:
    assert all(h_counts[k]==128 for k in ('norm','scan','replay','quant','down'))
    assert h_counts['groups']==5376 and h_counts['rotations']==10784
    assert not (h_pending or hn_pending or hd_pending or hq_pending)
    sim.h_storage_head(h_head);assert list(h_head)==[v&1048575 for v in case['delta'][:32]]
   if write:
    assert index==h_counts['residual']<128 and sim.h_storage_lane(index%16)==case['delta'][index]&1048575
    h_counts['residual']+=1
   h_counts['rotations']+=move;h_pending=int(gt or qw and hi%16==15)
   if available and tx is not None:
    assert h_counts['residual']==128 and h_counts['rotations']==10792
    if not tx.get('h_restored'):
     sim.h_storage_head(h_head);assert list(h_head)==[v&1048575 for v in case['delta'][:32]];tx['h_restored']=True
   if tx is not None:tx['h_storage']=h_counts.copy()
  sim.nl_step(value.to_bytes(4,'little'),buf);got=int.from_bytes(buf.raw,'little')
  assert (got^want)&mask==0,(clocks,hex(value),hex(got),hex(want),hex(mask),p,np,cp)
  line=f'{value:x} {want:x} {mask:x}\n'.encode();f.write(line);vd.update(line);clocks+=1
  pp=p
  if begin:pp=1
  if rb:pp=2
  if finish:pp=3
  ix=0 if reset or begin or rb else (index+int(write or take))&127
  pn=int(not scalar_x and not reset and (p==1 and scan and ni%32==31 or write and index%32==31))
  parent=(0 if reset else pp)+(ix<<2)+(pn<<9)
  return got,debug
 def reset():
  step(1+(1<<22)+(1<<23)+(1<<24)+(1<<25));step(0)
 def transaction(case,stall=False,abort=None):
  tx=dict(scan=0,replay=0,norm=0,writes=0,reads=0);previous,_=step(2);first=None
  for clock in range(260000):
   valid=int(bool(previous>>27&1) and tx['scan']<128 and (not stall or clock%7!=1))
   data=case['x'][tx['scan']] if tx['scan']<128 else 0
   enable=int(not stall or clock%11!=3);we=int(not stall or clock%13!=5)
   # Internal parent is used only to avoid accidentally restarting after done.
   early=int((parent&3) in (1,2) and clock%97==0)
   read=int(bool(previous>>30&1) and (not stall or clock%5!=2))
   value=(early<<1)+((data&1048575)<<2)+(valid<<22)+(enable<<23)+(we<<24)+(read<<25)
   got,debug=step(value,case,tx);previous=got
   if got>>30&1 and first is None:first=clock+2
   if tx['reads']==128:
    counts['completed']+=1
    return dict(clocks=clock+2,first_publish_clock=first,stalled=stall,abort=None,counts=tx)
   np=debug>>10&15;cp=debug>>14&7;dp=debug>>49&7;dr=debug>>52&127
   stop=(abort in ('scan_word','scan_word_pending') and tx['scan']==32 and (scalar_x or parent>>9))
   stop|=(abort=='norm_replay' and tx['replay']==32 and np==5)
   stop|=(abort=='down_writeback' and cp==4 and dp==4 and dr==31)
   stop|=(abort in ('residual_word','residual_word_pending') and tx['writes']==32 and (scalar_x or parent>>9))
   stop|=(abort=='partial_read' and tx['reads']==23)
   stop|=(abort=='partial_restart' and tx['reads']==45)
   if h_codes is not None:
    stop|=(abort=='h_norm_pending' and h_counts['norm']==16)
    stop|=(abort=='h_code_pending' and h_counts['quant']==16)
    stop|=(abort=='h_dot_pending' and h_pending==1)
   if stop:
    if abort=='partial_restart':counts['partial_restarts']+=1
    else:counts['aborted']+=1;reset()
    return dict(clocks=clock+2,stalled=stall,abort=abort,counts=tx)
  raise AssertionError('FFN sublayer did not complete within260000 clocks')
 runs=[]
 try:
  reset()
  for j,case in enumerate(fixtures):runs.append(transaction(case,stall=bool(j%2)))
  aborts=[('scan_word' if scalar_x else 'scan_word_pending'),'norm_replay','down_writeback',('residual_word' if scalar_x else 'residual_word_pending'),'partial_read','partial_restart']
  if h_codes is not None:aborts+=['h_norm_pending','h_code_pending','h_dot_pending']
  for kind in aborts:
   runs.append(transaction(fixtures[3],abort=kind))
  runs.append(transaction(fixtures[2],stall=True))
 finally:f.close()
 assert counts['completed']==7 and counts['aborted']==5+3*int(h_codes is not None) and counts['partial_restarts']==1
 assert counts['last_items']==7 and counts['result_items']==7*128+23+45
 return dict(clocks=clocks,counts=counts,transactions=runs,vector_sha256=vd.hexdigest(),scalar_x=scalar_x,
  scope='bounded actual NAND functional fixture; numeric values from frozen C; internal states used for protocol assertions/reset points only; not unbounded proof')


def prove_xport():
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from ci import cec
 b=Builder(3239);bits=list(range(2,3241))
 ds,x=xport(b,bits[:2560],bits[2560:3200],bits[3200:3202],bits[3202:3209],bits[3209:3216],bits[3216:3236],*bits[3236:])
 net=b.finish(ds+x)
 (OUT/'xport.blif').write_text(blif(net));(OUT/'xport.negative.blif').write_text(blif(flip_output(net)))
 (OUT/'xport.ref.v').write_text('''module top(input [3238:0] din,output [2579:0] dout);
wire [2559:0] old=din[2559:0];wire [639:0] h=din[3199:2560];
wire [1:0] phase=din[3201:3200];wire [6:0] ni=din[3208:3202],ix=din[3215:3209];
wire [19:0] external_x=din[3235:3216];wire scan=din[3236],wr=din[3237],advance=din[3238];
wire [4:0] address=phase[1]?ix[4:0]:ni[4:0];
wire signed [19:0] x=old[address*20+:20],delta=h[ix[4:0]*20+:20];
wire signed [20:0] sum={x[19],x}+{delta[19],delta};
wire [19:0] clipped=(sum>21'sd524287)?20'd524287:(sum< -21'sd524288)?20'h80000:sum[19:0];
reg [2559:0] next;
always @* begin
 next=old;
 if(advance)next={old[639:0],old[2559:640]};
 else if(scan || wr)next[address*20+:20]=wr?clipped:external_x;
end
assign dout={x,next};
endmodule
''')
 script=OUT/'xport.ys';script.write_text(f'read_verilog {OUT}/xport.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/xport.ref.json\n')
 subprocess.run(['yosys','-Q','-T','-l',str(OUT/'xport.yosys.log'),'-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=120)
 ref=from_yosys(json.loads((OUT/'xport.ref.json').read_text()),3239,2580);(OUT/'xport.ref.blif').write_text(blif(ref))
 abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
 positive=cec(abc,OUT/'xport.blif',OUT/'xport.ref.blif',OUT/'xport.cec.log');assert positive['verdict']=='equivalent'
 negative=cec(abc,OUT/'xport.negative.blif',OUT/'xport.ref.blif',OUT/'xport.negative.log');assert negative['verdict']=='different'
 return dict(scope='all X2560/H640 bits,indices,phase,external value and scan/write/advance combinations; actual saturated scalar-write/rotate X port',
  metrics=metrics(net),proof=positive,negative=negative)


def check(net,fixtures,scalar_x=False,ff_codes=None,h_codes=None,shared_h_read=False):
 assert os.getenv('GITHUB_ACTIONS')=='true'
 from golden import Netlist
 import verify as checks
 checks.OUT=OUT;checks.NI=NI;checks.NO=NO
 observed=cloud_vectors(net,fixtures,scalar_x=scalar_x,ff_codes=ff_codes,h_codes=h_codes,shared_h_read=shared_h_read)
 (OUT/'observed.json').write_text(json.dumps(observed,indent=2)+'\n')
 def prefix():
  with (OUT/'vectors.txt').open() as f:
   for line in f:
    row=tuple(int(v,16) for v in line.split());yield row
    if row[1]>>30&1:return
 gates=net.records.copy();target=len(gates)-NO;inverse=gates[target][1]
 op,a,b=gates[inverse-NI-2];assert op==0 and a==b;gates[target]=(0,a,a)
 bad=Netlist(NI,NO,gates);wrong=checks.check_nand(prefix(),bad.encode());assert wrong>0
 (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,'ff_sublayer'))
 (OUT/'tb.v').write_text(checks.testbench(NI,NO,'ff_sublayer',str(OUT/'vectors.txt')))
 normal=checks.compile_rtl('source',OUT/'stream.v');checks.run([normal],600)
 mutant=checks.compile_rtl('negative',OUT/'bad.v');run=subprocess.run([str(mutant)],capture_output=True,text=True,timeout=300)
 (OUT/'negative_verilator.log').write_text(run.stdout+run.stderr)
 assert run.returncode!=0 and 'C99 comparison failed' in run.stdout+run.stderr
 return dict(status='actual NAND/RTL/C complete FFN sublayer and scalar-output mutation pass',observed=observed,
  actual_output_gate_mutation_mismatches=wrong,actual_rtl_mutation_rejected=True)


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True)
 small=small_control();net,parts=make();c=reference();fixtures=cases(c);resid=small_residual(c)
 (OUT/'stream.nl').write_bytes(net.encode());(OUT/'stream.v').write_text(rtl(net,'ff_sublayer'))
 (OUT/'cases.json').write_text(json.dumps(fixtures,indent=2)+'\n')
 paths=set()
 for module in list(sys.modules.values()):
  path=getattr(module,'__file__',None)
  if path:
   path=Path(path).resolve()
   if R in path.parents and path.suffix=='.py':paths.add(path)
 for name in ['integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c','integer_opt/weights_golden.c',
  'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl','physical/units/dot32.nl','physical/units/resid.nl',
  'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
  paths.add(R/name)
 report=dict(status='small parent/residual and C cases pass; large graph constructed only',metrics=metrics(net),parts=parts,
  small_control=small,small_residual=resid,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),golden_cases=len(fixtures),
  residual_saturations=[x['residual_saturations'] for x in fixtures],numerical_contract_changed=False,
  contract='din reset,start,X20,xvalid,access_enable,write_enable,read; dout Y20,norm_index7,Xready,norm_replay,busy,available,last',
  source_layout='core6149,X2560,parent10; no result buffer; one external128-item X scan',
  run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths)})
 if args.cloud:
  report['xport_proof']=prove_xport();report['verification']=check(net,fixtures);report['status']=report['verification']['status']
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='sources'},indent=2))


if __name__=='__main__':main()
