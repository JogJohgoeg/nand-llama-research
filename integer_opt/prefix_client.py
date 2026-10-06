#!/usr/bin/env python3
"""A word client for the continuously rotating prefix bank.

Existing H/A word slices capture incoming values and old prefix data. A single
frozen s20 residual unit updates one lane per cycle, then writes the word when
its address returns. Standalone slice storage is explicit, never free state.
"""
from pathlib import Path
import sys,json,hashlib,random,ctypes as ct,subprocess,signal,os,argparse,shutil
R=Path(os.environ.get('H3_CLIENT_TEST_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state
from pilot_circulate import bank
from export import import_net,load_unit,rtl
from gate_check import Snapshot
sha=lambda b:hashlib.sha256(b).hexdigest()
OUT=R/'build/integer_opt/prefix_client'


def control(b,old,pins,a,l):
    phase=old[:3];index=old[3:3+l];address=old[3+l:3+l+a];mode=old[-2:]
    reset,start=pins[:2];requested_address=pins[2:2+a];requested_mode=pins[2+a:4+a]
    valid,read,match=pins[-3:];keep=b.inv(reset)
    def eq(bits,n):return b.reduce([w if n>>j&1 else b.inv(w) for j,w in enumerate(bits)],b.land,1)
    p=[eq(phase,n) for n in range(7)];last=b.reduce(index,b.land,1)
    begin=b.reduce([keep,start,b.lor(p[0],p[6]),b.inv(b.land(*requested_mode))],b.land,1)
    take=b.reduce([keep,p[1],valid],b.land,1)
    got=b.reduce([keep,p[2],match],b.land,1)
    add=b.land(keep,p[3]);written=b.reduce([keep,p[4],match],b.land,1)
    consume=b.reduce([keep,p[5],read],b.land,1)
    residual=eq(mode,2);direct_read=eq(requested_mode,1)
    ph=phase[:]
    for action,target in [(begin,[b.inv(direct_read),direct_read,0]),
                          (b.land(take,last),[0,residual,b.inv(residual)]),
                          (got,[1,residual,b.inv(residual)]),
                          (b.land(add,last),[0,0,1]),(written,[1,0,1]),(b.land(consume,last),[0,1,1])]:
        ph=[b.mux(action,x,y) for x,y in zip(ph,target)]
    advance=b.reduce([take,add,consume],b.lor,0)
    ix=b.add(index,[0]*l,advance)[0];clear=b.lor(reset,begin)
    nxt=[b.land(keep,v) for v in ph]+[b.land(b.inv(clear),v) for v in ix]
    nxt += [b.land(keep,b.mux(begin,x,y)) for x,y in zip(address,requested_address)]
    nxt += [b.land(keep,b.mux(begin,x,y)) for x,y in zip(mode,requested_mode)]
    input_ready=b.land(keep,p[1]);available=b.land(keep,p[5])
    busy=b.reduce([keep,b.reduce(p[1:6],b.lor,0)],b.land,1)
    done=b.reduce([keep,p[6],b.inv(begin)],b.land,1)
    out=[begin,take,b.land(got,residual),b.land(got,b.inv(residual)),add,written,consume,
         input_ready,available,b.land(available,last),busy,done]
    return nxt,out


def control_expected(old,pins,a,l):
    phase=old&7;index=old>>3&((1<<l)-1);address=old>>(3+l)&((1<<a)-1);mode=old>>(3+l+a)&3
    reset=pins&1;start=pins>>1&1;asked=pins>>2&((1<<a)-1);op=pins>>(2+a)&3
    valid=pins>>(4+a)&1;read=pins>>(5+a)&1;match=pins>>(6+a)&1
    begin=int(not reset and start and phase in (0,6) and op!=3)
    take=int(not reset and phase==1 and valid);got=int(not reset and phase==2 and match)
    add=int(not reset and phase==3);written=int(not reset and phase==4 and match)
    consume=int(not reset and phase==5 and read);last=index==(1<<l)-1
    nxt_phase=phase
    if begin:nxt_phase=2 if op==1 else 1
    if take and last:nxt_phase=2 if mode==2 else 4
    if got:nxt_phase=3 if mode==2 else 5
    if add and last:nxt_phase=4
    if written:nxt_phase=5
    if consume and last:nxt_phase=6
    index=(index+int(take or add or consume))&((1<<l)-1)
    if begin:index=0;address=asked;mode=op
    nxt=0 if reset else nxt_phase+(index<<3)+(address<<(3+l))+(mode<<(3+l+a))
    ready=int(not reset and phase==1);available=int(not reset and phase==5)
    out=[begin,take,int(got and mode==2),int(got and mode!=2),add,written,consume,
         ready,available,int(available and last),int(not reset and phase in range(1,6)),int(not reset and phase==6 and not begin)]
    return nxt,sum(v<<i for i,v in enumerate(out))


def small_control():
    a,l=2,1;ns=3+l+a+2;ni=7+a;b=Builder(ns+ni)
    ds,out=control(b,list(range(2,2+ns)),list(range(2+ns,2+ns+ni)),a,l);comb=b.finish(ds+out)
    rng=random.Random(260716);xs=[];ys=[]
    for j in range(4096):
        old=rng.getrandbits(ns);pins=rng.getrandbits(ni);d,y=control_expected(old,pins,a,l)
        xs.append(old+(pins<<ns));ys.append(d+(y<<ns))
    proof=verify_state(comb,xs,ys,ns);proof.pop('nl_hex');return proof


def make(rows,lanes):
    assert rows>1 and rows&(rows-1)==0 and lanes>1 and lanes&(lanes-1)==0
    a=(rows-1).bit_length();l=(lanes-1).bit_length();width=lanes*20
    comb_bank,storage=bank(rows,width);nb=storage.n_state;nc=3+l+a+2;ns=nb+2*width+nc;ni=26+a
    b=Builder(ns+ni);old=list(range(2,2+ns));ins=list(range(2+ns,2+ns+ni))
    bs=old[:nb];work=old[nb:nb+width];saved=old[nb+width:nb+2*width];cs=old[-nc:]
    reset,start=ins[:2];asked=ins[2:2+a];mode=ins[2+a:4+a];value=ins[4+a:24+a];valid,read=ins[-2:]
    address=cs[3+l:3+l+a];cursor=bs[-a:]
    match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(address,cursor)],b.land,1)
    ds,actions=control(b,cs,[reset,start]+asked+mode+[valid,read,match],a,l)
    begin,take,capture_old,capture_read,add,write,consume,*flags=actions
    request=b.lor(b.reduce([b.inv(cs[0]),cs[1],b.inv(cs[2])],b.land,1),
                  b.reduce([b.inv(cs[0]),b.inv(cs[1]),cs[2]],b.land,1))
    bd,bout=import_net(b,storage,work+address+[request,reset,write],bs)
    _,summed=import_net(b,load_unit('resid'),work[:20]+saved[:20])
    tail=[b.mux(add,x,y) for x,y in zip(value,summed)]
    change=b.lor(take,add);tail=[b.mux(change,x,y) for x,y in zip(work[:20],tail)]
    advance=b.lor(change,consume);rotated=work[20:]+tail
    wd=[b.mux(advance,x,y) for x,y in zip(work,rotated)]
    wd=[b.mux(capture_read,x,y) for x,y in zip(wd,bout[:width])]
    sd=[b.mux(add,x,y) for x,y in zip(saved,saved[20:]+saved[:20])]
    sd=[b.mux(capture_old,x,y) for x,y in zip(sd,bout[:width])]
    net=with_state(b.finish(bd+wd+sd+ds+work[:20]+flags),ns)
    assert net.n_in==ni and net.n_out==25
    return net,b.finish(bd+wd+sd+ds+work[:20]+flags),dict(bank=metrics(storage),bank_state_bits=nb,work_slice_bits=width,old_prefix_slice_bits=width,
        parent_state_bits=nc,rows=rows,lanes=lanes,word_bits=width,
        state_order='bank, existing work H/A slice, existing dead H/A capture slice, parent phase/index/address/mode',
        slot_scope='Two existing H/A word slices, explicit standalone state; integration into full128-wide slots remains outside this client')


