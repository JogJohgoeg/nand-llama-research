#!/usr/bin/env python3
"""Collect existing local counts/receipts; never runs a circuit or EDA."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
EXPECTED = {'weights_z0': 3, 'weights_z25': 3, 'weights_z50': 3,
            'embedding': 15, 'nonlinear': 12, 'dot': 36, 'state': 8}


def main():
    rows, small = [], []
    for group, count in EXPECTED.items():
        result = json.loads((ROOT/'build/full'/group/'counts.json').read_text())
        assert len(result['cases']) == count
        rows += result['cases']
    for z in (0, 25, 50):
        small += json.loads((ROOT/f'build/small/weights_z{z}/counts.json').read_text())['cases']
    assert len(small) == 9 and all(c['verification']['status'] == 'pass' for c in small)
    directory = ROOT/'evidence'
    directory.mkdir(exist_ok=True)
    prototypes = directory/'prototypes'
    prototypes.mkdir(exist_ok=True)
    for row in rows:
        if row['verification']['status'] != 'pass':
            continue
        source = ROOT/'build/full'/row['group']/row['name']
        # Archive all locally verified canonical bytes, including actual LATCH
        # records. Full >4k artifacts remain Actions outputs, not local claims.
        shutil.copyfile(source.with_suffix('.json'), prototypes/(row['group']+'_'+row['name']+'.json'))
        if row.get('stateful'):
            shutil.copyfile(source.with_suffix('.state.json'), prototypes/(row['name']+'.state.json'))
    files = [*sorted(ROOT.glob('*.py')), ROOT/'data/dot32_t.json', ROOT/'.github/workflows/research.yaml',
             ROOT/'README.md', *sorted(prototypes.glob('*.json'))]
    receipt = dict(status='local construction and <=4000 NAND verification; full-table CI pending',
                   cases=rows, small_selectors=small,
                   files={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    (directory/'local_counts.json').write_text(json.dumps(receipt, indent=2)+'\n')
    passed = [c for c in rows if c['verification']['status'] == 'pass']
    print(json.dumps(dict(cases=len(rows), locally_checked=len(passed), pending_actions=len(rows)-len(passed),
                         small_selector_cases=len(small), archived_files=len(files),
                         vectors=sum(c['verification']['vectors'] for c in passed+small),
                         checked_bits=sum(c['verification']['checked_bits'] for c in passed+small)), indent=2))


if __name__ == '__main__':
    main()
