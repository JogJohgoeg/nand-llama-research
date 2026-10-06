#!/usr/bin/env python3
"""Fixed real layer0 Q matrix, scalar A8 input, local ring and exact scale.

Mechanical NAND construction and independent C vectors are local. All large
gate/RTL/EDA work requires GitHub Actions. This is one matrix, not inference.
"""
import argparse,ctypes as ct,hashlib,json,os,random,signal,subprocess,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'integer_opt'),str(ROOT/'physical'),str(ROOT)]
from export import import_net,load_unit,rtl,MODEL_SHA
from nand import Builder,metrics,with_state,verify_state,flip_output,blif,from_yosys
from bench import lookup
from scale_pipeline import make as scale_net,BOUNDED_LATENCY
from prefix_codec import GOLDEN_SHA
NI,NO=33,32
NAME='int_c16_q_matrix'
OUT=ROOT/'build/matrix_physical'
sha=lambda x:hashlib.sha256(x).hexdigest()


def control(b,phase,ins):
    reset,start,legal,last_fill,last_group,last_row,qvalid,yready,enable,scaled=ins
    eq=lambda n:b.reduce([v if n>>i&1 else b.inv(v) for i,v in enumerate(phase)],b.land,1)
    p=[eq(i) for i in range(6)];keep=b.inv(reset)
    begin=b.reduce([keep,p[0],start,legal],b.land,1)
    ready=b.land(keep,p[1]);valid=b.land(keep,p[5])
    fill=b.land(ready,qvalid);take=b.reduce([keep,p[2],enable],b.land,1)
    launch=b.land(keep,p[3]);ack=b.land(valid,yready)
    nxt=phase[:]
    for en,n in ((begin,1),(b.land(fill,last_fill),2),(b.land(take,last_group),3),(launch,4),
                 (b.land(p[4],scaled),5),(ack,2)):
        nxt=[b.mux(en,v,n>>i&1) for i,v in enumerate(nxt)]
    clear=b.lor(reset,b.land(ack,last_row))
    return [b.land(b.inv(clear),v) for v in nxt],[begin,fill,take,launch,ack,ready,valid,b.inv(p[0])]


def small_control():
    b=Builder(13);ds,out=control(b,list(range(2,5)),list(range(5,15)))
    net=b.finish(ds+out);xs=list(range(8192));ys=[]
    for x in xs:
        p=x&7;r,s,l,lf,lg,lr,qv,yr,en,sc=[x>>(3+j)&1 for j in range(10)]
        begin=int(not r and p==0 and s and l);ready=int(not r and p==1);valid=int(not r and p==5)
        fill=ready*qv;take=int(not r and p==2 and en);launch=int(not r and p==3);ack=valid*yr
        n=p
        if r or ack and lr:n=0
        elif begin:n=1
        elif fill and lf:n=2
        elif take and lg:n=3
        elif launch:n=4
        elif p==4 and sc:n=5
        elif ack:n=2
        actions=[begin,fill,take,launch,ack,ready,valid,int(p!=0)]
        ys.append(n+(sum(v<<j for j,v in enumerate(actions))<<3))
    report=verify_state(net,xs,ys,3);report.pop('nl_hex');return report


def bank(b,old,q,fill,take):
    filled=old[8:]+q;rotated=old[256:]+old[:256]
    return [b.mux(take,b.mux(fill,a,f),r) for a,f,r in zip(old,filled,rotated)]


def small_bank():
    # 40 codes retain the actual32-code consumer stride, below4k NAND.
    ns=320;b=Builder(ns+10);old=list(range(2,ns+2));ins=list(range(ns+2,ns+12))
    net=b.finish(bank(b,old,ins[:8],*ins[8:])+old[:256]);rng=random.Random(260704);xs=[];ys=[]
    for _ in range(512):
        m=rng.getrandbits(ns);q=rng.randrange(256);f=rng.randrange(2);t=rng.randrange(2)
        nxt=(m>>256)+((m&((1<<256)-1))<<(ns-256)) if t else ((m>>8)+(q<<(ns-8)) if f else m)
        xs.append(m+((q+(f<<8)+(t<<9))<<ns));ys.append(nxt+((m&((1<<256)-1))<<ns))
    result=verify_state(net,xs,ys,ns);result.pop('nl_hex');return result