def c_reference():
    OUT.mkdir(parents=True,exist_ok=True)
    assert sha((R/'integer/int_model.c').read_bytes())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    p=OUT/'golden.c';p.write_text('#include '+json.dumps(str(R/'integer/int_model.c'))+'\nint32_t client_residual(int32_t a,int32_t b) {return sat((int64_t)a+b);}\n')
    so=OUT/'golden.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-fPIC','-shared',str(p),'-o',str(so)],check=True,timeout=30)
    c=ct.CDLL(str(so));c.client_residual.argtypes=[ct.c_int32,ct.c_int32];c.client_residual.restype=ct.c_int32
    return c.client_residual


def reference(rows,lanes):
    a=(rows-1).bit_length();l=(lanes-1).bit_length();w=20*lanes;nb=rows*w+a;nc=3+l+a+2;ns=nb+2*w+nc;ni=26+a
    return f'''module top(input [{ns+ni-1}:0] din,output [{ns+24}:0] dout);
wire [{rows*w-1}:0] memory=din[{rows*w-1}:0];
wire [{a-1}:0] cursor=din[{nb-1}:{rows*w}];
wire [{w-1}:0] work=din[{nb+w-1}:{nb}], saved=din[{nb+2*w-1}:{nb+w}];
wire [2:0] phase=din[{ns-nc+2}:{ns-nc}];
wire [{l-1}:0] index=din[{ns-nc+3+l-1}:{ns-nc+3}];
wire [{a-1}:0] address=din[{ns-3}:{ns-2-a}];
wire [1:0] mode=din[{ns-1}:{ns-2}];
wire [{ni-1}:0] pins=din[{ns+ni-1}:{ns}];
wire reset=pins[0],start=pins[1],valid=pins[{a+24}],read=pins[{a+25}];
wire [{a-1}:0] wanted=pins[{a+1}:2];
wire [1:0] operation=pins[{a+3}:{a+2}];
wire [19:0] data=pins[{a+23}:{a+4}];
wire begin_command=!reset && start && (phase==0 || phase==6) && operation!=3;
wire input_ready=!reset && phase==1, output_valid=!reset && phase==5;
wire take=input_ready && valid, consume=output_valid && read;
wire got=!reset && phase==2 && cursor==address;
wire add=!reset && phase==3, written=!reset && phase==4 && cursor==address;
wire last=&index;
reg [2:0] next_phase;
reg [{l-1}:0] next_index;
reg [{a-1}:0] next_address;
reg [1:0] next_mode;
always @* begin
 next_phase=phase;next_index=index;next_address=address;next_mode=mode;
 if(reset)begin next_phase=0;next_index=0;next_address=0;next_mode=0;end
 else if(begin_command)begin
  next_phase=operation==1 ? 2 : 1;next_index=0;next_address=wanted;next_mode=operation;
 end else begin
  if(take || add || consume)next_index=index+1'b1;
  if(take && last)next_phase=mode==2 ? 2 : 4;
  if(got)next_phase=mode==2 ? 3 : 5;
  if(add && last)next_phase=4;
  if(written)next_phase=5;
  if(consume && last)next_phase=6;
 end
end
wire signed [20:0] sum=$signed({{work[19],work[19:0]}})+$signed({{saved[19],saved[19:0]}});
wire [19:0] saturated=sum>21'sd524287 ? 20'h7ffff : sum < -21'sd524288 ? 20'h80000 : sum[19:0];
wire [{w-1}:0] next_work=got && mode!=2 ? memory[{w-1}:0] :
 take ? {{data,work[{w-1}:20]}} : add ? {{saturated,work[{w-1}:20]}} :
 consume ? {{work[19:0],work[{w-1}:20]}} : work;
wire [{w-1}:0] next_saved=got && mode==2 ? memory[{w-1}:0] : add ? {{saved[19:0],saved[{w-1}:20]}} : saved;
assign dout[{rows*w-1}:0]={{written ? work : memory[{w-1}:0],memory[{rows*w-1}:{w}]}};
assign dout[{nb-1}:{rows*w}]=reset ? {a}'d0 : cursor+{a}'d1;
assign dout[{nb+w-1}:{nb}]=next_work;
assign dout[{nb+2*w-1}:{nb+w}]=next_saved;
assign dout[{ns-1}:{ns-nc}]={{next_mode,next_address,next_index,next_phase}};
assign dout[{ns+19}:{ns}]=work[19:0];
assign dout[{ns+20}]=input_ready;
assign dout[{ns+21}]=output_valid;
assign dout[{ns+22}]=output_valid && last;
assign dout[{ns+23}]=!reset && phase>=1 && phase<=5;
assign dout[{ns+24}]=!reset && phase==6 && !begin_command;
endmodule
'''

