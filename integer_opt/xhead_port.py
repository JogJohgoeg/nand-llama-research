#!/usr/bin/env python3
"""R130: the accepted R120 x -> token head with a lookup port onto its embedding and escale tables.

The machine's front end needs x0[i] = sat(RNE(E8[token][i] * escale[token], 4096)); the head already
holds both constant tables (vocab_row.tables(): 43,915-NAND E8 scalar table on row 8 + column 7
address bits, 18-bit escale table on the row). They are bound to the actual R120 graph (rebuilt in
its canonical builder they must land on existing wires) and, in a gate-by-gate replay, both
polarities of their outputs are cut and fed from one new instance whose address is
mux(port_enable, head row/column, port row/column). The port outputs are that instance's outputs.
With port_enable=0 the graph is the R120 head (CEC); with port_enable=1 the port outputs are the two
tables at the port address (CEC against the tables alone). The caller enables the port only while
the head is idle.
Interface: R120's 56 inputs + port_enable, port_row8, port_col7 (56..71) -> R120's 19 outputs +
E8 code8 (19..26, two's complement) + escale18 (27..44).
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,sys,time
R=Path(os.environ.get('H3_XHPORT_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_XHPORT_OUT',str(R/'build/integer_opt/xhead_port')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from export import import_net,rtl
import xheadn,vocab_row
OLD_NI,OLD_NO=56,19
NI,NO=OLD_NI+16,OLD_NO+26
ROW=list(range(858,866));COL=list(range(886,893))
R120_SHA='eebc7733c3a253b08ac1969f6fef773ad0cb0a407d9620d4fe7d30ecf9c6ac6b'
sha=lambda b:hashlib.sha256(b).hexdigest()


def r120():
    net=xheadn.build(27)['joint'];assert metrics(net)['sha256']==R120_SHA;return net


def splice(net,fault=None):
    tab,sc=vocab_row.tables()[:2]
    ns=net.n_state;ni=ns+net.n_in;b=Builder(ni);q=list(range(2,2+ns));p=list(range(2+ns,2+ni))
    ds,out=import_net(b,net,p,q);before=len(b.gates);existing=lambda w:ni+2<=w<ni+2+before
    _,e=import_net(b,tab,[q[i] for i in ROW+COL]);_,g=import_net(b,sc,[q[i] for i in ROW])
    live_g=[k for k in range(18) if g[k] not in (0,1)]
    assert len(set(e))==8 and all(existing(w) for w in e),'E8 table not bound to actual wires'
    assert all(existing(g[k]) for k in live_g) and len({g[k] for k in live_g})==len(live_g),'escale table not bound'
    c=Builder(ns+NI);cq=list(range(2,2+ns));cp=list(range(2+ns,2+ns+NI))
    en,prow,pcol=cp[OLD_NI],cp[OLD_NI+1:OLD_NI+9],cp[OLD_NI+9:OLD_NI+16]
    sel=en if fault!='port_stuck' else 0
    row=[c.mux(sel,cq[i],v) for i,v in zip(ROW,prow)];col=[c.mux(sel,cq[i],v) for i,v in zip(COL,pcol)]
    _,ne=import_net(c,tab,row+col);_,ng=import_net(c,sc,row)
    for k in range(18):
        if k not in live_g:assert ng[k]==g[k]
    cuts={}
    def cut(w,v):
        assert w not in cuts;cuts[w]=v
        o=b.inverse.get(w)
        if o is not None and o>=ni+2:assert o not in cuts;cuts[o]=c.inv(v)
    for w,v in zip(e,ne):cut(w,v)
    for k in live_g:cut(g[k],ng[k])
    wires=list(range(ni+2))
    for i,(_,a,z) in enumerate(b.gates[:before]):
        w=ni+2+i;wires.append(cuts[w] if w in cuts else c.nand(wires[a],wires[z]))
    comb=c.finish([wires[w] for w in ds]+[wires[w] for w in out]+ne+ng)
    return with_state(comb,ns),comb,dict(cut_wires=len(cuts),escale_live_bits=len(live_g))


def comb_of(net,tie=None):
    ns=net.n_state;b=Builder(ns+OLD_NI);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+OLD_NI))
    ds,out=import_net(b,net,p+([] if tie is None else [0]*16),q)
    return b.finish(ds+out[:OLD_NO])


def port_only(net):
    """Port outputs with port_enable=1 as a graph of (state, head inputs, port row/col)."""
    ns=net.n_state;b=Builder(ns+OLD_NI+15);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+OLD_NI));pr=list(range(2+ns+OLD_NI,2+ns+OLD_NI+15))
    _,out=import_net(b,net,p+[1]+pr,q);return b.finish(out[OLD_NO:])


def tables_only(ns):
    tab,sc=vocab_row.tables()[:2];b=Builder(ns+OLD_NI+15);pr=list(range(2+ns+OLD_NI,2+ns+OLD_NI+15))
    _,e=import_net(b,tab,pr);_,g=import_net(b,sc,pr[:8]);return b.finish(e+g)


def cloud(old,new):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(parents=True,exist_ok=True);proofs={}
    graphs=dict(r120=comb_of(old),head=comb_of(new,0),head_negative=flip_output(comb_of(new,0)),
        tables=tables_only(new.n_state),port=port_only(new),port_stuck=port_only(splice(old,'port_stuck')[0]))
    for k,g in graphs.items():(d/(k+'.blif')).write_text(blif(g))
    for k,ref,w in [('head','r120','equivalent'),('head_negative','r120','different'),('port','tables','equivalent'),('port_stuck','tables','different')]:
        proofs[k]=cec(abc,d/(k+'.blif'),d/(ref+'.blif'),d/(k+'.log'));assert proofs[k]['verdict']==w,k
    return dict(status='pass',proofs=proofs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    t0=time.monotonic();old=r120();new,comb,info=splice(old)
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'xhead_port.nl').write_bytes(new.encode())
    import ci  # cloud-only import, listed so its source is bound too
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    sources['physical/model.bin']=sha((R/'physical/model.bin').read_bytes())
    report=dict(status='E8/escale tables bound to the actual R120 graph and re-addressed through a port mux; CEC awaits Actions',
        metrics=metrics(new),r120_metrics=metrics(old),splice=info,bindings=dict(row_state_bits=ROW,col_state_bits=COL),
        contract='R120 interface + port_enable,port_row8,port_col7 -> R120 outputs + E8 code8 + escale18 at the port address (enable only while the head is idle)',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(old,new);report['status']='port_enable=0 CEC-equivalent to R120; port outputs CEC-equivalent to the E8/escale tables'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('xhead_port',metrics(new),info,round(time.monotonic()-t0,1))


if __name__=='__main__':main()
