#!/usr/bin/env python3
"""Read/write64-bit values through the existing128-bit FF ring byte port."""
from pathlib import Path
import sys,json,hashlib,random,signal,os,argparse,shutil,subprocess
R=Path(os.environ.get('H3_FF_ACC_TEST_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,with_state,metrics,verify_state,flip_output
from ff_halfword import bank
from prefix_store import select
from export import rtl
from gate_check import Snapshot
sha=lambda raw:hashlib.sha256(raw).hexdigest()
OUT=R/'build/integer_opt/ff_acc_port'


def make(rows):
    a=(rows-1).bit_length();aw=(2*rows-1).bit_length();nc=2+3+aw+2
    ns=rows*128+64+a+nc;ni=2+aw+2+64+2
    b=Builder(ns+ni);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+ni))
    memory=old[:rows*128];payload=old[rows*128:rows*128+64];cursor=old[rows*128+64:rows*128+64+a];cs=old[-nc:]
    phase=cs[:2];index=cs[2:5];address=cs[5:5+aw];mode=cs[-2:]
    reset,start=pins[:2];asked=pins[2:2+aw];command=pins[2+aw:4+aw];data=pins[4+aw:68+aw];enable,read=pins[-2:]
    keep=b.inv(reset)
    def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
    p=[eq(phase,j) for j in range(4)];home=eq(mode,2);write_mode=eq(mode,1)
    _,invalid=b.add(asked,[((1<<aw)-2*rows)>>j&1 for j in range(aw)])
    valid_command=b.lor(eq(command,2),b.land(b.inv(command[1]),b.inv(invalid)))
    begin=b.reduce([keep,start,b.lor(p[0],p[3]),valid_command],b.land,1)
    target=[b.land(b.inv(home),x) for x in address[1:]];assert len(target)==a
    match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cursor,target)],b.land,1)
    seeking=b.land(keep,p[1]);rotate=b.land(seeking,b.inv(match));arrived=b.land(seeking,match)
    writing=b.reduce([keep,p[2],write_mode,enable],b.land,1)
    available=b.reduce([keep,p[2],b.inv(write_mode)],b.land,1);consume=b.land(available,read)
    last=b.reduce(index,b.land,1);finish=b.lor(consume,b.land(writing,last))
    ph=phase[:]
    for action,value in ((begin,[1,0]),(arrived,[home,1]),(finish,[1,1])):
        ph=[b.mux(action,x,y) for x,y in zip(ph,value)]
    cd=[b.land(keep,v) for v in ph]
    clear=b.lor(reset,begin)
    cd += [b.land(b.inv(clear),v) for v in b.add(index,[0]*3,writing)[0]]
    cd += [b.land(keep,b.mux(begin,x,y)) for x,y in zip(address,asked)]
    cd += [b.land(keep,b.mux(begin,x,y)) for x,y in zip(mode,command)]
    wrap=eq(cursor,rows-1);increment=b.add(cursor,[0]*a,1)[0]
    cursor_d=[b.land(keep,b.mux(rotate,x,b.land(b.inv(wrap),y))) for x,y in zip(cursor,increment)]
    capture=b.land(begin,eq(command,1));payload_d=[b.mux(capture,x,y) for x,y in zip(payload,data)]
    byte=select(b,[payload[j*8:j*8+8] for j in range(8)],index)
    bd,word=bank(b,memory,byte,index+address[:1],writing,rotate,0)
    value=[b.mux(address[0],x,y) for x,y in zip(word[:64],word[64:128])]
    busy=b.land(keep,b.lor(p[1],p[2]));done=b.reduce([keep,p[3],b.inv(begin)],b.land,1)
    states=bd+payload_d+cursor_d+cd;comb=b.finish(states+value+[available,busy,done])
    net=with_state(comb,ns)
    return net,comb,dict(rows=rows,values=rows*2,bank_bits=rows*128,payload_bits=64,cursor_bits=a,parent_bits=nc,
        state_order='FF ring bytes, captured write64, modulo-row cursor, phase/byte/address/mode')


