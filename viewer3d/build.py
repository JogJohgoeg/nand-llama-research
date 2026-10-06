#!/usr/bin/env python3
"""Actions-only layout conversion and complete Pages assembly.

Restore published, hash-checked layouts before overlaying new successful
main-branch artifacts. A demo-only deployment therefore cannot erase GDS.
No git writes; no conversion on the developer machine.
"""
from collections import Counter
import ast
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = 'JogJohgoeg/nand-llama-research'
BASE = 'https://jogjohgoeg.github.io/nand-llama-research/'
SITE = ROOT / 'build/pages'
OUT = SITE / 'gds'
WORK = ROOT / 'build/viewer'
FLOW = {'matrix-gds-site': '.github/workflows/matrix_layout.yaml',
        'state-gds-site': '.github/workflows/state_layout.yaml',
        'gds-site': '.github/workflows/layout.yaml'}
LAYERS = ['substrate', 'nwell', 'diff', 'poly', 'licon', 'li1', 'mcon',
          'met1', 'via', 'met2', 'via2', 'met3', 'via3', 'met4', 'via4', 'met5']
sha = lambda b: hashlib.sha256(b).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    assert not p.is_absolute() and '..' not in p.parts and '\\' not in name
    assert re.fullmatch(r'[a-zA-Z0-9_./-]+', name), name
    return name


