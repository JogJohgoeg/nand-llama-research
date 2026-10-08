#!/usr/bin/env python3
"""R137: chained weight-bank macros for the machine top (H2's 10 x 12 macro grid).

H2's hierarchical top carried 120 bank outputs x 64 bits = 7,680 wires to one 128:1 select in the
middle and did not route. Here the select is distributed: bank macro b (K=256 words, words
b*256..b*256+255 of the shared linear table) computes
    dout[63:0] = (bank_hi == b ? word_b[din] : 0) | chain_in
with din = addr[7:0], bank_hi = addr[14:8]. Grid: row r, column c holds b = r*12 + c; in each row
columns 0..5 chain left to right and 11..6 right to left (end macros take chain_in = 0), so each
row gives two 64-bit outputs (c=5 and c=6) and the middle ORs 10 x 2 x 64 wires. One-hot select +
OR equals the 128:1 select (banks 120..127 are all zero).
Files (out dir): bank_NNN.nl (n_in 79 = din[7:0], bank_hi[6:0], chain_in[63:0]; n_out 64),
or_tree.nl (n_in 20*64 in the order row0-left, row0-right, row1-left, ...; n_out 64), manifest.json.
Proof: the composed grid (15-bit address -> 64) equals shared_words at all 32,768 addresses;
a fault in one macro is rejected.
"""
from pathlib import Path
import argparse,hashlib,json,os,sys,time
R=Path(os.environ.get('H3_CHAIN_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,flip_output
from export import import_net
import layer0,r95_layers as r95l
K,NB,ROWS,COLS=256,120,10,12
sha=lambda b:hashlib.sha256(b).hexdigest()


def macro(b,words):
    t=r95l.ordered(words,64,list(range(7,-1,-1)))
    m=Builder(79);din=list(range(2,10));hi=list(range(10,17));ch=list(range(17,81))
    _,w=import_net(m,t,din)
    hit=m.reduce([x if b>>i&1 else m.inv(x) for i,x in enumerate(hi)],m.land,1)
    return m.finish([m.lor(m.land(hit,x),c) for x,c in zip(w,ch)]),t


def or_tree():
    m=Builder(20*64);vec=[list(range(2+64*k,2+64*k+64)) for k in range(20)]
    while len(vec)>1:vec=[[m.lor(a,c) for a,c in zip(vec[k],vec[k+1])] if k+1<len(vec) else vec[k] for k in range(0,len(vec),2)]
    return m.finish(vec[0])


def compose(macros,tree):
    b=Builder(15);a=list(range(2,17));outs=[]
    for r in range(ROWS):
        left=[0]*64
        for c in range(0,6):_,left=import_net(b,macros[r*COLS+c],a[:8]+a[8:15]+left)
        right=[0]*64
        for c in range(11,5,-1):_,right=import_net(b,macros[r*COLS+c],a[:8]+a[8:15]+right)
        outs+=left+right
    _,y=import_net(b,tree,outs);return b.finish(y)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(R/'build/integer_opt/chain_banks'));a=ap.parse_args()
    os.environ['GITHUB_ACTIONS']='true'   # large-graph simulation (remote machines only)
    from gate_check import simulate
    t0=time.monotonic();W=layer0.shared_words();assert not any(W[NB*K:])
    mac=[];tabs=[]
    for b in range(NB):
        m,t=macro(b,W[b*K:(b+1)*K]);mac.append(m);tabs.append(t)
    tree=or_tree();full=compose(mac,tree)
    got=simulate(full,list(range(32768)));bad=sum(g!=w for g,w in zip(got,W))
    neg=list(mac);neg[37]=flip_output(mac[37]);nbad=sum(g!=w for g,w in zip(simulate(compose(neg,tree),list(range(32768))),W))
    assert bad==0 and nbad>0,(bad,nbad)
    d=Path(a.out);d.mkdir(parents=True,exist_ok=True)
    for b,m in enumerate(mac):(d/f'bank_{b:03d}.nl').write_bytes(m.encode())
    (d/'or_tree.nl').write_bytes(tree.encode())
    man=dict(round='R137',convention=dict(
        address='15 bits, linear word index = layer*6144 + export.weight_words(model.bin, layer) index; din = addr[7:0], bank_hi = addr[14:8]',
        bank_contents='bank b = shared words b*256..b*256+255 (b = 0..119); banks 120..127 are all zero and absent',
        macro='n_in 79: din[7:0] (bit 0 first), bank_hi[6:0], chain_in[63:0]; n_out 64: dout = (bank_hi==b ? word : 0) | chain_in',
        grid='row r (0..9), column c (0..11) holds b = r*12+c; columns 0..5 chain left to right, 11..6 right to left, end macros chain_in = 0; row outputs at c=5 (left) and c=6 (right)',
        or_tree='n_in 1280: row0-left, row0-right, row1-left, ... (64 bits each, bit 0 first); n_out 64 = word',
        word='64 bits = 32 ternary lanes, lane i at bits 2i (plus) and 2i+1 (minus)',
        netlist_format='golden.Netlist 7-byte records (nand.py); verilog/int_c16_wtc_bNNN.v = physical/export.rtl: module (clk unused, din[78:0], dout[63:0]) with din[7:0]=addr[7:0], din[14:8]=bank_hi, din[78:15]=chain_in; or_tree: din[1279:0], dout[63:0]'),
        proof=dict(addresses=32768,mismatches=bad,fault='bank 37 output flip',fault_mismatches=nbad),
        metrics=dict(macros=[metrics(m)['nNand'] for m in mac],macro_total=sum(metrics(m)['nNand'] for m in mac),or_tree=metrics(tree)['nNand'],composed=metrics(full)),files={})
    from export import rtl
    vd=d/'verilog';vd.mkdir(exist_ok=True)
    for b,m in enumerate(mac):(vd/f'int_c16_wtc_b{b:03d}.v').write_text(rtl(m,f'int_c16_wtc_b{b:03d}'))
    (vd/'int_c16_wtc_or_tree.v').write_text(rtl(tree,'int_c16_wtc_or_tree'))
    for f in sorted(d.rglob('*')):
        if f.is_file() and f.name!='manifest.json':man['files'][str(f.relative_to(d))]=dict(sha256=sha(f.read_bytes()),bytes=f.stat().st_size)
    (d/'manifest.json').write_text(json.dumps(man,indent=1)+'\n')
    print(json.dumps(dict(proof=man['proof'],macro_total=man['metrics']['macro_total'],or_tree=man['metrics']['or_tree'],composed=man['metrics']['composed']['nNand'],seconds=round(time.monotonic()-t0,1))))


if __name__=='__main__':main()
