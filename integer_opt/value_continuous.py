#!/usr/bin/env python3
"""R56 V numerator path with a continuous FF bank and reused capture payload.

The frozen R56 parent command sequence is retained. RAM moves every clock;
HOME acknowledges a row0 pass, without any stopped or idle-home assumption.
"""
from pathlib import Path
import sys,os,inspect,hashlib,json,argparse,signal
R=Path(os.environ.get('H3_FF_CONTINUOUS_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
import value_row as base
import ff_continuous as continuous
from nand import metrics
from export import rtl
OUT=R/'build/integer_opt/value_continuous'
sha=lambda raw:hashlib.sha256(raw).hexdigest()


def replace_once(text,old,new):
 assert text.count(old)==1,old
 return text.replace(old,new)


def make():
 text=inspect.getsource(base.make)
 text=replace_once(text,'ff.make(21)','continuous.make(21)')
 assert text.count('fs[-13:-11]')==2;text=text.replace('fs[-13:-11]','fs[-10:-8]')
 text=replace_once(text,'accumulator=[b.mux(fs[-8],x,y) for x,y in zip(fs[:64],fs[64:128])]','accumulator=fs[2688:2752]')
 text=text.replace('FF current halfword holds old accumulator until write captures sum; no extra64-bit accumulator register',
                   'Existing FF payload captures read64 and holds the old sum until write captures the new sum; no extra64-bit register')
 env=dict(base.__dict__,continuous=continuous);exec(compile(text,'<continuous V composition>','exec'),env)
 return env['make']()


class Model(base.Model):
 def __init__(self,dequant):
  super().__init__(dequant);self.f=continuous.Model(21)


text=inspect.getsource(base.Model.tick)
import textwrap
text=textwrap.dedent(text)
text=replace_once(text,'accumulator=self.f.memory[0]>>(64*(self.f.address&1))&self.mask64','accumulator=self.f.payload')
env=dict(base.__dict__);exec(compile(text,'<continuous V reference tick>','exec'),env);Model.tick=env['tick']


def vectors(g,cases):
 text=inspect.getsource(base.vectors)
 text=replace_once(text,'assert m.f.cursor==0 or cmd==3','assert 0<=m.f.cursor<21  # every clock rotates; no stopped-home assumption')
 text=replace_once(text,'s.f.phase==2 and s.f.index==4','s.f.phase==1 and s.f.mode==1')
 env=dict(base.__dict__,Model=Model);exec(compile(text,'<continuous V reference vectors>','exec'),env)
 return env['vectors'](g,cases)


def reference():
 from nand import verilog
 k=base.kv.reference(32,32).replace('module top(','module kv_ref(',1)
 f=continuous.reference(21).replace('module top(','module ff_ref(',1)
 op=verilog(base.kv.operator()).replace('module top(','module deq_ref(',1)
 top='''module top(input [12420:0] din,output [12190:0] dout);
wire [9125:0] ks=din[9125:0];wire [2766:0] fs=din[11892:9126];
wire [191:0] ms=din[12084:11893];wire [15:0] cs=din[12100:12085];wire [16:0] weight=din[12117:12101];
wire [302:0] pins=din[12420:12118];wire reset=pins[0],start=pins[1],read=pins[302];
wire [1:0] command=pins[3:2];wire [4:0] address=pins[8:4],lane=cs[10:6];
wire [16:0] w=pins[25:9];wire [275:0] word=pins[301:26];
wire ka=!reset && ks[9114:9113]==2,kd=!reset && ks[9114:9113]==3;
wire fa=!reset && fs[2758:2757]==2 && fs[2766:2765]!=1,fd=!reset && fs[2758:2757]==3;
wire [27:0] ctl;ctl_ref controller({read,fd,fa,kd,ka,command,start,reset,cs},ctl);
wire begin_command=ctl[16],kstart=ctl[17],fwrite=ctl[18],fread=ctl[19],fhome=ctl[20];
wire ftake=ctl[21],ktake=ctl[22],mload=ctl[23];
wire [284:0] kpins={ktake,word,address,command==3,kstart,reset};
wire [9162:0] kres;kv_ref key_value({kpins,ks},kres);
wire [19:0] dequant;deq_ref deq(kres[9153:9126],dequant);
wire [63:0] acc=ms[63:0],a=ms[127:64],y=ms[191:128];
wire [63:0] next_acc=mload?64'd0:acc+(y[0]?a:64'd0);
wire [63:0] next_a=mload?{{44{dequant[19]}},dequant}:{a[62:0],1'b0};
wire [63:0] next_y=mload?{47'd0,weight}:{1'b0,y[63:1]};
wire [63:0] current=fs[2751:2688];
wire [63:0] updated=cs[5:4]==0?64'd0:current+acc;
wire [75:0] fpins={ftake,1'b1,updated,fhome,fwrite,1'b0,lane,fwrite|fread|fhome,reset};
wire [2833:0] fres;ff_ref ff_bank({fpins,fs},fres);
wire [16:0] next_weight=reset?17'd0:begin_command?w:weight;
assign dout[12117:0]={next_weight,ctl[15:0],next_y,next_a,next_acc,fres[2766:0],kres[9125:0]};
assign dout[12190:12118]={ctl[27:24],lane,fres[2830:2767]};
endmodule
'''
 return base.control_reference()+k+f+op+top


def timing(g,case,model_type):
 m=model_type(g.value_dequant);rows=[];calls=[];partial=[0]*32
 def tick(reset=0,start=0,cmd=0,address=0,w=0,word=0,read=0):
  x=reset+(start<<1)+(cmd<<2)+(address<<4)+(w<<9)+(word<<26)+(read<<302)
  y,mask=m.tick(x);rows.append((x,y,mask));return y
 def command(cmd,address=0,w=0,word=0):
  before=len(rows);tick(start=1,cmd=cmd,address=address,w=w,word=word);values=[]
  while m.control&15!=15:
   y=tick(read=1)
   if y>>69&1:
    assert y>>64&31==len(values)
    v=y&((1<<64)-1);values.append(v-(1<<64) if v>>63 else v)
   assert len(rows)-before<2400
  if cmd==2:assert values==partial
  calls.append(dict(command=cmd,clocks=len(rows)-before))
 tick(reset=1);command(0)
 for s,word in enumerate(case['words']):command(3,16+s,word=word)
 for s,w in enumerate(case['weights']):
  command(1,16+s,w=w);partial[:]=case['partials'][s]
 command(2)
 counts={label:sum(c['clocks'] for c in calls if c['command']==cmd) for cmd,label in enumerate(('clear','accumulate','read','load_kv'))}
 return rows,dict(clocks=len(rows),commands=len(calls),counts=counts,kv=m.k.counts,ff=m.f.counts,
  scope='one true C layer0 head0 position15, no random gaps or read stalls, clear+16 loads+16 updates+one read32; reference only until cloud replay')


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
 if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
 else:signal.alarm(55)
 OUT.mkdir(parents=True,exist_ok=True);base.OUT=OUT
 small=continuous.small_check();g,cases=base.golden();rows,expected=vectors(g,cases)
 old_rows,before=timing(g,cases['cases'][0],base.Model);extra,after=timing(g,cases['cases'][0],Model)
 rows+=extra;expected['primary_clocks']=expected['clocks'];expected['clocks']=len(rows)
 comparison=dict(before=before,after=after,limit='Reference schedule clocks, not token/physical timing or power; only candidate timing sequence is appended to actual cloud replay')
 net,comb,parts=make();assert sha(net.encode())=='a3a490e489589695d4da58c5daacb1faa2e785f1a27c4ca16f9a6c78c737f15b'
 (OUT/'row.nl').write_bytes(net.encode());(OUT/'row.v').write_text(rtl(net,'value_row'))
 (OUT/'row.ref.v').write_text(reference())
 (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
 report=dict(status='small continuous bank and complete real C reference pass; full source constructed only',
  metrics=metrics(net),parts=parts,small=small,expected=expected,timing=comparison,
  vector_sha256=sha((OUT/'vectors.txt').read_bytes()),cases_sha256=sha((OUT/'cases.json').read_bytes()),
  adopted=False,numerical_contract_changed=False,
  scope='C16 V numerator candidate, not connected to FFN code producer/down; every FF RAM position moves every clock, no idle-home/power claim')
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
  report['status']='whole D/output composition and actual NAND/RTL/C pass with real faults'
  (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('expected','sources')},indent=2))
 print(expected['clocks'],expected['counts'],expected['ff_counts'])
 for cmd in range(4):
  a=[c['clocks'] for c in expected['calls'] if c['command']==cmd];print(cmd,len(a),min(a),max(a))


if __name__=='__main__':main()
