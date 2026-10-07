#!/usr/bin/env python3
"""Exact sampling EXP: keep double RNE, discard only the frozen zero tail."""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,struct,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_SAMPLE_WEIGHT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_SAMPLE_WEIGHT_OUT',str(R/'build/integer_opt/sample_weight')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,flip_output,blif,verilog,from_yosys
from golden import Netlist
from bench import lookup
from export import import_net,MODEL_SHA
from gate_check import verify,simulate
from ci import cec,script
sha=lambda b:hashlib.sha256(b).hexdigest()
C_SHA='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'


def table():
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    words=list(struct.unpack_from('<1025I',blob,275604))
    assert max(i for i,v in enumerate(words) if v)==754 and max(words)==65536
    assert all(v==0 for v in words[755:])
    return words


def index_net(single_round=False):
    b=Builder(16);d=list(range(2,18))
    n,_=b.add(d+[0]*3,[0,0]+d+[0])
    # n=256*q+r. RNE(RNE(n/4)/64) increments q at r>=126
    # if q is odd, and at r>=131 if q is even. It is NOT RNE(n/256).
    if single_round:
        inc=b.land(n[7],b.lor(n[8],b.reduce(n[:7],b.lor,0)))
    else:
        ge126=b.lor(n[7],b.reduce(n[1:7],b.land,1))
        ge131=b.land(n[7],b.lor(b.reduce(n[2:7],b.lor,0),b.land(n[0],n[1])))
        inc=b.mux(n[8],ge131,ge126)
    j,_=b.add(n[8:],[0]*11,inc)
    return b.finish(j)


def make(single_round=False,table_fault=False):
    b=Builder(64);top=list(range(2,34));logit=list(range(34,66))
    d,_=b.add(top+[top[-1]],[b.inv(w) for w in logit+[logit[-1]]],1)
    _,j=import_net(b,index_net(single_round),d[:16])
    words=table()[:1024]
    if table_fault:words[127]^=1
    _,w=import_net(b,lookup(words,17,'phase'),j[:10])
    valid=b.inv(b.reduce(d[16:]+[j[10]],b.lor,0))
    return b.finish([b.land(valid,v) for v in w])


def rne(n,d):
    q,r=divmod(n,d);return q+int(2*r>d or (2*r==d and q&1))


def inputs():
    rng=random.Random(261007111);pairs=[]
    # Every low16 delta at both signed endpoints and across signed zero.
    for d in range(65536):
        for t,l in [(2147483647,2147483647-d),(-2147483648+d,-2147483648),(0,-d)]:
            pairs.append((t&0xffffffff)+((l&0xffffffff)<<32))
    for _ in range(4096):
        a,b=sorted([rng.randrange(-(1<<31),1<<31) for _ in range(2)],reverse=True)
        pairs.append((a&0xffffffff)+((b&0xffffffff)<<32))
    for d in [65535,65536,65537,1<<20,1<<31,(1<<32)-2,(1<<32)-1]:
        a=(1<<31)-1;b=a-d;pairs.append(a+((b&0xffffffff)<<32))
    # Out-of-contract top<logit has a deterministic zero extension, shared
    # with the archived h3a graph, never passed to C's negative EXP index.
    for a,b in [(-2147483648,2147483647),(-1,0),(0,1),(-2147483648,-2147483647)]:
        pairs.append((a&0xffffffff)+((b&0xffffffff)<<32))
    return pairs


