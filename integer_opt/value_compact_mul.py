#!/usr/bin/env python3
"""R57 V path with exact37-bit serial multiplication and stable cached x.

Only the product state changes; original17-step timing,64-bit FF accumulator,
commands, rounding and saturation are retained. Whole gate checks in Actions.
"""
from pathlib import Path
import os,sys,inspect,textwrap,json,hashlib,signal,argparse
R=Path(os.environ.get('H3_COMPACT_VALUE_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
import value_row as base
import value_continuous as previous
import ff_continuous as ff
import compact_mul as mul
from nand import Builder,metrics,with_state
from export import import_net,rtl
sha=lambda raw:hashlib.sha256(raw).hexdigest()
OUT=R/'build/integer_opt/value_compact_mul'


def make():
 kn,_,kp=base.kv.make();fn,_,fp=ff.make(21);mn,_=mul.make()
 nk,nf,nm=kn.n_state,fn.n_state,mn.n_state;ns=nk+nf+nm+16+17
 b=Builder(ns+base.NI);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+base.NI))
 ks=old[:nk];fs=old[nk:nk+nf];ms=old[nk+nf:nk+nf+nm];cs=old[-33:-17];weight=old[-17:]
 reset,start=pins[:2];cmd=pins[2:4];address=pins[4:9];w=pins[9:26];word=pins[26:302];read=pins[302]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 ka=AND(b.inv(reset),eq(ks[-13:-11],2));kd=AND(b.inv(reset),eq(ks[-13:-11],3))
 fa=AND(b.inv(reset),eq(fs[-10:-8],2),b.inv(eq(fs[-2:],1)));fd=AND(b.inv(reset),eq(fs[-10:-8],3))
 cd,act=base.control(b,cs,[reset,start]+cmd+[ka,kd,fa,fd,read])
 begin,kstart,fwrite,fread,fhome,ftake,ktake,mload=act[:8];flags=act[8:]
 kds,ko=import_net(b,kn,[reset,kstart,eq(cmd,3)]+address+word+[ktake],ks)
 below17=b.lor(b.inv(cs[15]),b.inv(b.reduce(cs[11:15],b.lor,0)))
 step=AND(b.inv(reset),eq(cs[:4],7),below17)
 mds,product37=import_net(b,mn,[mload,step]+ko[:20]+weight,ms)
 assert len(product37)==37
 product=product37+[product37[-1]]*27
 accumulator=fs[2688:2752];added=b.add(accumulator,product)[0]
 summand=[b.land(b.inv(eq(cs[4:6],0)),v) for v in added]
 fstart=b.lor(fwrite,b.lor(fread,fhome))
 fds,fo=import_net(b,fn,[reset,fstart]+cs[6:11]+[0]+[fwrite,fhome]+summand+[1,ftake],fs)
 wd=[AND(b.inv(reset),b.mux(begin,x,y)) for x,y in zip(weight,w)]
 outputs=fo[:64]+cs[6:11]+flags
 comb=b.finish(kds+fds+mds+cd+wd+outputs);net=with_state(comb,ns)
 return net,comb,dict(kv=metrics(kn),ff=metrics(fn),mul=metrics(mn),control=16,weight=17,
   reuse='stable KV cache supplies s20 x on17 steps; existing FF payload holds old64-bit sum; no x/shifted-x register',
   state_order='KV client, continuous FF port, serial product37, control16, weight17')


class Model(previous.Model):pass


text=textwrap.dedent(inspect.getsource(base.Model.tick))
r=previous.replace_once
text=r(text,'accumulator=self.f.memory[0]>>(64*(self.f.address&1))&self.mask64','accumulator=self.f.payload')
text=r(text,'payload=(accumulator+self.mp)&self.mask64 if mode else 0',
 '''product=self.mp-(1<<37) if self.mp>>36 else self.mp
    payload=(accumulator+product)&self.mask64 if mode else 0
    prior_cache,prior_index=self.k.cache,self.k.index''')
start=text.index('    if mload:');end=text.index('    if begin:self.weight=w',start)
text=text[:start]+'''    step=not reset and phase==7 and (c>>11)<17
    if mload:
        assert self.k.index==lane
        self.counts['multiplies']+=1
    if step:
        assert (self.k.cache,self.k.index)==(prior_cache,prior_index)
        assert prior_index==lane
    self.mp=mul.advance(self.mp,mload,step,value,self.weight)
'''+text[end:]
env=dict(base.__dict__,mul=mul);exec(compile(text,'<compact product V reference>','exec'),env);Model.tick=env['tick']


