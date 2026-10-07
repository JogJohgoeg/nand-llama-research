#!/usr/bin/env python3
"""Actions-only signoff of E8 sub-table macros cut from the pinned R118 head.

`prepare K k` binds the same pinned R118 proof artifact as xhead_physical/flow.py (run, artifact,
ZIP and graph SHA), cuts sub-table k of K with subtable.py, replays all 512 addresses on the RTL
(Verilator) plus an inverted-word negative, and writes the LibreLane config.
`harden`, `mapped` (post-route standard-cell replay of the same exhaustive vectors) and `report`
follow the existing per-block flows. Purpose: check that the m64 GRT estimate (K=32 at 60%
utilisation routes) holds through detailed routing, DRC/LVS/XOR and nine-corner STA.
"""
from pathlib import Path
import hashlib,importlib.util,io,json,os,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT),str(ROOT/'hier_physical')]
sha=lambda b:hashlib.sha256(b).hexdigest()
spec=importlib.util.spec_from_file_location('xhead_flow',ROOT/'xhead_physical/flow.py');X=importlib.util.module_from_spec(spec);spec.loader.exec_module(X)


def out(K,k):return ROOT/f'build/hier_physical/k{K}_{k}'


def prepare(K,k,util):
    from golden import Netlist
    from nand import metrics
    import subtable,verify
    raw=X.api('actions/artifacts/'+str(X.ARTIFACT)+'/zip');assert sha(raw)==X.ZIP_SHA
    meta=json.loads(X.api('actions/artifacts/'+str(X.ARTIFACT)));assert meta['workflow_run']['id']==X.RUN and meta['digest']=='sha256:'+X.ZIP_SHA
    with zipfile.ZipFile(io.BytesIO(raw)) as z:nl=z.read('joint.nl');r=json.loads(z.read('receipt.json'))
    assert sha(nl)==X.GRAPH_SHA==r['metrics']['sha256'] and r['verification']['status']=='pass'
    net=Netlist.decode(nl,X.NI,X.NO);assert metrics(net)==r['metrics']
    o=out(K,k);name=f'int_c16_e8_k{K}_{k}'
    rep=subtable.build(net,K,k,name,o);rep.update(source_run=X.RUN,proof_artifact=X.ARTIFACT,proof_archive_sha256=X.ZIP_SHA,graph_sha256=X.GRAPH_SHA)
    (o/'subtable.json').write_text(json.dumps(rep,indent=2)+'\n')
    verify.OUT=o;nw=rep['words']
    (o/'tb.v').write_text(verify.testbench(9,nw,name,str(o/'vectors.txt')))
    exe=verify.compile_rtl('source',o/'slice.v');verify.run([exe],600)
    src=(o/'slice.v').read_text();bad=src.replace('assign dout[0]=w','assign dout[0]=~w',1);assert bad!=src
    (o/'negative.v').write_text(bad);bexe=verify.compile_rtl('negative',o/'negative.v')
    res=subprocess.run([str(bexe)],capture_output=True,text=True,timeout=600);assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    (o/'adapter_verification.json').write_text(json.dumps(dict(status='pass',rtl_clocks=1024,addresses='all 512, two passes',
        actual_RTL_mutation_rejected=True,vectors_sha256=rep['files']['vectors.txt']['sha256']),indent=2)+'\n')
    base=json.loads((ROOT/'xhead_physical/config.json').read_text())
    cfg=dict(base,DESIGN_NAME=name,VERILOG_FILES=['dir::slice.v'],FP_CORE_UTIL=util,PL_TARGET_DENSITY_PCT=util+5,
             PNR_SDC_FILE='dir::'+os.path.relpath(ROOT/'physical/constraints.sdc',o),SIGNOFF_SDC_FILE='dir::'+os.path.relpath(ROOT/'physical/constraints.sdc',o))
    for key in ('PL_MAX_DISPLACEMENT_X','PL_MAX_DISPLACEMENT_Y'):cfg.pop(key,None)
    (o/'config.json').write_text(json.dumps(cfg,indent=2)+'\n');print(json.dumps({a:b for a,b in rep.items() if a!='word_records'},indent=2))


def harden(K,k):
    import harden as H
    H.OUT=out(K,k);sys.argv=[sys.argv[0],'--config',str(out(K,k)/'config.json')];H.main()


def mapped(K,k):
    import verify
    verify.OUT=out(K,k);verify.mapped_check()


def report(K,k):
    o=out(K,k);m=json.loads((o/'run/final/metrics.json').read_text())
    keys=('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count','route__drc_errors','design__xor_difference__count')
    rep=dict(design=f'int_c16_e8_k{K}_{k}',signoff={x:m.get(x) for x in keys},
             die_area_um2=m.get('design__die__area'),core_area_um2=m.get('design__core__area'),
             stdcell_area_um2=m.get('design__instance__area__stdcell'),utilization=m.get('design__instance__utilization'),
             wirelength_um=m.get('route__wirelength'),antenna_nets=m.get('antenna__violating__nets'),
             setup_vio=m.get('timing__setup_vio__count'),hold_vio=m.get('timing__hold_vio__count'),
             max_slew_vio=m.get('design__max_slew_violation__count'),max_cap_vio=m.get('design__max_cap_violation__count'),
             subtable=json.loads((o/'subtable.json').read_text()),mapped=json.loads((o/'mapped_verification.json').read_text()),
             resources=json.loads((o/'resources.json').read_text()),github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'))
    rep['subtable'].pop('word_records',None)
    (o/'hier_report.json').write_text(json.dumps(rep,indent=2)+'\n');print(json.dumps({a:b for a,b in rep.items() if a not in ('subtable','resources')},indent=2))
    for x in keys:assert m[x]==0,(x,m[x])


if __name__=='__main__':
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and gate simulation stay on Actions'
    os.chdir(ROOT);a=sys.argv[1:];K,k=int(a[1]),int(a[2])
    {'prepare':lambda:prepare(K,k,int(a[3])),'harden':lambda:harden(K,k),'mapped':lambda:mapped(K,k),'report':lambda:report(K,k)}[a[0]]()