def weight_table():
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    words=[]
    for address in range(512):
        w=0
        for j in range(32):
            k=32*address+j;digit=blob[8+k//4]>>(2*(k%4))&3;assert digit!=3;w|=digit<<(2*j)
        words.append(w)
    return lookup(words,64,'shannon'),words


def make(weights):
    scale=scale_net(True);dot=load_unit('dot32');assert scale.n_state==418
    blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    alpha=int.from_bytes(blob[243208:243212],'little');assert 0<alpha<262144
    ns=1498;b=Builder(ns+NI);old=list(range(2,ns+2));ins=list(range(ns+2,ns+2+NI))
    mem=old[:1024];ss=old[1024:1442];acc=old[1442:1459];maximum=old[1459:1479]
    index=old[1479:1486];row=old[1486:1493];group=old[1493:1495];phase=old[1495:1498]
    reset,start=ins[:2];q=ins[2:10];m=ins[10:30];qvalid,yready,enable=ins[30:]
    _,so=import_net(b,scale,[0]*57,ss)
    legal=b.land(b.reduce(m,b.lor,0),b.inv(b.land(m[19],b.reduce(m[:19],b.lor,0))))
    pd,actions=control(b,phase,[reset,start,legal,b.reduce(index,b.land,1),b.land(*group),
                       b.reduce(row,b.land,1),qvalid,yready,enable,so[20]])
    begin,fill,take,launch,ack,ready,valid,busy=actions
    _,trits=import_net(b,weights,group+row);_,dotout=import_net(b,dot,mem[:256]+trits)
    summed=b.add(acc,dotout[:17])[0]
    sd,so=import_net(b,scale,[reset,launch]+acc+maximum+[alpha>>j&1 for j in range(18)],ss)
    keep=b.inv(reset);clear=b.lor(reset,b.lor(begin,ack));init=b.lor(reset,begin)
    ds=bank(b,mem,q,fill,take)+sd
    ds += [b.land(b.inv(clear),b.mux(take,a,s)) for a,s in zip(acc,summed)]
    ds += [b.land(keep,b.mux(begin,a,s)) for a,s in zip(maximum,m)]
    ds += [b.land(b.inv(init),v) for v in b.add(index,[0]*7,fill)[0]]
    ds += [b.land(b.inv(init),v) for v in b.add(row,[0]*7,ack)[0]]
    ds += [b.land(b.inv(clear),v) for v in b.add(group,[0]*2,take)[0]]+pd
    assert len(ds)==ns
    result=with_state(b.finish(ds+so[:20]+row+group+[ready,valid,busy]),ns)
    return result,dict(weights=metrics(weights),dot=metrics(dot),scale=metrics(scale),alpha=alpha,
        activation_bits=1024,other_bits=474,real_trits=16384,layer=0,matrix=0,shape=[128,128],
        scope='fixed true Q matrix:128 scalar A8 inputs ->128 signed20 outputs; quantization,norm,RoPE,attention and model outside')


def reference():
    assert sha((ROOT/'integer/int_model.c').read_bytes())==GOLDEN_SHA
    p=OUT/'reference.c';p.write_text('#include '+json.dumps(str(ROOT/'integer_opt/weights_golden.c'))+'\n'+'''
void qmatrix_reference(const int8_t *q,int32_t m,int32_t *out) {
    for(int row=0;row<128;row++) {
        int32_t sum=0;
        for(int col=0;col<128;col++) {sum+=(int32_t)q[col]*weights[128*row+col];}
        out[row]=sat(int_rne((int64_t)sum*m*alpha[0],33292288));
    }
}
int32_t qmatrix_quant(const int32_t *x,int8_t *q,int32_t *out) {
    int32_t m=quant(x,128,q);linear(x,0,0,out);return m;
}
''')
    so=OUT/'reference.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared',str(p),'-o',str(so)],check=True,timeout=30)
    c=ct.CDLL(str(so));blob=(ROOT/'physical/model.bin').read_bytes();assert sha(blob)==MODEL_SHA
    c.int_init.argtypes=[ct.c_void_p,ct.c_int];assert c.int_init(blob,len(blob))==0
    c.true_weight_word.argtypes=[ct.c_uint,ct.c_int];c.true_weight_word.restype=ct.c_uint64
    c.qmatrix_reference.argtypes=[ct.POINTER(ct.c_int8),ct.c_int32,ct.POINTER(ct.c_int32)]
    c.qmatrix_quant.argtypes=[ct.POINTER(ct.c_int32),ct.POINTER(ct.c_int8),ct.POINTER(ct.c_int32)];c.qmatrix_quant.restype=ct.c_int32
    return c