def fixtures(values):
    source=Path(__file__).with_name('sample_weight_golden.c')
    assert sha((R/'integer/int_model.c').read_bytes())==C_SHA
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    with tempfile.TemporaryDirectory() as tmp:
        so=Path(tmp)/'weight.so'
        subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(R/'integer'),str(source),'-o',str(so)],check=True,timeout=30)
        lib=ct.CDLL(str(so));lib.int_init.argtypes=[ct.c_void_p,ct.c_int];assert lib.int_init(blob,len(blob))==0
        lib.weight_pairs.argtypes=[ct.POINTER(ct.c_uint64),ct.POINTER(ct.c_uint32),ct.POINTER(ct.c_uint32),ct.c_uint]
        index=(ct.c_uint32*len(values))();out=(ct.c_uint32*len(values))()
        lib.weight_pairs((ct.c_uint64*len(values))(*values),index,out,len(values))
    return dict(input_sha256=sha(b''.join(x.to_bytes(8,'little') for x in values)),
                index_sha256=sha(bytes(index)),weight_sha256=sha(bytes(out)),
                count=len(values),index=list(index),weight=list(out),C_sha256=C_SHA,model_sha256=MODEL_SHA)


def source_paths():
    return ['integer_opt/sample_weight.py','integer_opt/sample_weight_golden.c','integer_opt/gate_check.py',
        'integer/int_model.c','physical/model.bin','physical/export.py','nand.py','golden.py','bench.py','ci.py',
        'integer_opt/sample_weight_units/baseline.nl','integer_opt/sample_weight_units/manifest.json']


def baseline():
    directory=Path(__file__).with_name('sample_weight_units');raw=(directory/'baseline.nl').read_bytes()
    origin=json.loads((directory/'manifest.json').read_text());net=Netlist.decode(raw,64,17)
    assert sha(raw)==origin['metrics']['sha256']=='ed6b6b1fb7ad1b8deae34c1f8271647a3b74e98bf01ee94bd1740fc94ff4af2e'
    assert metrics(net)==origin['metrics'] and origin['C_sha256']==C_SHA
    return net,origin


def reference():
    # Wide, direct two-round expression; deliberately does not use the new
    # small-index identity or truncate the mathematical delta to16 bits.
    lines=['module top(input [63:0] din,output reg [16:0] dout);',
        'wire signed [31:0] a=din[31:0],b=din[63:32];',
        'wire signed [32:0] d={a[31],a}-{b[31],b};',
        "wire [35:0] n={3'b0,d}*36'd5;",
        "wire [33:0] t=n[35:2]+{{33{1'b0}},(n[1] && (n[0] || n[2]))};",
        "wire [28:0] j={1'b0,t[33:6]}+{{28{1'b0}},(t[5] && ((|t[4:0]) || t[6]))};",
        "always @* begin dout=17'd0;if(a>=b) begin case(j)"]
    lines += [f"29'd{i}: dout=17'd{v};" for i,v in enumerate(table())]
    return '\n'.join(lines+['default: begin end','endcase end end','endmodule',''])


def cloud(net,old,values,want):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    prefix=OUT/'weight';prefix.with_suffix('.ys').write_text(script(prefix))
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'weight.yosys.log'),'-s',str(OUT/'weight.ys')],check=True,stdout=subprocess.DEVNULL,timeout=240)
    mapped=from_yosys(json.loads((OUT/'weight.mapped.yosys.json').read_text()),64,17)
    refscript=f'read_verilog {OUT}/weight.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\nopt\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n'
    (OUT/'reference.ys').write_text(refscript)
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'reference.yosys.log'),'-s',str(OUT/'reference.ys')],check=True,stdout=subprocess.DEVNULL,timeout=240)
    ref=from_yosys(json.loads((OUT/'reference.json').read_text()),64,17)
    nets=dict(source=net,mapped=mapped,reference=ref,baseline=old,negative=flip_output(net),single_round=make(True),bad_table=make(table_fault=True))
    for name,g in nets.items():
        (OUT/(name+'.nl')).write_bytes(g.encode());(OUT/(name+'.blif')).write_text(blif(g))
    proofs={}
    for name,wanted in [('baseline','equivalent'),('reference','equivalent'),('mapped','equivalent'),('negative','different'),('single_round','different'),('bad_table','different')]:
        proofs[name]=cec(abc,OUT/'source.blif',OUT/(name+'.blif'),OUT/(name+'.cec.log'));assert proofs[name]['verdict']==wanted
    checked=[]
    for offset in range(0,len(values),8192):
        got=verify(mapped,values[offset:offset+8192],want[offset:offset+8192]);assert got['status']=='pass';checked.append(got)
    return dict(status='pass',all_input_bits=64,all_output_bits=17,proofs=proofs,nets={n:metrics(g) for n,g in nets.items()},mapped_C_batches=checked)


