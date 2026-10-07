#!/usr/bin/env python3
"""R126: the R124 O projection + residual serving all five layers (layer index input).

Same shell, quantizer and scale unit as R124 (shell built with alpha=0 and proved by R124's CEC
recipe); two changes in the composition only:
 - the ternary O table grows to address layer<<14 | row<<7 | col, read with the caller-held layer
   bits; the Shannon expansion takes the layer bits first (leaf level, layer2,layer1,layer0) and
   unused layers 5..7 repeat layers 1..3 (35,013 NAND; natural order with layer on top: 36,835);
 - the scale unit's alpha port (scale input bits 39..56, latched by the scale unit at its start)
   is a constant 5-way mux of alpha[layer*7+3] instead of the layer-0 constant.
Interface: R124's 45 inputs + layer3 (inputs 45..47, held for the whole run) -> R124's 23 outputs.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_OPROJ5_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_OPROJ5_OUT',str(R/'build/integer_opt/oproj_layers')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from bench import lookup
from export import import_net,rtl
import oproj,quant_stream,scale_pipeline
NI,NO=oproj.NI+3,oproj.NO
NS=oproj.NS
ALPHA0=39            # scale_pipeline input: reset,start,acc17,m20,alpha18
sha=lambda b:hashlib.sha256(b).hexdigest()


def tables():
    codes=[];alphas=[]
    for layer in range(5):
        c,al=oproj.weights(layer);codes.append(c);alphas.append(al)
    full=[v for layer in PAD for v in codes[layer]]
    return ordered(full,2,ORDER),codes,alphas


PAD=[0,1,2,3,4,1,2,3]
ORDER=[16,15,14]+list(range(14))


def ordered(table,width,order):
    """Shannon lookup expanding address bit order[0] first (as qkv_plain.ordered_full); inputs keep the original bit meaning."""
    assert len(table)==1<<len(order) and sorted(order)==list(range(len(order)))
    perm=[sum((i>>j&1)<<k for j,k in enumerate(order)) for i in range(len(table))]
    source=lookup([table[i] for i in perm],width,'shannon')
    b=Builder(len(order));_,y=import_net(b,source,[2+k for k in order]);return b.finish(y)


def alpha_mux(b,layer,alphas,fault=None):
    vals=alphas+[alphas[4]]*3
    if fault=='alpha_layer0':vals=[alphas[0]]*8
    out=[]
    for i in range(18):
        bits=[v>>i&1 for v in vals]
        l1=[b.mux(layer[0],bits[2*k],bits[2*k+1]) for k in range(4)]
        l2=[b.mux(layer[1],l1[2*k],l1[2*k+1]) for k in range(2)]
        out.append(b.mux(layer[2],l2[0],l2[1]))
    return out


def connect(shell_net,tab,qn,sn,alphas,fault=None):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2));SRG,SCT,SQU=oproj.SRG,oproj.SCT,oproj.SQU
    qq=pins[SRG+SCT:SRG+SCT+SQU];qs=pins[SRG+SCT+SQU:NS];reset=pins[NS]
    c=pins[SRG:SRG+SCT];layer=pins[NS+oproj.NI:NS+NI]
    if fault=='layer_bits_swapped':layer=[layer[1],layer[0],layer[2]]
    addr=c[3:10]+c[10:17]+layer
    _,w=import_net(b,tab,addr);al=alpha_mux(b,layer,alphas,fault)
    p=pins[:NS+oproj.NI]
    _,q0=import_net(b,qn,[reset]+[0]*(quant_stream.NI-1),qq)
    _,s0=import_net(b,sn,[reset]+[0]*(scale_pipeline.NI-1),qs)
    _,s1=import_net(b,shell_net,p+[0]*SQU+q0+[0]*oproj.SSC+s0+w);o=NS+NO
    qin=s1[o:o+quant_stream.NI];sin=s1[o+quant_stream.NI:o+quant_stream.NI+scale_pipeline.NI]
    assert sin[ALPHA0:ALPHA0+18]==[0]*18;sin=sin[:ALPHA0]+al
    qd,qo=import_net(b,qn,qin,qq);sd,so=import_net(b,sn,sin,qs)
    assert qo[:39]==q0[:39] and so==s0
    _,full=import_net(b,shell_net,p+qd+qo+sd+so+w);assert full[o:o+quant_stream.NI]==qin
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def build(fault=None):
    tab,codes,alphas=tables();qn=quant_stream.make();sn=scale_pipeline.make()
    sh=oproj.shell(0,None if fault in ('alpha_layer0','layer_bits_swapped') else fault)
    net,comb=connect(sh,tab,qn,sn,alphas,fault)
    return dict(net=net,comb=comb,shell=sh,table=tab,codes=codes,alphas=alphas,quant=qn,scale=sn)


def cases(layer):
    """R124's case recipe with a per-layer seed, golden y from C linear(h,layer,3)+residual."""
    import ctypes as ct,random
    rng=random.Random(26100724+layer);hs=[[0]*128,[524287]*128,[-524288]*128,[(-524288,524287)[i%2] for i in range(128)]]
    hs+=[[rng.randrange(-524288,524288)>>sh for _ in range(128)] for sh in (0,3,8,13)]
    out=[]
    with tempfile.TemporaryDirectory() as t:
        g=oproj.golden_lib(t)
        for k,hv in enumerate(hs):
            x=[rng.randrange(-524288,524288)>>(k%4*5) for _ in range(128)] if k%3 else [(524287 if i%2 else -524288) for i in range(128)]
            y=(ct.c_int32*128)();g.oproj((ct.c_int32*128)(*hv),(ct.c_int32*128)(*x),y,layer)
            out.append(dict(layer=layer,h=hv,x=x,y=list(y)))
    return out


