#!/usr/bin/env python3
"""Measure default ABC mapping of the accepted true-weight Shannon network.

Local mode constructs only and checks small cofactors. All EDA and full graph
truth/proofs run on Actions. The existing300s mapping cap is retained.
"""
from pathlib import Path
import argparse,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_DENSE_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,verilog,blif,flip_output,from_yosys
from export import import_net,weight_words,MODEL_SHA
from weight_order import ordered,exhaustive,FROZEN,C_SOURCES
from weights import c_words
from gate_check import simulate,verify
from ci import script,cec
OUT=Path(os.environ.get('H3_DENSE_OUT',str(R/'build/integer_opt/weights_dense')))
sha=lambda b:hashlib.sha256(b).hexdigest()
ACCEPTED={'layer0':('1033a022c0016818a8a8ad4ca7dccb144c8357d76b179eafe253211df1f8691a',74697),
          'all':('b78b1b2de8dcf3e46399add99e80085dd5807bc8ff063a2ce5d21927735e91d8',317093)}


def cofactor(net,high):
    assert not net.n_state and net.n_in>=8 and 0<=high<(1<<(net.n_in-8))
    b=Builder(8);pins=list(range(2,10))+[high>>i&1 for i in range(net.n_in-8)]
    _,y=import_net(b,net,pins);return b.finish(y)


def small():
    rng=random.Random(261001);cases=[]
    for case in range(8):
        b=Builder(10);w=list(range(12))
        for _ in range(128):w.append(b.nand(rng.choice(w),rng.choice(w)))
        original=b.finish([2,3,10,11]+w[-4:]);changed=flip_output(original)
        assert len(original.records)<=4000
        want=simulate(original,list(range(1024)))
        for high in range(4):
            part=cofactor(original,high);bad=cofactor(changed,high)
            check=verify(part,list(range(256)),want[high*256:(high+1)*256]);assert check['status']=='pass'
            assert want[high*256:(high+1)*256]!=want[(high^1)*256:((high^1)+1)*256]
            assert simulate(bad,list(range(256)))==[x^1 for x in want[high*256:(high+1)*256]]
            cases.append(dict(case=case,high=high,metrics=metrics(part),check=check,
                              actual_full_graph_fault_sha256=sha(bad.encode())))
    return dict(status='pass',partitions=len(cases),addresses=8192,
                actual_full_graph_faults_rejected=len(cases),cases=cases)


def partition(out,before,after,golden):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    assert before.n_in==after.n_in and before.n_out==after.n_out==64
    changed=flip_output(after);cuts=out/'parts';cuts.mkdir(exist_ok=True)
    report=dict(status='in_progress',free_bits=list(range(8)),
        fixed_bits=list(range(8,before.n_in)),complete_disjoint_partition=True,
        source_sha256=sha(before.encode()),mapped_sha256=sha(after.encode()),
        actual_full_graph_fault_sha256=sha(changed.encode()),parts=[])
    count=1<<(before.n_in-8)
    for high in range(count):
        graphs=[cofactor(g,high) for g in (before,after,changed)]
        expected=golden[high*256:(high+1)*256]
        truth=[verify(g,list(range(256)),expected) for g in graphs[:2]]
        assert all(v['status']=='pass' for v in truth)
        p=cuts/f'p{high:03}'
        for label,g in zip(('source','mapped','negative'),graphs):
            p.with_suffix('.'+label+'.nl').write_bytes(g.encode())
            p.with_suffix('.'+label+'.blif').write_text(blif(g))
        proof=cec(abc,p.with_suffix('.source.blif'),p.with_suffix('.mapped.blif'),p.with_suffix('.cec.log'))
        bad=cec(abc,p.with_suffix('.source.blif'),p.with_suffix('.negative.blif'),p.with_suffix('.negative.log'))
        assert proof['verdict']=='equivalent' and bad['verdict']=='different'
        report['parts'].append(dict(high=high,metrics=[metrics(g) for g in graphs],truth=truth,proof=proof,negative=bad))
        (out/'partition.json').write_text(json.dumps(report,indent=2)+'\n')
    assert [p['high'] for p in report['parts']]==list(range(count))
    report.update(status='pass',total_addresses=count*256)
    (out/'partition.json').write_text(json.dumps(report,indent=2)+'\n');return report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scope',choices=('layer0','all'),required=True)
    ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    start=time.monotonic();out=OUT/args.scope;out.mkdir(parents=True,exist_ok=True)
    check=small();blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    for n,h in C_SOURCES.items():assert sha((R/n).read_bytes())==h
    words=[w for l in (range(5) if args.scope=='all' else range(1)) for w in weight_words(blob,l)]
    assert sha(json.dumps(words).encode())==FROZEN[args.scope]['table']
    net=ordered(words,64,list(reversed(range((len(words)-1).bit_length()))))
    assert sha(net.encode())==ACCEPTED[args.scope][0]
    p=out/'dense';p.with_suffix('.nl').write_bytes(net.encode());p.with_suffix('.v').write_text(verilog(net))
    ys=script(p);assert ys.count('abc -g NAND -fast')==1
    ys=ys.replace('abc -g NAND -fast','abc -g NAND');p.with_suffix('.ys').write_text(ys)
    paths=[Path(__file__).resolve()]+[R/n for n in ('integer_opt/weight_order.py','integer_opt/weights.py',
        'integer_opt/weights_golden.c','integer_opt/gate_check.py','integer/int_model.c',
        'physical/model.bin','physical/export.py','nand.py','bench.py','golden.py','ci.py')]
    report=dict(status='prepared; default ABC mapping and full graph checks await Actions',scope=args.scope,
        small=check,before=metrics(net),accepted_fast_mapped_nand=ACCEPTED[args.scope][1],
        table_sha256=FROZEN[args.scope]['table'],model_sha256=MODEL_SHA,
        mapping_change='only remove -fast from abc -g NAND; other primitive lowering unchanged',
        mapping_timeout_seconds=300,numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in paths})
    receipt=out/'receipt.json';save=lambda:receipt.write_text(json.dumps(report,indent=2)+'\n');save()
    if args.cloud:
        report['yosys']=subprocess.check_output(['yosys','-V'],text=True,timeout=30).strip()
        golden=c_words(out,args.scope,words)
        report['source_all_addresses']=exhaustive(net,golden);save()
        begin=time.monotonic()
        subprocess.run(['yosys','-Q','-T','-l',str(p)+'.yosys.log','-s',str(p.with_suffix('.ys'))],
                       check=True,stdout=subprocess.DEVNULL,timeout=300)
        report['mapping_seconds']=time.monotonic()-begin
        mapped=from_yosys(json.loads(p.with_suffix('.mapped.yosys.json').read_text()),net.n_in,net.n_out)
        p.with_suffix('.mapped.nl').write_bytes(mapped.encode());report['mapped']=metrics(mapped);save()
        report['mapped_all_addresses']=exhaustive(mapped,golden);save()
        report['partition_proof']=partition(out,net,mapped,golden)
        report['saved_vs_accepted_fast_nand']=ACCEPTED[args.scope][1]-report['mapped']['nNand']
        report['status']='complete original/mapped C truth, full-domain partition CEC and actual graph faults pass'
    report['seconds']=time.monotonic()-start;save()
    print(json.dumps({k:v for k,v in report.items() if k in ('status','scope','before','mapped','saved_vs_accepted_fast_nand','seconds')},indent=2))


if __name__=='__main__':main()
