#!/usr/bin/env python3
"""R136: the shared weight table split into 1,024-word banks for hierarchical hardening (asked by H2).

The R132 table is one Shannon expansion over 15 address bits (317,113 NAND); inside the machine graph
its nodes are shared with the read logic so it cannot be cut into macros without copying. Here the
table is rebuilt as 30 independent banks: bank b = addr[10:15] holds words b*1024..b*1024+1023 of
the linear table (layer*6144 + weight_words index; one layer is exactly 6 banks), each a pure
function of addr[0:10] (Shannon, address bits expanded in reverse as R97), followed by a 32:1 bank
select per output bit (banks 30 and 31 are all zero and fold to constants). The machine/model graphs
are composed with wt_port=True (layer0/machine connect): the 15 table address wires become outputs
and the 64 word bits inputs, so the table can be attached outside unchanged.
Checks: banked table == R132 table at all 32,768 addresses; the R135 machine body + the R132 table
recomposes to the R135 graph byte for byte; body + banked table runs the machine (Verilator) == C.
Export (build/integer_opt/weight_banks/export): bank_XX.nl (10 -> 64), bank_select.nl
(addr[10:15] + 30x64 -> 64), machine_body.nl (44 pins + 64 word -> 15 outputs + 15 address), manifest.json.
"""
from pathlib import Path
import argparse,hashlib,json,os,sys,time
R=Path(os.environ.get('H3_WBANK_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_WBANK_OUT',str(R/'build/integer_opt/weight_banks')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state
from export import import_net,rtl
import layer0,r95_layers as r95l
BANKS,BW=30,10
sha=lambda b:hashlib.sha256(b).hexdigest()


def banks():
    w=layer0.shared_words();out=[]
    for k in range(BANKS):
        words=w[k*1024:(k+1)*1024];assert any(words)
        out.append(r95l.ordered(words,64,list(range(BW-1,-1,-1))))
    assert not any(w[BANKS*1024:])
    return out


def bank_select():
    """inputs: addr[10:15] (5), then bank 0..29 outputs (64 each) -> 64."""
    b=Builder(5+64*BANKS);sel=list(range(2,7));bo=[list(range(7+64*k,7+64*k+64)) for k in range(BANKS)]+[[0]*64]*2
    cur=bo
    for s in sel:cur=[[b.mux(s,x,y) for x,y in zip(cur[k],cur[k+1])] for k in range(0,len(cur),2)]
    return b.finish(cur[0])


def banked_table(bs,sel):
    b=Builder(15);a=list(range(2,17));outs=[]
    for t in bs:outs+=import_net(b,t,a[:BW])[1]
    _,y=import_net(b,sel,a[BW:]+outs);return b.finish(y)


def attach(body,table,ni,no):
    """body: with_state net with ni pins + 64 word inputs and no outputs + 15 address outputs."""
    ns=body.n_state;b=Builder(ns+ni);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+ni))
    _,y=import_net(b,body,p+[0]*64,q);addr=y[no:no+15]
    _,word=import_net(b,table,addr)
    ds,y2=import_net(b,body,p+word,q);assert y2[no:no+15]==addr,'address depends on the word'
    return with_state(b.finish(ds+y2[:no]),ns)


def machine_body():
    import model5,machine
    from types import SimpleNamespace
    mport=model5.build(shared=True,stream=True,wt_port=True)
    ch=machine.children(model=mport['net'])
    shim=dict(ch);m=ch['model'];shim['model']=SimpleNamespace(n_in=m.n_in-64,n_out=m.n_out-15,n_state=m.n_state)
    sh=machine.shell(shim);body=machine.connect(sh,ch,wt_port=True)[0]
    return body,sh,mport['ch']['wt']


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--tb');a=ap.parse_args()
    t0=time.monotonic();bs=banks();sel=bank_select();bt=banked_table(bs,sel)
    body,sh,wt=machine_body()
    import machine
    full_shared=attach(body,wt,machine.NI,machine.NO);full_banked=attach(body,bt,machine.NI,machine.NO)
    r135=machine.connect(machine.shell(machine.children(shared=True,stream=True)),machine.children(shared=True,stream=True))[0]
    same=full_shared.encode()==r135.encode()
    info=dict(banks=[metrics(t)['nNand'] for t in bs],bank_total=sum(metrics(t)['nNand'] for t in bs),select=metrics(sel)['nNand'],
        banked_table=metrics(bt),shared_table=metrics(wt),body=metrics(body),machine_banked=metrics(full_banked),r135=metrics(r135),
        body_plus_shared_table_is_r135_bytes=same)
    print(json.dumps(info,indent=1),round(time.monotonic()-t0,1),flush=True)
    d=OUT/'export';d.mkdir(parents=True,exist_ok=True);man=dict(convention=dict(
        address='15 bits, linear word index = layer*6144 + export.weight_words(model.bin, layer) index; bank = addr[10:15] (bits 10..14), word in bank = addr[0:10]',
        bank_contents='bank b = words b*1024..b*1024+1023; banks 30,31 all zero (not exported; bank_select folds them to 0)',
        word='64 bits = 32 ternary lanes, lane i at bits 2i (plus) and 2i+1 (minus)',
        bank_nl='n_in 10 (addr[0:10], bit 0 first), n_out 64',bank_select_nl='n_in 5 + 30*64: addr[10:15] then bank 0..29 outputs, n_out 64',
        machine_body_nl='n_in 44+64 (machine pins, then table word), n_out 15+15 (machine outputs, then table address), n_state as R135'),files={})
    for k,t in enumerate(bs):(d/f'bank_{k:02d}.nl').write_bytes(t.encode())
    (d/'bank_select.nl').write_bytes(sel.encode());(d/'machine_body.nl').write_bytes(body.encode());(d/'machine_banked.nl').write_bytes(full_banked.encode())
    for f in sorted(d.glob('*.nl')):man['files'][f.name]=dict(sha256=sha(f.read_bytes()),bytes=f.stat().st_size)
    man['metrics']=info;(d/'manifest.json').write_text(json.dumps(man,indent=1)+'\n')
    if a.tb:
        import layer0_tb
        td=Path(a.tb);td.mkdir(parents=True,exist_ok=True);(td/'machine.v').write_text(rtl(full_banked,'machine'));(td/'tb.cpp').write_text(machine.TB)
        print(machine.write_case(td/'case.txt',1,7,0))


if __name__=='__main__':main()