def reference(rows):
    a=(rows-1).bit_length();aw=(2*rows-1).bit_length();nc=7+aw;s=rows*128;ns=s+64+a+nc;ni=70+aw
    return f'''module top(input [{ns+ni-1}:0] din,output [{ns+66}:0] dout);
wire [{s-1}:0] memory=din[{s-1}:0];
wire [63:0] payload=din[{s+63}:{s}];
wire [{a-1}:0] cursor=din[{s+64+a-1}:{s+64}];
wire [1:0] phase=din[{ns-nc+1}:{ns-nc}];
wire [2:0] index=din[{ns-nc+4}:{ns-nc+2}];
wire [{aw-1}:0] address=din[{ns-3}:{ns-aw-2}];
wire [1:0] mode=din[{ns-1}:{ns-2}];
wire [{ni-1}:0] pins=din[{ns+ni-1}:{ns}];
wire reset=pins[0],start=pins[1],enable=pins[{ni-2}],read=pins[{ni-1}];
wire [{aw-1}:0] wanted=pins[{aw+1}:2];
wire [1:0] command=pins[{aw+3}:{aw+2}];
wire [63:0] data=pins[{aw+67}:{aw+4}];
wire legal=command==2 || (command<2 && wanted<{2*rows});
wire begin_command=!reset && start && (phase==0 || phase==3) && legal;
wire [{a-1}:0] target=mode==2 ? {a}'d0 : address[{aw-1}:1];
wire rotate=!reset && phase==1 && cursor!=target;
wire arrived=!reset && phase==1 && cursor==target;
wire writing=!reset && phase==2 && mode==1 && enable;
wire available=!reset && phase==2 && mode!=1;
wire finish=(available && read) || (writing && (&index));
reg [1:0] next_phase;
reg [2:0] next_index;
reg [{aw-1}:0] next_address;
reg [1:0] next_mode;
always @* begin
 next_phase=phase;next_index=index;next_address=address;next_mode=mode;
 if(reset)begin next_phase=0;next_index=0;next_address=0;next_mode=0;end
 else if(begin_command)begin next_phase=1;next_index=0;next_address=wanted;next_mode=command;end
 else begin
  if(arrived)next_phase=mode==2 ? 3 : 2;
  if(writing)next_index=index+3'd1;
  if(finish)next_phase=3;
 end
end
wire [5:0] input_offset={{index,3'b000}};
wire [6:0] bank_offset={{address[0],index,3'b000}};
reg [{s-1}:0] next_memory;
always @* begin
 next_memory=memory;
 if(rotate)next_memory={{memory[127:0],memory[{s-1}:128]}};
 else if(writing)next_memory[bank_offset +: 8]=payload[input_offset +: 8];
end
assign dout[{s-1}:0]=next_memory;
assign dout[{s+63}:{s}]=begin_command && command==1 ? data : payload;
assign dout[{s+64+a-1}:{s+64}]=reset ? {a}'d0 : rotate ? (cursor=={a}'d{rows-1} ? {a}'d0 : cursor+{a}'d1) : cursor;
assign dout[{ns-1}:{ns-nc}]={{next_mode,next_address,next_index,next_phase}};
assign dout[{ns+63}:{ns}]=address[0] ? memory[127:64] : memory[63:0];
assign dout[{ns+64}]=available;
assign dout[{ns+65}]=!reset && (phase==1 || phase==2);
assign dout[{ns+66}]=!reset && phase==3 && !begin_command;
endmodule
'''


