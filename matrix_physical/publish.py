#!/usr/bin/env python3
"""Convert only a signed-off, C-checked real Q-matrix GDS into an OAS bundle."""
import hashlib,json,os,shutil
from pathlib import Path
from urllib.parse import quote
assert os.getenv('GITHUB_ACTIONS')=='true','Layout handling stays on Actions'
import klayout.db as kdb
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'build/matrix_physical';RUN=OUT/'run';SITE=OUT/'site'
SITE.mkdir(exist_ok=True)
cfg=json.loads((RUN/'resolved.json').read_text());name=cfg['DESIGN_NAME'];assert name=='int_c16_q_matrix'
p=RUN/'final/metrics.json'
if not p.exists():p=RUN/'final/metrics-base.json'
metrics=json.loads(p.read_text())
for key in ['magic__drc_error__count','design__lvs_error__count','route__drc_errors']:
    assert key in metrics and metrics[key]==0,(key,metrics.get(key))
for key in ['design__xor_difference__count','klayout__drc_error__count','magic__illegal_overlap__count']:
    if key in metrics:assert metrics[key]==0,(key,metrics[key])
mapped=json.loads((OUT/'mapped_verification.json').read_text());assert mapped['status']=='pass'
source=json.loads((OUT/'receipt.json').read_text());assert source['verification']['status']=='pass'
assert mapped['vectors_sha256']==source['vector_sha256']
files=list((RUN/'final/gds').glob('*.gds'));assert len(files)==1
gds=SITE/(name+'.gds');shutil.copyfile(files[0],gds)
layout=kdb.Layout();layout.read(str(gds));assert len(layout.top_cells())==1 and layout.top_cell().name==name
top=layout.top_cell();oas=SITE/(name+'.oas');layout.write(str(oas))
check=kdb.Layout();check.read(str(oas));assert check.top_cell().name==name
assert check.top_cell().bbox()==top.bbox() and check.cells()==layout.cells()
url='https://jogjohgoeg.github.io/nand-llama-research/gds/'+name+'.oas'
viewer='https://gds-viewer.tinytapeout.com/?pdk=sky130A&model='+quote(url,safe='')
report=dict(status='DRC/LVS and post-route C99 functional checks pass',design=name,viewer=viewer,
    scope='real layer0 Q128x128 ternary matrix, DOT32, exact integer scale,1024 A8 state bits and scalar handshakes; not full-model inference',
    dbu_um=layout.dbu,bbox_dbu=[top.bbox().left,top.bbox().bottom,top.bbox().right,top.bbox().top],cell_definitions=layout.cells(),
    metrics=metrics,source=source,mapped_verification=mapped,resources=json.loads((OUT/'resources.json').read_text()),
    tool=dict(librelane='3.0.14',klayout=kdb.__version__,pdk=cfg['PDK']),github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'),
    files={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (gds,oas)})
(SITE/'layout.json').write_text(json.dumps(report,indent=2)+'\n')
(SITE/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>INT-C16 · Q 矩阵真版图</title><main style="max-width:760px;margin:40px auto;padding:20px;font:17px/1.8 system-ui"><h1>看芯片版图</h1><p>真实第 0 层 Q 矩阵：128×128 三值权重、DOT32、整数缩放及激活状态。此处是已布局布线的代表硬核，尚不是整颗语言模型芯片。</p><p><a href="'+viewer+'">打开 3D 查看器</a> · <a href="'+name+'.gds">下载 GDS</a> · <a href="'+name+'.oas">下载 OAS</a> · <a href="layout.json">面积及 DRC/LVS 证据</a></p><p><a href="../">返回整数生成演示</a></p></main></html>\n')
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
    f.write('Signed-off Q-matrix GDS/OAS is ready in matrix-gds-site. Audit and copy it into docs/gds, add the demo entry, then use the existing Pages workflow.\n\n[Viewer after publication]('+viewer+')\n')
print(json.dumps(dict(status=report['status'],viewer=viewer,files=report['files']),indent=2))