class Model:
    def __init__(self,rows,lanes,sat):
        self.rows=rows;self.lanes=lanes;self.a=(rows-1).bit_length();self.l=(lanes-1).bit_length();self.sat=sat
        self.bank=[[0]*lanes for _ in range(rows)];self.known=[False]*rows;self.cursor=0
        self.work=[0]*lanes;self.saved=[0]*lanes;self.phase=self.index=self.address=self.mode=0
        self.counts=dict(starts=0,aborts=0,inputs=0,read_words=0,write_words=0,residual_lanes=0,outputs=0,
            completed=0,busy_starts=0,input_stalls=0,output_stalls=0,seek=0,rotations=0,saturations=0)
    def tick(self,value):
        a=self.a;reset=value&1;start=value>>1&1;address=value>>2&((1<<a)-1);mode=value>>(a+2)&3
        data=value>>(a+4)&1048575;data=data-(1<<20) if data>>19 else data
        valid=value>>(a+24)&1;read=value>>(a+25)&1
        p=self.phase;i=self.index;match=self.cursor==self.address;last=i==self.lanes-1
        begin=bool(not reset and start and p in (0,6) and mode!=3)
        input_ready=int(not reset and p==1);available=int(not reset and p==5)
        flags=[input_ready,available,int(available and last),int(not reset and p in range(1,6)),int(not reset and p==6 and not begin)]
        result=(self.work[0]&1048575)+sum(v<<(20+j) for j,v in enumerate(flags))
        mask=((1<<25)-1) if available else (((1<<5)-1)<<20)
        c=self.counts;c['rotations']+=1;c['busy_starts']+=int(start and p in range(1,6) and not reset)
        c['input_stalls']+=int(input_ready and not valid);c['output_stalls']+=int(available and not read)
        c['seek']+=int(not reset and p in (2,4) and not match)
        bank_tail=self.bank[0];known_tail=self.known[0]
        if reset:
            c['aborts']+=int(p in range(1,6));self.phase=self.index=self.address=self.mode=0
        elif begin:
            self.phase=2 if mode==1 else 1;self.index=0;self.address=address;self.mode=mode;c['starts']+=1
        elif p==1 and valid:
            self.work=self.work[1:]+[data];self.index=(i+1)%self.lanes;c['inputs']+=1
            if last:self.phase=2 if self.mode==2 else 4
        elif p==2 and match:
            assert known_tail,'uninitialized read';c['read_words']+=1
            if self.mode==2:self.saved=list(self.bank[0]);self.phase=3
            else:self.work=list(self.bank[0]);self.phase=5
        elif p==3:
            x,y=self.work[0],self.saved[0];z=self.sat(x,y);c['saturations']+=int(z!=x+y)
            self.work=self.work[1:]+[z];self.saved=self.saved[1:]+self.saved[:1]
            self.index=(i+1)%self.lanes;c['residual_lanes']+=1
            if last:self.phase=4
        elif p==4 and match:
            bank_tail=list(self.work);known_tail=True;self.phase=5;c['write_words']+=1
        elif p==5 and read:
            self.work=self.work[1:]+self.work[:1];self.index=(i+1)%self.lanes;c['outputs']+=1
            if last:self.phase=6;c['completed']+=1
        self.bank=self.bank[1:]+[bank_tail];self.known=self.known[1:]+[known_tail]
        self.cursor=0 if reset else (self.cursor+1)%self.rows
        if reset:self.known=[False]*self.rows
        return result,mask


