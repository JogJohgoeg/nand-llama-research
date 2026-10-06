#!/usr/bin/env python3
"""Compose two verified replacements through the unchanged pilot compositor.

This isolated experiment does not alter the running physical flow. Local mode
only constructs files; Actions runs the same complete C/NAND/RTL/mutation suite.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'))
import export as pilot
from golden import Netlist


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true','Complete gate checks only on Actions'
    else:signal.alarm(55)
    sha=lambda raw:hashlib.sha256(raw).hexdigest()
    # The substitutions are intentionally tied to the reviewed compositor.
    assert sha(Path(pilot.__file__).read_bytes())=='6f6fd0bdc5f933c663c4870f300ef3baec36b21ad72f4df491e2b992c5551002'
    units=ROOT/'integer_opt/pilot_units';manifest=json.loads((units/'manifest.json').read_text())
    replacement={}
    for name,meta in manifest.items():
        raw=(units/(name+'.nl')).read_bytes();assert sha(raw)==meta['sha256']
        replacement[name]=Netlist.decode(raw,meta['nIn'],meta['nOut'])
    original_load=pilot.load_unit
    def selected_unit(name):
        return replacement[name] if name=='serial_div' else original_load(name)
    def selected_table(words,width,method):
        assert width==64 and method=='phase'
        assert words==pilot.weight_words((pilot.HERE/'model.bin').read_bytes())
        return replacement['weights_layer0']
    pilot.load_unit=selected_unit;pilot.lookup=selected_table
    pilot.OUT=ROOT/'build/integer_opt/pilot_v2'
    pilot.main()
    source_path=pilot.OUT/'source.json';report=json.loads(source_path.read_text())
    assert report['metrics']['nLatch']==9237
    report.update(status='construction only; full combined verification pending Actions',
                  variant='mapped Shannon layer0 and optimized serial divider; same interface/state schedule',
                  baseline_nand=155655,saved_nand=155655-report['metrics']['nNand'],
                  replacements=manifest)
    report['provenance'].update({str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),units/'manifest.json']})
    source_path.write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        import verify
        assert verify.OUT==pilot.OUT
        original_testbench=verify.testbench
        verify.testbench=lambda:original_testbench(vectors_path=str(pilot.OUT/'vectors.txt'))
        verify.source_check()
        report['status']='full source NAND/RTL/C99 and actual mutation checks pass; no physical result'
        report['run_id']=os.getenv('GITHUB_RUN_ID');report['revision']=os.getenv('GITHUB_SHA')
        source_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','metrics','saved_nand')},indent=2))


if __name__=='__main__':main()
