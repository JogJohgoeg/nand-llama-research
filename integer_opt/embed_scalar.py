#!/usr/bin/env python3
"""Fuse the frozen E8 row table and head column mux; keep row/column pins.

Only small gates run locally. Actions exhausts all 15-bit addresses through
C/source/mapped graphs, and proves original/compressed/mapped equivalence.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,time
R=Path(os.environ.get('H3_EMBED_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from bench import lookup
from export import import_net,MODEL_SHA
from nand import Builder,blif,flip_output,metrics
from gate_check import verify
from ci import cec
from weights import cloud_check
from weight_order import ordered,exhaustive,C_SOURCES
OUT=Path(os.environ.get('H3_EMBED_OUT',str(R/'build/integer_opt/embed_scalar')))
sha=lambda data:hashlib.sha256(data).hexdigest()


def composite(rows,width,column_bits):
    """Same row table and binary column mux as the original budget."""
    table=lookup(rows,width*(1<<column_bits),'shannon');rb=table.n_in
    b=Builder(rb+column_bits);_,values=import_net(b,table,list(range(2,2+rb)))
    cols=[values[i*width:(i+1)*width] for i in range(1<<column_bits)]
    for s in range(2+rb,2+rb+column_bits):
        cols=[[b.mux(s,a,c) for a,c in zip(cols[i],cols[i+1])] for i in range(0,len(cols),2)]
    return table,b.finish(cols[0])


def scalar_table(rows,width,column_bits):
    rb=max(1,(len(rows)-1).bit_length());mask=(1<<rb)-1
    return [((rows[a&mask]>>((a>>rb)*width))&((1<<width)-1)) if a&mask<len(rows) else 0
            for a in range(1<<(rb+column_bits))]


def small():
    rng=random.Random(260899);results=[];addresses=0
    for rb in range(1,5):
        for cb in range(1,5):
            for width in (1,3,8):
                count=(1<<rb)-1;rows=[rng.getrandbits(width*(1<<cb)) for _ in range(count)]
                actual_rb=max(1,(len(rows)-1).bit_length())
                truth=scalar_table(rows,width,cb)
                order=list(reversed(range(actual_rb)))+list(reversed(range(actual_rb,actual_rb+cb)))
                _,old=composite(rows,width,cb);new=ordered(truth,width,order)
                for label,net in [('composite',old),('reordered',new)]:
                    assert len(net.records)<=4000
                    checked=verify(net,list(range(len(truth))),truth);assert checked['status']=='pass'
                    results.append(dict(name=label,net=sha(net.encode()),negative=checked['negative_sha256']))
                    addresses+=len(truth)
    return dict(status='pass',graphs=len(results),addresses=addresses,
                actual_gate_mutations_rejected=len(results),result_sha256=sha(json.dumps(results).encode()))


def c_codes(words):
    source=Path(__file__).with_name('embed_scalar_golden.c');lib=OUT/'embedding.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared',
                    '-I',str(R/'integer_opt'),str(source),'-o',str(lib)],check=True,timeout=30)
    g=ct.CDLL(str(lib));g.int_init.argtypes=[ct.c_void_p,ct.c_int]
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    assert g.int_init(blob,len(blob))==0
    g.embedding_code.argtypes=[ct.c_uint];g.embedding_code.restype=ct.c_uint32
    codes=[g.embedding_code(i) for i in range(32768)]
    assert codes==words and g.embedding_code(32768)==0
    return dict(status='pass',addresses=len(codes),table_sha256=sha(bytes(codes)),
                valid_entries=192*128,padded_entries=64*128),codes


def column_cofactor(net,column):
    assert net.n_in==15 and net.n_out==8 and not net.n_state and 0<=column<128
    b=Builder(8)
    _,y=import_net(b,net,list(range(2,10))+[column>>i&1 for i in range(7)])
    return b.finish(y)


def replacement_proof(before,after,expected):
    """Disjoint exhaustive partition: all128 columns, every8-bit row free."""
    assert os.getenv('GITHUB_ACTIONS')=='true'
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    changed=flip_output(after);out=OUT/'columns';out.mkdir(exist_ok=True)
    result=dict(status='in_progress',source_sha256=sha(before.encode()),
        candidate_sha256=sha(after.encode()),actual_full_graph_fault_sha256=sha(changed.encode()),
        fixed_input_bits=list(range(8,15)),free_input_bits=list(range(8)),
        exhaustive_disjoint_partition=True,columns=[])
    for col in range(128):
        graphs=[column_cofactor(g,col) for g in (before,after,changed)]
        golden=expected[col*256:(col+1)*256]
        truth=[verify(g,list(range(256)),golden) for g in graphs[:2]]
        assert all(v['status']=='pass' for v in truth)
        prefix=out/f'c{col:03}'
        for label,g in zip(('baseline','candidate','negative'),graphs):
            prefix.with_suffix('.'+label+'.nl').write_bytes(g.encode())
            prefix.with_suffix('.'+label+'.blif').write_text(blif(g))
        good=cec(abc,prefix.with_suffix('.baseline.blif'),prefix.with_suffix('.candidate.blif'),prefix.with_suffix('.cec.log'))
        bad=cec(abc,prefix.with_suffix('.baseline.blif'),prefix.with_suffix('.negative.blif'),prefix.with_suffix('.negative.log'))
        assert good['verdict']=='equivalent' and bad['verdict']=='different'
        result['columns'].append(dict(column=col,metrics=[metrics(g) for g in graphs],
                                      truth=truth,proof=good,negative=bad))
        (OUT/'replacement.json').write_text(json.dumps(result,indent=2)+'\n')
    assert [r['column'] for r in result['columns']]==list(range(128))
    result.update(status='pass',total_input_addresses=128*256,
                  note='all cofactored gates come mechanically from the actual full graphs; no valid-row assumption')
    (OUT/'replacement.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    start=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    checks=small();blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    assert sha((R/'integer/int_model.c').read_bytes())==C_SOURCES['integer/int_model.c']
    e=blob[243348:267924];assert len(e)==192*128
    rows=[int.from_bytes(e[i*128:(i+1)*128],'little') for i in range(192)]
    assert sha(json.dumps(rows).encode())=='20cc44e64305efe1c974d317387d0aa7ecb733bbc78dbe13b9f7c5764905c1b2'
    wide,before=composite(rows,8,7);words=scalar_table(rows,8,7)
    assert metrics(wide)['nNand']==48702 and metrics(before)['nNand']==49725
    assert sha(wide.encode())=='b43d3cf1c0f320e7e12ab79fe09d54e25dd56299e0bab426a8ad827452267e76'
    order=list(reversed(range(8)))+list(reversed(range(8,15)));after=ordered(words,8,order)
    assert metrics(after)['nNand']==43915 and metrics(after)['nand_depth']==31
    parsed,expected=c_codes(words)
    for name,net in [('baseline',before),('scalar',after)]:(OUT/(name+'.nl')).write_bytes(net.encode())
    paths=[Path(__file__).resolve(),Path(__file__).with_name('embed_scalar_golden.c').resolve()]
    paths += [R/name for name in ('integer_opt/weight_order.py','integer_opt/weights.py',
        'integer_opt/gate_check.py','integer_opt/weights_golden.c','integer/int_model.c',
        'physical/model.bin','physical/export.py','bench.py','nand.py','golden.py','ci.py')]
    report=dict(status='prepared; large graph checks and CEC await Actions',small=checks,c_parser=parsed,
        order=order,wide_row=metrics(wide),before=metrics(before),after=metrics(after),
        old_separate_nand=48702+3071,boundary_copy_elimination_nand=2048,
        actual_composite_saved_nand=49725-43915,model_sha256=MODEL_SHA,
        interface='15 inputs: row[7:0] then column[6:0]; unsigned 8-bit two-complement E8 code; rows192..255=0',
        numerical_contract_changed=False,cycles_changed=False,whole_budget_changed=False,adopted=False,
        scope='shared scalar constant provider for embedding/head; no complete head controller claim',
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in paths})
    receipt=OUT/'receipt.json';receipt.write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['baseline_all_addresses']=exhaustive(before,expected)
        report['validation']=cloud_check(OUT,{'scalar':after},expected)
        assert report['validation']['status']=='pass'
        receipt.write_text(json.dumps(report,indent=2)+'\n')
        report['replacement_proof']=replacement_proof(before,after,expected)
        report['status']='all32768 C/original/compressed/mapped addresses and both CECs pass; actual faults rejected'
    report['seconds']=time.monotonic()-start;receipt.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','small','c_parser','before','after','seconds')},indent=2))


if __name__=='__main__':main()