def vectors(rows,lanes,sat,transactions=40,fixtures=None):
    model=Model(rows,lanes,sat);rng=random.Random(260717+rows+lanes);values=[];expected=[];masks=[];operations=[]
    def pack(reset=0,start=0,address=0,mode=0,data=0,valid=0,read=0):
        return reset+(start<<1)+(address<<2)+(mode<<(model.a+2))+((data&1048575)<<(model.a+4))+(valid<<(model.a+24))+(read<<(model.a+25))
    def tick(**kw):
        v=pack(**kw);y,m=model.tick(v);values.append(v);expected.append(y);masks.append(m);return y,m
    def command(mode,address,data=None,stall=True,abort_phase=None,preface=True):
        assert model.phase in (0,6)
        for _ in range(rng.randrange(4) if preface else 0):tick(start=1,mode=3,valid=1,read=1,data=524287)
        tick(start=1,mode=mode,address=address,data=-524288,valid=1,read=1)
        k=0;outputs=[];wait=0
        while model.phase!=6:
            halfway=model.index==lanes//2 if abort_phase in (1,3,5) else True
            if abort_phase is not None and model.phase==abort_phase and halfway:
                tick(reset=1,start=1,mode=3,valid=1,read=1);return False
            p=model.phase;take=int(p==1 and (not stall or rng.randrange(4)!=0));read=int(p==5 and (not stall or rng.randrange(4)!=0))
            kw=dict(start=rng.randrange(5)==0,mode=rng.randrange(4),address=rng.randrange(rows),
                valid=take if p==1 else rng.randrange(2),read=read if p==5 else rng.randrange(2),
                data=data[k] if p==1 else rng.randrange(-524288,524288))
            y,m=tick(**kw)
            if p==1 and take:k+=1
            if p==5 and read:
                v=y&1048575;outputs.append(v-(1<<20) if v>>19 else v)
            wait+=1;assert wait<2*rows+12*lanes+128
        operations.append(dict(mode=mode,address=address,input=data,output=outputs,clocks=wait+1))
        assert len(outputs)==lanes
        if mode==0:assert outputs==data
        return True
    tick(reset=1)
    real=[]
    if fixtures:
        assert lanes==32 and rows>=24
        for fi,f in enumerate(fixtures):
            for chunk in range(4):
                sl=slice(32*chunk,32*(chunk+1));real.append(dict(case=fi,chunk=chunk,x=f['x'][sl],delta=f['delta'][sl],out=f['out'][sl]))
    logical=[]
    for address in range(rows):
        data=[rng.choice([-524288,-524287,-1,0,1,524286,524287]) for _ in range(lanes)]
        if address<len(real):data=real[address]['x']
        command(0,address,data);logical.append(data)
    for address,f in enumerate(real):
        command(2,address,f['delta']);assert operations[-1]['output']==f['out']
        operations[-1]['real_C_fixture']=[f['case'],f['chunk']];logical[address]=f['out']
    for j in range(transactions):
        address=rng.randrange(rows);mode=1 if j%3==0 else 2
        data=None if mode==1 else [rng.randrange(-524288,524288) for _ in range(lanes)]
        before=list(logical[address]);command(mode,address,data)
        want=before if mode==1 else [sat(a,b) for a,b in zip(before,data)]
        assert operations[-1]['output']==want
        if mode==2:logical[address]=want
    for address in rng.sample(list(range(rows)),rows):
        command(1,address);assert operations[-1]['output']==logical[address]
    # Exhaust each entering bank phase with no external pauses. The serial
    # add loop consumes lanes clocks, so the second seek has fixed length.
    timing={}
    for desired in range(rows):
        while model.cursor!=desired:tick()
        command(2,0,[0]*lanes,stall=False,preface=False)
        assert operations[-1]['output']==logical[0]
        timing[str(desired)]=operations[-1]['clocks']
    assert len(set(timing.values()))==rows
    assert max(timing.values())==1+2*lanes+2*rows
    # Abort each active phase; refill every bank word after reset before reads.
    for phase in range(1,6):
        mode=0 if phase in (1,4,5) else 2
        assert not command(mode,0,[1]*lanes,abort_phase=phase)
        logical=[]
        for address in range(rows):
            data=[rng.randrange(-524288,524288) for _ in range(lanes)]
            command(0,address,data,stall=False);logical.append(data)
        command(1,0);assert operations[-1]['output']==logical[0]
    assert model.counts['aborts']==5 and model.counts['saturations']>0
    return list(zip(values,expected,masks)),dict(clocks=len(values),counts=model.counts,operations=operations,no_stall_residual_cycles=timing)


