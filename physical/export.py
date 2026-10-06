#!/usr/bin/env python3
"""Mechanical NAND/LATCH composition, without invoking an EDA tool.

The representative slice has all seven layer-0 true weight matrices, DOT32,
one C16 head's K/V codes+maxima, and shared serial integer arithmetic. It is a
micro-operation macro, not a complete transformer layer or autonomous model.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench import lookup
from golden import Netlist
from nand import Builder, metrics, with_state

HERE=ROOT/'physical'
OUT=ROOT/'build/physical'
NI,NO=294,276
MODEL_SHA='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
sha=lambda b:hashlib.sha256(b).hexdigest()


def load_unit(name):
    meta=json.loads((HERE/'units/manifest.json').read_text())[name]
    raw=(HERE/'units'/f'{name}.nl').read_bytes()
    assert sha(raw)==meta['sha256']
    return Netlist.decode(raw,meta['nIn'],meta['nOut'])


def import_net(b,net,inputs,state=()):
    assert len(inputs)==net.n_in and len(state)==net.n_state
    wires=[0,1]+list(inputs);idx=0;ds=[]
    for rec in net.records:
        if rec[0]==1:
            wires.append(state[idx]);idx+=1;ds.append(rec[1])
        else:wires.append(b.nand(wires[rec[1]],wires[rec[2]]))
    return [wires[d] for d in ds],wires[-net.n_out:]


def weight_words(blob,layer=0):
    words=[];offset=layer*194560
    for rows,cols in [(128,128)]*4+[(336,128)]*2+[(128,336)]:
        for row in range(rows):
            for start in range(0,cols,32):
                w=0
                for lane in range(min(32,cols-start)):
                    i=offset+row*cols+start+lane
                    q=(blob[8+i//4]>>(2*(i%4)))&3
                    assert q!=3
                    w|=q<<(2*lane)
                words.append(w)
        offset+=rows*cols
    assert len(words)==6144
    return words


def rtl(net,name='int_c16_slice'):
    base=net.n_in+2;total=base+len(net.records)
    lines=[f'module {name}(input clk,input [{net.n_in-1}:0] din,output [{net.n_out-1}:0] dout);',
           '// Scalar nets avoid artificial dependencies across a huge packed wire.',
           "wire w0=1'b0; wire w1=1'b1;"]
    lines += [f'wire w{i+2}=din[{i}];' for i in range(net.n_in)]
    clocks=[];ff=0
    for i,rec in enumerate(net.records):
        if rec[0]==1:
            lines += [f'reg q{ff}; wire w{base+i}=q{ff};']
            clocks.append(f'  q{ff} <= w{rec[1]};')
            ff+=1
        else:lines.append(f'wire w{base+i}=~(w{rec[1]}&w{rec[2]});')
    if clocks:lines += ['always @(posedge clk) begin']+clocks+['end']
    lines += [f'assign dout[{i}]=w{total-net.n_out+i};' for i in range(net.n_out)]
    lines += ['endmodule']
    return '\n'.join(lines)+'\n'


def main():
    if os.getenv('GITHUB_ACTIONS')!='true':signal.alarm(55)
    blob=(HERE/'model.bin').read_bytes();assert len(blob)==283804 and sha(blob)==MODEL_SHA
    units={n:load_unit(n) for n in ('serial_div','serial_mul','serial_sqrt','resid','exp','dot32')}
    state_count=32*276+sum(units[n].n_state for n in ('serial_div','serial_mul','serial_sqrt'))
    b=Builder(state_count+NI);state=list(range(2,state_count+2))
    inputs=list(range(state_count+2,state_count+2+NI))
    addr=inputs[:13];data=inputs[13:289];we=inputs[289];load=inputs[290];view=inputs[291:294]
    table=lookup(weight_words(blob),64,'phase')
    _,trits=import_net(b,table,addr)
    _,dot=import_net(b,units['dot32'],data[:256]+trits)
    # One head, sixteen tokens, K and V: 32 rows of (32*s8 + u20).
    # Explicit write enables and binary mux read port, no memory macro/ROM.
    next_state=[];rows=[state[i*276:(i+1)*276] for i in range(32)]
    for row,old in enumerate(rows):
        select=b.reduce([a if (row>>i)&1 else b.inv(a) for i,a in enumerate(addr[:5])],b.land,1)
        enable=b.land(we,select)
        next_state.extend(b.mux(enable,a,c) for a,c in zip(old,data))
    read=rows
    for select in addr[:5]:
        read=[[b.mux(select,a,c) for a,c in zip(read[i],read[i+1])] for i in range(0,len(read),2)]
    scalar={};offset=32*276
    for name in ('serial_div','serial_mul','serial_sqrt'):
        u=units[name];old=state[offset:offset+u.n_state];offset+=u.n_state
        operand={'serial_div':data[:64]+data[64:89],
                 'serial_mul':data[:128], 'serial_sqrt':data[:48]}[name]
        nxt,out=import_net(b,u,[load]+operand,old)
        next_state.extend(nxt);scalar[name]=out[u.n_state:]
    _,resid=import_net(b,units['resid'],data[:40])
    _,exp=import_net(b,units['exp'],data[:64])
    choices=[dot,trits,read[0],scalar['serial_div'],scalar['serial_mul'],scalar['serial_sqrt'],resid,exp]
    choices=[x+[0]*(NO-len(x)) for x in choices]
    for select in view:
        choices=[[b.mux(select,a,c) for a,c in zip(choices[i],choices[i+1])] for i in range(0,len(choices),2)]
    net=with_state(b.finish(next_state+choices[0]),state_count)
    assert net.n_in==NI and net.n_out==NO and net.n_state==state_count
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'slice.nl').write_bytes(net.encode())
    (OUT/'slice.v').write_text(rtl(net))
    report=dict(design='int_c16_slice',model_sha256=MODEL_SHA,layer=0,weight_trits=194560,
                weight_words=6144,weight_selector=metrics(table),state_bank_bits=32*276,
                scalar_register_bits=state_count-32*276,metrics=metrics(net),
                inputs='din[12:0] address; [288:13] data; [289] KV write; [290] scalar load; [293:291] view',
                outputs='276 zero-extended bits: view 0 DOT32, 1 trits64, 2 KV row, 3 RNE64+sat20, 4 MUL64, 5 root24, 6 residual20, 7 exp17',
                serial='All scalar units load together and then step every rising clock; sample root after 24 steps, div/mul after 64. KV read is before write.',
                provenance={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),ROOT/'nand.py',ROOT/'bench.py',ROOT/'golden.py',HERE/'units/manifest.json']})
    (OUT/'source.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('design','weight_selector','metrics')}))


if __name__=='__main__':main()