def drive(net,cs,sim_so,seed=26100726):
    """R124's host protocol (stalls, junk starts, aborts) with the layer bits held per case; layers switch between runs."""
    import ctypes as ct,random
    lib=ct.CDLL(str(sim_so));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    raw=net.encode();assert lib.nl_init(raw,len(raw),NI,NO)==0;buf=ct.create_string_buffer(3)
    rng=random.Random(seed);rows=[];stats=dict(vectors=0,aborts=0,stalls=0,layers=[0]*5)
    def tick(xx):
        lib.nl_step(xx.to_bytes(6,'little'),buf);y=int.from_bytes(buf.raw,'little');rows.append((xx,y,(1<<NO)-1));return y
    tick(1);tick(0)
    def run(c,stall,abort_at=None):
        L=c['layer']<<45;before=len(rows);sent=0;got=[]
        tick(L|2)
        while True:
            el=len(rows)-before
            if abort_at is not None and el==abort_at:tick(L|1|2);tick(L);stats['aborts']+=1;return
            hv=int(sent<128 and (not stall or rng.randrange(3)!=0));xv=int(not stall or rng.randrange(3)!=0);yr=int(not stall or rng.randrange(4)!=0)
            hval=c['h'][sent]&0xfffff if sent<128 else rng.getrandbits(20);xval=c['x'][len(got)]&0xfffff if len(got)<128 else 0
            junk=int(el>2 and rng.randrange(80)==0)
            y=tick(L|(junk<<1)|(hv<<2)|(hval<<3)|(xv<<23)|(xval<<24)|(yr<<44))
            if hv and y&1:sent+=1
            if y>>21&1 and yr:
                got.append(y>>1&0xfffff)
                if len(got)==128:break
            elif y>>21&1:stats['stalls']+=1
            assert el<200000
        assert got==[v&0xfffff for v in c['y']],(c['layer'],got[:4],[v&0xfffff for v in c['y'][:4]])
        stats['vectors']+=1;stats['layers'][c['layer']]+=1;tick(L)
    for k,c in enumerate(cs):run(c,stall=k%3==2)
    for at in (10,600,20000):run(cs[9],False,abort_at=at)
    run(cs[-1],False)
    return rows,stats


def all_cases():
    cs=[c for layer in range(5) for c in cases(layer)]
    return [cs[i] for i in random_order(len(cs))]


def random_order(n):
    import random
    o=list(range(n));random.Random(26100727).shuffle(o);return o


def table_check(g):
    from gate_check import verify as vtab
    want=[g['codes'][layer][a&16383] for a,layer in ((a,PAD[a>>14]) for a in range(1<<17))]
    return vtab(g['table'],list(range(1<<17)),want)


def alpha_check(alphas,fault=None):
    from gate_check import simulate
    b=Builder(3);al=alpha_mux(b,[2,3,4],alphas,fault);n=b.finish(al)
    return simulate(n,list(range(8)))


def remote_check():
    g=build();cs=all_cases()
    with tempfile.TemporaryDirectory() as t:
        so=Path(t)/'s.so';subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(so)],check=True)
        rows,stats=drive(g['net'],cs,so)
        import verify as checks
        checks.NI=NI;checks.NO=NO;checks.OUT=Path(t);shutil.copy(so,Path(t)/'sim.so')
        faults={k:checks.check_nand(rows,build(k)['net'].encode()) for k in ('alpha_layer0','layer_bits_swapped','bad_weight','no_writeback')}
        faults['output_flip']=checks.check_nand(rows,flip_output(g['net']).encode())
    tc=table_check(g);al=alpha_check(g['alphas'])
    return dict(metrics=metrics(g['net']),table=metrics(g['table']),table_all_131072=tc['status'],alpha_mux=al,alphas=g['alphas'],clocks=len(rows),stats=stats,faults=faults)


