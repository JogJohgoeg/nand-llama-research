#!/usr/bin/env python3
"""Finish R105 without re-routing: restore the pinned routed run, simulate the
post-route netlist against ALL 1,000,883 source C vectors with a longer limit,
then package GDS/OAS exactly as norm_physical/flow.py publish() does.

Run 37561598894 routed and signed off DRC/LVS/XOR/antenna and all nine timing
corners; only its Icarus step hit the fixed 1,200 s subprocess limit.
"""
from pathlib import Path
import hashlib,importlib.util,io,json,os,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
OUT=ROOT/'build/norm_physical'
RUN=37561598894;HEAD='354ecdc39e1fe33d6627f5efc36cc4372db4b046'
PINS={'norm-layout-source':(11457391249,'8b8beea2'),'norm-layout-logs':(11459764699,'068597bf46b9476fcce27f8e83e97d603fefb9b89bae94ccfc939d390014f792')}
PDK_VERSION='8afc8346a57fe1ab7934ba5a6056ea8b43078e71'
sha=lambda b:hashlib.sha256(b).hexdigest()


def api(path):
    return subprocess.check_output(['gh','api','repos/JogJohgoeg/nand-llama-research/'+path],timeout=600)


def restore():
    run=json.loads(api('actions/runs/'+str(RUN)))
    assert run['head_sha']==HEAD and run['path']=='.github/workflows/norm_layout.yaml'
    jobs={j['name']:j for j in json.loads(api('actions/runs/'+str(RUN)+'/jobs'))['jobs']}
    steps={s['name']:s['conclusion'] for s in jobs['gds']['steps']}
    assert jobs['verify']['conclusion']=='success' and steps['Route R92 norm and full C16 cache on SKY130A']=='success'
    assert steps['Post-route standard-cell simulation against all source C vectors']=='failure'
    OUT.mkdir(parents=True,exist_ok=True);record={}
    for name,(aid,digest) in PINS.items():
        meta=json.loads(api('actions/artifacts/'+str(aid)))
        assert meta['name']==name and not meta['expired'] and meta['workflow_run']['id']==RUN
        assert meta['digest'].startswith('sha256:'+digest)
        raw=api('actions/artifacts/'+str(aid)+'/zip');assert len(raw)==meta['size_in_bytes'] and 'sha256:'+sha(raw)==meta['digest']
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in z.namelist():
                assert not n.startswith('/') and '..' not in Path(n).parts
                p=OUT/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(n))
        record[name]=dict(id=aid,digest=meta['digest'],bytes=len(raw))
    m=json.loads((OUT/'run/final/metrics.json').read_text())
    for k in ('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count','design__xor_difference__count',
              'route__drc_errors','antenna__violating__nets','antenna__violating__pins','timing__hold_vio__count','timing__setup_vio__count'):
        assert m[k]==0,k
    src=json.loads((OUT/'source.json').read_text());assert src['clocks']==1000883
    for n,f in src['files'].items():assert sha((OUT/n).read_bytes())==f['sha256'],n
    cfg=json.loads((OUT/'run/resolved.json').read_text());assert cfg['PDK_ROOT'].endswith('/'+PDK_VERSION)
    (OUT/'post_restore.json').write_text(json.dumps(dict(run=RUN,head=HEAD,artifacts=record,post_script_sha256=sha(Path(__file__).read_bytes())),indent=2)+'\n')
    print(json.dumps(record,indent=2))


def mapped():
    import verify
    original=verify.run
    verify.run=lambda cmd,seconds=1200:original(cmd,max(seconds,17400))
    verify.OUT=OUT;verify.mapped_check()


def publish():
    spec=importlib.util.spec_from_file_location('norm_flow',ROOT/'norm_physical/flow.py');flow=importlib.util.module_from_spec(spec);spec.loader.exec_module(flow)
    flow.publish()


if __name__=='__main__':
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and large gate simulation stay on Actions'
    {'restore':restore,'mapped':mapped,'publish':publish}[sys.argv[1]]()
