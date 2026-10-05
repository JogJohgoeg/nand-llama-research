#!/usr/bin/env python3
"""NAND-only Yosys/ABC mapping, canonical-byte checks and CEC; Actions only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from bench import GROUPS, ROOT, generate, verify
from golden import Netlist
from nand import blif, flip_output, from_yosys, metrics, verify_state


def verdict(returncode, output):
    if returncode or re.search(r'(?i)error:|undecided|timed out|miter computation has failed', output):
        return 'error'
    eq = bool(re.search(r'(?m)^Networks are equivalent(?:[. ]|$)', output))
    neq = bool(re.search(r'(?m)^Networks are NOT EQUIVALENT(?:[. ]|$)', output))
    return ('equivalent' if eq else 'different') if eq != neq else 'error'


def cec(abc, source, target, log):
    start = time.monotonic()
    with log.open('w') as stream:
        result = subprocess.run([abc, '-c', f'cec -T 120 {source} {target}'],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=150)
    output = log.read_text()
    print(output[-1200:], flush=True)
    return dict(verdict=verdict(result.returncode, output), seconds=round(time.monotonic()-start, 3))


def script(prefix):
    return (f'read_verilog {prefix}.v\nhierarchy -check -top top\n'
            # The input is already an explicit combinational NAND graph. Full
            # synth repeats RTL/memory/FSM optimizers over 600k gates and timed
            # out before ABC in 186a71c. Lower primitives directly instead.
            'proc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\n'
            f'write_json {prefix}.mapped.yosys.json\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=GROUPS, required=True)
    parser.add_argument('--prepare', action='store_true', help='prepare small scripts only, no EDA')
    args = parser.parse_args()
    if not args.prepare and (platform.system() != 'Linux' or os.environ.get('GITHUB_ACTIONS') != 'true'):
        raise SystemExit('EDA is permitted only on GitHub Actions; use --prepare locally')
    result = generate(args.group, small=args.prepare)
    directory = ROOT/'build'/('small' if args.prepare else 'full')/args.group
    for case in result['cases']:
        prefix = directory/case['name']
        prefix.with_suffix('.ys').write_text(script(prefix))
    if args.prepare:
        print('Prepared small scripts only; no Yosys, ABC or SAT was run')
        return
    abc = shutil.which('yosys-abc') or shutil.which('berkeley-abc')
    assert abc, 'ABC missing'
    receipt_path = directory/'ci_receipt.json'
    receipt_path.unlink(missing_ok=True)
    receipt = dict(status='in_progress', group=args.group,
                   scope='independent reference vectors + source vs mapped canonical bytes CEC; no PDK mapping',
                   mapping_flow='explicit NAND graph: proc; flatten; techmap; opt -fast; abc -g NAND -fast',
                   run_id=os.environ.get('GITHUB_RUN_ID'), revision=os.environ.get('GITHUB_SHA'),
                   yosys=subprocess.check_output(['yosys', '-V'], text=True).strip(),
                   abc=abc, cases=[], files={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in sorted([*ROOT.glob('*.py'), ROOT/'data/dot32_t.json',
                                                             ROOT/'.github/workflows/research.yaml'])})
    receipt_path.write_text(json.dumps(receipt, indent=2)+'\n')
    # Save useful optimized-candidate evidence before the deliberately large
    # baseline. Every case is still mandatory for the final pass verdict.
    for case in sorted(result['cases'], key=lambda c: c['nNand']):
        prefix = directory/case['name']
        receipt['current_case'] = case['name']
        receipt_path.write_text(json.dumps(receipt, indent=2)+'\n')
        start = time.monotonic()
        subprocess.run(['yosys', '-Q', '-T', '-l', str(prefix.with_suffix('.yosys.log')),
                        '-s', str(prefix.with_suffix('.ys'))], check=True, timeout=300,
                       stdout=subprocess.DEVNULL)
        mapping_seconds = round(time.monotonic()-start, 3)
        source_data = json.loads(prefix.with_suffix('.json').read_text())
        source = Netlist.decode(bytes.fromhex(source_data['nl_hex'][2:]), case['nIn'], case['nOut'])
        mapped = from_yosys(json.loads(prefix.with_suffix('.mapped.yosys.json').read_text()), case['nIn'], case['nOut'])
        vectors = json.loads(prefix.with_suffix('.vectors.json').read_text())
        checked = verify(mapped, vectors['input'], vectors['expected'])
        assert checked['status'] == 'pass' and case['verification']['status'] == 'pass'
        mapped_data = dict(metrics(mapped), nl_hex='0x'+mapped.encode().hex(), verification=checked)
        prefix.with_suffix('.mapped.json').write_text(json.dumps(mapped_data)+'\n')
        stateful = None
        if case.get('state_latches'):
            actual = verify_state(mapped, vectors['input'], vectors['expected'], case['state_latches'])
            prefix.with_suffix('.mapped.state.json').write_text(json.dumps(actual)+'\n')
            stateful = {k: v for k, v in actual.items() if k != 'nl_hex'}
        for label, net in (('source', source), ('mapped', mapped), ('negative', flip_output(mapped))):
            prefix.with_suffix('.'+label+'.blif').write_text(blif(net))
        proof = cec(abc, prefix.with_suffix('.source.blif'), prefix.with_suffix('.mapped.blif'),
                    prefix.with_suffix('.cec.log'))
        assert proof['verdict'] == 'equivalent', proof
        negative = cec(abc, prefix.with_suffix('.source.blif'), prefix.with_suffix('.negative.blif'),
                       prefix.with_suffix('.negative.log'))
        assert negative['verdict'] == 'different', negative
        files = [prefix.with_suffix(s) for s in ('.json', '.v', '.ref.v', '.vectors.json', '.ys',
                                                '.mapped.json', '.mapped.yosys.json', '.source.blif', '.mapped.blif',
                                                '.negative.blif', '.cec.log', '.negative.log')]
        if stateful:
            files += [prefix.with_suffix('.state.json'), prefix.with_suffix('.mapped.state.json')]
        receipt['cases'].append(dict(name=case['name'], before=case, after=metrics(mapped),
                                     vectors=checked, cec=proof, negative_control=negative,
                                     stateful=stateful,
                                     mapping_seconds=mapping_seconds,
                                     files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}))
        receipt_path.write_text(json.dumps(receipt, indent=2)+'\n')
    receipt['status'] = 'pass'
    receipt.pop('current_case')
    receipt_path.write_text(json.dumps(receipt, indent=2)+'\n')


if __name__ == '__main__':
    main()