def fixtures(c,words,alpha):
    assert words==[int(c.true_weight_word(i,0)) for i in range(512)]
    rng=random.Random(260705);cases=[]
    inputs=[[0]*128,[-524288]*128,[524287]*128,[(-524288,524287)[j%2] for j in range(128)],
            [524287]+[0]*127,[0]*127+[-524288]]
    inputs += [[rng.randrange(-4096,4097) for _ in range(128)] for _ in range(8)]
    for x in inputs:
        q=(ct.c_int8*128)();out=(ct.c_int32*128)();m=c.qmatrix_quant((ct.c_int32*128)(*x),q,out)
        cases.append(dict(m=m,q=list(q),result=list(out),source='frozen C quant and linear'))
    cases += [dict(m=m,q=q,result=None,source='independent C raw A8 domain') for m,q in
              [(524288,[-128]*128),(1,[127]*128),(131072,[(-128,127)[j%2] for j in range(128)])]]
    rne=lambda n,d:(-1 if n<0 else 1)*(abs(n)//d+int(2*(abs(n)%d)>d or 2*(abs(n)%d)==d and (abs(n)//d)%2))
    for case in cases:
        out=(ct.c_int32*128)();c.qmatrix_reference((ct.c_int8*128)(*case['q']),case['m'],out)
        if case['result'] is not None:assert case['result']==list(out)
        case['result']=list(out)
        py=[]
        for row in range(128):
            digits=[words[4*row+j//32]>>(2*(j%32))&3 for j in range(128)]
            total=sum(q*(-1 if w==2 else w) for q,w in zip(case['q'],digits))
            py.append(max(-524288,min(524287,rne(total*case['m']*alpha,33292288))))
        assert py==case['result']
    return cases


def vectors(cases):
    rng=random.Random(260706);rows=[];p=0;idx=row=group=wait=0;current=None
    count=dict(completed_matrices=0,result_items=0,filled_codes=0,dot_groups=0,aborts=0,busy_starts=0,input_stalls=0,output_stalls=0,dot_stalls=0)
    def tick(reset=0,start=0,m=0,q=0,qvalid=0,yready=0,enable=1,context=None,first=False):
        nonlocal p,idx,row,group,wait,current
        ready=int(not reset and p==1);valid=int(not reset and p==5)
        y=(current['result'][row]&1048575) if valid else 0
        want=y+(row<<20)+(group<<27)+(ready<<29)+(valid<<30)+(int(p!=0)<<31)
        mask=0 if first else (((1<<NO)-1)^(0 if valid else 1048575))
        value=reset+(start<<1)+((q&255)<<2)+(m<<10)+(qvalid<<30)+(yready<<31)+(enable<<32)
        rows.append((value,want,mask))
        if reset:
            count['aborts']+=int(p!=0);p=idx=row=group=wait=0;current=None
        elif p==0:
            if start and 1<=m<=524288:
                assert context and context['m']==m;current=context;p=1;idx=row=group=0
        else:
            count['busy_starts']+=int(bool(start))
            if p==1:
                if qvalid:
                    assert q==current['q'][idx];count['filled_codes']+=1;idx=(idx+1)%128
                    if idx==0:p=2
                else:count['input_stalls']+=1
            elif p==2:
                if enable:
                    count['dot_groups']+=1;group=(group+1)%4
                    if group==0:p=3
                else:count['dot_stalls']+=1
            elif p==3:p=4;wait=BOUNDED_LATENCY-1
            elif p==4:
                if wait:wait-=1
                else:p=5
            elif p==5:
                if yready:
                    count['result_items']+=1;row=(row+1)%128;group=0
                    if row==0:count['completed_matrices']+=1;p=0
                    else:p=2
                else:count['output_stalls']+=1
    def active(stall):
        tick(start=int(rng.randrange(47)==0),m=rng.randrange(1048576),
             q=current['q'][idx] if p==1 else rng.randrange(-128,128),
             qvalid=int(not stall or rng.randrange(5)!=0),yready=int(not stall or rng.randrange(4)!=0),enable=int(not stall or rng.randrange(3)!=0))
    tick(reset=1,first=True)
    for m in [0,524289,1048575]:tick(start=1,m=m)
    completed=[]
    for j,case in enumerate(cases):
        before=len(rows);tick(start=1,m=case['m'],context=case)
        while p:active(j%2==1)
        completed.append(len(rows)-before);tick()
    for elapsed in [1,31,127,129,132,134,150,176,224,231,270]:
        case=cases[6];tick(start=1,m=case['m'],context=case)
        for _ in range(elapsed):active(False)
        tick(reset=1,start=1);tick()
    case=cases[7];tick(start=1,m=case['m'],context=case)
    while p:active(True)
    tick();assert count['completed_matrices']==len(cases)+1
    return rows,dict(clocks=len(rows),counts=count,completed_clocks=completed,
                    no_stall_matrix_clocks=1+128+128*(4+1+BOUNDED_LATENCY+1),
                    scope='independent scalar protocol recurrence and frozen C arithmetic, no graph simulation locally')


def prove(name,net,reference_verilog):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    (OUT/(name+'.blif')).write_text(blif(net));(OUT/(name+'.negative.blif')).write_text(blif(flip_output(net)))
    (OUT/(name+'.ref.v')).write_text(reference_verilog)
    # proc may infer ROM from the independent constant case table. Lower it
    # before requiring the proof graph to contain only NAND/NOT primitives.
    script=OUT/(name+'.ys');script.write_text(f'read_verilog {OUT}/{name}.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/{name}.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/(name+'.yosys.log')),'-s',str(script)],stdout=subprocess.DEVNULL,check=True,timeout=120)
    ref=from_yosys(json.loads((OUT/(name+'.ref.json')).read_text()),net.n_in,net.n_out)
    (OUT/(name+'.ref.blif')).write_text(blif(ref));abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    good=cec(abc,OUT/(name+'.blif'),OUT/(name+'.ref.blif'),OUT/(name+'.cec.log'));assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/(name+'.negative.blif'),OUT/(name+'.ref.blif'),OUT/(name+'.negative.log'));assert bad['verdict']=='different'
    return dict(metrics=metrics(net),proof=good,negative=bad)


def check_cloud(net,weights,words,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    import verify as verifier
    verifier.OUT=OUT
    proof_weights=prove('weights',weights,'module top(input [8:0] din,output reg [63:0] dout);\nalways @* begin\ncase(din)\n'+
        '\n'.join(f"9'd{i}:dout=64'h{w:016x};" for i,w in enumerate(words))+"\ndefault:dout=64'd0;\nendcase\nend\nendmodule\n")
    b=Builder(1034);bits=list(range(2,1036));ds=bank(b,bits[:1024],bits[1024:1032],*bits[1032:])
    proof_bank=prove('bank',b.finish(ds+bits[:256]),'''module top(input [1033:0] din,output [1279:0] dout);
wire [1023:0] old=din[1023:0];wire [7:0] code=din[1031:1024];
wire fill=din[1032],take=din[1033];
wire [1023:0] next=take?{old[255:0],old[1023:256]}:fill?{code,old[1023:8]}:old;
assign dout={old[255:0],next};endmodule
''')
    libpath=OUT/'sim.so';subprocess.run(['cc','-O3','-std=c99','-fPIC','-shared',str(ROOT/'physical/nl_sim.c'),'-o',str(libpath)],check=True,timeout=60)
    lib=ct.CDLL(str(libpath));lib.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32];lib.nl_step.argtypes=[ct.c_void_p,ct.c_void_p]
    def wrong(graph,seq):
        raw=graph.encode();assert lib.nl_init(raw,len(raw),graph.n_in,graph.n_out)==0
        buf=ct.create_string_buffer((graph.n_out+7)//8);bad=0
        for x,y,m in seq:
            lib.nl_step(x.to_bytes((graph.n_in+7)//8,'little'),buf)
            bad+=int(bool((int.from_bytes(buf.raw,'little')^y)&m))
        return bad
    weight_seq=[(a,w,(1<<64)-1) for a,w in enumerate(words)]
    assert wrong(weights,weight_seq)==0 and wrong(flip_output(weights),weight_seq)>0
    assert wrong(net,rows)==0,'actual matrix NAND differs from C/protocol reference'
    bad=flip_output(net);negative=wrong(bad,rows);assert negative>0
    (OUT/'bad.nl').write_bytes(bad.encode());(OUT/'bad.v').write_text(rtl(bad,NAME))
    (OUT/'tb.v').write_text(verifier.testbench(NI,NO,NAME,str(OUT/'vectors.txt')))
    binary=verifier.compile_rtl('source',OUT/'slice.v');verifier.run([binary],300)
    binary=verifier.compile_rtl('negative',OUT/'bad.v')
    rejected=subprocess.run([str(binary)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(rejected.stdout+rejected.stderr)
    assert rejected.returncode!=0 and 'C99 comparison failed' in rejected.stdout+rejected.stderr
    return dict(status='pass',clocks=len(rows),nand_mismatches=0,negative_nand_mismatches=negative,
        rtl_clocks=len(rows),actual_rtl_mutation_rejected=True,all_512_weight_words_match_C=True,
        weight_proof=proof_weights,bank_proof=proof_bank)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);small=small_control();store=small_bank();weights,words=weight_table()
    net,parts=make(weights);c=reference();cases=fixtures(c,words,parts['alpha']);rows,expected=vectors(cases)
    assert len(rows)>1000 and net.n_in==NI and net.n_out==NO
    (OUT/'slice.nl').write_bytes(net.encode());(OUT/'slice.v').write_text(rtl(net,NAME))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    (OUT/'cases.json').write_text(json.dumps(cases,indent=2)+'\n')
    report=dict(status='construction + small gates + independent C/Python fixture; large checks pending',metrics=metrics(net),parts=parts,
                small_control=small,small_bank=store,expected=expected,fixtures_sha256=sha((OUT/'cases.json').read_bytes()),
                vector_sha256=sha((OUT/'vectors.txt').read_bytes()),model_sha256=MODEL_SHA,numerical_contract_changed=False)
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=check_cloud(net,weights,words,rows)
        report['status']='full source NAND/RTL/C, weight/bank CEC and actual mutations pass'
        (OUT/'source.json').write_text(json.dumps(dict(design=NAME,variant='fixed real layer0 Q128x128 matrix',
            metrics=report['metrics'],parts=parts,model_sha256=MODEL_SHA,status=report['status'],clocks=len(rows)),indent=2)+'\n')
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        path=getattr(module,'__file__',None)
        if path and ROOT in Path(path).resolve().parents and Path(path).suffix=='.py':paths.add(Path(path).resolve())
    for name in ['ci.py','integer/int_model.c','integer_opt/weights_golden.c','physical/model.bin','physical/nl_sim.c','physical/verify.py',
                 'physical/units/manifest.json','physical/units/serial_mul.nl','physical/units/dot32.nl',
                 'integer_opt/pilot_units/manifest.json','integer_opt/pilot_units/serial_div.nl']:
        paths.add(ROOT/name)
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(ROOT)) if ROOT in p.parents else str(p):sha(p.read_bytes()) for p in sorted(paths)})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
