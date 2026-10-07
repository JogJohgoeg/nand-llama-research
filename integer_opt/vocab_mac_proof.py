"""Cut the actual head MAC output and prove every signed8 sample cofactor.

Mechanical recomposition binds the cut to the full transition. All38 MAC
input bits remain covered:256 disjoint sample cofactors, free acc22/E8.
"""
from pathlib import Path
import json,os,shutil,subprocess
from nand import Builder,blif,flip_output,from_yosys,metrics
from export import import_net
from gate_check import verify
from ci import cec

PINS=list(range(326,348))+list(range(382,398))


def abstract(core,mac):
    assert (core.n_in,core.n_out,core.n_state)==(416,401,0)
    b=Builder(core.n_in);p=list(range(2,418));_,actual=import_net(b,core,p);before=len(b.gates)
    _,ports=import_net(b,mac,[p[i] for i in PINS])
    assert len(set(ports))==22
    assert all(418<=v<418+before for v in ports),'every cut must be an actual existing MAC output'
    c=Builder(438);free=list(range(418,440));cuts={}
    for wire,value in zip(ports,free):
        assert wire not in cuts;cuts[wire]=value
        opposite=b.inverse.get(wire)
        if opposite is not None and opposite<418+before:
            assert opposite not in cuts;cuts[opposite]=c.inv(value)
    wires=list(range(418))
    for i,(_,a,z) in enumerate(b.gates[:before]):
        wire=418+i;wires.append(cuts[wire] if wire in cuts else c.nand(wires[a],wires[z]))
    body=c.finish([wires[w] for w in actual])
    d=Builder(416);pins=list(range(2,418));_,original=import_net(d,core,pins)
    _,values=import_net(d,mac,[pins[i] for i in PINS]);_,rebuilt=import_net(d,body,pins+values)
    assert rebuilt==original,'all351D/50out must reconstruct from actual macro'
    return body,dict(actual_MAC_input_bits=PINS,positive_cut_wires=ports,cut_bits=22,cut_polarities=len(cuts),
                     recomposition_all_401_outputs_identical=True,actual_source=metrics(core),macro=metrics(mac),body=metrics(body))


def cut_sample(net,sample):
    assert (net.n_in,net.n_out,net.n_state)==(38,22,0) and 0<=sample<256
    b=Builder(30);acc=list(range(2,24));coefficient=list(range(24,32))
    _,out=import_net(b,net,acc+[sample>>i&1 for i in range(8)]+coefficient)
    return b.finish(out)


def common_reference(original):
    head='module top(input [415:0] din,output [400:0] dout);'
    expression='wire [21:0] sum=acc+{{6{product[15]}},product};'
    assert original.count(head)==original.count(expression)==1
    return original.replace(head,'module top(input [437:0] din,output [400:0] dout);').replace(expression,'wire [21:0] sum=din[437:416];')


def mac_reference():
    return '''module top(input [37:0] din,output [21:0] dout);
wire signed [21:0] acc=din[21:0];
wire signed [7:0] sample=din[29:22],coefficient=din[37:30];
wire signed [15:0] product=sample*coefficient;
assign dout=acc+{{6{product[15]}},product};
endmodule
'''


def small(mac):
    accs=[-(1<<21),-65536,-1,0,1,127,65536,(1<<21)-1];coeff=[-128,-127,-1,0,1,2,126,127]
    inputs=[(a&((1<<22)-1))+((e&255)<<22) for a in accs for e in coeff];fault=flip_output(mac)
    counts=[]
    for sample in range(256):
        s=sample if sample<128 else sample-256;expected=[(a+s*e)&((1<<22)-1) for a in accs for e in coeff]
        g=cut_sample(mac,sample);bad=cut_sample(fault,sample);assert max(len(g.records),len(bad.records))<=4000
        good=verify(g,inputs,expected);negative=verify(bad,inputs,[v^1 for v in expected])
        assert good['status']==negative['status']=='pass'
        counts.append(dict(sample=sample,source=metrics(g),actual_full_MAC_fault=metrics(bad),cases=len(inputs)))
    return dict(status='pass',cofactors=256,total_cases=256*len(inputs),actual_full_MAC_output_faults_rejected=256*len(inputs),
                free_input_bits=list(range(22))+list(range(30,38)),fixed_input_bits=list(range(22,30)),parts=counts)


