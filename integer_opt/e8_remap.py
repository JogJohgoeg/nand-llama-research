#!/usr/bin/env python3
"""Measurement only: can stronger ABC scripts shrink the 43,915-NAND E8 table?

Table: 15 inputs (row8 low, col7 high) -> signed byte, rows 192..255 unused.
Variants: 'exact' keeps the archived zero rows; 'dup' fills unused rows 192..255
with rows 128..191 (row bit 6 ignored when bit 7 is set). 'dup' is only usable
after a separate proof that every caller keeps row < 192; nothing here is adopted.
Every candidate is checked on all 32,768 addresses (legal 24,576 for 'dup') and
by ABC CEC against its own specification table. Actions only.
"""
from pathlib import Path
import json,os,shutil,subprocess,sys,time,hashlib
R=Path(__file__).resolve().parents[1]
OUT=R/'build/integer_opt/e8_remap'
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import metrics,verilog,blif,from_yosys,flip_output
from bench import lookup
from gate_check import simulate
from ci import cec
import vocab_row as row
sha=lambda b:hashlib.sha256(b).hexdigest()
SCRIPTS={
 'fast':'strash; map',
 'resyn2':'strash; balance; rewrite; refactor; balance; rewrite; rewrite -z; balance; refactor -z; rewrite -z; balance; map',
 'dc2x3':'strash; dc2; dc2; dc2; map',
 'dch':'strash; dc2; dch -f; map; topo',
 'compress2rs':'strash; balance -l; resub -K 6 -l; rewrite -l; resub -K 6 -N 2 -l; refactor -l; resub -K 8 -l; balance -l; resub -K 8 -N 2 -l; rewrite -l; resub -K 10 -l; rewrite -z -l; resub -K 10 -N 2 -l; balance -l; resub -K 12 -l; refactor -z -l; resub -K 12 -N 2 -l; rewrite -z -l; balance -l; dch -f; map',
}


def run(name,net,script):
    d=OUT/name;d.mkdir(parents=True,exist_ok=True)
    (d/'in.v').write_text(verilog(net));(d/'abc.script').write_text(script+'\n')
    ys=f'read_verilog {d}/in.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -script {d}/abc.script\nopt_clean\ncheck -assert\nwrite_json {d}/out.json\n'
    (d/'run.ys').write_text(ys);t=time.monotonic()
    try:subprocess.run(['yosys','-Q','-T','-l',str(d/'yosys.log'),'-s',str(d/'run.ys')],check=True,stdout=subprocess.DEVNULL,timeout=1500)
    except subprocess.TimeoutExpired:return dict(status='timeout')
    g=from_yosys(json.loads((d/'out.json').read_text()),net.n_in,net.n_out);return dict(status='ok',net=g,seconds=round(time.monotonic()-t,1))


def main():
    assert os.getenv('GITHUB_ACTIONS')=='true'
    OUT.mkdir(parents=True,exist_ok=True)
    e,_,words,_,_=row.tables();assert metrics(e)['nNand']==43915
    dup=[words[(a&255)-64+((a>>8)<<8)] if (a&255)>=192 else words[a] for a in range(32768)]
    specs={'exact':words,'dup':dup};results={}
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc')
    for vname,table in specs.items():
        src=e if vname=='exact' else lookup(table,8,'shannon')
        (OUT/(vname+'.spec.blif')).write_text(blif(src))
        assert simulate(src,list(range(32768)))==table
        for sname,script in SCRIPTS.items():
            key=vname+'_'+sname;res=run(key,src,script)
            if res['status']!='ok':results[key]=res;print(key,res,flush=True);continue
            g=res.pop('net');legal=[a for a in range(32768) if (a&255)<192]
            got=simulate(g,list(range(32768)))
            exact_all=got==table;legal_ok=all(got[a]==words[a] for a in legal)
            (OUT/key/'out.blif').write_text(blif(g))
            proof=cec(abc,OUT/(vname+'.spec.blif'),OUT/key/'out.blif',OUT/key/'cec.log')
            neg=cec(abc,OUT/(vname+'.spec.blif'),OUT/key/'neg.blif',OUT/key/'neg.log') if (OUT/key/'neg.blif').write_text(blif(flip_output(g))) else None
            results[key]=dict(res,metrics=metrics(g),all_addresses_equal_spec=exact_all,legal_rows_equal_C=legal_ok,cec=proof,negative=neg)
            (OUT/key/'out.nl').write_bytes(g.encode())
            print(key,metrics(g)['nNand'],metrics(g)['nand_depth'],exact_all,legal_ok,proof['verdict'],flush=True)
        results[vname+'_source']=metrics(src)
    (OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n')


if __name__=='__main__':main()
