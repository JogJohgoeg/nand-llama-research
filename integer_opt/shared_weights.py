#!/usr/bin/env python3
"""R132: one shared weight table for the five-layer model (R97's full-model table, 317,113 NAND).

R129 carries four five-layer weight tables (Q/K/V 90,801 + O 35,013 + gate/up 164,386 + down 86,206
NAND). Their reads never overlap in time: R95 reads in layer phases 1..13, the O projection in
14..16, the FFN in 17..20, and inside the FFN all gate/up reads finish before the down stage
(ff_writeback.py; R98's owner state 5936 marks the down stage). So the children lose their tables
(R127/R128 port splices, R126 word mode) and the composition reads one table at the linear address
layer*6144 + word (weight_words order) computed by the controller (layer0.shell with 'wt').
Checks: R95 port + selector re-attached == R127 (CEC); O word mode + O words == R126 (CEC); the
shared table at all 32,768 addresses == weight_words; the FFN port with the shared table addressed
by owner ? down : gate/up equals C on R128's 12 runs (Verilator); the controller shell == RTL (CEC);
the shared five-layer graph == int_run after layer 4 for L=1..3 (Verilator); faults rejected.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,time
R=Path(os.environ.get('H3_SHARED_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
OUT=Path(os.environ.get('H3_SHARED_OUT',str(R/'build/integer_opt/shared_weights')))
sys.path[:0]=[str(Path(__file__).resolve().parent),str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,blif,flip_output
from export import import_net,rtl
import layer0,model5,r95_layers as r95l,r98_layers as r98l,oproj_layers
sha=lambda b:hashlib.sha256(b).hexdigest()


def comb_with(net,feed):
    """Combinational next-state/output graph of a port child whose 64 word inputs are fed by feed(builder,state,pins)."""
    ns=net.n_state;ni=net.n_in-64;b=Builder(ns+ni);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+ni))
    ds,out=import_net(b,net,p+feed(b,q,p),q);return b,ds,out


def r95_reattached():
    port=r95l.splice(r95l.r95(),None,r95l.gamma5(r95l.GAM5_ORDER),port=True)[0];s5=r95l.selector5(r95l.SEL5_ORDER)
    b,ds,out=comb_with(port,lambda b,q,p:import_net(b,s5,[q[i] for i in r95l.SEL]+p[44:47])[1])
    return port,b.finish(ds+out[:r95l.NO])


def r95_original():
    net=r95l.splice(r95l.r95(),r95l.selector5(r95l.SEL5_ORDER),r95l.gamma5(r95l.GAM5_ORDER))[0]
    ns=net.n_state;b=Builder(ns+net.n_in);ds,out=import_net(b,net,list(range(2+ns,2+ns+net.n_in)),list(range(2,2+ns)));return b.finish(ds+out)


def oproj_word():
    g=oproj_layers.build();return oproj_layers.connect(g['shell'],None,g['quant'],g['scale'],g['alphas'],word=True)[0],g


def o_words_table():
    from export import weight_words
    blob=(R/'physical/model.bin').read_bytes()
    words=[w for l in r95l.PAD for w in weight_words(blob,l)[1536:2048]]
    return r95l.ordered(words,64,list(range(12)))           # address row<<2|group (9 bits) + layer3


def o_reattached():
    net,_=oproj_word();t=o_words_table()
    b,ds,out=comb_with(net,lambda b,q,p:import_net(b,t,out_addr(b,net,q,p)+p[45:48])[1])
    return b.finish(ds+out[:oproj_layers.NO])


def out_addr(b,net,q,p):
    _,y=import_net(b,net,p+[0]*64,q);return y[oproj_layers.NO:]     # the 9 address outputs are state-only


def o_trit_net():
    """(col7,row7,layer3) -> 2-bit code through the O words table and the word-mode lane select."""
    t=o_words_table();b=Builder(17);col,row,ly=list(range(2,9)),list(range(9,16)),list(range(16,19))
    _,w=import_net(b,t,col[5:7]+row+ly);return b.finish(oproj_layers.trit_select(b,w,col))


def o_substituted():
    """R126's composition with its 2-bit table replaced by o_trit_net (functionally equal: exhaustive check)."""
    g=oproj_layers.build();net=oproj_layers.connect(g['shell'],o_trit_net(),g['quant'],g['scale'],g['alphas'])[0]
    ns=net.n_state;b=Builder(ns+net.n_in);ds,out=import_net(b,net,list(range(2+ns,2+ns+net.n_in)),list(range(2,2+ns)));return b.finish(ds+out)


def o_original():
    net=oproj_layers.build()['net'];ns=net.n_state;b=Builder(ns+net.n_in)
    ds,out=import_net(b,net,list(range(2+ns,2+ns+net.n_in)),list(range(2,2+ns)));return b.finish(ds+out)


