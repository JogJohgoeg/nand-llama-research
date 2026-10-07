#!/usr/bin/env python3
"""Actions-only hierarchical hardening of the pinned R118 x -> token head.

Sub-table macros (`prepare/harden/mapped/report K k util [macro]`): bind the same pinned R118
proof artifact as xhead_physical/flow.py, cut sub-table k of K (subtable.py), replay all 512
addresses on RTL plus an inverted-word negative, harden, replay them post-route.
With `macro`, the block is hardened for integration: met4-only power grid, routing up to met4,
GRT/DRT antenna repair on.

Top (`top-prepare/top-harden/top-mapped/top-report K`): standard-cell rest (every record except
the 9-bit table cone) + K macro instances (top.py). RTL of top + K sub-tables replays the same
152,400-clock C prefix as the flat x-head layout, with a negative in one macro; post-route the
routed top netlist plus every routed macro netlist replays it again.
"""
from pathlib import Path
import hashlib,importlib.util,io,json,os,re,shutil,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT),str(ROOT/'hier_physical')]
sha=lambda b:hashlib.sha256(b).hexdigest()
spec=importlib.util.spec_from_file_location('xhead_flow',ROOT/'xhead_physical/flow.py');X=importlib.util.module_from_spec(spec);spec.loader.exec_module(X)
SDC=ROOT/'physical/constraints.sdc'
SIGNOFF=('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count','route__drc_errors','design__xor_difference__count')


def out(K,k):return ROOT/f'build/hier_physical/k{K}_{k}'
TOP=ROOT/'build/hier_physical/top'


def pinned_net():
    from golden import Netlist
    from nand import metrics
    raw=X.api('actions/artifacts/'+str(X.ARTIFACT)+'/zip');assert sha(raw)==X.ZIP_SHA
    meta=json.loads(X.api('actions/artifacts/'+str(X.ARTIFACT)));assert meta['workflow_run']['id']==X.RUN and meta['digest']=='sha256:'+X.ZIP_SHA
    with zipfile.ZipFile(io.BytesIO(raw)) as z:nl=z.read('joint.nl');r=json.loads(z.read('receipt.json'))
    assert sha(nl)==X.GRAPH_SHA==r['metrics']['sha256'] and r['verification']['status']=='pass'
    net=Netlist.decode(nl,X.NI,X.NO);assert metrics(net)==r['metrics'];return net


def base_config(o,**kw):
    cfg=json.loads((ROOT/'xhead_physical/config.json').read_text())
    for key in ('PL_MAX_DISPLACEMENT_X','PL_MAX_DISPLACEMENT_Y'):cfg.pop(key,None)
    rel='dir::'+os.path.relpath(SDC,o);cfg.update(PNR_SDC_FILE=rel,SIGNOFF_SDC_FILE=rel,**kw);return cfg


def prepare(K,k,util,macro=False):
    import subtable,verify
    net=pinned_net();o=out(K,k);name=f'int_c16_e8_k{K}_{k}'
    rep=subtable.build(net,K,k,name,o);rep.update(source_run=X.RUN,proof_artifact=X.ARTIFACT,proof_archive_sha256=X.ZIP_SHA,graph_sha256=X.GRAPH_SHA,macro_views=macro)
    (o/'subtable.json').write_text(json.dumps(rep,indent=2)+'\n')
    verify.OUT=o
    (o/'tb.v').write_text(verify.testbench(9,rep['words'],name,str(o/'vectors.txt')))
    exe=verify.compile_rtl('source',o/'slice.v');verify.run([exe],600)
    src=(o/'slice.v').read_text();bad=src.replace('assign dout[0]=w','assign dout[0]=~w',1);assert bad!=src
    (o/'negative.v').write_text(bad);bexe=verify.compile_rtl('negative',o/'negative.v')
    res=subprocess.run([str(bexe)],capture_output=True,text=True,timeout=600);assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    (o/'adapter_verification.json').write_text(json.dumps(dict(status='pass',rtl_clocks=1024,addresses='all 512, two passes',
        actual_RTL_mutation_rejected=True,vectors_sha256=rep['files']['vectors.txt']['sha256']),indent=2)+'\n')
    cfg=base_config(o,DESIGN_NAME=name,VERILOG_FILES=['dir::slice.v'],FP_CORE_UTIL=util,PL_TARGET_DENSITY_PCT=util+5)
    if macro:cfg.update(PDN_MULTILAYER=False,RT_MAX_LAYER='met4',GRT_REPAIR_ANTENNAS=True,DRT_ANTENNA_REPAIR_ITERS=3)
    (o/'config.json').write_text(json.dumps(cfg,indent=2)+'\n');print(json.dumps({a:b for a,b in rep.items() if a!='word_records'},indent=2))


