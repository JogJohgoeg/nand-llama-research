#!/usr/bin/env python3
"""Package the UNSIGNED whole-machine preview layout for the Pages viewer (Actions only).

The layout itself was produced on the m64 host (rootless LibreLane 3.0.14 rootfs, synthesis ->
floorplan -> PDN -> global/detailed placement; no CTS, no routing, no DRC/LVS/STA signoff) from the
R133 whole-machine graph. Its GDS/OAS and the flow's metrics are attached to a GitHub release;
pin.json binds every file by size and SHA-256. This script only downloads, checks and labels.
"""
from pathlib import Path
import hashlib,json,os,subprocess
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve().parent
OUT=ROOT/'build/machine_view';SITE=OUT/'site'
sha=lambda b:hashlib.sha256(b).hexdigest()


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import klayout.db as kdb
    pin=json.loads((HERE/'pin.json').read_text());name=pin['design'];SITE.mkdir(parents=True,exist_ok=True)
    subprocess.run(['gh','release','download',pin['release'],'--repo','JogJohgoeg/nand-llama-research','--dir',str(OUT),'--clobber'],check=True,timeout=1800)
    for f,e in pin['files'].items():
        b=(OUT/f).read_bytes();assert len(b)==e['bytes'] and sha(b)==e['sha256'],f
    for ext in ('gds','oas'):(SITE/f'{name}.{ext}').write_bytes((OUT/f'{name}.{ext}').read_bytes())
    lay=kdb.Layout();lay.read(str(SITE/f'{name}.oas'));assert len(lay.top_cells())==1 and lay.top_cell().name==name
    box=lay.top_cell().bbox();metrics=json.loads((OUT/'metrics.json').read_text())
    # Same counting as viewer3d convert(): arrays exploded, then top-level instances (cells + PDN vias).
    for cell in lay.each_cell():
        for inst in list(cell.each_inst()):inst.explode()
    count=sum(1 for _ in lay.top_cell().each_inst())
    report=dict(design=name,unsigned=True,
        status='UNSIGNED PREVIEW: synthesis, floorplan, power grid and placement only; no clock tree, no routing, no DRC/LVS/STA',
        scope=pin['scope'],source=pin['source'],flow=pin['flow'],dbu_um=lay.dbu,bbox_dbu=[box.left,box.bottom,box.right,box.top],
        metrics=metrics,gds_instance_count=count,github_run=os.getenv('GITHUB_RUN_ID'),github_sha=os.getenv('GITHUB_SHA'),
        files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p.read_bytes())) for p in (SITE/f'{name}.gds',SITE/f'{name}.oas')})
    (SITE/'layout.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='metrics'},indent=2))


if __name__=='__main__':main()