def local_check(net,rows):
    assert metrics(net)['nNand']+net.n_state<=4000
    snap=Snapshot.decode(net.encode(),net.n_in,net.n_out);state=bytes(net.n_state)
    for i,(x,y,mask) in enumerate(rows):
        state,out=snap.step(state,bytes(x>>j&1 for j in range(net.n_in)))
        got=sum(v<<j for j,v in enumerate(out));assert (got^y)&mask==0,(i,hex(got),hex(y),hex(mask))
    from nand import flip_output
    bad=Snapshot.decode(flip_output(net).encode(),net.n_in,net.n_out);state=bytes(net.n_state);mismatches=0
    for x,y,mask in rows:
        state,out=bad.step(state,bytes(x>>j&1 for j in range(net.n_in)))
        mismatches+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&mask))
    assert mismatches>0;return dict(clocks=len(rows),mismatches=0,actual_result_gate_mutation_rejected=mismatches)


def small_transitions(sat):
    rows,lanes=4,2;net,comb,parts=make(rows,lanes);ns=net.n_state
    assert metrics(net)['nNand']+ns<=4000
    rng=random.Random(260718);xs=[];ys=[]
    def state(m):
        data=[x for word in m.bank for x in word]
        x=sum((v&1048575)<<(20*j) for j,v in enumerate(data));shift=rows*lanes*20
        x+=m.cursor<<shift;shift+=m.a
        for words in (m.work,m.saved):
            x+=sum((v&1048575)<<(shift+20*j) for j,v in enumerate(words));shift+=20*lanes
        parent=m.phase+(m.index<<3)+(m.address<<(3+m.l))+(m.mode<<(3+m.l+m.a))
        return x+(parent<<shift)
    for j in range(1024):
        model=Model(rows,lanes,sat)
        pick=lambda:rng.choice([-524288,-524287,-1,0,1,524286,524287]) if j<128 else rng.randrange(-524288,524288)
        model.bank=[[pick() for _ in range(lanes)] for _ in range(rows)]
        model.known=[True]*rows;model.work=[pick() for _ in range(lanes)];model.saved=[pick() for _ in range(lanes)]
        model.phase=j&7;model.index=j>>3&1;model.mode=j>>4&3
        model.address=rng.randrange(rows);model.cursor=model.address if j>>6&1 else (model.address+1)%rows
        value=rng.getrandbits(net.n_in);x=state(model)+(value<<ns)
        output,_=model.tick(value);y=state(model)+(output<<ns);xs.append(x);ys.append(y)
    result=verify_state(comb,xs,ys,ns);result.pop('nl_hex');return result