class Model:
    def __init__(self,rows):
        self.rows=rows;self.a=(rows-1).bit_length();self.aw=(2*rows-1).bit_length()
        self.memory=[0]*rows;self.known=[0]*rows;self.payload=self.cursor=self.phase=self.index=self.address=self.mode=0
        self.counts=dict(start=0,complete=0,abort=0,read64=0,write_bytes=0,home=0,rotations=0,write_stalls=0,read_stalls=0,busy_starts=0,invalid_starts=0)
    def tick(self,v):
        aw=self.aw;reset=v&1;start=v>>1&1;address=v>>2&((1<<aw)-1);mode=v>>(2+aw)&3
        data=v>>(4+aw)&((1<<64)-1);enable=v>>(68+aw)&1;read=v>>(69+aw)&1;p=self.phase
        valid=mode==2 or mode<2 and address<2*self.rows;begin=bool(not reset and start and p in (0,3) and valid)
        target=0 if self.mode==2 else self.address//2;match=self.cursor==target
        available=int(not reset and p==2 and self.mode!=1);busy=int(not reset and p in (1,2));done=int(not reset and p==3 and not begin)
        word=self.memory[0]>>(64*(self.address&1))&((1<<64)-1)
        y=word+(available<<64)+(busy<<65)+(done<<66);mask=(1<<67)-1 if available else 7<<64
        c=self.counts;c['busy_starts']+=int(not reset and start and p in (1,2));c['invalid_starts']+=int(not reset and start and p in (0,3) and not valid)
        c['read_stalls']+=int(available and not read);c['write_stalls']+=int(not reset and p==2 and self.mode==1 and not enable)
        if reset:
            c['abort']+=int(p in (1,2));self.cursor=self.phase=self.index=self.address=self.mode=0;self.known=[0]*self.rows
        elif begin:
            self.phase=1;self.index=0;self.address=address;self.mode=mode;c['start']+=1
            if mode==1:self.payload=data
        elif p==1:
            if not match:
                self.memory=self.memory[1:]+self.memory[:1];self.known=self.known[1:]+self.known[:1]
                self.cursor=0 if self.cursor==self.rows-1 else (self.cursor+1)&((1<<self.a)-1);c['rotations']+=1
            elif self.mode==2:self.phase=3;c['complete']+=1;c['home']+=1
            else:self.phase=2
        elif p==2 and self.mode==1 and enable:
            at=8*self.index+64*(self.address&1);byte=self.payload>>(8*self.index)&255
            self.memory[0]=(self.memory[0]&~(255<<at))+(byte<<at);self.known[0]|=1<<(self.index+8*(self.address&1))
            self.index=(self.index+1)&7;c['write_bytes']+=1
            if self.index==0:self.phase=3;c['complete']+=1
        elif p==2 and self.mode!=1 and read:
            assert self.known[0]>>(8*(self.address&1))&255==255
            self.phase=3;c['complete']+=1;c['read64']+=1
        return y,mask


