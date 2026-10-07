#!/usr/bin/env python3
"""R127: the accepted R95 norm0/A8 cache + Q/K/V producer serving all five layers (layer index input).

The accepted R95 graph (layer0_units/r95_norm_qkv.nl, run 37553280961) carries three layer-0
constants. Each is bound to the actual graph structurally, then replaced in a gate-by-gate replay:
 - Q/K/V selector: qkv_plain's 2048 x 64-bit table on cursor state bits SEL (as weight_cone, both
   polarities of its 64 actual output wires are cut) -> table over SEL + layer3;
 - norm[0] gamma: norm_stream's 128 x 16-bit table on the norm index state bits GAMMA (outputs
   that are constant 0 in every layer stay as they are) -> table over GAMMA + layer3 for norm[2l];
 - Q/K/V alpha: the scale unit's 18 alpha registers ALPHA load mux(begin, alpha, F(matrix)) with F a
   constant mux of factors[matrix]; their next-state functions are rebuilt with
   F(matrix, layer) = alpha[7*layer + matrix] (matrix 7 -> 0, as before).
Every binding is checked by rebuilding the old function in the same canonical builder (it must land
on the existing wires/next states); with layer=0 the new graph is equivalent to R95 (CEC on Actions).
Interface: R95's 44 inputs + layer3 (inputs 44..46, held while a cache fill or matrix call runs).
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_R95L_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_R95L_OUT',str(R/'build/integer_opt/r95_layers')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from bench import lookup
from export import import_net,rtl,weight_words
U=Path(__file__).with_name('layer0_units')
OLD_NI,NO=44,76
NI=OLD_NI+3
SEL=list(range(18223,18234))     # selector address state bits (R95 receipt transition_domain.cursor_address_state_bits)
GAMMA=list(range(18125,18132))   # norm index state bits
ALPHA=list(range(18480,18498))   # scale unit alpha registers
MATRIX=[18564,18565,18566]       # linear engine matrix register
PAD=[0,1,2,3,4,1,2,3]
sha=lambda b:hashlib.sha256(b).hexdigest()
blob=lambda:(R/'physical/model.bin').read_bytes()


def r95():
    man=json.loads((U/'manifest.json').read_text());m=man['r95_norm_qkv.nl'];raw=(U/'r95_norm_qkv.nl').read_bytes()
    assert sha(raw)==m['sha256'];return Netlist.decode(raw,m['nIn'],m['nOut'])


def ordered(table,width,order):
    """Shannon lookup expanding address bit order[0] first; inputs keep the original bit meaning."""
    assert len(table)==1<<len(order) and sorted(order)==list(range(len(order)))
    perm=[sum((i>>j&1)<<k for j,k in enumerate(order)) for i in range(len(table))]
    source=lookup([table[i] for i in perm],width,'shannon')
    b=Builder(len(order));_,y=import_net(b,source,[2+k for k in order]);return b.finish(y)


SEL_ORDER0=[9,10]+list(range(2,9))+[0,1]          # qkv_plain's accepted order (address = mat<<9|row<<2|group)
SEL5_ORDER=SEL_ORDER0+[11,12,13]                   # layer bits expanded last (top): 90,801 NAND vs 95,045 layer-first
GAM5_ORDER=list(range(10))                         # natural, layer on top: 2,695 vs 2,831


def sel_words(layer):
    w=weight_words(blob(),layer)[:1536];return w+w[1024:1536]


def selector0():
    return ordered(sel_words(0),64,SEL_ORDER0)


def selector5(order):
    return ordered([v for l in PAD for v in sel_words(l)],64,order)


def gamma_words(which):
    raw=blob();return [int.from_bytes(raw[268692+2*(which*128+i):268694+2*(which*128+i)],'little') for i in range(128)]


def gamma0():
    return lookup(gamma_words(0),16,'shannon')


def gamma5(order):
    return ordered([v for l in PAD for v in gamma_words(2*l)],16,order)


def alphas(layer):
    raw=blob();return [int.from_bytes(raw[243208+4*(layer*7+j):243212+4*(layer*7+j)],'little') for j in range(7)]+[0]


def factor_mux(b,matrix,factors):
    table=[[a>>j&1 for j in range(18)] for a in factors]
    for select in matrix:table=[[b.mux(select,x,y) for x,y in zip(table[k],table[k+1])] for k in range(0,len(table),2)]
    return table[0]


def layer_mux(b,layer,vals):
    """vals[l] (8 wire lists) -> list selected by layer bits (layer[0] first, like factor_mux)."""
    t=vals
    for select in layer:t=[[b.mux(select,x,y) for x,y in zip(t[k],t[k+1])] for k in range(0,len(t),2)]
    return t[0]


def bind(net):
    """Rebuild each layer-0 constant in the canonical builder of the actual graph and locate its wires."""
    ns=net.n_state;ni=ns+net.n_in;b=Builder(ni);q=list(range(2,2+ns));p=list(range(2+ns,2+ni))
    ds,out=import_net(b,net,p,q);before=len(b.gates);existing=lambda w:ni+2<=w<ni+2+before
    _,sel=import_net(b,selector0(),[q[i] for i in SEL])
    assert len(set(sel))==64 and all(existing(w) for w in sel),'selector not bound to actual wires'
    _,gam=import_net(b,gamma0(),[q[i] for i in GAMMA])
    live=[k for k in range(16) if gam[k] not in (0,1)]
    assert all(existing(gam[k]) for k in live) and len({gam[k] for k in live})==len(live),'gamma not bound to actual wires'
    keep=b.inverse[p[0]]
    # begin: the alpha register's hold mux is nand(nand(inv(begin),q),nand(begin,F))
    g1=b.inverse[ds[ALPHA[0]]];a,z=b.gates[g1-ni-2][1:];M=z if a==keep else a
    x,y=b.gates[M-ni-2][1:];u=x if q[ALPHA[0]] in b.gates[x-ni-2][1:] else y
    nb=[t for t in b.gates[u-ni-2][1:] if t!=q[ALPHA[0]]][0];begin=b.inverse[nb]
    F=factor_mux(b,[q[i] for i in MATRIX],alphas(0))
    for i,s in enumerate(ALPHA):assert b.land(keep,b.mux(begin,q[s],F[i]))==ds[s],('alpha binding',i)
    return b,dict(ni=ni,before=before,sel=sel,gam=gam,live=live,keep=keep,begin=begin,ds=ds,out=out)


def splice(net,sel5,gam5,fault=None,port=False):
    """port=True (R132): no selector inside; 64 word inputs follow the 47 pins and the 11 selector
    address state bits are appended to the outputs (the parent reads a shared weight table)."""
    b,k=bind(net);ns=net.n_state;ni=k['ni']
    c=Builder(ns+NI+(64 if port else 0));cq=list(range(2,2+ns));cp=list(range(2+ns,2+ns+NI));layer=cp[OLD_NI:]
    if fault=='layer_bits_swapped':layer=[layer[1],layer[0],layer[2]]
    nsel=list(range(2+ns+NI,2+ns+NI+64)) if port else import_net(c,sel5,[cq[i] for i in SEL]+layer)[1]
    _,ngam=import_net(c,gam5,[cq[i] for i in GAMMA]+layer)
    for kk in range(16):
        if kk not in k['live']:assert ngam[kk]==k['gam'][kk],('gamma bit constant in layer 0 but not in all layers',kk)
    cuts={}
    def cut(w,v):
        assert w not in cuts;cuts[w]=v
        o=b.inverse.get(w)
        if o is not None and o>=ni+2:assert o not in cuts;cuts[o]=c.inv(v)
    for w,v in zip(k['sel'],nsel):cut(w,v)
    for kk in k['live']:cut(k['gam'][kk],ngam[kk])
    if fault=='old_gamma':cuts={w:v for w,v in cuts.items() if w not in [k['gam'][kk] for kk in k['live']]+[b.inverse.get(k['gam'][kk]) for kk in k['live']]}
    wires=list(range(ni+2))     # states and the original 44 inputs keep their numbers (inputs grow at the end)
    for i,(_,a,z) in enumerate(b.gates[:k['before']]):
        w=ni+2+i;wires.append(cuts[w] if w in cuts else c.nand(wires[a],wires[z]))
    m=[cq[i] for i in MATRIX];keep,begin=wires[k['keep']],wires[k['begin']]
    vals=[factor_mux(c,m,alphas(l) if fault!='alpha_layer0' else alphas(0)) for l in PAD]
    F=layer_mux(c,layer,vals)
    ds=[wires[w] for w in k['ds']]
    for i,s in enumerate(ALPHA):ds[s]=c.land(keep,c.mux(begin,cq[s],F[i]))
    comb=c.finish(ds+[wires[w] for w in k['out']]+([cq[i] for i in SEL] if port else []));return with_state(comb,ns),comb,dict(cut_wires=len(cuts),selector_outputs=64,gamma_live_outputs=len(k['live']),alpha_registers=len(ALPHA))


TB=r"""
#include "Vdut.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
typedef unsigned __int128 u128;
static Vdut*t;static uint64_t clk=0;
static u128 rd(){return (u128)t->dout[0]|((u128)t->dout[1]<<32)|((u128)t->dout[2]<<64);}
static u128 step(uint64_t d){t->din=d;t->clk=0;t->eval();u128 o=rd();t->clk=1;t->eval();t->clk=0;t->eval();clk++;return o;}
static u128 peek(uint64_t d){t->din=d;t->clk=0;t->eval();return rd();}
static uint64_t base(int layer,int pos){return (1ull<<23)|((uint64_t)pos<<24)|((uint64_t)layer<<44);}
static int wait_ready(uint64_t b){long g=0;while(!(peek(b)>>74&1)){step(b);if(++g>2000000)return 0;}return 1;}
int main(int argc,char**argv){
  if(argc<2){printf("usage: vsim case.txt\n");return 2;}
  FILE*f=fopen(argv[1],"r");if(!f){printf("no case\n");return 2;}
  t=new Vdut;step(1);step(0);
  char kind[4];int fills=0,calls=0,bad=0;
  while(fscanf(f,"%3s",kind)==1){
    int layer,pos,mat=0;int32_t v[128];
    if(kind[0]=='F'){if(fscanf(f,"%d %d",&layer,&pos)!=2)return 2;}else{if(fscanf(f,"%d %d %d",&layer,&pos,&mat)!=3)return 2;}
    for(int i=0;i<128;i++)if(fscanf(f,"%d",&v[i])!=1)return 2;
    uint64_t b=base(layer,pos);
    if(!wait_ready(b)){printf("{\"status\":\"stall\",\"at\":\"ready\",\"clocks\":%llu}\n",(unsigned long long)clk);return 4;}
    if(kind[0]=='F'){
      step(b|2);int k=0;long g=0;
      while(k<384){uint64_t x=b|((uint64_t)(v[k%128]&0xfffff)<<2)|(1ull<<22);if(peek(x)>>7&1){step(x);k++;}else step(b);if(++g>4000000){printf("{\"status\":\"stall\",\"at\":\"x\"}\n");return 4;}}
      g=0;while(!(peek(b)>>10&1)){step(b);if(++g>4000000){printf("{\"status\":\"stall\",\"at\":\"pdone\"}\n");return 4;}}
      step(b);fills++;
    }else{
      step(b|(1ull<<28)|((uint64_t)mat<<29));int n=0;long g=0;int32_t got[128];int seen[128];memset(seen,0,sizeof seen);
      while(n<128){uint64_t x=b|(1ull<<31);u128 o=peek(x);
        if(o>>69&1){int row=(int)(o>>60&127);int32_t r=(int32_t)((uint32_t)(o>>40&0xfffff)<<12)>>12;if(seen[row]){printf("dup row\n");bad++;}seen[row]=1;got[row]=r;n++;}
        step(x);if(++g>8000000){printf("{\"status\":\"stall\",\"at\":\"rows\"}\n");return 4;}}
      for(int i=0;i<128;i++)if(got[i]!=v[i]){if(bad<5)printf("mismatch call %d L%d p%d m%d row %d got %d want %d\n",calls,layer,pos,mat,i,got[i],v[i]);bad++;}
      calls++;
    }
  }
  printf("{\"status\":\"%s\",\"fills\":%d,\"calls\":%d,\"clocks\":%llu,\"mismatches\":%d}\n",bad?"fail":"pass",fills,calls,(unsigned long long)clk,bad);
  delete t;return bad?1:0;
}
"""


def golden_lib(tmp):
    import ctypes as ct
    so=Path(tmp)/'g.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer'),
        str(Path(__file__).with_name('r95_layers_golden.c')),'-o',str(so)],check=True,timeout=30)
    g=ct.CDLL(str(so));raw=blob();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(raw,len(raw))==0
    g.r95_rows.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.c_int,ct.POINTER(ct.c_int32)]
    g.int_run.argtypes=[ct.POINTER(ct.c_int32),ct.c_int,ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int32)]
    return g


def case_text(seed=26100727):
    """Fills of positions 0..9 with real layer inputs (int_run trace of each layer) and extremes, each
    with its own layer; then Q/K/V calls for every filled position, layers switching between calls."""
    import ctypes as ct,random
    rng=random.Random(seed);lines=[];plan=[]
    with tempfile.TemporaryDirectory() as t:
        g=golden_lib(t);toks=[rng.randrange(192) for _ in range(4)]
        tr=(ct.c_int32*(6*4*128))();lg=(ct.c_int32*(4*192))();assert g.int_run((ct.c_int32*4)(*toks),4,lg,tr)==0
        xs=[(l,list(tr[(l*4+p)*128:(l*4+p+1)*128])) for l in range(5) for p in (0,3)]
        xs+=[(rng.randrange(5),[524287 if i%3 else -524288 for i in range(128)]),(rng.randrange(5),[rng.randrange(-524288,524288)>>rng.randrange(19) for _ in range(128)])]
        for pos,(layer,x) in enumerate(xs):lines.append(f'F {layer} {pos} '+' '.join(map(str,x)));plan.append((layer,pos,x))
        calls=[(layer,pos,x,m) for layer,pos,x in plan for m in range(3)];rng.shuffle(calls)
        for layer,pos,x,m in calls:
            y=(ct.c_int32*128)();g.r95_rows((ct.c_int32*128)(*x),layer,m,y);lines.append(f'C {layer} {pos} {m} '+' '.join(map(str,y)))
    return '\n'.join(lines)+'\n',dict(tokens=toks,fills=len(plan),calls=len(calls),layers_filled=[l for l,_,_ in plan])


def flip_out(net,k):
    """flip_output on output k (R95 output 0 is not observed by the host protocol; 40 = result bit 0)."""
    gates=net.records.copy();inverse=gates[k-net.n_out][1];op,a,b=gates[inverse-net.n_in-2];assert op==0 and a==b
    gates[k-net.n_out]=(0,a,a);return Netlist(net.n_in,net.n_out,gates)


def comb_of(net,tie=None):
    """Combinational next-state/output graph; tie fixes the layer inputs (new graph) to a constant layer."""
    ns=net.n_state;b=Builder(ns+OLD_NI);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+OLD_NI))
    extra=[] if tie is None else [tie>>k&1 for k in range(3)]
    ds,out=import_net(b,net,p+extra,q);return b.finish(ds+out)


VFLAGS=['--cc','--exe','--build','-O2','-Wno-fatal','--x-assign','fast','--x-initial','fast','--prefix','Vdut','--top-module','r95',
        '-j','4','--output-split','20000','--output-split-cfuncs','2000','r95.v','tb.cpp','-o','vsim']


def vrun(d,net,case):
    d.mkdir(parents=True,exist_ok=True);(d/'r95.v').write_text(rtl(net,'r95'));(d/'tb.cpp').write_text(TB)
    r=subprocess.run(['verilator']+VFLAGS,cwd=d,capture_output=True,text=True,timeout=3600);(d/'build.log').write_text(r.stdout[-20000:]+r.stderr[-20000:]);assert r.returncode==0,d
    res=subprocess.run([str(d/'obj_dir/vsim'),str(case)],capture_output=True,text=True,timeout=3600)
    lines=[l for l in res.stdout.splitlines() if l.startswith('{')]
    return dict(rc=res.returncode,result=json.loads(lines[-1]) if lines else None,tail=res.stdout[-400:],rtl_sha256=sha(rtl(net,'r95').encode()))


def cloud(old,new,s5,g5,net_faults):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    from gate_check import verify as vtab,simulate
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);proofs={}
    graphs=dict(r95=comb_of(old),layer0=comb_of(new,0),layer1=comb_of(new,1),negative=flip_output(comb_of(new,0)))
    for k,g in graphs.items():(d/(k+'.blif')).write_text(blif(g))
    for k,w in [('layer0','equivalent'),('layer1','different'),('negative','different')]:
        proofs[k]=cec(abc,d/(k+'.blif'),d/'r95.blif',d/(k+'.log'));assert proofs[k]['verdict']==w,k
    tables=dict(selector=vtab(s5,list(range(1<<14)),[v for l in PAD for v in sel_words(l)]),
        gamma=vtab(g5,list(range(1<<10)),[v for l in PAD for v in gamma_words(2*l)]))
    b=Builder(6);m=[2,3,4];lay=[5,6,7];F=layer_mux(b,lay,[factor_mux(b,m,alphas(l)) for l in PAD]);an=b.finish(F)
    want=[alphas(PAD[v>>3])[v&7] for v in range(64)];tables['alpha']=vtab(an,list(range(64)),want)
    for k,t in tables.items():assert t['status']=='pass',k
    case=OUT/'case.txt';runs=dict(source=vrun(OUT/'vlt_source',new,case))
    assert runs['source']['rc']==0 and runs['source']['result']['status']=='pass' and runs['source']['result']['mismatches']==0
    for k,n in net_faults.items():runs[k]=vrun(OUT/('vlt_'+k),n,case);assert runs[k]['rc']!=0,k
    return dict(status='pass',proofs=proofs,tables=tables,runs=runs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--orders',action='store_true');ap.add_argument('--tb');ap.add_argument('--fault');ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    t0=time.monotonic();net=r95()
    if a.orders:
        for name,o in [('sel_accepted+layer_top',SEL_ORDER0+[11,12,13]),('layer_first',[13,12,11]+SEL_ORDER0),('layer_after_mat',[9,10,13,12,11]+list(range(2,9))+[0,1])]:
            print('selector',name,metrics(selector5(o))['nNand'],flush=True)
        for name,o in [('natural',list(range(10))),('layer_first',[9,8,7]+list(range(7)))]:
            print('gamma',name,metrics(gamma5(o))['nNand'],flush=True)
        return
    s5=selector5(SEL5_ORDER);g5=gamma5(GAM5_ORDER)
    new,comb,info=splice(net,s5,g5)
    if a.tb:
        d=Path(a.tb);d.mkdir(parents=True,exist_ok=True);n=new if not a.fault else (flip_output(new) if a.fault=='output_flip' else splice(net,s5,g5,a.fault)[0])
        (d/'r95.v').write_text(rtl(n,'r95'));(d/'tb.cpp').write_text(TB);txt,meta=case_text();(d/'case.txt').write_text(txt);print(meta);return
    OUT.mkdir(parents=True,exist_ok=True)
    txt,meta=case_text();(OUT/'case.txt').write_text(txt)
    for k,g in (('r95_layers',new),('selector5',s5),('gamma5',g5)):(OUT/(k+'.nl')).write_bytes(g.encode())
    import ci,gate_check  # cloud-only imports, listed so their sources are bound too
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','integer_opt/r95_layers_golden.c','physical/model.bin','integer_opt/layer0_units/manifest.json','integer_opt/layer0_units/r95_norm_qkv.nl'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='three layer-0 constants bound to the actual R95 graph and replaced; C cases prepared; CEC, tables and Verilator runs await Actions',
        metrics=metrics(new),r95_metrics=metrics(net),selector5=metrics(s5),gamma5=metrics(g5),selector0=metrics(selector0()),splice=info,
        bindings=dict(selector_state_bits=SEL,gamma_state_bits=GAMMA,alpha_registers=ALPHA,matrix_register=MATRIX,pad_layers=PAD[5:],selector_order=SEL5_ORDER,gamma_order=GAM5_ORDER),
        cases=meta,case_sha256=sha(txt.encode()),
        contract='R95 interface + layer3 (inputs 44..46, held during a fill or matrix call): fill(pos,x) caches A8(norm(x,2*layer)); call(pos,mat) rows = linear(norm(x,2*layer),layer,mat)',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:
        faults={k:splice(net,s5,g5,k)[0] for k in ('alpha_layer0','layer_bits_swapped','old_gamma')};faults['result_flip']=flip_out(new,40)
        report['verification']=cloud(net,new,s5,g5,faults)
        report['status']='layer-0 configuration CEC-equivalent to accepted R95; all selector/gamma/alpha entries; actual graph (Verilator) == C for all 5 layers; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('r95_layers',metrics(new),info,'r95',metrics(net)['nNand'],round(time.monotonic()-t0,1))


if __name__=='__main__':main()
