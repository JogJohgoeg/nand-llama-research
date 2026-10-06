#!/usr/bin/env python3
"""Snapshot a rotating KV word and stream its frozen integer dequantization.

The276 captured bits map into the dead H head used by R53. Their state is
explicit in this standalone component; whole-model ownership is outside.
"""
from pathlib import Path
import sys,json,hashlib,random,ctypes as ct,subprocess,signal,os,argparse,shutil
R=Path(os.environ.get('H3_KV_TEST_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state,flip_output
from pilot_circulate import bank
from prefix_store import select
from export import import_net,rtl
from golden import Netlist
from gate_check import Snapshot
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=R/'build/integer_opt/kv_client'
OP_SHA='2274f041001ed72005246632918b16da76ff2b2b570fae7dba45de5df69738b9'


UNITS=Path(__file__).resolve().parent/'kv_units'


def operator():
    d=json.loads((UNITS/'manifest.json').read_text());raw=(UNITS/'kv_deq.nl').read_bytes()
    assert d['metrics']['sha256']==OP_SHA and sha(raw)==OP_SHA
    assert d['golden_c_sha256']=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    return Netlist.decode(raw,28,20)


def control(b,old,pins,a,l):
    phase=old[:2];address=old[2:2+a];mode=old[2+a];index=old[3+a:]
    reset,start,write=pins[:3];asked=pins[3:3+a];read,match=pins[-2:];keep=b.inv(reset)
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    p=[eq(phase,n) for n in range(4)];last=b.reduce(index,b.land,1)
    begin=b.reduce([keep,start,b.lor(p[0],p[3])],b.land,1)
    accept=b.reduce([keep,p[1],match],b.land,1)
    load_input=b.land(begin,write);load_bank=b.land(accept,b.inv(mode));store=b.land(accept,mode)
    available=b.land(keep,p[2]);consume=b.land(available,read)
    ph=phase[:]
    for action,target in ((begin,[1,0]),(accept,[mode,1]),(b.land(consume,last),[1,1])):
        ph=[b.mux(action,x,y) for x,y in zip(ph,target)]
    ds=[b.land(keep,v) for v in ph]
    ds += [b.land(keep,b.mux(begin,x,y)) for x,y in zip(address,asked)]
    ds += [b.land(keep,b.mux(begin,mode,write))]
    clear=b.lor(reset,begin)
    ds += [b.land(b.inv(clear),v) for v in b.add(index,[0]*l,consume)[0]]
    busy=b.land(keep,b.lor(p[1],p[2]));done=b.reduce([keep,p[3],b.inv(begin)],b.land,1)
    return ds,[load_input,load_bank,store,available,b.land(available,last),busy,done]


def make(rows=32,lanes=32,arithmetic=True):
    assert rows&(rows-1)==0 and lanes&(lanes-1)==0
    a=(rows-1).bit_length();l=(lanes-1).bit_length();width=8*lanes+20
    _,storage=bank(rows,width);nb=storage.n_state;nc=3+a+l;ns=nb+width+nc;ni=4+a+width
    b=Builder(ns+ni);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+ni))
    bs=old[:nb];cache=old[nb:nb+width];cs=old[-nc:]
    reset,start,we=pins[:3];asked=pins[3:3+a];data=pins[3+a:3+a+width];read=pins[-1]
    match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(bs[-a:],cs[2:2+a])],b.land,1)
    ds,actions=control(b,cs,[reset,start,we]+asked+[read,match],a,l)
    load_input,load_bank,write,*flags=actions
    request=b.land(cs[0],b.inv(cs[1]))
    bd,bo=import_net(b,storage,cache+cs[2:2+a]+[request,reset,write],bs)
    cache_d=[b.mux(load_input,x,y) for x,y in zip(cache,data)]
    cache_d=[b.mux(load_bank,x,y) for x,y in zip(cache_d,bo[:width])]
    q=select(b,[cache[8*i:8*i+8] for i in range(lanes)],cs[3+a:])
    m=cache[8*lanes:];raw=q+m;states=bd+cache_d+ds
    protocol=b.finish(states+raw+cs[3+a:]+flags)
    if arithmetic:
        _,value=import_net(b,operator(),raw)
    else:value=raw
    net=with_state(b.finish(states+value+cs[3+a:]+flags),ns)
    return net,protocol,dict(rows=rows,lanes=lanes,bank=metrics(storage),captured_bits=width,parent_bits=nc,
        state_order='continuous bank, captured q8[lanes]+m20, phase/address/mode/index',
        numerical_operator=OP_SHA if arithmetic else None)


