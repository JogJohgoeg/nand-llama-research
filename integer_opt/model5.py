#!/usr/bin/env python3
"""R129: all five transformer layers as one self-running graph (R125 controller + layer loop).

Children: R126 O projection, R127 R95 producer and R128 R98 FFN (each with a 3-bit layer input),
R123 feeder, R72 head and R52 bank unchanged. The R125 controller gains a 3-bit layer register
(layer0.shell(..., layers=5)): when the last position of a layer has been written back, the layer
register steps and phase A refills the R95 cache from the bank (which now holds that layer's
output); after layer 4 the run ends with done. The bank then holds int_run's x after all five
layers (trace index 5); the R120 output head consumes it next.
Interface: identical to R125 (reset,start,L5,host bank port -> bank outputs,busy,done).
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,time
R=Path(os.environ.get('H3_MODEL5_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_MODEL5_OUT',str(R/'build/integer_opt/model5')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import metrics,blif,flip_output
from export import rtl
import layer0
LAYERS=5
CASES=[(1,7),(2,11),(3,13)]
sha=lambda b:hashlib.sha256(b).hexdigest()


def build(fault=None,ch=None,shared=False):
    ch=ch or layer0.children(LAYERS,shared);sh=layer0.shell(ch,fault,LAYERS);net,comb=layer0.connect(sh,ch,LAYERS)
    return dict(ch=ch,shell=sh,net=net,comb=comb)


def vrun(d,case,limit,timeout):
    res=subprocess.run([str(d/'obj_dir/vsim'),str(case),str(limit)],capture_output=True,text=True,timeout=timeout)
    lines=[l for l in res.stdout.splitlines() if l.startswith('{')]
    return dict(rc=res.returncode,result=json.loads(lines[-1]) if lines else None,tail=res.stdout[-400:])


def cloud(g,faults=()):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import sampler as smp,layer0_tb as tb
    from ci import cec
    ch,sh,net=g['ch'],g['shell'],g['net']
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(exist_ok=True);prefix=d/'shell';prefix.with_suffix('.ref.v').write_text(layer0.reference(ch,LAYERS))
    ref=smp.mapped_reference(prefix,sh.n_in,sh.n_out);proofs={}
    for k,n in [('source',sh),('negative',flip_output(sh)),('reference',ref)]+[(f,layer0.shell(ch,f,LAYERS)) for f in ('layer_stuck0',)+tuple(faults)]:
        prefix.with_suffix('.'+k+'.blif').write_text(blif(n))
    for k,w in [('source','equivalent'),('negative','different')]+[(f,'different') for f in ('layer_stuck0',)+tuple(faults)]:
        proofs[k]=cec(abc,prefix.with_suffix('.'+k+'.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.'+k+'.log'));assert proofs[k]['verdict']==w,k
    cases={f'L{L}':tb.write_case(OUT/f'case_L{L}.txt',L,seed,LAYERS) for L,seed in CASES}
    builds={}
    for k,n in [('source',net),('layer_stuck0',build('layer_stuck0',ch)['net']),('output_flip',flip_output(net))]:
        bd=OUT/('vlt_'+k);tb.write_tb(bd,n);t=time.monotonic()
        r=subprocess.run(['verilator']+layer0.VFLAGS,cwd=bd,capture_output=True,text=True,timeout=7200)
        (OUT/f'verilator_{k}.log').write_text(r.stdout[-20000:]+r.stderr[-20000:]);assert r.returncode==0,k
        builds[k]=dict(seconds=round(time.monotonic()-t,1),rtl_sha256=sha((bd/'layer0.v').read_bytes()))
    runs={}
    for L,seed in CASES:
        r=vrun(OUT/'vlt_source',OUT/f'case_L{L}.txt',200_000_000*L,5*3600);runs[f'L{L}']=r
        assert r['rc']==0 and r['result']['status']=='pass' and r['result']['mismatches']==0,(L,r)
    neg={}
    for k,c in [('layer_stuck0','L1'),('output_flip','L1')]:
        lim=2*runs[c]['result']['layer_clocks'];r=vrun(OUT/('vlt_'+k),OUT/f'case_{c}.txt',lim,3*3600);neg[k]=dict(r,case=c,limit=lim);assert r['rc']!=0,k
    return dict(status='pass',proofs=proofs,reference_metrics=metrics(ref),cases=cases,verilator_builds=builds,model_runs=runs,actual_rtl_faults_rejected=neg)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--tb');ap.add_argument('--L',type=int,default=1);ap.add_argument('--seed',type=int,default=7);ap.add_argument('--fault');ap.add_argument('--shared',action='store_true');a=ap.parse_args()
    t0=time.monotonic();g=build(shared=a.shared)
    if a.tb:
        import layer0_tb as tb
        n=g['net'] if not a.fault else (flip_output(g['net']) if a.fault=='output_flip' else build(a.fault,g['ch'])['net'])
        tb.write_tb(a.tb,n);print(tb.write_case(Path(a.tb)/'case.txt',a.L,a.seed,LAYERS),metrics(n)['nNand']);return
    OUT.mkdir(parents=True,exist_ok=True)
    for k in ('net','shell'):(OUT/(k+'.nl')).write_bytes(g[k].encode())
    import layer0_tb,sampler,ci,oproj_layers,r95_layers,r98_layers  # bound sources
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','integer_opt/final_a8_golden.c','integer_opt/oproj_golden.c','physical/model.bin','physical/nl_sim.c','integer_opt/layer0_units/manifest.json')+tuple('integer_opt/layer0_units/'+f for f in ('r95_norm_qkv.nl','r72_head.nl','r98_ffn.nl','r52_xbank.nl')):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='five-layer children (R126/R127/R128) + R123/R72/R52 with the R125 controller and a layer loop; shell CEC and model runs await Actions',
        metrics=metrics(g['net']),comb_metrics=metrics(g['comb']),shell=metrics(g['shell']),children={k:metrics(g['ch'][k]) for k in layer0.ORDER},
        controller_state_bits=sum(layer0.ctl(LAYERS).values()),r125_metrics_for_comparison=dict(nNand=208625,nLatch=85885),
        contract='R125 interface; start with L runs int_model.c layers 0..4 over positions 0..L-1; the bank then holds x after layer 4 (int_run trace index 5)',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(g);report['status']='shell CEC vs independent RTL; actual five-layer graph (Verilator) == int_run after layer 4 for L=1..3; actual faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('model5',metrics(g['net']),{k:metrics(g['ch'][k])['nNand'] for k in layer0.ORDER},round(time.monotonic()-t0,1))


if __name__=='__main__':main()
