#!/usr/bin/env python3
"""Prepare and fully verify the already-proved R15 pilot for physical routing."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integer_opt'))
import pilot_bank


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true'
    assert sys.argv[1:]==['--cloud'],'full source checks are required'
    pilot_bank.OUT=ROOT/'build/physical'
    pilot_bank.main()
    out=pilot_bank.OUT;receipt=json.loads((out/'receipt.json').read_text())
    assert receipt['verification']['status']=='pass'
    assert receipt['metrics']['sha256']=='ebed6952838d5e9ca05a3c3a8f87d3a27bc5854f2176fa0248aee8af2bfab02f'
    source=dict(design='int_c16_ring_slice',variant='R15 held KV ring with seek/ready',
                metrics=receipt['metrics'],bank=receipt['bank'],model_sha256=receipt['model_sha256'],
                source_receipt_sha256=hashlib.sha256((out/'receipt.json').read_bytes()).hexdigest(),
                source_nand_sha256=hashlib.sha256((out/'slice.nl').read_bytes()).hexdigest(),
                status='bank CEC and full C/NAND/RTL source checks passed',clocks=receipt['verification']['clocks'])
    (out/'source.json').write_text(json.dumps(source,indent=2)+'\n')


if __name__=='__main__':main()