def record(p):
    return {'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())}


def check(p, expected):
    assert record(p) == expected, str(p)


def fetch(url, dst, missing=False):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(Request(url, headers={'User-Agent': 'nand-llama-layout',
                                          'Cache-Control': 'no-cache'}), timeout=60) as r:
            with dst.open('wb') as f:
                shutil.copyfileobj(r, f)
    except HTTPError as e:
        if e.code == 404 and missing:
            return False
        raise
    return True


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', 'repos/' + REPO + '/' + path], timeout=60))


def restore():
    p = WORK / 'published.json'
    if not fetch(BASE + 'gds/catalog.json?run=' + os.environ['GITHUB_RUN_ID'], p, missing=True):
        return {}
    catalog = json.loads(p.read_text())
    assert catalog['schema'] == 1
    for name, expected in catalog['files'].items():
        safe_name(name)
        f = OUT / name
        fetch(BASE + 'gds/' + name + '?sha=' + expected['sha256'], f)
        check(f, expected)
    return catalog['designs']


def sources():
    """Pick only successful main runs of the trusted layout workflows."""
    for name, workflow in FLOW.items():
        items = api('actions/artifacts?name=' + name + '&per_page=30')['artifacts']
        for a in sorted(items, key=lambda a: a['id'], reverse=True):
            wr = a['workflow_run']
            if a['expired'] or wr['head_branch'] != 'main':
                continue
            run = api('actions/runs/' + str(wr['id']))
            if run['conclusion'] != 'success':
                continue
            assert run['path'] == workflow and run['head_repository']['full_name'] == REPO
            assert run['event'] in ('push', 'workflow_dispatch')
            assert run['head_sha'] == wr['head_sha']
            yield a
            break


def download(a):
    z = WORK / (str(a['id']) + '.zip')
    with z.open('wb') as f:
        subprocess.run(['gh', 'api', 'repos/' + REPO + '/actions/artifacts/' + str(a['id']) + '/zip'],
                       stdout=f, check=True, timeout=180)
    assert z.stat().st_size == a['size_in_bytes']
    assert sha(z.read_bytes()) == a['digest'].removeprefix('sha256:')
    dest = WORK / str(a['id'])
    with zipfile.ZipFile(z) as archive:
        assert sum(i.file_size for i in archive.infolist()) < 2_000_000_000
        for item in archive.infolist():
            safe_name(item.filename)
            assert not (item.external_attr >> 16) & 0o170000 == 0o120000
        archive.extractall(dest)
    report = json.loads((dest / 'layout.json').read_text())
    name = report['design']
    assert re.fullmatch(r'int_c16_[a-z_]+', name)
    assert report['github_sha'] == a['workflow_run']['head_sha']
    assert int(report['github_run']) == a['workflow_run']['id']
    for key in ('magic__drc_error__count', 'klayout__drc_error__count',
                'design__lvs_error__count', 'design__xor_difference__count', 'route__drc_errors'):
        assert report['metrics'][key] == 0, key
    assert report['mapped_verification']['status'] == 'pass'
    for ext in ('gds', 'oas'):
        file = name + '.' + ext
        check(dest / file, report['files'][file])
    return dest, report


def glb_info(path, layer):
    with path.open('rb') as f:
        magic, version, size = struct.unpack('<4sII', f.read(12))
        assert magic == b'glTF' and version == 2 and size == path.stat().st_size
        n, kind = struct.unpack('<II', f.read(8))
        assert kind == 0x4E4F534A
        doc = json.loads(f.read(n))
    materials = doc.get('materials', [])
    assert materials and all(m.get('name') == layer for m in materials), layer
    assert all('baseColorTexture' not in m.get('pbrMetallicRoughness', {}) for m in materials)
    assert 'EXT_meshopt_compression' in doc.get('extensionsUsed', [])
    return dict(file=path.name, **record(path),
                color=materials[0]['pbrMetallicRoughness']['baseColorFactor'],
                decoded_buffer_bytes=sum(b['byteLength'] for b in doc['buffers']),
                mesh_count=len(doc['meshes']), node_count=len(doc['nodes']))


def convert(directory, report, builder):
    # Keep heavy geometry modules unavailable to local validation/imports.
    assert os.getenv('GITHUB_ACTIONS') == 'true', 'Conversion stays on Actions'
    import klayout.db as kdb
    name = report['design']
    lay = kdb.Layout()
    lay.read(str(directory / (name + '.oas')))
    assert len(lay.top_cells()) == 1 and lay.top_cell().name == name
    top = lay.top_cell()
    box = top.bbox()
    assert [box.left, box.bottom, box.right, box.top] == report['bbox_dbu']
    # gdspy's template handles single references; expand arrays without flattening cells.
    for cell in lay.each_cell():
        for inst in list(cell.each_inst()):
            inst.explode()
    inst = []
    for item in top.each_inst():
        b = item.bbox()
        inst.append([item.cell.name.removeprefix('sky130_fd_sc_hd__')] +
                    [round(v * lay.dbu, 6) for v in (b.left, b.bottom, b.right, b.top)])
    counts = Counter(c[0] for c in inst)
    assert len(inst) == report['metrics']['design__instance__count']
    (directory / 'cells.json').write_text(json.dumps(dict(inst=inst), separators=(',', ':')) + '\n')
    gds = WORK / (name + '.gds')
    lay.write(str(gds))
    layers = []
    tree = ast.parse((HERE / 'gds2gltf.py').read_text())
    stack = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                  and any(isinstance(t, ast.Name) and t.id == 'layerstack' for t in n.targets)))
    colors = {v['name']: v['color'] for v in stack.values()}
    assert len({tuple(v) for v in colors.values()}) == len(colors)
    tool = ROOT / 'build/viewer-tools/node_modules/.bin/gltf-transform'
    for layer in LAYERS:
        begin = time.monotonic()
        with (WORK / (name + '-' + layer + '.log')).open('w') as log:
            subprocess.run([sys.executable, str(HERE / 'gds2gltf.py'), str(gds), layer],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            gltf = Path(str(gds) + '.' + layer + '.gltf')
            # Some physical layers are legitimately empty (e.g. no met5 signals).
            doc = json.loads(gltf.read_text())
            if not doc.get('meshes'):
                gltf.unlink()
                continue
            del doc
            glb = directory / (layer + '.glb')
            cmd = [str(tool), 'optimize', str(gltf), str(glb), '--compress', 'meshopt',
                   '--palette', 'false', '--texture-compress', 'false', '--simplify', 'false']
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            gltf.unlink()
        info = glb_info(glb, layer)
        assert all(abs(a - b) < 1e-7 for a, b in zip(info['color'], colors[layer]))
        layers.append(dict(name=layer, seconds=time.monotonic() - begin, **info))
        print(name, layer, info['bytes'], 'bytes', flush=True)
    assert len(layers) >= 5
    report['viewer'] = 'https://gds-viewer.tinytapeout.com/?pdk=sky130A&model=' + quote(
        BASE + 'gds/' + name + '/' + name + '.oas', safe='')
    (directory / 'layout.json').write_text(json.dumps(report, indent=2) + '\n')
    data = dict(design=name, builder_sha256=builder, layers=layers,
                bbox_um=[round(v * lay.dbu, 6) for v in report['bbox_dbu']],
                instance_count=len(inst), cell_counts=dict(counts), source_run=report['github_run'],
                gds=report['files'][name + '.gds'], oas=report['files'][name + '.oas'],
                tt_viewer=report['viewer'], metrics=report['metrics'],
                scope=report.get('scope', 'Representative integer-model core; not the complete language-model chip.'))
    (directory / 'viewer.json').write_text(json.dumps(data, indent=2) + '\n')
    return data


def main():
    assert os.getenv('GITHUB_ACTIONS') == 'true', 'Build/conversion stays on Actions'
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'docs', SITE, dirs_exist_ok=True)
    OUT.mkdir(exist_ok=True)
    designs = restore()
    for a in sources():
        dest, report = download(a)
        name = report['design']
        if name in designs and int(designs[name]['source_run']) >= int(report['github_run']):
            continue
        directory = OUT / name
        directory.mkdir(exist_ok=True)
        for file in (name + '.gds', name + '.oas', 'layout.json'):
            shutil.copyfile(dest / file, directory / file)
        designs[name] = dict(source_run=report['github_run'], artifact=a)
    assert designs, 'No successful, verified layout is available; preserve existing Pages deployment'
    builder = sha((HERE / 'gds2gltf.py').read_bytes() + Path(__file__).read_bytes())
    for name, source in designs.items():
        safe_name(name)
        directory = OUT / name
        previous = directory / 'viewer.json'
        report = json.loads((directory / 'layout.json').read_text())
        view = json.loads(previous.read_text()) if previous.exists() else {}
        current_oas = report['files'][name + '.oas']
        if (view.get('builder_sha256') != builder or view.get('oas') != current_oas
                or view.get('source_run') != report['github_run']):
            view = convert(directory, report, builder)
        for file in ('index.html', 'viewer.mjs'):
            shutil.copyfile(HERE / file, directory / file)
        source.update(dict(source_run=report['github_run'], area_mm2=report['metrics']['design__die__area'] / 1e6,
                           instance_count=view['instance_count']))
    links = ''.join('<li><a href="' + html.escape(n) + '/">' + html.escape(n) +
                    '</a> — ' + str(round(d['area_mm2'], 6)) + ' mm²</li>' for n, d in designs.items())
    (OUT / 'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width"><title>Integer model · 3D layouts</title>'
        '<main style="max-width:800px;margin:40px auto;padding:20px;font:17px/1.8 system-ui">'
        '<h1>Real SKY130 layouts</h1><p>Representative cores from the integer model. '
        'DRC/LVS and C-vector checks pass; electrical limitations are listed in each viewer.</p>'
        '<ul>' + links + '</ul><p><a href="../">Integer generation demo</a></p></main></html>\n')
    shutil.copyfile(HERE / 'LICENSE-GDS2glTF.txt', OUT / 'LICENSE-GDS2glTF.txt')
    # Keep the viewer and geometry self-hosted, including the pinned runtime modules.
    vendor = ROOT / 'build/viewer-tools/node_modules/three'
    for file in ('build/three.module.js', 'examples/jsm/controls/OrbitControls.js',
                 'examples/jsm/loaders/GLTFLoader.js', 'examples/jsm/libs/meshopt_decoder.module.js',
                 'examples/jsm/utils/BufferGeometryUtils.js', 'LICENSE'):
        dst = OUT / 'assets/three' / file
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(vendor / file, dst)
    files = {str(p.relative_to(OUT)): record(p) for p in sorted(OUT.rglob('*'))
             if p.is_file() and p != OUT / 'catalog.json'}
    assert sum(p.stat().st_size for p in SITE.rglob('*') if p.is_file()) < 950_000_000, 'Pages size limit'
    catalog = dict(schema=1, designs=designs, files=files, build_run=os.environ['GITHUB_RUN_ID'])
    (OUT / 'catalog.json').write_text(json.dumps(catalog, indent=2) + '\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write('Actual layout files preserved, hashes checked; generated by cloud-only OAS/GDS/meshopt.\n\n')
        for name in designs:
            f.write('- [' + name + ' 3D](' + BASE + 'gds/' + name + '/)\n')


if __name__ == '__main__':
    main()