def reference(rows,lanes):
    a=(rows-1).bit_length();l=(lanes-1).bit_length();w=8*lanes+20;nb=rows*w+a;nc=3+a+l;ns=nb+w+nc;ni=4+a+w
    return f'''module top(input [{ns+ni-1}:0] din,output [{ns+28+l+4-1}:0] dout);
wire [{rows*w-1}:0] memory=din[{rows*w-1}:0];
wire [{a-1}:0] cursor=din[{nb-1}:{rows*w}];
wire [{w-1}:0] cache=din[{nb+w-1}:{nb}];
wire [1:0] phase=din[{ns-nc+1}:{ns-nc}];
wire [{a-1}:0] address=din[{ns-nc+1+a}:{ns-nc+2}];
wire mode=din[{ns-nc+2+a}];
wire [{l-1}:0] index=din[{ns-1}:{ns-l}];
wire [{ni-1}:0] pins=din[{ns+ni-1}:{ns}];
wire reset=pins[0],start=pins[1],write=pins[2],read=pins[{ni-1}];
wire [{a-1}:0] wanted=pins[{a+2}:3];
wire [{w-1}:0] data=pins[{a+w+2}:{a+3}];
wire begin_command=!reset && start && (phase==0 || phase==3);
wire accept=!reset && phase==1 && cursor==address;
wire available=!reset && phase==2, consume=available && read;
wire last=&index;
wire store=accept && mode;
reg [1:0] next_phase;
reg [{a-1}:0] next_address;
reg next_mode;
reg [{l-1}:0] next_index;
always @* begin
 next_phase=phase;next_address=address;next_mode=mode;next_index=index;
 if(reset)begin next_phase=0;next_address=0;next_mode=0;next_index=0;end
 else if(begin_command)begin next_phase=1;next_address=wanted;next_mode=write;next_index=0;end
 else begin
  if(accept)next_phase=mode ? 3 : 2;
  if(consume)begin next_index=index+1'b1;if(last)next_phase=3;end
 end
end
assign dout[{rows*w-1}:0]={{store ? cache : memory[{w-1}:0],memory[{rows*w-1}:{w}]}};
assign dout[{nb-1}:{rows*w}]=reset ? {a}'d0 : cursor+{a}'d1;
assign dout[{nb+w-1}:{nb}]=accept && !mode ? memory[{w-1}:0] : begin_command && write ? data : cache;
assign dout[{ns-1}:{ns-nc}]={{next_index,next_mode,next_address,next_phase}};
wire [{l+2}:0] bit_offset={{index,3'b000}};
assign dout[{ns+7}:{ns}]=cache[bit_offset +: 8];
assign dout[{ns+27}:{ns+8}]=cache[{w-1}:{8*lanes}];
assign dout[{ns+28+l-1}:{ns+28}]=index;
assign dout[{ns+28+l}]=available;
assign dout[{ns+29+l}]=available && last;
assign dout[{ns+30+l}]=!reset && (phase==1 || phase==2);
assign dout[{ns+31+l}]=!reset && phase==3 && !begin_command;
endmodule
'''


