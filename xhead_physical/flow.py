#!/usr/bin/env python3
"""Harden the accepted R118 final-x -> token head (62,311 NAND / 3,778 LATCH), Actions only.

Source evidence is the pinned R118 artifact (run 37572617795): 31 actual-graph
transactions whose tokens equal C int_pick on int_run's own logits, 2,461,082
NAND/RTL clocks. Post-route simulation replays the first 152,400 of those clocks
(reset + two complete transactions: same x, two random words, tokens 23 and 25),
because a 2.46M-clock Icarus run of the routed netlist does not fit a runner.
"""
from pathlib import Path
import hashlib,io,json,os,shutil,subprocess,sys,zipfile
ROOT=Path(os.environ.get('H3_XHEAD_LAYOUT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
OUT=ROOT/'build/xhead_physical'
NAME='int_c16_x_head'
RUN=37572617795
ARTIFACT=11461394309
ZIP_SHA='33f95e2b80c1db90ed0a0e70d1ccdfec4873c4172b1b3a5a3f7baefd4b545d97'
GRAPH_SHA='b533750daaf41c86409a0f00dd1040b39c5bc643a83798fa768ce3d68b883887'
CLOCKS=2461082;PREFIX=152400
PREFIX_SHA='c6f7b48cd3e17a913c80bf2f6d37320044acd40f8377679e7b170188d84c7b56'
NI,NO=56,19
sha=lambda b:hashlib.sha256(b).hexdigest()


def api(path):
    return subprocess.check_output(['gh','api','repos/JogJohgoeg/nand-llama-research/'+path],timeout=300)


def prepare():
    from golden import Netlist
    from nand import metrics,flip_output
    from export import rtl
    import verify
    OUT.mkdir(parents=True,exist_ok=True)
    run_raw=api('actions/runs/'+str(RUN));run=json.loads(run_raw)
    assert run['conclusion']=='success' and run['head_sha'].startswith('08ee444') and run['path']=='.github/workflows/x_head_s.yaml'
    meta_raw=api('actions/artifacts/'+str(ARTIFACT));meta=json.loads(meta_raw)
    assert not meta['expired'] and meta['name']=='integer-x-head-s' and meta['digest']=='sha256:'+ZIP_SHA and meta['workflow_run']['id']==RUN
    raw=api('actions/artifacts/'+str(ARTIFACT)+'/zip');assert len(raw)==meta['size_in_bytes'] and sha(raw)==ZIP_SHA
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist();assert len(names)==len(set(names)) and all(not n.startswith('/') and '..' not in Path(n).parts for n in names)
        files={n:z.read(n) for n in names}
    r=json.loads(files['receipt.json']);v=r['verification']
    assert str(r['run_id'])==str(RUN) and v['status']=='pass' and v['clocks']==v['rtl_clocks']==CLOCKS and v['actual_rtl_fault_rejected']
    assert v['protocol']['completed']==31 and v['proofs']['source']['verdict']=='equivalent'
    for k,x in v['actual_fault_mismatches'].items():assert x>0,k
    for name,digest in r['sources'].items():assert sha((ROOT/name).read_bytes())==digest,name
    assert sha(files['joint.nl'])==r['metrics']['sha256']==GRAPH_SHA
    net=Netlist.decode(files['joint.nl'],NI,NO);assert metrics(net)==r['metrics']
    assert files['x_head_s.v'].decode()==rtl(net,'x_head_s')
    vec=files['vectors.txt'];assert sha(vec)==r['vector_sha256'] and vec.count(b'\n')==CLOCKS
    lines=vec.split(b'\n');prefix=b'\n'.join(lines[:PREFIX])+b'\n';assert sha(prefix)==PREFIX_SHA
    tokens=[];prev=0
    for l in lines[:PREFIX]:
        y=int(l.split()[1],16);d=y>>9&1
        if d and not prev:tokens.append(y&255)
        prev=d
    cases=json.loads(files['cases.json']);assert tokens==[cases[0]['token'],cases[1]['token']]
    payload={'receipt.json':files['receipt.json'],'slice.nl':files['joint.nl'],'slice.v':rtl(net,NAME).encode(),
             'tb.v':verify.testbench(NI,NO,NAME,str(OUT/'vectors.txt')).encode(),'vectors.txt':prefix,'cases.json':files['cases.json']}
    for name,b in payload.items():(OUT/name).write_bytes(b)
    for name,b in [('proof.zip',raw),('proof-run.json',run_raw),('proof-artifact.json',meta_raw)]:(OUT/name).write_bytes(b)
    source=dict(design=NAME,variant='R118 final residual x -> sampled token (norm[10]/A8 front end with shared DIV, E8 scan, top40, sampler)',
        metrics=r['metrics'],source_run=RUN,proof_artifact=ARTIFACT,proof_archive_sha256=ZIP_SHA,full_clocks=CLOCKS,
        layout_vector_prefix=dict(clocks=PREFIX,sha256=PREFIX_SHA,tokens=tokens,scope='reset + two complete transactions'),
        whole_model=False,adapter_sha256=sha(Path(__file__).read_bytes()),config_sha256=sha(Path(__file__).with_name('config.json').read_bytes()),
        constraints_sha256=sha((ROOT/'physical/constraints.sdc').read_bytes()),files={n:dict(bytes=len(b),sha256=sha(b)) for n,b in payload.items()})
    (OUT/'source.json').write_text(json.dumps(source,indent=2)+'\n')
    verify.OUT=OUT
    exe=verify.compile_rtl('source',OUT/'slice.v');verify.run([exe],1800)
    (OUT/'negative.v').write_text(rtl(flip_output(net),NAME))
    bad=verify.compile_rtl('negative',OUT/'negative.v');res=subprocess.run([str(bad)],capture_output=True,text=True,timeout=600)
    (OUT/'negative_verilator.log').write_text(res.stdout+res.stderr)
    assert res.returncode!=0 and 'C99 comparison failed' in res.stdout+res.stderr
    result=dict(status='pass',rtl_clocks=PREFIX,actual_RTL_mutation_rejected=True,vectors_sha256=PREFIX_SHA,
        rtl_sha256=source['files']['slice.v']['sha256'],proof_archive_sha256=ZIP_SHA,method='pinned R118 evidence, exact graph/RTL binding, renamed RTL replay of the layout prefix')
    (OUT/'adapter_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


def publish():
    import klayout.db as kdb
    from urllib.parse import quote
    run=OUT/'run';site=OUT/'site';site.mkdir(exist_ok=True)
    cfg=json.loads((run/'resolved.json').read_text());assert cfg['DESIGN_NAME']==NAME
    m=json.loads((run/'final/metrics.json').read_text())
    for key in ('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count','route__drc_errors','design__xor_difference__count'):
        assert m[key]==0,(key,m[key])
    source=json.loads((OUT/'source.json').read_text());adapter=json.loads((OUT/'adapter_verification.json').read_text())
    mapped=json.loads((OUT/'mapped_verification.json').read_text())
    assert adapter['status']==mapped['status']=='pass' and adapter['vectors_sha256']==mapped['vectors_sha256']==PREFIX_SHA
    gds_files=list((run/'final/gds').glob('*.gds'));assert len(gds_files)==1
    gds=site/(NAME+'.gds');shutil.copyfile(gds_files[0],gds)
    layout=kdb.Layout();layout.read(str(gds));assert len(layout.top_cells())==1 and layout.top_cell().name==NAME
    box=layout.top_cell().bbox();oas=site/(NAME+'.oas');layout.write(str(oas))
    again=kdb.Layout();again.read(str(oas));assert again.top_cell().name==NAME and again.top_cell().bbox()==box and again.cells()==layout.cells()
    url='https://jogjohgoeg.github.io/nand-llama-research/gds/'+NAME+'/'+NAME+'.oas'
    report=dict(status='DRC/LVS/XOR and post-route C vectors pass; inspect electrical metrics',design=NAME,
        viewer='https://gds-viewer.tinytapeout.com/?pdk=sky130A&model='+quote(url,safe=''),
        scope='R118 final residual x -> sampled token head; caller replays x and supplies the random word; no transformer layers',
        dbu_um=layout.dbu,bbox_dbu=[box.left,box.bottom,box.right,box.top],cell_definitions=layout.cells(),
        metrics=m,source=source,adapter=adapter,mapped_verification=mapped,resources=json.loads((OUT/'resources.json').read_text()),
        tool=dict(librelane='3.0.14',klayout=kdb.__version__,pdk=cfg['PDK']),github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'),
        files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p.read_bytes())) for p in (gds,oas)})
    (site/'layout.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['files'],indent=2))


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and all large gate simulation stay on Actions'
    action=sys.argv[1];os.chdir(ROOT)
    if action=='prepare':prepare()
    elif action=='publish':publish()
    elif action=='harden':
        import harden
        harden.OUT=OUT;sys.argv=[sys.argv[0],'--config',str(ROOT/'xhead_physical/config.json')];harden.main()
    elif action=='mapped':
        import verify
        original=verify.run;verify.run=lambda cmd,seconds=1200:original(cmd,max(seconds,9000))
        verify.OUT=OUT;verify.mapped_check()
    else:raise SystemExit('unknown action')


if __name__=='__main__':main()