def cloud_check(net,comb,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from nand import blif,flip_output,from_yosys
    from ci import cec
    import verify as checks
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    (OUT/'client.blif').write_text(blif(comb))
    ys=OUT/'reference.ys'
    ys.write_text(f'read_verilog {OUT}/client.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=300)
    ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out)
    (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
    positive=cec(abc,OUT/'client.blif',OUT/'reference.blif',OUT/'cec.log');assert positive['verdict']=='equivalent'
    negative=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert negative['verdict']=='different'
    (OUT/'formal.json').write_text(json.dumps(dict(positive=positive,negative=negative),indent=2)+'\n')
    checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
    checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
    assert checks.check_nand(rows,net.encode())==0
    bad=flip_output(net);wrong=checks.check_nand(rows,bad.encode());assert wrong>0
    (OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'prefix_client',str(OUT/'vectors.txt')))
    (OUT/'negative.v').write_text(rtl(bad,'prefix_client'))
    exe=checks.compile_rtl('source',OUT/'client.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'negative.v')
    failed=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
    assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
    return dict(status='pass',all_state_input_cec=positive,actual_D_mutation=negative,clocks=len(rows),
        nand_mismatches=0,rtl_clocks=len(rows),actual_data_output_gate_mutation_mismatches=wrong,
        actual_RTL_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);small=small_control()
    sat=c_reference();transitions=small_transitions(sat)
    net,comb,parts=make(4,2);rows,expected=vectors(4,2,sat)
    proof=local_check(net,rows)
    small_client=dict(metrics=metrics(net),verification=proof,arbitrary_state_transitions=transitions,expected=expected)
    (OUT/'small.json').write_text(json.dumps(small_client,indent=2)+'\n')
    import ff_sublayer as fixture_source
    fixture_source.OUT=OUT/'model_fixtures';fixture_source.OUT.mkdir(exist_ok=True)
    fixtures=fixture_source.cases(fixture_source.reference())
    raw=(json.dumps(fixtures,indent=2)+'\n').encode();assert sha(raw)=='e586e22a4dd3a2501781d75b3f790dff4454ec07b0e16a959525150eaa424074'
    (OUT/'cases.json').write_bytes(raw)
    net,comb,parts=make(64,32);rows,expected=vectors(64,32,sat,80,fixtures)
    (OUT/'client.ref.v').write_text(reference(64,32))
    (OUT/'client.nl').write_bytes(net.encode());(OUT/'client.v').write_text(rtl(net,'prefix_client'))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    assert sha(net.encode())=='e8e794293f0ef7a578316c75bb484b4e85abcd68bb5e2158927dd67632e67005'
    report=dict(status='small actual client pass; full client and frozen-C expectations constructed only',
        metrics=metrics(net),parts=parts,small_control=small,small_client=small_client,
        expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        fixtures_sha256=sha(raw),numerical_contract_changed=False,adopted=False,
        budget_scope='Standalone client explicitly includes two640-bit H/A word slices. Reusing full-model slots needs separate integration; whole model budgets unchanged.',
        scope='Continuous-bank serial write/read/saturated-residual client, not attention or autonomous transformer control')
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/units/manifest.json',
                 'physical/units/resid.nl','physical/nl_sim.c','physical/verify.py']:
        paths.add(R/name)
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=cloud_check(net,comb,rows)
        report['status']='all current state/input RTL CEC and full actual NAND/RTL/C sequences pass, including real faults'
        (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','expected','small_client')},indent=2))
    print(expected['clocks'],expected['counts'],expected['no_stall_residual_cycles'])


if __name__=='__main__':main()