def vectors(rows,trace=None):
    m=Model(rows);rng=random.Random(260722+rows);stim=[];calls=[];logical=[None]*(2*rows)
    def tick(reset=0,start=0,address=0,mode=0,data=0,enable=0,read=0):
        x=reset+(start<<1)+(address<<2)+(mode<<(2+m.aw))+(data<<(4+m.aw))+(enable<<(68+m.aw))+(read<<(69+m.aw))
        y,mask=m.tick(x);stim.append((x,y,mask));return y
    def command(op,address,data=0,stall=True,abort=None):
        assert m.phase in (0,3)
        for _ in range(rng.randrange(3)):tick(start=1,mode=3,address=(1<<m.aw)-1,enable=1,read=1,data=rng.getrandbits(64))
        tick(start=1,mode=op,address=address,data=data,enable=1,read=1);clock=1;result=None;initial=m.cursor
        while m.phase!=3:
            if abort=='seek' and m.phase==1 or abort=='write' and m.phase==2 and m.index==4 or abort=='read' and m.phase==2 and m.mode==0:
                tick(reset=1,start=1,enable=1,read=1);return False
            p=m.phase;take=int(not stall or rng.randrange(4)!=0)
            y=tick(start=rng.randrange(4)==0,mode=rng.randrange(4),address=rng.randrange(1<<m.aw),data=rng.getrandbits(64),enable=take,read=take)
            if p==2 and op==0 and take:result=y&((1<<64)-1)
            clock+=1;assert clock<rows+128
        if op==1:logical[address]=data
        if op==0:assert result==logical[address]
        if op==2:assert m.cursor==0
        calls.append(dict(mode=op,address=address,data=data,result=result,initial_cursor=initial,clocks=clock))
        return True
    def fill():
        for i in range(2*rows):command(1,i,rng.getrandbits(64))
    tick(reset=1);fill()
    for _ in range(3):
        for i in rng.sample(list(range(2*rows)),2*rows):
            command(0,i)
            if i%3==0:command(1,i,rng.getrandbits(64));command(0,i)
        command(2,(1<<m.aw)-1)
    for abort,op in [('seek',1),('write',1),('read',0)]:
        assert not command(op,0,0,abort=abort);fill();command(0,0)
    trace_stats=None
    if trace:
        command(2,0,stall=False)
        before=dict(m.counts);before_clock=len(stim);c_read=c_write=c_home=0
        for op,address,data in trace:
            if op==0:assert logical[address]==data;c_read+=1
            elif op==1:c_write+=1
            else:assert op==2;c_home+=1
            command(op,address,data,stall=False)
        trace_stats=dict(operations=len(trace),read64=c_read,write64=c_write,home=c_home,
            protocol_clocks=sum(x['clocks'] for x in calls[-len(trace):]),
            stimulus_clocks_including_invalid_start_probes=len(stim)-before_clock,
            rotations=m.counts['rotations']-before['rotations'])
        assert trace_stats['protocol_clocks']==3*c_read+10*c_write+2*c_home+trace_stats['rotations']
        for address in range(2*rows):command(0,address)
    command(2,0)
    return stim,dict(clocks=len(stim),counts=m.counts,calls=calls,real_C_trace=trace_stats)


def c_trace():
    import ctypes as ct,subprocess,re
    from block_kv_model import variant as block_variant
    from ring_model import load,forward
    source=(R/'integer/int_model.c').read_text();assert sha(source.encode())=='db3219b3f1156eb9c7c0de879624379be52575fc972a188a480d04b9b159b777'
    blob=(R/'physical/model.bin').read_bytes();assert sha(blob)=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
    _,text=block_variant(source)
    helper='''static uint64_t acc_trace_data[4096][3];
static unsigned acc_trace_size;
static void acc_trace_record(unsigned op,unsigned address,uint64_t bits) {
    if(acc_trace_size<4096) {
        acc_trace_data[acc_trace_size][0]=op;acc_trace_data[acc_trace_size][1]=address;
        acc_trace_data[acc_trace_size][2]=bits;acc_trace_size++;
    }
}
unsigned trace_count(void){return acc_trace_size;}
uint64_t trace_field(unsigned i,unsigned j){return acc_trace_data[i][j];}
'''
    for before,after in [
        ('static uint64_t cached_dequants',helper+'\nstatic uint64_t cached_dequants'),
        ('static void acc_home(void) {acc_seek(0);}','static void acc_home(void) {acc_seek(0);acc_trace_record(2,0,0);}'),
        ('acc_byte_writes+=8;all_tick(8);','acc_byte_writes+=8;all_tick(8);acc_trace_record(1,lane,(uint64_t)value);'),
        ('acc_byte_reads+=8;all_tick(8);','acc_byte_reads+=8;all_tick(8);acc_trace_record(0,lane,bits);'),
        ('acc_cursor=0;','acc_cursor=0;acc_trace_size=0;')]:
        assert text.count(before)==1,before;text=text.replace(before,after)
    path=OUT/'trace.c';path.write_text(text);so=OUT/'trace.so'
    subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC','-DBANK_ROWS=32','-DPREFIX_ROWS=64',str(path),'-o',str(so)],check=True,timeout=30)
    g=load(so,blob);g.trace_count.restype=ct.c_uint;g.trace_field.argtypes=[ct.c_uint,ct.c_uint];g.trace_field.restype=ct.c_uint64
    payload=json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>',(R/'docs/index.html').read_text(),re.S)[1])
    fixture=next(f for f in payload['fixtures'] if f['name']=='random16');got=forward(g,fixture['ids'])
    assert got['logits']==fixture['logit_sha256'] and got['trace']==fixture['trace_sha256']
    assert g.trace_count()==4096
    rows=[tuple(g.trace_field(i,j) for j in range(3)) for i in range(g.trace_count())]
    receipt=dict(status='full traced C random16 logits and layer traces match frozen published fixture',
        fixture=fixture,actual=got,records=rows,trace_c_sha256=sha(text.encode()),
        scope='First4096 real FF scratch read/write/home operations, not an entire token hardware trace')
    (OUT/'c_trace.json').write_text(json.dumps(receipt,indent=2)+'\n');return rows,receipt


