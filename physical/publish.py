#!/usr/bin/env python3
"""Actions-only conversion of signed-off, simulated GDS to an OAS Pages bundle."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote

assert os.getenv('GITHUB_ACTIONS')=='true','Layout handling stays on Actions'
import klayout.db as kdb

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'build/physical';RUN=OUT/'run';SITE=OUT/'site'
SITE.mkdir(exist_ok=True)
cfg=json.loads((RUN/'resolved.json').read_text())
design=cfg['DESIGN_NAME'];assert design in ('int_c16_slice','int_c16_ring_slice')
metrics_path=RUN/'final/metrics.json'
if not metrics_path.exists():metrics_path=RUN/'final/metrics-base.json'
metrics=json.loads(metrics_path.read_text())
required=['magic__drc_error__count','design__lvs_error__count','route__drc_errors']
for key in required:assert key in metrics and metrics[key]==0,(key,metrics.get(key))
for key in ['design__xor_difference__count','klayout__drc_error__count','magic__illegal_overlap__count']:
    if key in metrics:assert metrics[key]==0,(key,metrics[key])
mapped=json.loads((OUT/'mapped_verification.json').read_text());assert mapped['status']=='pass'
files=list((RUN/'final/gds').glob('*.gds'));assert len(files)==1,files
gds=SITE/(design+'.gds');shutil.copyfile(files[0],gds)
layout=kdb.Layout();layout.read(str(gds));assert len(layout.top_cells())==1
top=layout.top_cell();assert top.name==design
oas=SITE/(design+'.oas');layout.write(str(oas))
check=kdb.Layout();check.read(str(oas));assert check.top_cell().bbox()==top.bbox() and check.cells()==layout.cells()
model='https://jogjohgoeg.github.io/nand-llama-research/gds/'+design+'.oas'
viewer='https://gds-viewer.tinytapeout.com/?pdk=sky130A&model='+quote(model,safe='')
report=dict(status='DRC/LVS pass and post-route functional simulation pass',viewer=viewer,
            design=top.name,dbu_um=layout.dbu,bbox_dbu=[top.bbox().left,top.bbox().bottom,top.bbox().right,top.bbox().top],
            cell_definitions=layout.cells(),metrics=metrics,source=json.loads((OUT/'source.json').read_text()),mapped_verification=mapped,
            tool=dict(librelane='3.0.14',klayout=kdb.__version__,pdk_root=cfg['PDK_ROOT'],pdk=cfg['PDK']),
            github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'),
            files={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (gds,oas)})
(SITE/'layout.json').write_text(json.dumps(report,indent=2)+'\n')
(SITE/'index.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>INT-C16 真版图</title><h1>INT-C16 代表切片</h1><p>第0层真权重 + 一个 C16 头的 KV + 整数算子。此处展示实际布局布线结果；整机尚未完成。</p><p><a href="'+viewer+'">看芯片版图（3D）</a> · <a href="'+design+'.gds">GDS</a> · <a href="'+design+'.oas">OAS</a> · <a href="layout.json">面积与 DRC/LVS 记录</a> · <a href="../">整数生成演示</a></p></html>\n')
print(json.dumps({k:report[k] for k in ('status','viewer','files')},indent=2))
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
    f.write('Real SKY130 pilot GDS/OAS bundle is ready; copy the gds-site artifact into docs/gds before the next Pages deployment.\n\n[Viewer after publication]('+viewer+')\n')
