#!/usr/bin/env python3
"""Replace only the actual gate/up selector in the proved full FFN sublayer.

All original state/scheduling and external pins remain; down weights are kept.
Local mode reads source-bound accepted fixtures, never runs the large graph.
"""
from pathlib import Path
import sys,os,json,hashlib,signal,argparse,shutil,time
R=Path(os.environ.get('H3_FF_ORDER_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import h_read_shared,ff_row
from weight_order import ordered
from nand import Builder,metrics,with_state,flip_output,blif
from export import import_net,rtl
from golden import Netlist
from gate_check import verify
from weight_order import small
OUT=Path(os.environ.get('H3_FF_ORDER_OUT',str(R/'build/integer_opt/ff_weight_order')))
SOURCE_SHA='5c75b1aca14e6f96c8250724fd55f8da9745c073c077bf8612152c7b11e87f96'
VECTOR_SHA='5ea387ad71b85ca4fc248179d9c6857daa8c0cf206459086a71b0365dd711910'
CASES={'cases.json':'e586e22a4dd3a2501781d75b3f790dff4454ec07b0e16a959525150eaa424074',
 'bank_cases.json':'bdf2bc5c437f2e4354ee113564b8f7f9b90508a9e62783a1a78afdcdcb5b6502',
 'h_cases.json':'249821383d8d5bcc0fa3548220745ef88d35134b9e69844f8576b21f4f1ba154'}
sha=lambda b:hashlib.sha256(b).hexdigest()


def table_inputs(b,q):
    phase=q[534:538]
    eq=lambda n:b.reduce([v if n>>i&1 else b.inv(v) for i,v in enumerate(phase)],b.land,1)
    up=b.lor(eq(4),b.lor(eq(5),eq(6)))
    return q[512:514]+q[503:512]+[up]


def bind(net,table):
    ni=net.n_state+net.n_in;b=Builder(ni)
    q=list(range(2,net.n_state+2));p=list(range(net.n_state+2,ni+2))
    ds,y=import_net(b,net,p,q);before=len(b.gates)
    _,ports=import_net(b,table,table_inputs(b,q))
    assert len(set(ports))==64 and all(ni+2<=w<ni+2+before for w in ports)
    return b,before,ds+y,ports


def rebuild(b,before,outs,ports,c,values):
    ni=b.n_in;cuts={}
    for w,v in zip(ports,values):
        assert w not in cuts;cuts[w]=v
        other=b.inverse.get(w)
        if other is not None:
            assert other not in cuts;cuts[other]=c.inv(v)
    wires=list(range(ni+2))
    for i,(_,a,z) in enumerate(b.gates[:before]):
        w=ni+2+i;wires.append(cuts[w] if w in cuts else c.nand(wires[a],wires[z]))
    return [wires[v] for v in outs],len(cuts)


def replacement(net,old,new):
    b,before,outs,ports=bind(net,old);c=Builder(b.n_in)
    _,values=import_net(c,new,table_inputs(c,list(range(2,net.n_state+2))))
    result,cuts=rebuild(b,before,outs,ports,c,values)
    return with_state(c.finish(result),net.n_state),cuts


def abstract(net,table):
    b,before,outs,ports=bind(net,table);c=Builder(b.n_in+64)
    result,cuts=rebuild(b,before,outs,ports,c,list(range(2+b.n_in,2+b.n_in+64)))
    body=c.finish(result)
    d=Builder(b.n_in);q=list(range(2,net.n_state+2));p=list(range(net.n_state+2,b.n_in+2))
    ds,original=import_net(d,net,p,q);_,values=import_net(d,table,table_inputs(d,q))
    _,back=import_net(d,body,list(range(2,b.n_in+2))+values)
    assert back==ds+original
    return body,dict(actual_ports=ports,cut_polarities=cuts,recomposition_all_D_outputs_identical=True)


def compose(old):
    assert sha(old.encode())==SOURCE_SHA
    table,words=ff_row.weight_table();candidate=ordered(words,64,list(reversed(range(12))))
    new,cuts=replacement(old,table,candidate)
    left,l=abstract(old,table);right,r=abstract(new,candidate)
    b=Builder(left.n_in);pins=list(range(2,left.n_in+2))
    _,lo=import_net(b,left,pins);_,ro=import_net(b,right,pins);assert lo==ro
    changed,_=abstract(flip_output(new),candidate);_,bo=import_net(b,changed,pins)
    assert sum(x!=y for x,y in zip(lo,bo))==1
    bodies=(b.finish(lo),b.finish(ro),b.finish(bo))
    binding=dict(previous=l,candidate=r,all_retained_state_bits=old.n_state,outputs=old.n_out,
        body_same_canonical_D_outputs=True,actual_whole_graph_output_mutation_breaks_body=True,
        proof_method='actual64 selector outputs and both polarities, exact recomposition, full selector plus common-body CEC',
        scope='replacement valid for every old state/input without a new range or ownership premise; inherited FFN arithmetic contracts unchanged')
    return new,table,candidate,words,bodies,binding


def fixtures(cloud,path):
    common=h_read_shared.source.source.source.source;common.OUT=OUT
    if cloud:
        assert path is None
        old=h_read_shared.make()[0]
        data=common.cases(common.reference())
        h_read_shared.source.source.OUT=OUT
        ff=h_read_shared.source.source.c_bank_cases(data)
        h_read_shared.source.OUT=OUT
        h=h_read_shared.source.h_cases(data)
        files={n:(json.dumps(value,indent=2)+'\n').encode() for n,value in zip(CASES,(data,ff,h))}
    else:
        assert path is not None,'local construction needs accepted R49 artifact directory'
        r=json.loads((path/'receipt.json').read_text())
        assert r['metrics']['sha256']==SOURCE_SHA and r['verification']['actual_rtl_mutation_rejected']
        assert r['verification']['observed']['clocks']==2362346
        assert r['verification']['observed']['vector_sha256']==VECTOR_SHA
        for n,digest in r['sources'].items():assert sha((R/n).read_bytes())==digest,n
        raw=(path/'stream.nl').read_bytes();assert sha(raw)==SOURCE_SHA
        old=Netlist.decode(raw,26,32);files={n:(path/n).read_bytes() for n in CASES}
    for n,raw in files.items():
        assert sha(raw)==CASES[n],n
        (OUT/n).write_bytes(raw)
    return old,common,[json.loads(files[n]) for n in CASES]


def prove(a,b,negative,name):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    for label,net in [('reference',a),('candidate',b),('negative',negative)]:
        (OUT/(name+'.'+label+'.blif')).write_text(blif(net))
    good=cec(abc,OUT/(name+'.reference.blif'),OUT/(name+'.candidate.blif'),OUT/(name+'.cec.log'))
    bad=cec(abc,OUT/(name+'.reference.blif'),OUT/(name+'.negative.blif'),OUT/(name+'.negative.log'))
    assert good['verdict']=='equivalent' and bad['verdict']=='different'
    return dict(proof=good,negative=bad)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true')
    ap.add_argument('--reference-dir',type=Path);args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);begin=time.monotonic()
    sm=small();old,common,data=fixtures(args.cloud,args.reference_dir)
    net,previous,candidate,words,bodies,binding=compose(old)
    for name,g in [('stream',net),('previous',previous),('candidate',candidate),('body',bodies[0]),('body_negative',bodies[2])]:
        (OUT/(name+'.nl')).write_bytes(g.encode())
    (OUT/'stream.v').write_text(rtl(net,'ff_sublayer'))
    paths={Path(__file__).resolve()}
    for mod in list(sys.modules.values()):
        name=getattr(mod,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/verify.py','physical/nl_sim.c',
        'integer_opt/weights_golden.c','integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl',
        'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/serial_sqrt.nl',
        'physical/units/dot32.nl','physical/units/resid.nl']:paths.add(R/name)
    report=dict(status='prepared; actual-cone binding and small checks pass, full cloud checks pending',
        before=metrics(old),metrics=metrics(net),selector_before=metrics(previous),selector_after=metrics(candidate),
        body_metrics=[metrics(x) for x in bodies],binding=binding,small=sm,
        table_sha256=sha(json.dumps(words).encode()),fixtures_sha256=CASES,
        expected_clocks=2362346,expected_vector_sha256=VECTOR_SHA,
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,
        run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
    receipt=OUT/'receipt.json';receipt.write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        ff_row.OUT=OUT/'weight_c';ff_row.OUT.mkdir(exist_ok=True);c=ff_row.reference()
        expected=[c.ff_word(i) for i in range(4096)];assert expected==words
        report['all_weight_addresses']={name:verify(g,list(range(4096)),expected) for name,g in [('previous',previous),('candidate',candidate)]}
        report['selector_proof']=prove(previous,candidate,flip_output(candidate),'selector')
        report['body_proof']=prove(*bodies,'body')
        receipt.write_text(json.dumps(report,indent=2)+'\n')
        report['verification']=common.check(net,data[0],scalar_x=True,ff_codes=data[1],h_codes=data[2],shared_h_read=True)
        observed=report['verification']['observed']
        assert observed['clocks']==observed['counts']['h_read_owner_checks']==2362346
        assert observed['vector_sha256']==VECTOR_SHA
        report['status']='full-domain selector and common-body CEC, complete actual FFN NAND/RTL/C and real faults pass'
    report['seconds']=time.monotonic()-begin;receipt.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','before','metrics','selector_before','selector_after','seconds')},indent=2))


if __name__=='__main__':main()

