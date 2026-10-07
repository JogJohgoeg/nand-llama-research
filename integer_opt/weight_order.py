#!/usr/bin/env python3
"""Reverse Shannon variable order, preserving every original weight address.

Local work is construction plus <=4k gate checks. Actions exhausts the entire
original/padded domain against frozen C, maps the candidate and proves CEC.
"""
from pathlib import Path
import argparse,hashlib,itertools,json,os,random,signal,sys,time
R=Path(os.environ.get('H3_WEIGHT_ORDER_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from bench import lookup
from export import weight_words,import_net,MODEL_SHA
from nand import Builder,metrics
from gate_check import verify
from weights import c_words,cloud_check
OUT=Path(os.environ.get('H3_WEIGHT_ORDER_OUT',str(R/'build/integer_opt/weight_order')))
sha=lambda b:hashlib.sha256(b).hexdigest()
FROZEN={
 'layer0':dict(table='8020424e306ad27a049dacb85363522e0b6fdfb4ccb37fc3ade64554b460169c',
               raw='ef662aa90315fd9b3881cb4c5623b6e67b808f914f0d482ed5eac2b54687cdae',nand=80187,mapped=80154),
 'all':dict(table='c4b50ff7b4b45052cc570324d70b4296a8204c6076b07f26c06e578cede27c1a',
            raw='c897a3290761da3fad1c000bc0d71ad920eeca15e202dc56635da11d09da0774',nand=323304,mapped=323266)}
C_SOURCES={'integer/int_model.c':'db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777',
 'integer_opt/weights_golden.c':'3804019df159e59e7be704ba61562537870ecf328f9cfa3193aa3f0f8321434f'}


def ordered(table,width,order):
    n=max(1,(len(table)-1).bit_length());assert sorted(order)==list(range(n))
    full=table+[0]*((1<<n)-len(table))
    perm=[sum((i>>j&1)<<k for j,k in enumerate(order)) for i in range(len(full))]
    assert len(set(perm))==len(full)
    inner=lookup([full[i] for i in perm],width,'shannon')
    b=Builder(n);_,y=import_net(b,inner,[2+k for k in order])
    return b.finish(y)


def small():
    cases=0;addresses=0;hashes=[]
    def check(table,width,order):
        nonlocal cases,addresses
        net=ordered(table,width,order);assert metrics(net)['nNand']<=4000
        expected=table+[0]*((1<<net.n_in)-len(table))
        result=verify(net,list(range(len(expected))),expected);assert result['status']=='pass'
        hashes.append(dict(net=sha(net.encode()),negative=result['negative_sha256']))
        cases+=1;addresses+=len(expected)
    # Every 3-input one-bit function under every address permutation.
    for truth in range(256):
        for order in itertools.permutations(range(3)):
            check([truth>>j&1 for j in range(8)],1,list(order))
    rng=random.Random(260897)
    for n in range(1,8):
        for k in range(8):
            length=(1<<n)-k%min(4,1<<n);table=[rng.getrandbits(6) for _ in range(length)]
            order=list(range(max(1,(length-1).bit_length())));rng.shuffle(order)
            check(table,6,order)
    return dict(status='pass',cases=cases,addresses=addresses,actual_gate_mutations_rejected=cases,
                result_hashes_sha256=sha(json.dumps(hashes).encode()),small_limit_nand=4000)


def exhaustive(net,golden):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    batches=[verify(net,list(range(start,min(start+2048,len(golden)))),golden[start:start+2048])
             for start in range(0,len(golden),2048)]
    assert all(x['status']=='pass' for x in batches)
    return dict(status='pass',addresses=len(golden),batches=batches)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scope',required=True,choices=('layer0','all'))
    ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    start=time.monotonic();out=OUT/args.scope;out.mkdir(parents=True,exist_ok=True)
    checks=small();blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    for name,digest in C_SOURCES.items():assert sha((R/name).read_bytes())==digest,name
    words=[w for layer in (range(5) if args.scope=='all' else range(1)) for w in weight_words(blob,layer)]
    known=FROZEN[args.scope];assert sha(json.dumps(words).encode())==known['table']
    n=(len(words)-1).bit_length();order=list(reversed(range(n)))
    before=lookup(words,64,'shannon');after=ordered(words,64,order)
    assert sha(before.encode())==known['raw'] and metrics(before)['nNand']==known['nand']
    assert metrics(after)['nNand']<known['mapped'] and metrics(after)['nand_depth']==metrics(before)['nand_depth']
    for name,net in [('baseline',before),('reverse',after)]:(out/(name+'.nl')).write_bytes(net.encode())
    paths=[Path(__file__).resolve()]+[R/name for name in (
        'integer_opt/weights.py','integer_opt/weights_golden.c','integer_opt/gate_check.py',
        'integer/int_model.c','physical/model.bin','physical/export.py','nand.py','bench.py','golden.py','ci.py')]
    report=dict(status='prepared; large gate checks and mapping await Actions',scope=args.scope,
        small=checks,order=order,before=metrics(before),after=metrics(after),prior_mapped_nand=known['mapped'],
        words=len(words),addresses=1<<n,table_sha256=known['table'],model_sha256=MODEL_SHA,
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        local_reference='exact table/C-source SHA match to R4 run37415286971/5282002; no repeated local C fixture',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in paths})
    receipt=out/'receipt.json';receipt.write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        expected=c_words(out,args.scope,words)
        report['baseline_all_addresses']=exhaustive(before,expected)
        report['validation']=cloud_check(out,{'reverse':after},expected)
        assert report['validation']['status']=='pass'
        report['status']='all original/candidate/mapped addresses match frozen C; mapped CEC and actual gate faults pass'
    report['seconds']=time.monotonic()-start;receipt.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','scope','small','before','after','seconds')},indent=2))


if __name__=='__main__':main()