class Model:
    def __init__(self,rows,lanes,dequant,arithmetic):
        self.rows=rows;self.lanes=lanes;self.a=(rows-1).bit_length();self.l=(lanes-1).bit_length();self.w=lanes*8+20
        self.bank=[0]*rows;self.known=[False]*rows;self.cursor=self.cache=self.phase=self.address=self.mode=self.index=0
        self.dequant=dequant;self.arithmetic=arithmetic
        self.counts=dict(starts=0,aborts=0,completed=0,bank_reads=0,bank_writes=0,outputs=0,stalls=0,seeks=0,busy_starts=0)
    def tick(self,v):
        reset=v&1;start=v>>1&1;mode=v>>2&1;address=v>>3&((1<<self.a)-1)
        data=v>>(3+self.a)&((1<<self.w)-1);read=v>>(3+self.a+self.w)&1;p=self.phase
        begin=bool(not reset and start and p in (0,3));match=self.cursor==self.address
        available=int(not reset and p==2);last=self.index==self.lanes-1
        q=self.cache>>(8*self.index)&255;m=self.cache>>(8*self.lanes)
        bits=20 if self.arithmetic else 28
        value=self.dequant(q-256 if q>=128 else q,m)&1048575 if self.arithmetic else q+(m<<8)
        flags=[available,int(available and last),int(not reset and p in (1,2)),int(not reset and p==3 and not begin)]
        out=value+(self.index<<bits)+sum(v<<(bits+self.l+i) for i,v in enumerate(flags))
        mask=(1<<(bits+self.l+4))-1 if available else ((1<<4)-1)<<(bits+self.l)
        c=self.counts;c['busy_starts']+=int(start and p in (1,2) and not reset)
        c['stalls']+=int(available and not read);c['seeks']+=int(not reset and p==1 and not match)
        tail=self.bank[0];known=self.known[0]
        if reset:
            c['aborts']+=int(p in (1,2));self.phase=self.address=self.mode=self.index=0
        elif begin:
            self.phase=1;self.index=0;self.address=address;self.mode=mode;c['starts']+=1
            if mode:self.cache=data
        elif p==1 and match:
            if self.mode:tail=self.cache;known=True;self.phase=3;c['bank_writes']+=1;c['completed']+=1
            else:assert known;self.cache=self.bank[0];self.phase=2;c['bank_reads']+=1
        elif p==2 and read:
            self.index=(self.index+1)%self.lanes;c['outputs']+=1
            if last:self.phase=3;c['completed']+=1
        self.bank=self.bank[1:]+[tail];self.known=self.known[1:]+[known];self.cursor=0 if reset else (self.cursor+1)%self.rows
        if reset:self.known=[False]*self.rows
        return out,mask


def golden():
    OUT.mkdir(parents=True,exist_ok=True)
    assert sha((R/'integer/int_model.c').read_bytes())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    c=OUT/'golden.c';c.write_text('#include '+json.dumps(str(R/'integer/int_model.c'))+'\nint32_t stream_dequant(int32_t q,int32_t m){return kv_dequant((int8_t)q,m);}\n')
    so=OUT/'golden.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(c),'-o',str(so)],check=True,timeout=30)
    lib=ct.CDLL(str(so));f=lib.stream_dequant;f.argtypes=[ct.c_int32,ct.c_int32];f.restype=ct.c_int32;return f


