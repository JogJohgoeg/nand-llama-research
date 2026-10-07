#!/usr/bin/env python3
"""q8 -> token: accepted R107 vocabulary scanner streams into R113 pick40.

Same parent-shell pattern as R110: the shell has free child-output inputs and is
proved alone; the actual scanner and pick40 graphs are reconnected unchanged.
Inputs reset,start,max20,input_valid,q8,sample,random32; outputs token8,busy,
input_ready,done. A start is accepted only when the scanner and both pick40
cores are idle/done. The token equals C int_pick on the C E8/scale logits.
"""
from pathlib import Path
import argparse,ctypes as ct,hashlib,json,os,random,shutil,signal,subprocess,sys,tempfile,time
R=Path(os.environ.get('H3_VOCAB_PICK_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_VOCAB_PICK_OUT',str(R/'build/integer_opt/vocab_pick')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from golden import Netlist
from gate_check import verify
from export import import_net,rtl,MODEL_SHA
import vocab_scan as scan,pick40,sampler,top40
SA,SP=1420,pick40.NS;NS=SA+SP
NI,NO=64,11
SHELL_IN=NS+NI+(SA+44)+(SP+11);SHELL_OUT=NS+NO+32+68
sha=lambda b:hashlib.sha256(b).hexdigest()


def control():
    """inputs: scanner phase3, sorter phase3, sampler phase3, reset, start -> begin, ready."""
    b=Builder(11);a=[2,3,4];t=[5,6,7];m=[8,9,10];reset,start=11,12
    eq=lambda bits,v:b.reduce([w if v>>i&1 else b.inv(w) for i,w in enumerate(bits)],b.land,1)
    begin=b.reduce([b.inv(reset),start,b.lor(eq(a,0),eq(a,4)),b.lor(eq(t,0),eq(t,6)),b.lor(eq(m,0),eq(m,4))],b.land,1)
    return b.finish([begin,b.land(b.inv(reset),eq(t,1))])


def control_ref(x):
    a=x&7;t=x>>3&7;m=x>>6&7;reset=x>>9&1;start=x>>10&1
    return int(not reset and start and a in (0,4) and t in (0,6) and m in (0,4))|(int(not reset and t==1)<<1)


def shell(ungated=False):
    b=Builder(SHELL_IN);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    s=list(range(NS+NI+2,NS+NI+2+SA+44));t=list(range(NS+NI+2+SA+44,SHELL_IN+2))
    assert len(t)==SP+11
    sy=s[SA:];ty=t[SP:];qp=q[SA:]
    _,ctl=import_net(b,control(),q[1417:1420]+qp[1661:1664]+qp[1664:1667]+p[:2]);begin=p[1] if ungated else ctl[0]
    sc=[p[0],begin]+p[2:31]+[ctl[1]];tc=[p[0],begin,p[31]]+p[32:64]+[sy[32]]+sy[:32]
    assert len(sc)==32 and len(tc)==68
    outputs=ty[:8]+[b.lor(sy[33],ty[9]),sy[34],ty[10]]
    return b.finish(s[:SA]+t[:SP]+outputs+sc+tc)


def connect(shell_net,scanner,pick):
    b=Builder(NS+NI);pins=list(range(2,NS+NI+2))
    _,first=import_net(b,shell_net,pins+[0]*(SA+44+SP+11));sc=first[NS+NO:NS+NO+32]
    sd,sy=import_net(b,scanner,sc,pins[:SA])
    _,second=import_net(b,shell_net,pins+sd+sy+[0]*(SP+11));tc=second[NS+NO+32:]
    assert second[NS+NO:NS+NO+32]==sc
    td,ty=import_net(b,pick,tc,pins[SA:NS]);assert ty[8]==sc[-1]
    _,full=import_net(b,shell_net,pins+sd+sy+td+ty);assert full[NS+NO:]==sc+tc
    comb=b.finish(full[:NS+NO]);return with_state(comb,NS),comb


def reference():
    return f'''module top(input [{SHELL_IN-1}:0] din,output [{SHELL_OUT-1}:0] dout);
wire [{NS-1}:0] q=din[{NS-1}:0];wire [63:0] p=din[{NS+63}:{NS}];
wire [{SA+43}:0] scanner=din[{NS+64+SA+43}:{NS+64}];wire [{SP+10}:0] pick=din[{SHELL_IN-1}:{NS+64+SA+44}];
wire [43:0] sy=scanner[{SA+43}:{SA}];wire [10:0] ty=pick[{SP+10}:{SP}];
wire [2:0] ap=q[1419:1417],tp=q[{SA+1663}:{SA+1661}],mp=q[{SA+1666}:{SA+1664}];
wire begin_op=!p[0] && p[1] && (ap==0 || ap==4) && (tp==0 || tp==6) && (mp==0 || mp==4);
wire ready=!p[0] && tp==1;
wire [31:0] sc={{ready,p[30:2],begin_op,p[0]}};
wire [67:0] tc={{sy[31:0],sy[32],p[63:31],begin_op,p[0]}};
wire [10:0] result={{ty[10],sy[34],(sy[33] || ty[9]),ty[7:0]}};
assign dout={{tc,sc,result,pick[{SP-1}:0],scanner[{SA-1}:0]}};
endmodule
'''


def pick_net():
    scomb,_=pick40.sorter_r();_,mcomb=sampler.make();return pick40.connect(scomb,mcomb)[0]


def scanner_net():
    e,g,words,scales,weights=scan.row.tables();core=scan.row.body();one=scan.row.connect(core,e,g)[0]
    ss=scan.shell();net=scan.connect(ss,one)[0]
    assert metrics(net)['sha256']=='e4e9359812c48a131645b1ecc3f81746c14124f0839518a732b8c5f91edbe9ab'
    return net,weights,scales


def golden(lists,picks):
    src=R/'integer_opt/sample_stream.c'
    with tempfile.TemporaryDirectory() as tmp:
        so=Path(tmp)/'s.so';subprocess.run(['cc','-O2','-std=c99','-Wall','-Wextra','-Werror','-shared','-fPIC',str(src),'-o',str(so)],check=True,timeout=30)
        g=ct.CDLL(str(so));g.int_pick.argtypes=[ct.POINTER(ct.c_int32),ct.c_uint32,ct.c_int]
        blob=(R/'physical/model.bin').read_bytes();g.int_init.argtypes=[ct.c_void_p,ct.c_int];assert g.int_init(blob,len(blob))==0
        return [g.int_pick((ct.c_int32*192)(*lists[blk]),r,s) for blk,r,s in picks]


def picks(lists):
    rng=random.Random(26100715);out=[]
    for blk,lg in enumerate(lists):
        o=sorted(range(192),key=lambda i:(-lg[i],i))[:40];ws=[sampler.weight(lg[o[0]],lg[i]) for i in o];T=sum(ws)
        cum=sum(ws[:rng.randrange(1,40)]);out.append((blk,-(-(cum<<32)//T)&0xffffffff,1))
        out.append((blk,rng.getrandbits(32),blk%2));
    return out


def vectors(c,weights,scales,cases,want):
    sm=scan.Protocol(weights,scales);ps=0;rows=[];rng=random.Random(261007114)
    stats=dict(starts=0,ignored_busy_starts=0,aborts=0,handoffs=0);spans=[]
    expected_rows={(t['block'],t['row']):t['expected'] for t in c['tests']}
    golden_rows=[[expected_rows[block,i] for i in range(192)] for block in range(4)]
    active=None
    def tick(reset=0,start=0,m=0,iv=0,value=0,smp=0,rnd=0,mask=None):
        nonlocal ps
        tp=ps>>1661&7;mp=ps>>top40.NS&7
        begin=int(not reset and start and sm.phase in (0,4) and tp in (0,6) and mp in (0,4));ready=int(not reset and tp==1)
        busy=sm.phase in (1,2,3) or tp not in (0,6) or mp not in (0,4)
        stats['ignored_busy_starts']+=int(bool(start and busy and not reset));stats['aborts']+=int(bool(reset and busy));stats['starts']+=begin
        x=reset+(start<<1)+(m<<2)+(iv<<22)+((value&255)<<23)+(smp<<31)+(rnd<<32)
        old_ps=ps
        sm.tick(reset=reset,start=begin,m=m,iv=iv,value=value,out_ready=ready)
        sy=sm.rows[-1][1]
        tc=reset+(begin<<1)+(smp<<2)+(rnd<<3)+((sy>>32&1)<<35)+((sy&0xffffffff)<<36)
        ps,ty=pick40.transition(ps,tc)
        y=(ty&255)+((int(bool(sy>>33&1 or ty>>9&1)))<<8)+((sy>>34&1)<<9)+((ty>>10&1)<<10)
        rows.append((x,y,(1<<NO)-1 if mask is None else mask))
        if sy>>32&1 and ready:
            index=sy>>35&255;logit=(sy&0x7fffffff)-(sy&0x80000000)
            assert logit==golden_rows[active][index],(active,index,logit,golden_rows[active][index],len(rows),sm.completed);stats['handoffs']+=1
        return y
    tick(reset=1,mask=0);tick()
    def run(k,stall=False,abort_at=None):
        nonlocal active
        blk,r,smp=cases[k];active=blk;before=len(rows)
        tick(start=1,m=c['blocks'][blk]['maximum'],smp=smp,rnd=r)
        while True:
            elapsed=len(rows)-before
            if abort_at is not None and elapsed==abort_at:
                tick(reset=1,start=1,iv=1,value=-128);tick();return
            value=c['blocks'][blk]['q'][sm.loaded] if sm.phase==1 else rng.randrange(-128,128)
            iv=int(not stall or rng.randrange(13)!=0)
            busy=sm.phase in (1,2,3) or (ps>>1661&7) not in (0,6) or (ps>>top40.NS&7) not in (0,4)
            y=tick(start=int(busy and rng.randrange(4)==0),m=rng.randrange(1<<20),iv=iv,value=value,smp=rng.getrandbits(1),rnd=rng.getrandbits(32))
            if y>>10&1:break
            assert len(rows)-before<100000
        assert y&255==want[k],(k,y&255,want[k])
        spans.append(dict(case=k,block=blk,sample=smp,clocks=len(rows)-before,stalls=stall))
        tick()
    for k in range(len(cases)):run(k,stall=k%3==2)
    for at in (1,345,49000,49330,49400,49470):run(0,abort_at=at)
    run(1)
    return rows,dict(clocks=len(rows),picks=len(spans),**stats,
        no_stall_sample_clocks=sorted({s['clocks'] for s in spans if not s['stalls'] and s['sample']}),
        no_stall_greedy_clocks=sorted({s['clocks'] for s in spans if not s['stalls'] and not s['sample']}),spans=spans)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');ap.add_argument('--cases',type=Path);a=ap.parse_args()
    if a.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    xs=list(range(1<<11));checked=verify(control(),xs,[control_ref(x) for x in xs]);assert checked['status']=='pass'
    scanner,weights,scales=scanner_net();pick=pick_net();sh=shell()
    net,comb=connect(sh,scanner,pick)
    print('vocab_pick',metrics(net),round(time.monotonic()-t0,1),flush=True)
    c=json.loads(a.cases.read_text())
    lists=[[ {(t['block'],t['row']):t['expected'] for t in c['tests']}[b,i] for i in range(192)] for b in range(4)]
    cases=picks(lists);want=golden(lists,cases)
    rows,proto=vectors(c,weights,scales,cases,want)
    print({k:v for k,v in proto.items() if k!='spans'},round(time.monotonic()-t0,1))


if __name__=='__main__':main()