def harden(o,cap=None):
    import harden as H
    if cap:os.environ['H3_FLOW_CAP_SECONDS']=str(cap)
    H.OUT=o;sys.argv=[sys.argv[0],'--config',str(o/'config.json')];H.main()


def harden_macro(K,k,utils=(60,50,42)):
    """Per-block fallback: some sub-tables congest at 60% once routing stops at met4."""
    o=out(K,k);tries=[]
    for u in utils:
        cfg=json.loads((o/'config.json').read_text());cfg.update(FP_CORE_UTIL=u,PL_TARGET_DENSITY_PCT=u+5)
        (o/'config.json').write_text(json.dumps(cfg,indent=2)+'\n');shutil.rmtree(o/'run',ignore_errors=True)
        try:harden(o);tries.append(dict(util=u,ok=True));break
        except SystemExit as e:tries.append(dict(util=u,ok=False,code=str(e)))
    (o/'harden_tries.json').write_text(json.dumps(tries,indent=2)+'\n')
    assert tries[-1]['ok'],tries


def mapped(K,k):
    import verify
    verify.OUT=out(K,k);verify.mapped_check()


def summary(o,m):
    return dict(signoff={x:m.get(x) for x in SIGNOFF},die_area_um2=m.get('design__die__area'),core_area_um2=m.get('design__core__area'),
        stdcell_area_um2=m.get('design__instance__area__stdcell'),utilization=m.get('design__instance__utilization'),
        wirelength_um=m.get('route__wirelength'),antenna_nets=m.get('antenna__violating__nets'),
        setup_vio=m.get('timing__setup_vio__count'),hold_vio=m.get('timing__hold_vio__count'),
        max_slew_vio=m.get('design__max_slew_violation__count'),max_cap_vio=m.get('design__max_cap_violation__count'),
        resources=json.loads((o/'resources.json').read_text()),github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'))


def report(K,k):
    o=out(K,k);m=json.loads((o/'run/final/metrics.json').read_text())
    rep=dict(design=f'int_c16_e8_k{K}_{k}',**summary(o,m),subtable=json.loads((o/'subtable.json').read_text()),
             mapped=json.loads((o/'mapped_verification.json').read_text()))
    rep['subtable'].pop('word_records',None)
    (o/'hier_report.json').write_text(json.dumps(rep,indent=2)+'\n');print(json.dumps({a:b for a,b in rep.items() if a not in ('subtable','resources')},indent=2))
    for x in SIGNOFF:assert m[x]==0,(x,m[x])


# ---- top ----
def lef_size(p):
    s=re.search(r'SIZE\s+([\d.]+)\s+BY\s+([\d.]+)',p.read_text());return float(s.group(1)),float(s.group(2))


def plan(K,w,h,rest_um2,halo=20.0):
    """Two macro bands (bottom/top), standard-cell rest in the middle band; returns die and locations."""
    cols=-(-K//4);pitch_x=w+halo;pitch_y=h+halo;margin=30.0
    W=cols*pitch_x+2*margin;band=max(rest_um2/W,4*20.0);H=4*pitch_y+band+2*margin
    snap=lambda v,g:round(round(v/g)*g,3)
    loc={}
    for k in range(K):
        r,c=divmod(k,cols);y=margin+r*pitch_y if r<2 else margin+2*pitch_y+band+(r-2)*pitch_y
        loc[f'u_t{k}']=[snap(margin+c*pitch_x,0.46),snap(y,2.72)]
    return [0,0,snap(W,0.46),snap(H,2.72)],loc


def top_prepare(K,rest_um2):
    import top,verify,subtable
    net=pinned_net();TOP.mkdir(parents=True,exist_ok=True)
    X.prepare()          # pinned x-head prefix vectors (152,400 clocks) and their RTL replay
    for n in ('vectors.txt','cases.json','source.json'):shutil.copyfile(X.OUT/n,TOP/n)
    v,rep=top.top(net,K);(TOP/'top.v').write_text(v)
    for k in range(K):subtable.build(net,K,k,f'int_c16_e8_k{K}_{k}',TOP/f'rtl/k{K}_{k}')
    subs=[TOP/f'rtl/k{K}_{k}/slice.v' for k in range(K)]
    verify.OUT=TOP;(TOP/'tb.v').write_text(verify.testbench(X.NI,X.NO,'int_c16_x_head',str(TOP/'vectors.txt')))
    def build(label,files):
        d=TOP/('obj_'+label)
        verify.run(['verilator','--binary','--timing','--top-module','tb','-j','4','--output-split','10000','--output-split-cfuncs','1000','-Wno-fatal','--Mdir',d,TOP/'top.v',*files,TOP/'tb.v'],1800)
        return d/'Vtb'
    verify.run([build('source',subs)],1800)
    bad=subs[17].read_text().replace('assign dout[3]=w','assign dout[3]=~w',1);(TOP/'negative_k17.v').write_text(bad)
    res=subprocess.run([str(build('negative',subs[:17]+[TOP/'negative_k17.v']+subs[18:]))],capture_output=True,text=True,timeout=1800)
    assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    macros={};w=h=0
    for k in range(K):
        f=ROOT/f'build/hier_physical/k{K}_{k}/run/final';name=f'int_c16_e8_k{K}_{k}'
        mw,mh=lef_size(f/f'lef/{name}.lef');w,h=max(w,mw),max(h,mh)
        macros[name]=dict(gds=[str(f/f'gds/{name}.gds')],lef=[str(f/f'lef/{name}.lef')],nl=[str(f/f'nl/{name}.nl.v')],
            lib={**{c.name:[str(next(c.glob('*.lib')))] for c in sorted((f/'lib').iterdir())},**{'nom_'+c.name[4:]:[str(next(c.glob('*.lib')))] for c in sorted((f/'lib').iterdir()) if c.name.startswith('max_')}},
            spef={f'{c.name}_*':[str(next(c.glob('*.spef')))] for c in sorted((f/'spef').iterdir())},instances={})
    die,loc=plan(K,w,h,rest_um2)
    for k in range(K):macros[f'int_c16_e8_k{K}_{k}']['instances'][f'u_t{k}']=dict(location=loc[f'u_t{k}'],orientation='N')
    cfg=base_config(TOP,DESIGN_NAME='int_c16_x_head',VERILOG_FILES=['dir::top.v'],FP_SIZING='absolute',DIE_AREA=die,
                    PL_TARGET_DENSITY_PCT=50,MACROS=macros,PDN_MACRO_CONNECTIONS=['u_t.* VPWR VGND VPWR VGND'])
    for key in ('FP_CORE_UTIL',):cfg.pop(key,None)
    (TOP/'config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    res=dict(status='pass',rtl_clocks=X.PREFIX,vectors_sha256=X.PREFIX_SHA,negative='inverted word 3 of macro 17 rejected',top=rep,die=die,macro_wh=[w,h],
             files={p.name:sha(p.read_bytes()) for p in [TOP/'top.v',*subs]})
    (TOP/'adapter_verification.json').write_text(json.dumps(res,indent=2)+'\n');print(json.dumps(res,indent=2)[:3000])


def top_mapped(K):
    import verify
    cfg=json.loads((TOP/'run/resolved.json').read_text());lib=Path(cfg['PDK_ROOT'])/cfg['PDK']/'libs.ref/sky130_fd_sc_hd/verilog'
    nets=[TOP/'run/final/nl/int_c16_x_head.nl.v']+[ROOT/f'build/hier_physical/k{K}_{k}/run/final/nl/int_c16_e8_k{K}_{k}.nl.v' for k in range(K)]
    verify.OUT=TOP
    verify.run(['iverilog','-g2012','-DFUNCTIONAL','-DUNIT_DELAY=#0','-I',lib,'-s','tb','-o',TOP/'mapped.vvp',lib/'primitives.v',lib/'sky130_fd_sc_hd.v',*nets,TOP/'tb.v'],3600)
    verify.run(['vvp',TOP/'mapped.vvp'],16000)
    (TOP/'mapped_verification.json').write_text(json.dumps(dict(status='pass',vectors_sha256=sha((TOP/'vectors.txt').read_bytes()),
        netlists={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in nets},method='post-route functional standard-cell simulation of routed top + every routed macro, zero delay'),indent=2)+'\n')


def top_report(K):
    m=json.loads((TOP/'run/final/metrics.json').read_text())
    rep=dict(design='int_c16_x_head (hierarchical)',macros=K,**summary(TOP,m),adapter=json.loads((TOP/'adapter_verification.json').read_text()))
    p=TOP/'mapped_verification.json'
    if p.exists():rep['mapped']=json.loads(p.read_text())
    (TOP/'hier_report.json').write_text(json.dumps(rep,indent=2)+'\n');print(json.dumps({a:b for a,b in rep.items() if a not in ('resources','adapter')},indent=2))
    for x in SIGNOFF:assert m[x]==0,(x,m[x])


if __name__=='__main__':
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and gate simulation stay on Actions'
    os.chdir(ROOT);a=sys.argv[1:];K=int(a[1])
    {'prepare':lambda:prepare(K,int(a[2]),int(a[3]),len(a)>4 and a[4]=='macro'),'harden':lambda:harden(out(K,int(a[2]))),
     'harden-macro':lambda:harden_macro(K,int(a[2])),
     'mapped':lambda:mapped(K,int(a[2])),'report':lambda:report(K,int(a[2])),
     'top-prepare':lambda:top_prepare(K,float(a[2])),'top-harden':lambda:harden(TOP,20100),
     'top-mapped':lambda:top_mapped(K),'top-report':lambda:top_report(K)}[a[0]]()