def vectors(rows,lanes,dequant,arithmetic):
    model=Model(rows,lanes,dequant,arithmetic);rng=random.Random(260719+rows+lanes);vectors=[];calls=[]
    def tick(reset=0,start=0,mode=0,address=0,data=0,read=0):
        x=reset+(start<<1)+(mode<<2)+(address<<3)+(data<<(3+model.a))+(read<<(3+model.a+model.w))
        y,m=model.tick(x);vectors.append((x,y,m));return y
    def command(mode,address,data=0,abort=False):
        assert model.phase in (0,3)
        tick(start=1,mode=mode,address=address,data=data,read=1);values=[];clocks=1
        while model.phase!=3:
            if abort and (model.phase==1 or model.phase==2 and model.index==lanes//2):
                tick(reset=1,start=1,mode=1,read=1);return False
            phase=model.phase;take=int(rng.randrange(4)!=0)
            y=tick(start=rng.randrange(4)==0,mode=rng.randrange(2),address=rng.randrange(rows),data=rng.getrandbits(model.w),read=take)
            if phase==2 and take:values.append(y&((1<<(20 if arithmetic else 28))-1))
            clocks+=1;assert clocks<rows+8*lanes+64
        calls.append(dict(mode=mode,address=address,data=data,values=values,clocks=clocks));return True
    def fill():
        words=[]
        for address in range(rows):
            q=[rng.randrange(-128,128) for _ in range(lanes)];m=rng.choice([0,1,2,126,127,128,524287,524288,1048575])
            if address%4==0:q=[127 if i%2 else -128 for i in range(lanes)];m=524288
            word=sum((v&255)<<(8*i) for i,v in enumerate(q))+(m<<(8*lanes));command(1,address,word);words.append(word)
        return words
    tick(reset=1);words=fill()
    for _ in range(3):
        for address in rng.sample(list(range(rows)),rows):
            command(0,address);word=words[address];m=word>>(8*lanes);wanted=[]
            for i in range(lanes):
                q=word>>(8*i)&255
                wanted.append(dequant(q-256 if q>=128 else q,m)&1048575 if arithmetic else q+(m<<8))
            assert calls[-1]['values']==wanted
    assert not command(1,0,0,abort=True);words=fill()
    # Read capture has happened, so corrupt inputs during half-read reset cannot
    # destroy data being observed before the explicit reset invalidates banks.
    address=0;tick(start=1,address=address)
    while model.phase!=2:tick(start=1,mode=1,data=rng.getrandbits(model.w))
    for _ in range(lanes//2):tick(read=1,start=1,mode=1,data=rng.getrandbits(model.w))
    tick(reset=1,start=1,read=1);words=fill()
    for address in range(rows):command(0,address)
    assert model.counts['aborts']==2
    return vectors,dict(clocks=len(vectors),counts=model.counts,transactions=calls)


def verify_small(net,rows):
    assert metrics(net)['nNand']+net.n_state<=4000
    snap=Snapshot.decode(net.encode(),net.n_in,net.n_out);state=bytes(net.n_state)
    for i,(x,y,m) in enumerate(rows):
        state,out=snap.step(state,bytes(x>>j&1 for j in range(net.n_in)));got=sum(v<<j for j,v in enumerate(out))
        assert (got^y)&m==0,(i,got,y,m)
    bad=Snapshot.decode(flip_output(net).encode(),net.n_in,net.n_out);state=bytes(net.n_state);wrong=0
    for x,y,m in rows:
        state,out=bad.step(state,bytes(x>>j&1 for j in range(net.n_in)));wrong+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&m))
    assert wrong;return dict(clocks=len(rows),mismatches=0,actual_data_gate_mutation_mismatches=wrong)


def small_transitions(g):
    rows,lanes=4,4;net,comb,_=make(rows,lanes,False);ns=net.n_state;rng=random.Random(260720);xs=[];ys=[]
    def pack(m):
        v=sum(w<<(m.w*j) for j,w in enumerate(m.bank));shift=m.rows*m.w
        v+=m.cursor<<shift;shift+=m.a;v+=m.cache<<shift;shift+=m.w
        parent=m.phase+(m.address<<2)+(m.mode<<(2+m.a))+(m.index<<(3+m.a))
        return v+(parent<<shift)
    for j in range(1024):
        m=Model(rows,lanes,g,False);m.bank=[rng.getrandbits(m.w) for _ in range(rows)];m.known=[True]*rows
        m.cursor=rng.randrange(rows);m.cache=rng.getrandbits(m.w);m.phase=j&3;m.mode=j>>2&1;m.index=j>>3&3
        m.address=m.cursor if j>>5&1 else (m.cursor+1)%rows
        x=rng.getrandbits(net.n_in);old=pack(m);y,_=m.tick(x);xs.append(old+(x<<ns));ys.append(pack(m)+(y<<ns))
    result=verify_state(comb,xs,ys,ns);result.pop('nl_hex');return result