def cloud(g,cs):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as checks,sampler as smp
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'shell';prefix.with_suffix('.ref.v').write_text(oproj.reference(0))
    ref=smp.mapped_reference(prefix,oproj.SHELL_IN,oproj.SHELL_OUT);proofs={}
    for k,n in [('source',g['shell']),('negative',flip_output(g['shell'])),('no_writeback',oproj.shell(0,'no_writeback')),('reference',ref)]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(n))
    for k,w in [('source','equivalent'),('negative','different'),('no_writeback','different')]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    table=table_check(g);assert table['status']=='pass'
    al=alpha_check(g['alphas']);assert al==g['alphas']+[g['alphas'][4]]*3
    assert alpha_check(g['alphas'],'alpha_layer0')!=al
    subprocess.run(['cc','-O3','-std=c99','-shared','-fPIC',str(R/'physical/nl_sim.c'),'-o',str(OUT/'sim.so')],check=True,timeout=60)
    rows,stats=drive(g['net'],cs,OUT/'sim.so');(OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    checks.OUT=OUT;checks.NI=NI;checks.NO=NO;faults={}
    for k,n in [('output_flip',flip_output(g['net']))]+[(k,build(k)['net']) for k in ('alpha_layer0','layer_bits_swapped','bad_weight','no_writeback')]:
        faults[k]=checks.check_nand(rows,n.encode());assert faults[k]>0,k
    bad=build('alpha_layer0')['net'];(OUT/'bad.v').write_text(rtl(bad,'oproj_layers'));(OUT/'tb.v').write_text(checks.testbench(NI,NO,'oproj_layers',str(OUT/'vectors.txt')))
    exe=checks.compile_rtl('source',OUT/'oproj_layers.v');checks.run([exe],1800)
    exe=checks.compile_rtl('negative',OUT/'bad.v');res=subprocess.run([str(exe)],capture_output=True,text=True,timeout=1800)
    (OUT/'rtl.negative.log').write_text(res.stdout+res.stderr);assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),table_all_addresses=table,alpha_mux=al,protocol=stats,clocks=len(rows),
        actual_fault_mismatches=faults,actual_rtl_fault_rejected='alpha_layer0')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--remote-check',action='store_true');a=ap.parse_args()
    if a.remote_check:print(json.dumps(remote_check()));return
    OUT.mkdir(parents=True,exist_ok=True);t0=time.monotonic();g=build()
    assert metrics(g['quant'])['sha256']=='0caf2626931329b14df86526aa4e294d020d7b4004a408c897045ce916e3550b'
    assert metrics(g['scale'])['sha256']=='444ddb4f2929b26cf03ef62b4670254d2b538d139490fed94cfaaa9c4b1d8b8b'
    cs=all_cases();(OUT/'cases.json').write_text(json.dumps(cs,separators=(',',':'))+'\n')
    for k in ('net','comb','shell','table'):(OUT/(k+'.nl')).write_bytes(g[k].encode())
    (OUT/'oproj_layers.v').write_text(rtl(g['net'],'oproj_layers'))
    import sampler,ci,verify  # cloud-only imports, listed so their sources are bound too
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer_opt/oproj_golden.c','integer/int_model.c','physical/model.bin','physical/nl_sim.c'):sources[n]=sha((R/n).read_bytes())
    report=dict(status='R124 shell/quantizer/scale reused; 5-layer O table and alpha mux built; C cases prepared; proofs and drive await Actions',
        metrics=metrics(g['net']),comb_metrics=metrics(g['comb']),shell=metrics(g['shell']),table=metrics(g['table']),alphas=g['alphas'],
        table_order=dict(expand_first=ORDER[:3],pad_layers=PAD[5:]),r124_metrics_for_comparison=dict(nNand=23586,nLatch=3182,table_nNand=8642),
        C_cases=len(cs),cases_sha256=sha((OUT/'cases.json').read_bytes()),
        contract='R124 interface + layer3 (inputs 45..47, held during a run): y = sat(x + linear(h,layer,3))',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(g,cs);report['vector_sha256']=sha((OUT/'vectors.txt').read_bytes());report['status']='shell CEC, all 131,072 table addresses, alpha mux, actual-graph outputs == C for all 5 layers, RTL replay and actual faults pass'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('oproj_layers',metrics(g['net']),'table',metrics(g['table'])['nNand'],'alphas',g['alphas'],round(time.monotonic()-t0,1))


if __name__=='__main__':main()