def main():
    start=time.monotonic();p=argparse.ArgumentParser();p.add_argument('--reuse',type=Path);p.add_argument('--cloud',action='store_true');a=p.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);words=table();ix=index_net();net=make();single=make(True);badtab=make(table_fault=True)
    old,origin=baseline()
    values=inputs();c=json.loads(a.reuse.read_text()) if a.reuse else fixtures(values)
    assert c['count']==len(values) and c['input_sha256']==sha(b''.join(x.to_bytes(8,'little') for x in values))
    assert c['C_sha256']==C_SHA and c['model_sha256']==MODEL_SHA
    assert c['index_sha256']==sha(b''.join(x.to_bytes(4,'little') for x in c['index']))
    assert c['weight_sha256']==sha(b''.join(x.to_bytes(4,'little') for x in c['weight']))
    # Pure mathematical exhaustive check of the double-round identity.
    expected=[rne(rne(5*d,4),64) for d in range(65536)]
    assert all(j==((5*d)>>8)+int(((5*d)&255)>130 or ((((5*d)&255)>=126) and (((5*d)>>8)&1))) for d,j in enumerate(expected))
    ixcheck=verify(ix,list(range(65536)),expected);assert ixcheck['status']=='pass'
    assert c['index'][:3*65536:3]==c['index'][1:3*65536:3]==c['index'][2:3*65536:3]==expected
    assert c['weight'][:3*65536:3]==[words[j] if j<=1024 else 0 for j in expected]
    batches=[]
    for offset in range(0,len(values),8192):
        record=verify(net,values[offset:offset+8192],c['weight'][offset:offset+8192]);assert record['status']=='pass';batches.append(record)
    faults={}
    for name,g in [('single_round',single),('table_entry_127',badtab)]:
        mismatches=sum(x!=y for x,y in zip(simulate(g,values[:65536*3:3]),c['weight'][:65536*3:3]));assert mismatches>0;faults[name]=mismatches
    for name,g in [('weight',net),('index',ix),('single_round',single),('bad_table',badtab)]:
        (OUT/(name+'.nl')).write_bytes(g.encode());(OUT/(name+'.v')).write_text(verilog(g))
    (OUT/'weight.ref.v').write_text(reference())
    sources={}
    for n in source_paths():
        path=Path(__file__).parent/Path(n).name if n in ('integer_opt/sample_weight.py','integer_opt/sample_weight_golden.c') else (Path(__file__).parent/Path(n).relative_to('integer_opt') if n.startswith('integer_opt/sample_weight_units/') else R/n)
        sources[n]=sha(path.read_bytes())
    (OUT/'cases.json').write_text(json.dumps(c,separators=(',',':'))+'\n')
    report=dict(status='local pass; universal CEC pending Actions',metrics=metrics(net),index=metrics(ix),baseline=origin,
        rule='exp_weight(RNE(5*(top-logit),4)); top>=logit signed32; invalid order extends to zero',
        zero_tail=dict(first_zero=755,last_nonzero=754,all_high16_delta_zero_weight=True),
        exact_index_identity='n=5*delta; q=n>>8; r=n&255; j=q+(q odd ? r>=126 : r>=131)',
        C_vectors=len(values),index_exhaustive=ixcheck,weight_batches=batches,actual_fault_mismatches=faults,
        C_cases_sha256=sha((OUT/'cases.json').read_bytes()),sources=sources,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False)
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if a.cloud:report['verification']=cloud(net,old,values,c['weight']);report['status']='exact C and universal baseline/reference/mapped CEC pass, actual faults rejected'
    report['seconds']=time.monotonic()-start
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('sources','weight_batches')},indent=2))


if __name__=='__main__':main()