def small_check(net,rows):
    assert metrics(net)['nNand']+net.n_state<=4000
    state=bytes(net.n_state);g=Snapshot.decode(net.encode(),net.n_in,net.n_out)
    for i,(x,y,m) in enumerate(rows):
        state,out=g.step(state,bytes(x>>b&1 for b in range(net.n_in)))
        assert (sum(v<<b for b,v in enumerate(out))^y)&m==0,i
    bad=Snapshot.decode(flip_output(net).encode(),net.n_in,net.n_out);state=bytes(net.n_state);wrong=0
    for x,y,m in rows:
        state,out=bad.step(state,bytes(x>>b&1 for b in range(net.n_in)));wrong+=int(bool((sum(v<<b for b,v in enumerate(out))^y)&m))
    assert wrong;return dict(clocks=len(rows),mismatches=0,actual_data_gate_mutation_mismatches=wrong)


def small_transitions():
    rows=3;net,comb,_=make(rows);ns=net.n_state;rng=random.Random(260723);xs=[];ys=[]
    def state(m):
        v=sum(x<<(128*j) for j,x in enumerate(m.memory));shift=128*m.rows
        v+=m.payload<<shift;shift+=64;v+=m.cursor<<shift;shift+=m.a
        parent=m.phase+(m.index<<2)+(m.address<<5)+(m.mode<<(5+m.aw))
        return v+(parent<<shift)
    assert metrics(net)['nNand']+net.n_state<=4000
    for j in range(1024):
        m=Model(rows);m.memory=[rng.getrandbits(128) for _ in range(rows)];m.known=[65535]*rows
        m.payload=rng.getrandbits(64);m.cursor=rng.randrange(1<<m.a);m.phase=j&3;m.index=j>>2&7;m.mode=j>>5&3;m.address=rng.randrange(1<<m.aw)
        x=rng.getrandbits(net.n_in);old=state(m);y,_=m.tick(x);xs.append(old+(x<<ns));ys.append(state(m)+(y<<ns))
    proof=verify_state(comb,xs,ys,ns);proof.pop('nl_hex');return proof


