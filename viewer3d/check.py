#!/usr/bin/env python3
"""Small validation checks without importing/running any geometry or EDA tool."""
import importlib.util
import ast
from types import SimpleNamespace
from html.parser import HTMLParser
import json
from pathlib import Path
import struct
import tempfile

spec = importlib.util.spec_from_file_location('viewer_build', Path(__file__).with_name('build.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def rejected(fn):
    try:
        fn()
    except (AssertionError, ValueError):
        return
    raise AssertionError('negative control was accepted')


def structure(source):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(); self.stack=[]; self.parents={}
        def handle_starttag(self, tag, attrs):
            ident=dict(attrs).get('id')
            parent=next((i for _,i in reversed(self.stack) if i), None)
            if ident or tag=='aside':self.parents[ident or tag]=parent
            if tag not in {'meta','link','br','hr','img','input','source','wbr','area','base','col','embed','param','track'}:
                self.stack.append((tag,ident))
        def handle_endtag(self, tag):
            assert self.stack and self.stack[-1][0]==tag, ('unbalanced HTML',tag,self.stack[-3:])
            self.stack.pop()
    parser=Parser();parser.feed(source);parser.close();assert not parser.stack
    assert {k:parser.parents[k] for k in ('view','aside','hud','tip','status')}=={
        'view':'app','aside':'app','hud':'view','tip':'view','status':'view'}


def converter_names(source):
    # Inspect only the naming expression: no geometry imports or conversion.
    tree=ast.parse(source)
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='output_path' for t in n.targets))
    save=next(n.value for n in tree.body if isinstance(n,ast.Expr)
        and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute)
        and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='gltf'
        and n.value.func.attr=='save')
    def value(node,env):return eval(compile(ast.Expression(node),'<output name>','eval'),{'__builtins__':{'len':len}},env)
    for argv,expected in [(['convert','chip.gds'],'chip.gds.gltf'),
                          (['convert','chip.gds','met2'],'chip.gds.met2.gltf')]:
        env=dict(gdsii_file_path=argv[1],sys=SimpleNamespace(argv=argv))
        env['output_path']=value(assignment.value,env)
        assert value(save.args[0],env)==expected


def main():
    converter=Path(__file__).with_name('gds2gltf.py').read_text()
    converter_names(converter)
    rejected(lambda: converter_names(converter.replace('gltf.save(output_path)', 'gltf.save(gdsii_file_path + \".gltf\")')))
    page=Path(__file__).with_name('index.html').read_text()
    structure(page)
    # Regression: an extra HUD closing div detached the overlays and side panel.
    old='id="dimensions">SKY130A</div></div>'
    assert page.count(old)==1
    rejected(lambda: structure(page.replace(old,old+'</div>')))
    for n in ('int_c16_q_matrix/met2.glb', 'assets/three/LICENSE'):
        assert b.safe_name(n) == n
    for n in ('../escape', '/absolute', 'x/../../escape', 'x\\bad', 'https://example.org/a'):
        rejected(lambda: b.safe_name(n))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'layer.glb'
        doc = dict(materials=[dict(name='met1', pbrMetallicRoughness=dict(baseColorFactor=[.25,.45,.95,1]))],
                   buffers=[dict(byteLength=12)], meshes=[], nodes=[], extensionsUsed=['EXT_meshopt_compression'])
        def write():
            j = json.dumps(doc).encode();j += b' ' * (-len(j) % 4)
            p.write_bytes(struct.pack('<4sIII4s', b'glTF', 2, len(j)+20, len(j), b'JSON') + j)
        write();info = b.glb_info(p, 'met1');assert info['decoded_buffer_bytes'] == 12
        old = b.record(p)
        doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture'] = dict(index=0)
        write();rejected(lambda: b.glb_info(p, 'met1'));rejected(lambda: b.check(p, old))
        del doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']
        doc['materials'][0]['name'] = 'merged';write();rejected(lambda: b.glb_info(p, 'met1'))
    # A newer failed run or untrusted PR artifact must not replace the published layout.
    orig = b.api
    def api(path):
        if path.startswith('actions/artifacts?'):
            return dict(artifacts=[dict(id=2, expired=False, workflow_run=dict(id=2, head_branch='main', head_sha='bad')),
                                   dict(id=1, expired=False, workflow_run=dict(id=1, head_branch='main', head_sha='good'))])
        if path.endswith('/2'):return dict(conclusion='failure')
        return dict(conclusion='success', path=b.FLOW['matrix-gds-site'],
                    head_repository=dict(full_name=b.REPO), event='push', head_sha='good')
    flows = b.FLOW;b.FLOW={'matrix-gds-site':flows['matrix-gds-site']};b.api=api
    assert [a['id'] for a in b.sources()] == [1]
    def malicious(path):
        result=api(path)
        if path.endswith('/1'):result['head_repository']['full_name']='elsewhere/fork'
        return result
    b.api=malicious;rejected(lambda: list(b.sources()));b.api=orig;b.FLOW=flows
    # A demo rebuild restores every prior asset, and rejects a changed published byte.
    with tempfile.TemporaryDirectory() as d:
        old_work,old_out,old_fetch=b.WORK,b.OUT,b.fetch
        b.WORK=Path(d)/'work';b.OUT=Path(d)/'out'
        blobs={'a/index.html':b'old layout', 'a/chip.glb':b'geometry'}
        catalog=dict(schema=1,designs={'a':dict(source_run='123')},files={
            n:dict(bytes=len(v),sha256=b.sha(v)) for n,v in blobs.items()})
        def fetch(url,dst,missing=False):
            name=url.split('/gds/')[1].split('?')[0]
            data=json.dumps(catalog).encode() if name=='catalog.json' else blobs[name]
            dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data);return True
        b.fetch=fetch;b.os.environ.setdefault('GITHUB_RUN_ID','local-helper-check')
        assert b.restore()==catalog['designs']
        assert all((b.OUT/n).read_bytes()==v for n,v in blobs.items())
        blobs['a/chip.glb']=b'geomEtry';rejected(b.restore)
        b.WORK,b.OUT,b.fetch=old_work,old_out,old_fetch
    print('viewer output-name/HTML structure/path/hash/GLB-color/source-selection positives and actual negatives pass; no EDA')


if __name__ == '__main__':
    main()