def ffn_shared(fault=None):
    """R128's FFN with the shared table: word = T(layer*6144 + (owner ? 4736+row*11+group : 2048+up*1344+row*4+group))."""
    port=r98l.splice(r98l.r98(),(None,None,r98l.tables5(r98l.GU5_ORDER,r98l.DOWN5_ORDER,r98l.GAM5_ORDER)[2]),port=True)[0];wt=layer0.shared_table()
    ns=port.n_state;NI=port.n_in-64;b=Builder(ns+NI);q=list(range(2,2+ns));p=list(range(2+ns,2+ns+NI))
    _,y=import_net(b,port,p+[0]*64,q);gu=y[32:44];dn=y[44:55];owner=y[55] if fault!='owner_swapped' else b.inv(y[55])
    z=lambda bits,n:bits+[0]*(n-len(bits));cst=lambda v,n:[v>>t&1 for t in range(n)]
    gl=b.add(b.add(z(gu[:11],13),[b.land(gu[11],t) for t in cst(1344,13)])[0],cst(2048,13))[0]
    row,grp=dn[4:11],dn[0:4];r11=b.add(b.add(z([0,0,0]+row,13),z([0]+row,13))[0],z(row,13))[0]
    dl=b.add(b.add(r11,z(grp,13))[0],cst(4736,13))[0];loc=[b.mux(owner,x,v) for x,v in zip(gl,dl)]
    ly=p[26:29];l3=b.add(z(ly,4),z([0]+ly,4))[0];addr=loc[:11]+b.add(z(loc[11:],4),l3)[0]
    _,word=import_net(b,wt,addr);ds,out=import_net(b,port,p+word,q)
    return with_state(b.finish(ds+out[:r98l.NO]),ns)


def cloud(g):
    assert os.getenv('GITHUB_ACTIONS')=='true'
    from ci import cec
    from gate_check import verify as vtab
    abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
    d=OUT/'proofs';d.mkdir(parents=True,exist_ok=True);proofs={}
    port,r95a=r95_reattached();graphs=dict(r95_reattached=r95a,r127=r95_original(),o_reattached=o_reattached(),r126_substituted=o_substituted())
    graphs['r95_negative']=flip_output(r95a);graphs['o_negative']=flip_output(graphs['o_reattached'])
    for k,n in graphs.items():(d/(k+'.blif')).write_text(blif(n))
    for k,ref,w in [('r95_reattached','r127','equivalent'),('r95_negative','r127','different'),('o_reattached','r126_substituted','equivalent'),('o_negative','r126_substituted','different')]:
        proofs[k]=cec(abc,d/(k+'.blif'),d/(ref+'.blif'),d/(k+'.log'));assert proofs[k]['verdict']==w,k
    table=vtab(g['ch']['wt'],list(range(32768)),layer0.shared_words());assert table['status']=='pass'
    gg=oproj_layers.build();want=[gg['codes'][oproj_layers.PAD[a>>14]][a&16383] for a in range(1<<17)]
    o_codes=vtab(o_trit_net(),list(range(1<<17)),want);assert o_codes['status']=='pass'   # == R126's table at every address
    # FFN alone with the shared table, R128's cases
    txt,meta=r98l.case_text();(OUT/'ffn_case.txt').write_text(txt);(OUT/'ffn_case_short.txt').write_text(''.join(txt.splitlines(True)[:3]));ffn={}
    for k,n,c in (('source',ffn_shared(),'ffn_case.txt'),('owner_swapped',ffn_shared('owner_swapped'),'ffn_case_short.txt')):
        ffn[k]=r98l.vrun(OUT/('ffn_'+k),n,OUT/c)
    assert ffn['source']['rc']==0 and ffn['source']['result']['mismatches']==0 and ffn['owner_swapped']['rc']!=0
    model5.OUT=OUT;mv=model5.cloud(g,faults=('owner_swapped',))
    return dict(status='pass',child_proofs=proofs,table_all_addresses=table,o_word_select_all_addresses=o_codes,ffn_alone=ffn,ffn_cases=meta,model=mv)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');a=ap.parse_args()
    t0=time.monotonic();OUT.mkdir(parents=True,exist_ok=True);g=model5.build(shared=True)
    (OUT/'shell.nl').write_bytes(g['shell'].encode())
    import ci,gate_check,sampler,layer0_tb,final_a8  # bound sources
    paths={Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m,'__file__',None)}
    sources={str(p.relative_to(R)):sha(p.read_bytes()) for p in sorted(paths) if R in p.parents and p.suffix=='.py'}
    for n in ('integer/int_model.c','integer_opt/final_a8_golden.c','integer_opt/oproj_golden.c','integer_opt/r98_layers_golden.c','physical/model.bin','physical/nl_sim.c','integer_opt/layer0_units/manifest.json')+tuple('integer_opt/layer0_units/'+f for f in ('r95_norm_qkv.nl','r72_head.nl','r98_ffn.nl','r52_xbank.nl')):
        sources[n]=sha((R/n).read_bytes())
    report=dict(status='children without tables + one shared weight table; proofs and runs await Actions',
        metrics=metrics(g['net']),r129_metrics_for_comparison=dict(nNand=501098,nLatch=85888),shell=metrics(g['shell']),
        children={k:metrics(g['ch'][k]) for k in layer0.ORDER+['wt']},
        tables_replaced=dict(qkv=90801,o=35013,gate_up=164386,down=86206,shared=metrics(g['ch']['wt'])['nNand']),
        ownership='layer phases 1..13 R95, 14..16 O projection, 17..20 FFN (owner ? down : gate/up); address layer*6144 + weight_words index',
        numerical_contract_changed=False,whole_budget_changed=False,adopted=False,run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),sources=sources)
    if a.cloud:report['verification']=cloud(g);report['status']='child CECs, all 32,768 shared-table words, FFN alone with the shared table == C, shell CEC, shared five-layer graph == int_run (L=1..3), faults rejected'
    report['seconds']=round(time.monotonic()-t0,3);(OUT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    print('shared',metrics(g['net']),round(time.monotonic()-t0,1))


if __name__=='__main__':main()
