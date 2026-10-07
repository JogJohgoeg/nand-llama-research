#!/usr/bin/env python3
"""Harden the already proved R92 integer norm/A8/C16 cache, on Actions only."""
from pathlib import Path
import hashlib,io,json,os,re,shutil,subprocess,sys,zipfile

ROOT=Path(os.environ.get('H3_NORM_LAYOUT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
OUT=ROOT/'build/norm_physical'
NAME='int_c16_norm_cache'
RUN=37550016136
HEAD='71f7212d8d00aa275038524b53f46b8323de2dc5'
ARTIFACT=11452117630
ZIP_SHA='d2257c2faff347ce8e5043189fad5bae9888f1b3e04f2c0e640e64daeda415f3'
GRAPH_SHA='0ab450f0ccac0389394fc071f0ff26b3aa84b862fcb57cede47db9373ab6587a'
CLOCKS=1000883
sha=lambda b:hashlib.sha256(b).hexdigest()


def unpack(raw):
    assert len(raw)==5305630 and sha(raw)==ZIP_SHA,'pinned proof archive differs'
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist()
        assert len(names)==len(set(names))
        assert all(not n.startswith('/') and '..' not in Path(n).parts for n in names)
        return {n:z.read(n) for n in names}


def stage_verified(files,target):
    from golden import Netlist
    from nand import metrics
    from export import rtl
    import verify
    r=json.loads(files['receipt.json']);v=r['verification']
    assert str(r['run_id'])==str(RUN) and r['revision']==HEAD
    assert v['status']=='pass' and v['clocks']==v['rtl_clocks']==r['expected']['clocks']==CLOCKS
    assert v['nand_mismatches']==0 and v['actual_RTL_mutation_rejected']
    for k in ('actual_cached_data_fault_mismatches','actual_removed_rotation_mismatches',
              'actual_wrong_divider_owner_mismatches','actual_wrong_restart_phase_mismatches'):
        assert v[k]>0,k
    proofs=[(r[k],good,bad) for k,good,bad in (
        ('root_control_proof','root_control.cec.log','root_control.negative.log'),
        ('divider_proof','div.cec.log','div.negative.log'),
        ('projection_proof','projection/div.cec.log','projection/div.negative.log'))]
    proofs += [(v['connector_proof'],'connector.cec.log','negative.cec.log'),
               (v['shared_slot_proof'],'port.cec.log','port.negative.log')]
    for p,good,bad in proofs:
        assert p['proof']['verdict']=='equivalent' and p['negative']['verdict']=='different'
        assert b'Networks are equivalent' in files[good] and b'NOT EQUIVALENT' in files[bad]
    assert v['weight_proof']['status']=='pass'
    w=v['weight_proof']['cases']['norm0']
    assert w['cec']['verdict']=='equivalent' and w['negative']['verdict']=='different'
    assert b'Networks are equivalent' in files['weights/norm0.cec.log']
    assert b'NOT EQUIVALENT' in files['weights/norm0.negative.log']
    assert b'C99 comparison failed' in files['negative_verilator.log']
    for name,digest in r['sources'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert sha((ROOT/name).read_bytes())==digest,name
    assert sha(files['fill.nl'])==r['metrics']['sha256']==GRAPH_SHA
    assert sha(files['vectors.txt'])==r['vector_sha256']
    assert files['vectors.txt'].count(b'\n')==CLOCKS
    assert sha(files['cases.json'])==r['cases_sha256']
    net=Netlist.decode(files['fill.nl'],40,40)
    assert metrics(net)==r['metrics'] and (net.n_state,len(net.records)-net.n_state)==(18367,12873)
    assert files['fill.v'].decode()==rtl(net,'norm_cache_fill'),'source RTL does not encode pinned NAND bytes'
    old=files['tb.v'].decode();paths=re.findall(r'file=\$fopen\("([^"\n]+)","r"\)',old)
    assert len(paths)==1 and Path(paths[0]).name=='vectors.txt'
    assert old==verify.testbench(40,40,'norm_cache_fill',paths[0])
    payload={'receipt.json':files['receipt.json'],'slice.nl':files['fill.nl'],
             'slice.v':rtl(net,NAME).encode(),'tb.v':verify.testbench(40,40,NAME,str(target/'vectors.txt')).encode(),
             'vectors.txt':files['vectors.txt'],'cases.json':files['cases.json']}
    target.mkdir(parents=True,exist_ok=True)
    for name,raw in payload.items():
        (target/name).write_bytes(raw);assert (target/name).read_bytes()==raw
    source=dict(design=NAME,variant='R92 exact-root norm0, A8 quantization and C16 normalized-input cache',
                metrics=r['metrics'],clocks=CLOCKS,source_run=RUN,source_revision=HEAD,
                proof_artifact=ARTIFACT,proof_archive_sha256=ZIP_SHA,
                source_hash_checks=len(r['sources']),whole_model=False,
                scope='External X is replayed identically for384 scalar accepts; no QKV or whole-token controller. Integer values unchanged.',
                adapter_sha256=sha(Path(__file__).read_bytes()),
                config_sha256=sha(Path(__file__).with_name('config.json').read_bytes()),
                constraints_sha256=sha((ROOT/'physical/constraints.sdc').read_bytes()),
                files={n:dict(bytes=len(b),sha256=sha(b)) for n,b in payload.items()})
    (target/'source.json').write_text(json.dumps(source,indent=2)+'\n')
    return net,source


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    def api(path):
        raw=subprocess.check_output(['gh','api','repos/JogJohgoeg/nand-llama-research/'+path],timeout=60)
        return raw
    run_raw=api('actions/runs/'+str(RUN));run=json.loads(run_raw)
    assert run['conclusion']=='success' and run['head_sha']==HEAD and run['head_branch']=='main'
    assert run['path']=='.github/workflows/norm_cache_root.yaml'
    artifact_raw=api('actions/artifacts/'+str(ARTIFACT));artifact=json.loads(artifact_raw)
    assert not artifact['expired'],'pinned proof expired: regenerate and audit R92, then update its pin'
    assert artifact['name']=='integer-norm-cache-root' and artifact['digest']=='sha256:'+ZIP_SHA
    assert artifact['workflow_run']['id']==RUN and artifact['workflow_run']['head_sha']==HEAD
    raw=api('actions/artifacts/'+str(ARTIFACT)+'/zip')
    assert len(raw)==artifact['size_in_bytes']
    files=unpack(raw);net,source=stage_verified(files,OUT)
    for name,b in [('proof.zip',raw),('proof-run.json',run_raw),('proof-artifact.json',artifact_raw)]:
        (OUT/name).write_bytes(b)
    import verify
    from export import rtl
    from nand import flip_output
    verify.OUT=OUT
    executable=verify.compile_rtl('source',OUT/'slice.v')
    verify.run([executable],600)
    (OUT/'negative.v').write_text(rtl(flip_output(net),NAME))
    bad_exe=verify.compile_rtl('negative',OUT/'negative.v')
    bad=subprocess.run([str(bad_exe)],capture_output=True,text=True,timeout=120)
    (OUT/'negative_verilator.log').write_text(bad.stdout+bad.stderr)
    assert bad.returncode!=0 and 'C99 comparison failed' in bad.stdout+bad.stderr
    result=dict(status='pass',rtl_clocks=CLOCKS,actual_RTL_mutation_rejected=True,
                vectors_sha256=source['files']['vectors.txt']['sha256'],
                rtl_sha256=source['files']['slice.v']['sha256'],
                proof_archive_sha256=ZIP_SHA,method='pinned full NAND/C and formal evidence, exact graph/RTL binding, renamed RTL replay')
    (OUT/'adapter_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


def publish():
    import klayout.db as kdb
    from urllib.parse import quote
    run=OUT/'run';site=OUT/'site';site.mkdir(exist_ok=True)
    cfg=json.loads((run/'resolved.json').read_text());assert cfg['DESIGN_NAME']==NAME
    p=run/'final/metrics.json'
    if not p.exists():p=run/'final/metrics-base.json'
    m=json.loads(p.read_text())
    for key in ('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count',
                'route__drc_errors','design__xor_difference__count'):
        assert m[key]==0,(key,m[key])
    if 'magic__illegal_overlap__count' in m:assert m['magic__illegal_overlap__count']==0
    source=json.loads((OUT/'receipt.json').read_text())
    adapter=json.loads((OUT/'adapter_verification.json').read_text())
    mapped=json.loads((OUT/'mapped_verification.json').read_text())
    assert source['verification']['status']==adapter['status']==mapped['status']=='pass'
    assert source['vector_sha256']==adapter['vectors_sha256']==mapped['vectors_sha256']
    assert adapter['rtl_clocks']==source['expected']['clocks']==CLOCKS
    gds_files=list((run/'final/gds').glob('*.gds'));assert len(gds_files)==1
    gds=site/(NAME+'.gds');shutil.copyfile(gds_files[0],gds)
    layout=kdb.Layout();layout.read(str(gds));assert len(layout.top_cells())==1 and layout.top_cell().name==NAME
    box=layout.top_cell().bbox();oas=site/(NAME+'.oas');layout.write(str(oas))
    again=kdb.Layout();again.read(str(oas))
    assert again.top_cell().name==NAME and again.top_cell().bbox()==box and again.cells()==layout.cells()
    url='https://jogjohgoeg.github.io/nand-llama-research/gds/'+NAME+'/'+NAME+'.oas'
    report=dict(status='DRC/LVS/XOR and post-route C vectors pass; inspect electrical metrics',design=NAME,
        viewer='https://gds-viewer.tinytapeout.com/?pdk=sky130A&model='+quote(url,safe=''),
        scope='R92 integer norm0/A8/cache macro, externally replayed X, no full language model',
        dbu_um=layout.dbu,bbox_dbu=[box.left,box.bottom,box.right,box.top],cell_definitions=layout.cells(),
        metrics=m,source=source,adapter=adapter,mapped_verification=mapped,
        resources=json.loads((OUT/'resources.json').read_text()),
        tool=dict(librelane='3.0.14',klayout=kdb.__version__,pdk=cfg['PDK']),
        github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'),
        files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p.read_bytes())) for p in (gds,oas)})
    (site/'layout.json').write_text(json.dumps(report,indent=2)+'\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
        f.write('Actual R92 norm/cache GDS and OAS are in norm-gds-site. Audit before adding this third design to the viewer.\n')
    print(json.dumps(report['files'],indent=2))


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and all large gate simulation stay on Actions'
    assert len(sys.argv)==2 and sys.argv[1] in ('prepare','harden','mapped','publish')
    action=sys.argv[1];os.chdir(ROOT)
    if action=='prepare':prepare()
    elif action=='publish':publish()
    elif action=='harden':
        import harden
        harden.OUT=OUT;sys.argv=[sys.argv[0],'--config',str(ROOT/'norm_physical/config.json')];harden.main()
    else:
        import verify
        verify.OUT=OUT;verify.mapped_check()


if __name__=='__main__':main()
