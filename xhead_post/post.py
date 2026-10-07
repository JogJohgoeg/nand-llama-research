#!/usr/bin/env python3
"""Finish R118 x -> token head run 37676721047 without re-routing (norm_post precedent).

That run routed and passed route/Magic/KLayout DRC, LVS, XOR and the hold/setup checkers
(hold margin 0.5 ns). Its post-route Icarus job stopped at clock 0: every sky130 flip-flop
powers up X in gate-level simulation, while the C vectors (like the NAND/LATCH contract)
start from all-zero state and compare outputs from clock 0. Verilator RTL replay passed
because it initialises state to zero.

A 4-clock reset preroll was tried first (run 37703096514) and left the token register X: it has no
reset, by contract every latch starts at 0. So only the simulation model's DFF UDPs are given an
all-zero initial value; netlist, testbench and all 152,400 vectors are unchanged.
Then GDS/OAS are packaged by xhead_physical/flow.py publish() unchanged.
"""
from pathlib import Path
import hashlib,importlib.util,io,json,os,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'physical'),str(ROOT)]
OUT=ROOT/'build/xhead_physical'
RUN=37676721047;HEAD='1a039b4a759492e3cef46c6754faf461aa402c03'
PINS={'xhead-layout-source':(11510673296,'e1eb23aeed985aa083581d2752729f8553733aeff1838f98d3f042e40266eb16'),
      'xhead-layout-logs':(11517395391,'63fc8f31ce32cb3e94155ce5eaa413506cc14528c43088d5b407a5214292c8bf')}
sha=lambda b:hashlib.sha256(b).hexdigest()
spec=importlib.util.spec_from_file_location('xhead_flow',ROOT/'xhead_physical/flow.py');X=importlib.util.module_from_spec(spec);spec.loader.exec_module(X)


def restore():
    run=json.loads(X.api('actions/runs/'+str(RUN)))
    assert run['head_sha']==HEAD and run['path']=='.github/workflows/xhead_layout.yaml'
    jobs={j['name']:j for j in json.loads(X.api('actions/runs/'+str(RUN)+'/jobs'))['jobs']}
    assert jobs['verify']['conclusion']=='success' and jobs['gds']['conclusion']=='success' and jobs['post']['conclusion']=='failure'
    OUT.mkdir(parents=True,exist_ok=True);record={}
    for name,(aid,digest) in PINS.items():
        meta=json.loads(X.api('actions/artifacts/'+str(aid)))
        assert meta['name']==name and not meta['expired'] and meta['workflow_run']['id']==RUN and meta['digest']=='sha256:'+digest
        raw=X.api('actions/artifacts/'+str(aid)+'/zip');assert len(raw)==meta['size_in_bytes'] and sha(raw)==digest
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in z.namelist():
                assert not n.startswith('/') and '..' not in Path(n).parts
                p=OUT/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(n))
        record[name]=dict(id=aid,digest=meta['digest'],bytes=len(raw))
    m=json.loads((OUT/'run/final/metrics.json').read_text())
    for k in ('magic__drc_error__count','klayout__drc_error__count','design__lvs_error__count','design__xor_difference__count',
              'route__drc_errors','timing__hold_vio__count','timing__setup_vio__count'):
        assert m[k]==0,(k,m[k])
    src=json.loads((OUT/'source.json').read_text())
    for n,f in src['files'].items():
        if n!='tb.v':assert sha((OUT/n).read_bytes())==f['sha256'],n
    (OUT/'post_restore.json').write_text(json.dumps(dict(run=RUN,head=HEAD,artifacts=record,post_script_sha256=sha(Path(__file__).read_bytes())),indent=2)+'\n')
    print(json.dumps(record,indent=2))


def mapped():
    """Gate-level replay with the NAND/LATCH contract's all-zero initial state.

    The token register has no reset (the contract starts every latch at 0), and sky130's
    functional DFF UDPs power up X. Only the simulation model is changed: each sequential
    `udp_dff*` primitive gets `initial Q = 1'b0;`. The routed netlist, the testbench and all
    152,400 vectors are the original ones and every clock is compared from clock 0.
    """
    import re,verify
    src=json.loads((OUT/'source.json').read_text())
    assert sha((OUT/'tb.v').read_bytes())==src['files']['tb.v']['sha256']
    cfg=json.loads((OUT/'run/resolved.json').read_text());lib=Path(cfg['PDK_ROOT'])/cfg['PDK']/'libs.ref/sky130_fd_sc_hd/verilog'
    prim=(lib/'primitives.v').read_text();out=[];n=0
    for block in re.split(r'(?=\bprimitive\b)',prim):
        m=re.match(r'primitive\s+(\S+)',block)
        if m and 'udp_dff' in m.group(1):
            regs=re.findall(r'\breg\s+(\w+)\s*;',block);assert len(regs)==1,m.group(1)
            block=block.replace(f'reg {regs[0]};',f"reg {regs[0]};\n    initial {regs[0]} = 1'b0;",1);n+=1
        out.append(block)
    assert n>0;zp=OUT/'primitives_zero_init.v';zp.write_text(''.join(out))
    netlist=next((OUT/'run/final/nl').glob('*.v'))
    verify.run(['iverilog','-g2012','-DFUNCTIONAL','-DUNIT_DELAY=#0','-I',lib,'-s','tb','-o',OUT/'mapped.vvp',zp,lib/'sky130_fd_sc_hd.v',netlist,OUT/'tb.v'],3600)
    verify.run(['vvp',OUT/'mapped.vvp'],16000)
    (OUT/'mapped_verification.json').write_text(json.dumps(dict(status='pass',vectors_sha256=sha((OUT/'vectors.txt').read_bytes()),
        netlist_sha256=sha(netlist.read_bytes()),tb_sha256=src['files']['tb.v']['sha256'],
        zero_init_primitives=dict(count=n,sha256=sha(zp.read_bytes()),original_sha256=sha(prim.encode())),
        method='post-route functional standard-cell simulation, zero delay, same C99 vectors from clock 0; DFF UDPs start at 0 as in the NAND/LATCH contract'),indent=2)+'\n')


if __name__=='__main__':
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and large gate simulation stay on Actions'
    {'restore':restore,'mapped':mapped,'publish':X.publish}[sys.argv[1]]()