def vectors(g,cases):
 text=inspect.getsource(base.vectors)
 text=r(text,'assert m.f.cursor==0 or cmd==3','assert 0<=m.f.cursor<21')
 text=r(text,'s.f.phase==2 and s.f.index==4','s.f.phase==1 and s.f.mode==1')
 env=dict(base.__dict__,Model=Model);exec(compile(text,'<compact product V vectors>','exec'),env)
 return env['vectors'](g,cases)


def reference():
 text=previous.reference()
 text=r(text,'input [12420:0] din,output [12190:0] dout','input [12265:0] din,output [12035:0] dout')
 text=r(text,'wire [191:0] ms=din[12084:11893];wire [15:0] cs=din[12100:12085];wire [16:0] weight=din[12117:12101];',
 'wire [36:0] ms=din[11929:11893];wire [15:0] cs=din[11945:11930];wire [16:0] weight=din[11962:11946];')
 text=r(text,'wire [302:0] pins=din[12420:12118]','wire [302:0] pins=din[12265:11963]')
 start=text.index('wire [63:0] acc=ms[63:0]');end=text.index('wire [63:0] current=',start)
 text=text[:start]+'''wire signed [20:0] high={ms[36],ms[36:17]};
wire signed [20:0] x={dequant[19],dequant};
wire signed [20:0] added=high+(ms[0]?x:21'sd0);
wire step=!reset && cs[3:0]==7 && cs[15:11]<17;
wire [36:0] next_mul=mload?{20'd0,weight}:step?{added,ms[16:1]}:ms;
wire [63:0] acc={{27{ms[36]}},ms};
'''+text[end:]
 text=r(text,'assign dout[12117:0]={next_weight,ctl[15:0],next_y,next_a,next_acc,fres[2766:0],kres[9125:0]};',
 'assign dout[11962:0]={next_weight,ctl[15:0],next_mul,fres[2766:0],kres[9125:0]};')
 text=r(text,'assign dout[12190:12118]','assign dout[12035:11963]')
 return text


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 small=mul.small_check();g,cases=base.golden();rows,expected=vectors(g,cases)
 extra,timing=previous.timing(g,cases['cases'][0],Model);rows+=extra
 expected['primary_clocks']=expected['clocks'];expected['clocks']=len(rows)
 net,comb,parts=make()
 assert sha(net.encode())=='96fced08b6faacb4992371adba7ba4dc26db19c1d0a53dd420387e5d5064c616'
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'))
 (OUT/'row.ref.v').write_text(reference());(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 assert sha((OUT/'vectors.txt').read_bytes())=='9028d8197a40bc0787be5ac8c57e1565b8a8071daf48d6ba303fc2ef850442db'
 assert sha((OUT/'cases.json').read_bytes())=='522c606f2257faf6578dad2a3d7272836edf81813a8bc3c18e0779fdfea378ce'
 report=dict(status='small exact product and full C reference pass; full source constructed only',metrics=metrics(net),parts=parts,
  small=small,expected=expected,timing=timing,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
  cases_sha256=sha((OUT/'cases.json').read_bytes()),adopted=False,numerical_contract_changed=False,
  scope='R57 command/clock sequence unchanged; s20 KV value must stay fixed across17 step pulses; full64-bit accumulation retained; not a whole-model budget deduction')
 paths={Path(__file__).resolve()}
 for module in list(sys.modules.values()):
  name=getattr(module,'__file__',None)
  if name:
   p=Path(name).resolve()
   if (R in p.parents or p.parent==Path(__file__).resolve().parent) and p.suffix=='.py':paths.add(p)
 for n in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','docs/index.html',
           'physical/units/manifest.json','physical/units/serial_mul.nl','integer_opt/kv_units/manifest.json','integer_opt/kv_units/kv_deq.nl']:
  paths.add(R/n)
 report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
  sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 if args.cloud:
  base.reference=reference;report['verification']=base.cloud_check(net,comb,rows)
  report['verification']['formal_scope']='Independent bank/control/37-bit product step/add behavior with same pinned dequantizer; exact17-step product also requires stable cached x as checked in C schedule; not a new dequantizer theorem'
  report['status']='whole D/output composition and actual NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2))
 print(expected['clocks'],expected['counts'],expected['ff_counts'])


if __name__=='__main__':main()
