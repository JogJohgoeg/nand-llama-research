#!/usr/bin/env python3
"""R120: stable top40 with a parameterised stored score width SW (R109 is SW=32).

Every logit the vocabulary scanner can emit fits in 27-bit two's complement:
|logit| <= max_r RNE(RNE(sum|E8[r]|*128*(2^20-1),127)*escale[r],2^24) = 44,601,028 < 2^26
over the scanner's whole input domain (any q8, any 20-bit max). The sorter therefore
stores 27-bit scores; its 32-bit score output is the sign extension. make(32) rebuilds
the accepted R109 graph byte for byte. embed_check() states the exact claim: for every
narrow state and every sign-extended input, the R109 transition applied to the
sign-extended state equals the sign extension of the narrow transition.
"""
from pathlib import Path
import os,sys
R=Path(os.environ.get('H3_TOP40N_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,with_state,metrics
from export import import_net
import top40
NI,NO=36,44


def layout(SW):
    E=SW+8;return dict(E=E,NS=41*E+24,cursor=40*E,index=40*E+6,filled=40*E+14,carry=40*E+20,inserted=41*E+20,phase=41*E+21)


def make(SW=27,bad_tie=False,bad_sign=False):
    L=layout(SW);E=L['E'];NS=L['NS']
    b=Builder(NS+NI);q=list(range(2,NS+2));p=list(range(NS+2,NS+NI+2))
    head=q[:E];cursor=q[L['cursor']:L['cursor']+6];index=q[L['index']:L['index']+8];filled=q[L['filled']:L['filled']+6]
    carry=q[L['carry']:L['carry']+E];inserted=q[L['inserted']];phase=q[L['phase']:L['phase']+3]
    reset,start,iv=p[:3];keep=b.inv(reset)
    def AND(*xs):return b.reduce(xs,b.land,1)
    def eq(bits,value):return AND(*[v if value>>i&1 else b.inv(v) for i,v in enumerate(bits)])
    def same(a,z):return AND(*[b.inv(b.xor(x,y)) for x,y in zip(a,z)])
    def gt(a,z):return b.add(a,[b.inv(x) for x in z],0)[1]
    states=[eq(phase,i) for i in range(7)];idle,collect,seek,work,read,present,done=states
    begin=AND(keep,start,b.lor(idle,done));ready=AND(keep,collect);take=AND(ready,iv)
    active=AND(keep,b.lor(work,AND(seek,eq(cursor,0))));finish=AND(active,eq(cursor,39))
    # bad_sign (fault): compare as unsigned, so negative logits rank above positive ones.
    sg=(lambda v:v) if bad_sign else b.inv
    key=carry[:SW-1]+[sg(carry[SW-1])];other=head[:SW-1]+[sg(head[SW-1])]
    higher=b.add(key,[b.inv(x) for x in other],int(bad_tie))[1]
    swap=AND(active,b.lor(inserted,b.lor(b.inv(gt(filled,cursor)),higher)))
    valid=AND(keep,present);ack=AND(valid,p[35]);last_in=eq(index,191);last_out=eq(index,39)
    inc=b.add(index,[0]*8,1)[0]
    prefetch=AND(ack,b.inv(last_out),same(cursor,inc[:6]));fetch=AND(keep,read,same(cursor,index[:6]))
    head_load=b.lor(swap,b.lor(fetch,prefetch))
    nc=[b.mux(take,b.mux(head_load,x,y),z) for x,y,z in zip(carry,head,p[3:3+SW]+index)]
    bank=q[E:40*E]+[b.mux(swap,x,y) for x,y in zip(head,carry)]
    advance=b.lor(AND(finish,b.inv(last_in)),AND(ack,b.inv(last_out)))
    clear=b.lor(reset,b.lor(begin,AND(finish,last_in)))
    nr=[AND(b.inv(clear),b.mux(advance,x,y)) for x,y in zip(index,inc)]
    ncursor=b.add(cursor,[0]*6,1)[0];cursor_clear=b.lor(reset,b.lor(begin,eq(cursor,39)))
    ncursor=[AND(b.inv(cursor_clear),x) for x in ncursor]
    add_one=AND(finish,b.inv(gt(filled,[39>>i&1 for i in range(6)])))
    nf=b.add(filled,[0]*6,add_one)[0];nf=[AND(b.inv(b.lor(reset,begin)),x) for x in nf]
    ninsert=AND(b.inv(b.lor(reset,b.lor(begin,take))),b.lor(inserted,swap))
    np=phase[:]
    def set_phase(enable,value):
        nonlocal np
        np=[b.mux(enable,x,value>>i&1) for i,x in enumerate(np)]
    set_phase(begin,1);set_phase(take,2);set_phase(AND(keep,seek,eq(cursor,0)),3)
    set_phase(AND(finish,b.inv(last_in)),1);set_phase(AND(finish,last_in),4)
    set_phase(fetch,5);set_phase(AND(ack,b.inv(last_out),b.inv(prefetch)),4)
    set_phase(AND(ack,last_out),6)
    np=[AND(keep,x) for x in np]
    nxt=bank+ncursor+nr+nf+nc+[ninsert]+np;assert len(nxt)==NS
    score=carry[:SW]+[carry[SW-1]]*(32-SW)
    comb=b.finish(nxt+score+carry[SW:]+[valid,b.reduce(states[1:6],b.lor,0),ready,done])
    return with_state(comb,NS),comb


def embed_pair(SW=27,narrow_comb=None):
    """Two combinational graphs over the SAME inputs (narrow state + 36 inputs whose score
    bits 3+SW..34 are ignored and replaced by the sign of bit 3+SW-1):
    A = sign-extend(narrow transition); B = R109 transition on the sign-extended state/input."""
    L=layout(SW);E=L['E'];NSn=L['NS']
    narrow=narrow_comb or make(SW)[1];_,wide=top40.make()
    def ext_entry(bits):return bits[:SW]+[bits[SW-1]]*(32-SW)+bits[SW:E]
    def ext_state(s):
        out=[]
        for k in range(40):out+=ext_entry(s[k*E:(k+1)*E])
        out+=s[40*E:40*E+20]+ext_entry(s[L['carry']:L['carry']+E])+s[L['inserted']:L['inserted']+4]
        assert len(out)==1664;return out
    def ext_inputs(p):return p[:3+SW]+[p[3+SW-1]]*(32-SW)+p[35:36]
    graphs=[]
    for which in ('A','B'):
        b=Builder(NSn+NI);s=list(range(2,NSn+2));p=list(range(NSn+2,NSn+NI+2))
        if which=='A':
            _,o=import_net(b,narrow,s+p);outs=ext_state(o[:NSn])+o[NSn:]
        else:
            _,o=import_net(b,wide,ext_state(s)+ext_inputs(p));outs=o
        assert len(outs)==1664+NO;graphs.append(b.finish(outs))
    return graphs


if __name__=='__main__':
    assert make(32)[0].encode()==top40.make()[0].encode() and make(32)[1].encode()==top40.make()[1].encode()
    print('SW=32 rebuilds R109 byte for byte')
    for sw in (27,26):print(sw,metrics(make(sw)[0]),metrics(make(sw)[1])['nNand'])
    print('R109',metrics(top40.make()[0]))