def cloud_check(net,comb,rows):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from nand import blif,from_yosys
    from ci import cec
    import verify as checks
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    (OUT/'port.blif').write_text(blif(comb));(OUT/'port.ref.v').write_text(reference(21))
    ys=OUT/'reference.ys';ys.write_text(f'read_verilog {OUT}/port.ref.v\nhierarchy -check -top top\nproc\nflatten\nmemory_map\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {OUT}/reference.json\n')
    subprocess.run(['yosys','-Q','-T','-l',str(OUT/'yosys.log'),'-s',str(ys)],check=True,stdout=subprocess.DEVNULL,timeout=240)
    ref=from_yosys(json.loads((OUT/'reference.json').read_text()),comb.n_in,comb.n_out)
    (OUT/'reference.blif').write_text(blif(ref));(OUT/'negative.blif').write_text(blif(flip_output(comb)))
    good=cec(abc,OUT/'port.blif',OUT/'reference.blif',OUT/'cec.log');assert good['verdict']=='equivalent'
    bad=cec(abc,OUT/'negative.blif',OUT/'reference.blif',OUT/'negative.cec.log');assert bad['verdict']=='different'
    checks.OUT=OUT;checks.NI=net.n_in;checks.NO=net.n_out
    checks.run(['cc','-O3','-std=c99','-shared','-fPIC',R/'physical/nl_sim.c','-o',OUT/'sim.so'],60)
    assert checks.check_nand(rows,net.encode())==0
    fault=flip_output(net);wrong=checks.check_nand(rows,fault.encode());assert wrong>0
    (OUT/'tb.v').write_text(checks.testbench(net.n_in,net.n_out,'ff_acc_port',str(OUT/'vectors.txt')))
    (OUT/'negative.v').write_text(rtl(fault,'ff_acc_port'))
    exe=checks.compile_rtl('source',OUT/'port.v');checks.run([exe],300)
    exe=checks.compile_rtl('negative',OUT/'negative.v')
    failed=subprocess.run([str(exe)],capture_output=True,text=True,timeout=300)
    (OUT/'negative_verilator.log').write_text(failed.stdout+failed.stderr)
    assert failed.returncode!=0 and 'C99 comparison failed' in failed.stdout+failed.stderr
    return dict(status='pass',all_current_state_input_cec=good,actual_D_mutation=bad,
        clocks=len(rows),nand_mismatches=0,rtl_clocks=len(rows),actual_data_gate_mismatches=wrong,actual_RTL_mutation_rejected=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    OUT.mkdir(parents=True,exist_ok=True);transition=small_transitions()
    net,comb,parts=make(3);rows,expected=vectors(3)
    small=dict(metrics=metrics(net),arbitrary_state=transition,sequence=small_check(net,rows),expected=expected)
    trace,traced=c_trace();net,comb,parts=make(21);rows,expected=vectors(21,trace)
    (OUT/'port.nl').write_bytes(net.encode());(OUT/'port.v').write_text(rtl(net,'ff_acc_port'))
    (OUT/'port.ref.v').write_text(reference(21))
    (OUT/'vectors.txt').write_text(''.join(f'{x:x} {y:x} {m:x}\n' for x,y,m in rows))
    assert sha(net.encode())=='059c7f7c83f89ff0a9e5263f52a775be0a84e78b30718fdb2585494313a266d1'
    report=dict(status='small actual port and traced frozen-C model pass; full source and vectors constructed only',
        metrics=metrics(net),parts=parts,small=small,expected=expected,vector_sha256=sha((OUT/'vectors.txt').read_bytes()),
        trace_json_sha256=sha((OUT/'c_trace.json').read_bytes()),numerical_contract_changed=False,adopted=False,
        storage_scope='Existing2688-bit FF bank; standalone write operand64/cursor5/parent13 explicitly counted. Whole-model sharing/ownership still outside.',
        timing_scope='No-stall protocol: read3,write10,home2 clocks plus exact held-ring rotations; traced4096 operations checked in independent reference, actual-size gate sequence pending')
    paths={Path(__file__).resolve()}
    for module in list(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            p=Path(name).resolve()
            if R in p.parents and p.suffix=='.py':paths.add(p)
    for name in ['ci.py','integer/int_model.c','physical/model.bin','physical/nl_sim.c','physical/verify.py','docs/index.html',
        'integer_opt/block_kv_access.c','integer_opt/block_attention.c','integer_opt/circulate_access.c','integer_opt/prefix_circulate_access.c',
        'integer_opt/ring_access.c','integer_opt/prefix_access.c']:
        paths.add(R/name)
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
        sources={str(p.relative_to(R)) if R in p.parents else 'integer_opt/'+p.name:sha(p.read_bytes()) for p in sorted(paths)})
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.cloud:
        report['verification']=cloud_check(net,comb,rows);report['status']='all D/output CEC and actual NAND/RTL/C-trace pass, with real faults'
        (OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','small','expected')},indent=2))
    print(expected['clocks'],expected['counts'],expected['real_C_trace'])


if __name__=='__main__':main()