def prepare(core,mac,reference,out):
    out=Path(out);body,bind=abstract(core,mac);mutant,_=abstract(flip_output(core),mac)
    b=Builder(438);pins=list(range(2,440));_,good=import_net(b,body,pins);_,bad=import_net(b,mutant,pins)
    assert sum(x!=y for x,y in zip(good,bad))==1
    bind['actual_whole_body_D_fault_breaks_common_part']=True
    (out/'body.mac_abstract.nl').write_bytes(body.encode())
    (out/'body.mac_abstract.ref.v').write_text(common_reference(reference))
    (out/'head_mac.ref.v').write_text(mac_reference());(out/'head_mac.nl').write_bytes(mac.encode())
    (out/'head_mac.bad.nl').write_bytes(flip_output(mac).encode())
    result=dict(binding=bind,small=small(mac),method='actual MAC cut + universal common body + all256 disjoint signed8 sample cofactors; original hardware and C vectors unchanged')
    (out/'mac_preparation.json').write_text(json.dumps(result,indent=2)+'\n');return result


def mapped_reference(prefix,ni,no):
    ys=Path(str(prefix)+'.ys')
    ys.write_text(f'read_verilog {prefix}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {prefix}.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(prefix)+'.yosys.log','-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    return from_yosys(json.loads(Path(str(prefix)+'.ref.json').read_text()),ni,no)


def prove(core,mac,out):
    assert os.getenv('GITHUB_ACTIONS')=='true';out=Path(out)
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    body,bind=abstract(core,mac);reference=mapped_reference(out/'body.mac_abstract',438,401)
    for name,g in [('source',body),('reference',reference),('negative',flip_output(body))]:(out/('mac_body.'+name+'.blif')).write_text(blif(g))
    proof=cec(abc,out/'mac_body.source.blif',out/'mac_body.reference.blif',out/'mac_body.cec.log');assert proof['verdict']=='equivalent'
    negative=cec(abc,out/'mac_body.negative.blif',out/'mac_body.reference.blif',out/'mac_body.negative.log');assert negative['verdict']=='different'
    ref=mapped_reference(out/'head_mac',38,22);actual_fault=flip_output(mac)
    result=dict(status='in_progress',binding=bind,free_MAC_outputs_common_body_CEC=proof,actual_common_D_fault=negative,
                all_256_disjoint_sample_partitions=True,free_acc22_and_coefficient8=True,partitions=[])
    folder=out/'mac_parts';folder.mkdir(exist_ok=True)
    for sample in range(256):
        parts=[cut_sample(g,sample) for g in (mac,ref,actual_fault)];prefix=folder/f's{sample:03}'
        for name,g in zip(('source','reference','negative'),parts):
            prefix.with_suffix('.'+name+'.nl').write_bytes(g.encode());prefix.with_suffix('.'+name+'.blif').write_text(blif(g))
        good=cec(abc,prefix.with_suffix('.source.blif'),prefix.with_suffix('.reference.blif'),prefix.with_suffix('.cec.log'))
        bad=cec(abc,prefix.with_suffix('.source.blif'),prefix.with_suffix('.negative.blif'),prefix.with_suffix('.negative.log'))
        assert good['verdict']=='equivalent' and bad['verdict']=='different'
        result['partitions'].append(dict(sample=sample,metrics=[metrics(g) for g in parts],proof=good,negative=bad))
        (out/'mac_proof.json').write_text(json.dumps(result,indent=2)+'\n')
    assert [x['sample'] for x in result['partitions']]==list(range(256))
    result.update(status='pass',universal_MAC_input_bits=38,universal_common_input_bits=438,
                  total_partition_cec_seconds=round(sum(x['proof']['seconds'] for x in result['partitions']),3))
    (out/'mac_proof.json').write_text(json.dumps(result,indent=2)+'\n');return result