def numerical_check(g):
    from bench import verify
    net=operator();assert metrics(net)['nNand']<=4000
    rng=random.Random(260721);pairs=[]
    for q in (-128,-127,-64,-1,0,1,64,126,127):
        for m in (0,1,2,63,64,126,127,128,524287,524288,1048575):pairs.append((q,m))
    for q in (-1,1):
        for k in (0,1,2,63,127,1024,8191):
            for edge in (63,64):pairs.append((q,127*k+edge))
    pairs += [(rng.randrange(-128,128),rng.randrange(1<<20)) for _ in range(4096)]
    return dict(metrics=metrics(net),verification=verify(net,[(q&255)+(m<<8) for q,m in pairs],[g(q,m)&1048575 for q,m in pairs]))


def cloud_check(net,protocol,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from nand import blif,from_yosys
    from ci import cec
    import verify as checks
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    (OUT/'protocol.blif').write_text(blif(protocol));(OUT/'protocol.ref.v').write_text(reference(32,32))
    ys=OUT/'protocol.ys';ys.write_text(f'read_verilog {OUT}/protocol.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/protocol.ref.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'protocol.yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    ref=from_yosys(json.loads((OUT/'protocol.ref.json').read_text()),protocol.n_in,protocol.n_out)
    (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(protocol)))
    good=cec(abc,OUT/'protocol.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
    checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
    checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
    assert checks.check_nand(rows,net.encode())==0
    fault=flip_output(net);wrong=checks.check_nand(rows,fault.encode());assert wrong>0
    (OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'kv_client',str(OUT/'vectors.txt')))
    (OUT/'negative.v').write_text(rtl(fault,'kv_client'))
    exe=checks.compile_rtl('source',OUT/'client.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'negative.v')
    failed=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
    assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
    return dict(status='pass',protocol_cec=good,actual_D_mutation=bad,
        proof_scope='Every state bit plus captured q8/m20,index,flags against independent RTL. Fixed imported dequantization follows identical q/m; numeric operator checked separately against C, not a new arithmetic formal proof.',
        clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),actual_data_gate_mismatches=wrong,actual_RTL_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    g=golden();numeric=numerical_check(g);transitions=small_transitions(g)
    net,protocol,parts=make(4,4,False);rows,expected=vectors(4,4,g,False)
    small=dict(metrics=metrics(net),sequence=verify_small(net,rows),arbitrary_state=transitions,expected=expected)
    net,protocol,parts=make();rows,expected=vectors(32,32,g,True)
    (OUT/'client.nl').write_bytes(net.encode());(OUT/'client.v').write_text(rtl(net,'kv_client'))
    (OUT/'protocol.ref.v').write_text(reference(32,32))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    assert sha(net.encode())=='ec5ecd6b6e083bcf945d3e3eee8aca435e3971880f7f74ea267d52f8f0cc378f'
    report=dict(status='small actual capture protocol and exact imported dequantization pass; full client constructed only',
        metrics=metrics(net),parts=parts,small=small,numerical_operator=numeric,expected=expected,
        vector_sha256=sha((OUT/'vectors.txt').read_bytes()),numerical_contract_changed=False,adopted=False,
        arithmetic_tradeoff='Dedicated2488N combinational dequantization instead of scheduling the shared arithmetic; no full-chip area/cycle/power adoption. Depth260 must be timed physically.',
        storage_scope='276-bit captured cache explicitly counted; full H-slot reuse and downstream attention ownership still outside',
        proof_scope='Protocol CEC excludes a new arithmetic proof; fixed operator bytes are independently compared to frozen C')
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    paths.update([UNITS/'kv_deq.nl',UNITS/'manifest.json',R/'integer/int_model.c',R/'physical/nl_sim.c',R/'physical/verify.py',R/'ci.py'])
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+('kv_units/' if p.parent==UNITS else '')+p.name:sha(p.read_bytes()) for p in sorted(paths)})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=cloud_check(net,protocol,rows);report['status']='protocol all-state CEC and complete actual NAND/RTL/C pass, with actual D/data faults'
        (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','small','expected')},indent=2))
    print(expected['clocks'],expected['counts'],small['sequence'])


if __name__=='__main__':main()
