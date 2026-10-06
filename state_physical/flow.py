#!/usr/bin/env python3
"""Run the existing pinned physical tools in a separate state-leaf output tree."""
from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'physical'))

def main():
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA and large checks stay on Actions'
    assert len(sys.argv)==2 and sys.argv[1] in ('harden','mapped')
    action=sys.argv[1];out=ROOT/'build/state_physical';os.chdir(ROOT)
    if action=='harden':
        import harden
        harden.OUT=out
        sys.argv=[sys.argv[0],'--config',str(ROOT/'state_physical/config.json')]
        harden.main()
    else:
        import verify
        verify.OUT=out
        verify.mapped_check()

if __name__=='__main__':main()
