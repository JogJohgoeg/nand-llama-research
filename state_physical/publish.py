#!/usr/bin/env python3
"""Package an actual DRC/LVS-checked state leaf; electrical metrics stay visible."""
import hashlib,json,os,shutil
from pathlib import Path
from urllib.parse import quote
assert os.getenv('GITHUB_ACTIONS')=='true','Layout handling stays on Actions'
import klayout.db as kdb
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'build/state_physical';RUN=OUT/'run';SITE=OUT/'site'
SITE.mkdir(exist_ok=True)
cfg=json.loads((RUN/'resolved.json').read_text());name=cfg['DESIGN_NAME'];assert name=='int_c16_prefix_leaf'
p=RUN/'final/metrics.json'
if not p.exists():p=RUN/'final/metrics-base.json'
metrics=json.loads(p.read_text())
for key in ('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count','route__drc_errors','design__xor_difference__count'):
    assert metrics[key]==0,(key,metrics[key])
if 'magic__illegal_overlap__count' in metrics:assert metrics['magic__illegal_overlap__count']==0
mapped=json.loads((OUT/'mapped_verification.json').read_text());assert mapped['status']=='pass'
source=json.loads((OUT/'receipt.json').read_text());assert source['verification']['status']=='pass'
assert mapped['vectors_sha256']==source['vector_sha256']
files=list((RUN/'final/gds').glob('*.gds'));assert len(files)==1
gds=SITE/(name+'.gds');shutil.copyfile(files[0],gds)
layout=kdb.Layout();layout.read(str(gds));assert len(layout.top_cells())==1 and layout.top_cell().name==name
box=layout.top_cell().bbox();oas=SITE/(name+'.oas');layout.write(str(oas))
check=kdb.Layout();check.read(str(oas))
assert check.top_cell().name==name and check.top_cell().bbox()==box and check.cells()==layout.cells()
url='https://jogjohgoeg.github.io/nand-llama-research/gds/'+name+'/'+name+'.oas'
viewer='https://gds-viewer.tinytapeout.com/?pdk=sky130A&model='+quote(url,safe='')
report=dict(status='DRC/LVS/XOR and post-route C99 storage checks pass; inspect raw electrical metrics',design=name,viewer=viewer,
    scope='64x20-bit held prefix-state leaf, one of32 width slices with a shared external cursor; no model-value change; not full prefix controller or full language model',
    dbu_um=layout.dbu,bbox_dbu=[box.left,box.bottom,box.right,box.top],cell_definitions=layout.cells(),
    metrics=metrics,source=source,mapped_verification=mapped,resources=json.loads((OUT/'resources.json').read_text()),
    tool=dict(librelane='3.0.14',klayout=kdb.__version__,pdk=cfg['PDK']),github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'),
    files={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (gds,oas)})
(SITE/'layout.json').write_text(json.dumps(report,indent=2)+'\n')
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
    f.write('Actual held prefix-state leaf GDS/OAS is ready in state-gds-site; the unified Pages workflow consumes successful main runs.\n\n[Viewer after publication]('+viewer+')\n')
print(json.dumps(dict(status=report['status'],viewer=viewer,files=report['files']),indent=2))
