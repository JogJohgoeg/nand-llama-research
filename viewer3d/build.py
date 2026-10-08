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
        'gds-site': '.github/workflows/layout.yaml',
        # R105 norm/A8/C16 cache: routed by norm_layout, finished (post-route sim + package) by norm_post.
        'norm-gds-site': ('.github/workflows/norm_post.yaml', '.github/workflows/norm_layout.yaml'),
        # R118 x -> token head: routed by xhead_layout run 37676721047, finished by xhead_post.
        'xhead-gds-site': ('.github/workflows/xhead_post.yaml', '.github/workflows/xhead_layout.yaml')}
# Unsigned previews (e.g. the whole machine placed but not routed): labelled on the page, no signoff asserted.
PREVIEW = {'machine-preview-site': '.github/workflows/machine_view.yaml'}
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


def layout_report(text):
    # OpenROAD uses Infinity for unconstrained timing paths. Keep the literal
    # as a string, rather than losing it or emitting invalid browser JSON.
    return json.loads(text, parse_constant=str)


def json_text(data):
    return json.dumps(data, indent=2, allow_nan=False) + '\n'


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
    cached = OUT / 'catalog.json'
    if cached.exists():
        catalog = json.loads(cached.read_text())
        assert catalog['schema'] == 1
        for name, expected in catalog['files'].items():
            check(OUT / safe_name(name), expected)
        print('Restored hash-checked converted geometry from Actions cache', flush=True)
        return catalog['designs']
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
    for name, workflow in {**FLOW, **PREVIEW}.items():
        items = api('actions/artifacts?name=' + name + '&per_page=30')['artifacts']
        for a in sorted(items, key=lambda a: a['id'], reverse=True):
            wr = a['workflow_run']
            if a['expired'] or wr['head_branch'] != 'main':
                continue
            run = api('actions/runs/' + str(wr['id']))
            if run['conclusion'] != 'success':
                continue
            allowed = workflow if isinstance(workflow, tuple) else (workflow,)
            assert run['path'] in allowed and run['head_repository']['full_name'] == REPO
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
    report = layout_report((dest / 'layout.json').read_text())
    name = report['design']
    assert re.fullmatch(r'int_c16_[a-z_]+', name)
    assert report['github_sha'] == a['workflow_run']['head_sha']
    assert int(report['github_run']) == a['workflow_run']['id']
    if a['name'] in PREVIEW:
        assert report['unsigned'] is True and report['status'].startswith('UNSIGNED PREVIEW'), report['status']
    else:
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
    # Unsigned previews keep PDN via instances in the top cell; their count is pinned at packaging time.
    assert len(inst) == (report['gds_instance_count'] if report.get('unsigned') else report['metrics']['design__instance__count'])
    (directory / 'cells.json').write_text(json.dumps(dict(inst=inst), separators=(',', ':'), allow_nan=False) + '\n')
    # Very large cores (R105: 484,732 instances, ~409k fillers) time out in meshopt.
    # Above the threshold only logic-free filler/decap/tap instances are left out of
    # the 3D geometry; cells.json above still lists every instance.
    omitted = Counter()
    if len(inst) > 150000:
        for item in list(top.each_inst()):
            base = item.cell.name.removeprefix('sky130_fd_sc_hd__')
            if base.startswith(('fill_', 'decap_', 'tapvpwrvgnd_')):
                omitted[base.split('_')[0]] += 1
                item.delete()
        # Their now-unreferenced definitions would become extra top cells.
        for cell in list(lay.top_cells()):
            if cell.name != name:
                lay.prune_cell(cell.cell_index(), -1)
        assert len(lay.top_cells()) == 1 and lay.top_cell().name == name
    gds = WORK / (name + '.gds')
    lay.write(str(gds))
    tree = ast.parse((HERE / 'gds2gltf.py').read_text())
    stack = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                  and any(isinstance(t, ast.Name) and t.id == 'layerstack' for t in n.targets)))
    colors = {v['name']: v['color'] for v in stack.values()}
    assert len({tuple(v) for v in colors.values()}) == len(colors)
    tool = ROOT / 'build/viewer-tools/node_modules/.bin/gltf-transform'
    def one(layer):
        # Layers are independent; the whole-machine preview (~370k cells) is too slow to convert serially.
        begin = time.monotonic()
        with (WORK / (name + '-' + layer + '.log')).open('w') as log:
            subprocess.run([sys.executable, str(HERE / 'gds2gltf.py'), str(gds), layer],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=5400)
            gltf = Path(str(gds) + '.' + layer + '.gltf')
            # Some physical layers are legitimately empty (e.g. no met5 signals).
            doc = json.loads(gltf.read_text())
            if not doc.get('meshes'):
                gltf.unlink()
                return None
            del doc
            glb = directory / (layer + '.glb')
            cmd = [str(tool), 'optimize', str(gltf), str(glb), '--compress', 'meshopt',
                   '--palette', 'false', '--texture-compress', 'false', '--simplify', 'false']
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=5400)
            gltf.unlink()
        info = glb_info(glb, layer)
        assert all(abs(a - b) < 1e-7 for a, b in zip(info['color'], colors[layer]))
        print(name, layer, info['bytes'], 'bytes', round(time.monotonic() - begin), 's', flush=True)
        return dict(name=layer, seconds=time.monotonic() - begin, **info)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        layers = [x for x in pool.map(one, LAYERS) if x]
    assert len(layers) >= 5
    report['viewer'] = 'https://gds-viewer.tinytapeout.com/?pdk=sky130A&model=' + quote(
        BASE + 'gds/' + name + '/' + name + '.oas', safe='')
    (directory / 'layout.json').write_text(json_text(report))
    data = dict(design=name, builder_sha256=builder, layers=layers,
                bbox_um=[round(v * lay.dbu, 6) for v in report['bbox_dbu']],
                instance_count=len(inst), cell_counts=dict(counts), source_run=report['github_run'],
                geometry_omitted_logic_free_instances=dict(omitted),
                gds=report['files'][name + '.gds'], oas=report['files'][name + '.oas'],
                tt_viewer=report['viewer'], metrics=report['metrics'], unsigned=report.get('unsigned', False),
                scope=report.get('scope', 'Representative integer-model core; not the complete language-model chip.'))
    (directory / 'viewer.json').write_text(json_text(data))
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
        report = layout_report((directory / 'layout.json').read_text())
        view = json.loads(previous.read_text()) if previous.exists() else {}
        current_oas = report['files'][name + '.oas']
        if (view.get('builder_sha256') != builder or view.get('oas') != current_oas
                or view.get('source_run') != report['github_run']):
            view = convert(directory, report, builder)
        for file in ('index.html', 'viewer.mjs'):
            shutil.copyfile(HERE / file, directory / file)
        source.update(dict(source_run=report['github_run'], area_mm2=report['metrics']['design__die__area'] / 1e6,
                           instance_count=view['instance_count'], unsigned=bool(report.get('unsigned'))))
    links = ''.join('<li><a href="' + html.escape(n) + '/">' + html.escape(n) +
                    '</a> — ' + str(round(d['area_mm2'], 6)) + ' mm²' +
                    (' — <b>unsigned preview (placement only, not routed)</b>' if d.get('unsigned') else '') + '</li>'
                    for n, d in designs.items())
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
    (OUT / 'catalog.json').write_text(json_text(catalog))
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write('Actual layout files preserved, hashes checked; generated by cloud-only OAS/GDS/meshopt.\n\n')
        for name in designs:
            f.write('- [' + name + ' 3D](' + BASE + 'gds/' + name + '/)\n')


if __name__ == '__main__':
    main()
