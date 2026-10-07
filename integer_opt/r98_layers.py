#!/usr/bin/env python3
"""R128: the accepted R98 norm1/FFN/residual serving all five layers (layer index input).

Same method as R127 on layer0_units/r98_ffn.nl (run 37555484285). Four layer-0 constants are bound
to the actual graph by rebuilding them in its canonical builder, then replaced in a gate-by-gate
replay (both polarities of every table output are cut):
 - gate/up selector: R98's reversed-order 4096 x 64-bit table on ff_weight_order.table_inputs
   (cursor bits + up phase) -> + layer3;
 - down selector: down_engine's 2048 x 64-bit table on DOWN (group 4 bits, row 7 bits) -> + layer3;
 - norm[1] gamma: 128 x 16-bit table on index bits GAMMA -> norm[2l+1];
 - FFN alphas: the shared scale unit's 18 alpha registers load mux(begin, alpha, F) with
   F = owner ? alpha6 : (up ? alpha5 : alpha4); rebuilt with alpha[7*layer + 4/5/6].
With layer=0 the graph is CEC-equivalent to R98 (Actions).
Interface: R98's 26 inputs + layer3 (inputs 26..28, held while a FFN run is in progress).
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_R98L_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_R98L_OUT',str(R/'build/integer_opt/r98_layers')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from bench import lookup
from export import import_net,rtl
from r95_layers import ordered,layer_mux,PAD,blob
import ff_weight_order as fwo
U=Path(__file__).with_name('layer0_units')
OLD_NI,NO=26,32
NI=OLD_NI+3
DOWN=list(range(5928,5932))+list(range(5921,5928))   # down address bit order: group0..3, row0..6
GAMMA=list(range(6107,6114))
ALPHA=list(range(371,389))
GU_ORDER0=list(reversed(range(12)))
sha=lambda b:hashlib.sha256(b).hexdigest()


def r98():
    man=json.loads((U/'manifest.json').read_text());m=man['r98_ffn.nl'];raw=(U/'r98_ffn.nl').read_bytes()
    assert sha(raw)==m['sha256'];return Netlist.decode(raw,m['nIn'],m['nOut'])


def trits(base,count):
    raw=blob();w=0
    for i in range(count):
        k=base+i;d=raw[8+k//4]>>(2*(k%4))&3;assert d!=3;w|=d<<(2*i)
    return w


def gu_words(layer):
    """ff_row.weight_table() with the layer offset: address up<<11|row<<2|group."""
    o=layer*194560;words=[]
    for a in range(4096):
        up=a>>11;row=(a>>2)&511;group=a&3
        words.append(trits(o+(108544 if up else 65536)+row*128+group*32,32) if row<336 else 0)
    return words


def down_words(layer):
    """down_engine.weight_table() with the layer offset: address row<<4|group."""
    o=layer*194560;words=[]
    for a in range(2048):
        row=a>>4;group=a&15
        words.append(trits(o+151552+row*336+32*group,min(32,336-32*group)) if group<11 else 0)
    return words


def gamma_words(which):
    raw=blob();return [int.from_bytes(raw[268692+2*(which*128+i):268694+2*(which*128+i)],'little') for i in range(128)]


def alphas(layer):
    raw=blob();return [int.from_bytes(raw[243208+4*(layer*7+j):243212+4*(layer*7+j)],'little') for j in (4,5,6)]


def tables5(gu_order,down_order,gam_order):
    return (ordered([v for l in PAD for v in gu_words(l)],64,gu_order),ordered([v for l in PAD for v in down_words(l)],64,down_order),
            ordered([v for l in PAD for v in gamma_words(2*l+1)],16,gam_order))


GU5_ORDER=GU_ORDER0+[12,13,14]
DOWN5_ORDER=list(range(11))+[11,12,13]
GAM5_ORDER=list(range(10))


def fmux(b,owner,up,a):
    return [b.mux(owner,b.mux(up,a[0]>>j&1,a[1]>>j&1),a[2]>>j&1) for j in range(18)]


def bind(net):
    ns=net.n_state;ni=ns+net.n_in;b=Builder(ni);q=list(range(2,2+ns));p=list(range(2+ns,2+ni))
    ds,out=import_net(b,net,p,q);before=len(b.gates);existing=lambda w:ni+2<=w<ni+2+before
    assert gu_words(0)==fwo.ff_row.weight_table()[1]
    _,gu=import_net(b,ordered(gu_words(0),64,GU_ORDER0),fwo.table_inputs(b,q))
    _,dn=import_net(b,lookup(down_words(0),64,'shannon'),[q[i] for i in DOWN])
    _,gam=import_net(b,lookup(gamma_words(1),16,'shannon'),[q[i] for i in GAMMA])
    for name,ws in (('gate/up',gu),('down',dn)):assert len(set(ws))==64 and all(existing(w) for w in ws),name
    live=[k for k in range(16) if gam[k] not in (0,1)];assert all(existing(gam[k]) for k in live)
    keep=b.inverse[p[0]];up=fwo.table_inputs(b,q)[-1]
    g1=b.inverse[ds[ALPHA[0]]];a,z=b.gates[g1-ni-2][1:];M=z if a==keep else a
    x,y=b.gates[M-ni-2][1:];u=x if q[ALPHA[0]] in b.gates[x-ni-2][1:] else y
    nb=[t for t in b.gates[u-ni-2][1:] if t!=q[ALPHA[0]]][0];begin=b.inverse[nb]
    cand=set()                 # state leaves of the 18 next-state cones (depth-limited), registers themselves excluded
    for r in ALPHA:
        stack=[(ds[r],0)]
        while stack:
            w,dd=stack.pop()
            if w<2 or dd>12:continue
            if w<ns+2:cand.add(w-2);continue
            if w<ni+2:continue
            stack+=[(t,dd+1) for t in b.gates[w-ni-2][1:]]
    cand-=set(ALPHA)
    owner=None
    for s in sorted(cand):     # probe gates land after `before` and are never replayed
        F=fmux(b,q[s],up,alphas(0))
        if all(b.land(keep,b.mux(begin,q[r],F[i]))==ds[r] for i,r in enumerate(ALPHA)):owner=s;break
    assert owner is not None,'alpha binding'
    return b,dict(ni=ni,before=before,gu=gu,dn=dn,gam=gam,live=live,keep=keep,begin=begin,up=up,owner=owner,ds=ds,out=out)


def splice(net,t5,fault=None,bound=None):
    b,k=bound or bind(net);ns=net.n_state;ni=k['ni'];gu5,dn5,gm5=t5
    c=Builder(ns+NI);cq=list(range(2,2+ns));cp=list(range(2+ns,2+ns+NI));layer=cp[OLD_NI:]
    if fault=='layer_bits_swapped':layer=[layer[1],layer[0],layer[2]]
    _,ngu=import_net(c,gu5,fwo.table_inputs(c,cq)+layer)
    _,ndn=import_net(c,dn5,[cq[i] for i in DOWN]+layer)
    _,ngm=import_net(c,gm5,[cq[i] for i in GAMMA]+layer)
    for kk in range(16):
        if kk not in k['live']:assert ngm[kk]==k['gam'][kk],kk
    cuts={}
    def cut(w,v):
        assert w not in cuts;cuts[w]=v
        o=b.inverse.get(w)
        if o is not None and o>=ni+2:assert o not in cuts;cuts[o]=c.inv(v)
    for w,v in zip(k['gu'],ngu):cut(w,v)
    if fault!='old_down':
        for w,v in zip(k['dn'],ndn):cut(w,v)
    for kk in k['live']:cut(k['gam'][kk],ngm[kk])
    wires=list(range(ni+2))
    for i,(_,a,z) in enumerate(b.gates[:k['before']]):
        w=ni+2+i;wires.append(cuts[w] if w in cuts else c.nand(wires[a],wires[z]))
    keep,begin,up=wires[k['keep']],wires[k['begin']],wires[k['up']];owner=cq[k['owner']]
    F=layer_mux(c,layer,[fmux(c,owner,up,alphas(l) if fault!='alpha_layer0' else alphas(0)) for l in PAD])
    ds=[wires[w] for w in k['ds']]
    for i,s in enumerate(ALPHA):ds[s]=c.land(keep,c.mux(begin,cq[s],F[i]))
    comb=c.finish(ds+[wires[w] for w in k['out']])
    return with_state(comb,ns),comb,dict(cut_wires=len(cuts),gamma_live_outputs=len(k['live']),alpha_registers=len(ALPHA),owner_state=k['owner'])


TB=r"""
#include "Vdut.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
static Vdut*t;static uint64_t clk=0;
static uint32_t step(uint32_t d){t->din=d;t->clk=0;t->eval();uint32_t o=t->dout;t->clk=1;t->eval();t->clk=0;t->eval();clk++;return o;}
static uint32_t peek(uint32_t d){t->din=d;t->clk=0;t->eval();return t->dout;}
int main(int argc,char**argv){
  if(argc<2){printf("usage: vsim case.txt\n");return 2;}
  FILE*f=fopen(argv[1],"r");if(!f)return 2;
  t=new Vdut;step(1);step(0);int runs=0,bad=0;int layer;
  while(fscanf(f,"%d",&layer)==1){
    int32_t x[128],y[128];for(int i=0;i<128;i++)if(fscanf(f,"%d",&x[i])!=1)return 2;for(int i=0;i<128;i++)if(fscanf(f,"%d",&y[i])!=1)return 2;
    uint32_t b=(1u<<23)|(1u<<24)|((uint32_t)layer<<26);long g=0;
    while(peek(b)>>29&1){step(b);if(++g>4000000){printf("{\"status\":\"stall\",\"at\":\"busy\"}\n");return 4;}}
    step(b|2);int k=0;g=0;
    while(k<128){uint32_t d=b|((uint32_t)(x[k]&0xfffff)<<2)|(1u<<22);if(peek(d)>>27&1){step(d);k++;}else step(b);if(++g>8000000){printf("{\"status\":\"stall\",\"at\":\"x\"}\n");return 4;}}
    int n=0;g=0;
    while(n<128){uint32_t o=peek(b|(1u<<25));if(o>>30&1){int32_t r=(int32_t)((o&0xfffff)<<12)>>12;if(r!=y[n]){if(bad<5)printf("mismatch run %d L%d i%d got %d want %d\n",runs,layer,n,r,y[n]);bad++;}step(b|(1u<<25));n++;}else step(b);if(++g>20000000){printf("{\"status\":\"stall\",\"at\":\"y\"}\n");return 4;}}
    runs++;
  }
  printf("{\"status\":\"%s\",\"runs\":%d,\"clocks\":%llu,\"mismatches\":%d}\n",bad?"fail":"pass",runs,(unsigned long long)clk,bad);
  delete t;return bad?1:0;
}
"""


def golden_lib(tmp):
    import ctypes as ct
    so=Path(tmp)/'g.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer'),
        str(Path(__file__).with_name('r98_layers_golden.c')),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));raw=blob();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(raw,len(raw))==0
    g.ffn_res.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int32)]
    g.int_run.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32)]
    return g


def case_text(seed=26100728):
    """Two real FFN inputs per layer (x after attention residual is not traced; the int_run layer inputs are
    realistic magnitudes) plus extremes; layers interleaved."""
    import ctypes as ct,random
    rng=random.Random(seed);runs=[]
    with tempfile.TemporaryDirectory() as t:
        g=golden_lib(t);toks=[rng.randrange(192) for _ in range(3)]
        tr=(ct.c_int32*(6*3*128))();lg=(ct.c_int32*(3*192))();assert g.int_run((ct.c_int32*3)(*toks),3,lg,tr)==0
        xs=[(l,list(tr[(l*3+p)*128:(l*3+p+1)*128])) for l in range(5) for p in (0,2)]
        xs+=[(4,[524287 if i%2 else -524288 for i in range(128)]),(2,[rng.randrange(-524288,524288)>>rng.randrange(19) for _ in range(128)])]
        rng.shuffle(xs)
        lines=[]
        for layer,x in xs:
            y=(ct.c_int32*128)();g.ffn_res((ct.c_int32*128)(*x),layer,y);lines.append(f'{layer} '+' '.join(map(str,x))+' '+' '.join(map(str,y)));runs.append(layer)
    return '\n'.join(lines)+'\n',dict(tokens=toks,runs=len(runs),layers=runs)


def comb_of(net,tie=None):
    ns=net.n_state;b=Builder(ns+OLD_NI);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+OLD_NI))
    extra=[] if tie is None else [tie>>k&1 for k in range(3)]
    ds,out=import_net(b,net,p+extra,q);return b.finish(ds+out)


VFLAGS=['--cc','--exe','--build','-O2','-Wno-fatal','--x-assign','fast','--x-initial','fast','--prefix','Vdut','--top-module','r98',
        '-j','4','--output-split','20000','--output-split-cfuncs','2000','r98.v','tb.cpp','-o','vsim']


def vrun(d,net,case):
    d.mkdir(parents=True,exist_ok=True);(d/'r98.v').write_text(rtl(net,'r98'));(d/'tb.cpp').write_text(TB)
    r=subprocess.run(['verilator']+VFLAGS,cwd=d,capture_output=True,text=True,timeout=5400);(d/'build.log').write_text(r.stdout[-20000:]+r.stderr[-20000:]);assert r.returncode==0,d
    res=subprocess.run([str(d/'obj_dir/vsim'),str(case)],capture_output=True,text=True,timeout=4*3600)
    lines=[l for l in res.stdout.splitlines() if l.startswith('{')]
    return dict(rc=res.returncode,result=json.loads(lines[-1]) if lines else None,tail=res.stdout[-400:],rtl_sha256=sha(rtl(net,'r98').encode()))


def cloud(old,new,t5,net_faults):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    from gate_check import verify as vtab
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);proofs={}
    graphs=dict(r98=comb_of(old),layer0=comb_of(new,0),layer1=comb_of(new,1),negative=flip_output(comb_of(new,0)))
    for k,g in graphs.items():(d/(k+'.blif')).write_text(blif(g))
    for k,w in [('layer0','equivalent'),('layer1','different'),('negative','different')]:
        proofs[k]=cec(abc,d/(k+'.blif'),d/'r98.blif',d/(k+'.log'));assert proofs[k]['verdict']==w,k
    gu5,dn5,gm5=t5
    tables=dict(gate_up=vtab(gu5,list(range(1<<15)),[v for l in PAD for v in gu_words(l)]),
        down=vtab(dn5,list(range(1<<14)),[v for l in PAD for v in down_words(l)]),
        gamma=vtab(gm5,list(range(1<<10)),[v for l in PAD for v in gamma_words(2*l+1)]))
    b=Builder(5);F=layer_mux(b,[4,5,6],[fmux(b,2,3,alphas(l)) for l in PAD]);an=b.finish(F)
    want=[(lambda a,o,u:a[2] if o else (a[1] if u else a[0]))(alphas(PAD[v>>2]),v&1,v>>1&1) for v in range(32)]
    tables['alpha']=vtab(an,list(range(32)),want)
    for k,t in tables.items():assert t['status']=='pass',k
    case=OUT/'case.txt';runs=dict(source=vrun(OUT/'vlt_source',new,case))
    assert runs['source']['rc']==0 and runs['source']['result']['status']=='pass' and runs['source']['result']['mismatches']==0
    short=OUT/'case_faults.txt';short.write_text(''.join(case.read_text().splitlines(True)[:5]))   # first 5 runs (layers 2,0,2,1,4) for the negatives
    for k,n in net_faults.items():runs[k]=vrun(OUT/('vlt_'+k),n,short);assert runs[k]['rc']!=0,k
    return dict(status='pass',proofs=proofs,tables=tables,runs=runs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--orders',action='store_true');ap.add_argument('--tb');ap.add_argument('--fault');ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    t0=time.monotonic();net=r98()
    if a.orders:
        for name,o in [('gu_layer_top',GU_ORDER0+[12,13,14]),('gu_layer_first',[14,13,12]+GU_ORDER0)]:
            print('gate/up',name,metrics(ordered([v for l in PAD for v in gu_words(l)],64,o))['nNand'],flush=True)
        for name,o in [('down_layer_top',list(range(14))),('down_layer_first',[13,12,11]+list(range(11))),('down_reversed',list(range(13,-1,-1)))]:
            print('down',name,metrics(ordered([v for l in PAD for v in down_words(l)],64,o))['nNand'],flush=True)
        print('down0',metrics(lookup(down_words(0),64,'shannon'))['nNand'],'gu0',metrics(ordered(gu_words(0),64,GU_ORDER0))['nNand'])
        return
    t5=tables5(GU5_ORDER,DOWN5_ORDER,GAM5_ORDER);new,comb,info=splice(net,t5)
    if a.tb:
        d=Path(a.tb);d.mkdir(parents=True,exist_ok=True);n=new if not a.fault else (flip_output(new) if a.fault=='output_flip' else splice(net,t5,a.fault)[0])
        (d/'r98.v').write_text(rtl(n,'r98'));(d/'tb.cpp').write_text(TB);txt,meta=case_text();(d/'case.txt').write_text(txt);print(meta);return
    OUT.mkdir(parents=True,exist_ok=True)
    txt,meta=case_text();(OUT/'case.txt').write_text(txt)
    for k,g in zip(('r98_layers','gate_up5','down5','gamma5'),(new,)+t5):(OUT/(k+'.nl')).write_bytes(g.encode())
    import ci,gate_check  # cloud-only imports, listed so their sources are bound too
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','integer_opt/r98_layers_golden.c','physical/model.bin','integer_opt/layer0_units/manifest.json','integer_opt/layer0_units/r98_ffn.nl'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='four layer-0 constants bound to the actual R98 graph and replaced; C cases prepared; CEC, tables and Verilator runs await Actions',
        metrics=metrics(new),r98_metrics=metrics(net),tables5={k:metrics(t) for k,t in zip(('gate_up','down','gamma'),t5)},splice=info,
        bindings=dict(gate_up='ff_weight_order.table_inputs',down_state_bits=DOWN,gamma_state_bits=GAMMA,alpha_registers=ALPHA,owner_state=info['owner_state'],
            pad_layers=PAD[5:],gate_up_order=GU5_ORDER,down_order=DOWN5_ORDER,gamma_order=GAM5_ORDER),
        cases=meta,case_sha256=sha(txt.encode()),
        contract='R98 interface + layer3 (inputs 26..28, held during a run): results = sat(x + FFN_layer(norm(x,2*layer+1)))',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:
        faults={k:splice(net,t5,k)[0] for k in ('alpha_layer0','layer_bits_swapped','old_down')};faults['output_flip']=flip_output(new)
        report['verification']=cloud(net,new,t5,faults)
        report['status']='layer-0 configuration CEC-equivalent to accepted R98; all gate/up, down, gamma and alpha entries; actual graph (Verilator) == C for all 5 layers; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('r98_layers',metrics(new),info,'r98',metrics(net)['nNand'],[metrics(t)['nNand'] for t in t5],round(time.monotonic()-t0,1))


if __name__=='__main__':main()
