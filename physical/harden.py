#!/usr/bin/env python3
"""Cloud-only LibreLane run, retaining runtime/RSS evidence even on failure."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'build/physical'


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true','EDA runs only on GitHub Actions'
    os.chdir(ROOT);stop=threading.Event();start=time.monotonic()
    # Click validates --force-run-dir before LibreLane can create its run tree.
    (OUT/'run').mkdir(parents=True,exist_ok=True)
    def sample():
        with (OUT/'runner_memory.jsonl').open('w') as f:
            while not stop.is_set():
                memory={l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines()}
                f.write(json.dumps(dict(seconds=time.monotonic()-start,total_bytes=memory['MemTotal'],
                                       available_bytes=memory['MemAvailable']))+'\n');f.flush()
                stop.wait(10)
    watcher=threading.Thread(target=sample,daemon=True);watcher.start()
    cmd=[sys.executable,'-m','librelane','--docker-no-tty','--dockerized','--pdk','sky130A',
         '--scl','sky130_fd_sc_hd','--jobs','4','--run-tag','pilot',
         '--force-run-dir',str(OUT/'run'),'--hide-progress-bar',str(ROOT/'physical/config.json')]
    code=-1;error=None
    try:
        code=subprocess.run(cmd,timeout=18000).returncode
    except subprocess.TimeoutExpired:error='LibreLane exceeded five-hour flow cap'
    finally:
        stop.set();watcher.join(12)
        stats=[]
        for p in sorted((OUT/'run').glob('*/*.process_stats.json')):
            stats.append(dict(path=str(p.relative_to(OUT)),data=json.loads(p.read_text())))
        report=dict(returncode=code,error=error,seconds=time.monotonic()-start,
                    runner=os.getenv('RUNNER_NAME'),os=os.getenv('RUNNER_OS'),cpu_count=os.cpu_count(),
                    flow_version='3.0.14',pdk='sky130A',scl='sky130_fd_sc_hd',
                    command=cmd,process_stats=stats)
        (OUT/'resources.json').write_text(json.dumps(report,indent=2)+'\n')
    if code:raise SystemExit(code if code>0 else 1)


if __name__=='__main__':main()
