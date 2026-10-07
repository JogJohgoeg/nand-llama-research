"""Bind and abstract an actual64-bit selector cone from a sequential NAND DAG.

This is construction/structural comparison, not local full gate simulation.
Every cut wire must already exist in the actual graph. Recomposition must
recover every original D/output before the common body can be compared.
"""
from nand import Builder,metrics,flip_output
from export import import_net


def address_states(bind,producer_states):
 dk,mk,kept=bind['DIV']['kept'],bind['MUL']['kept'],bind['kept']
 old=[dk[mk[i]] for i in kept];index={v:i for i,v in enumerate(old)}
 return [index[producer_states+j] for j in range(11)]


def abstract(net,table,addr):
 ni=net.n_state+net.n_in;assert len(addr)==table.n_in==11 and table.n_out==64
 b=Builder(ni);q=list(range(2,2+net.n_state));p=list(range(2+net.n_state,2+ni))
 ds,out=import_net(b,net,p,q);before=len(b.gates);actual=ds+out
 _,ports=import_net(b,table,[q[i] for i in addr])
 assert len(set(ports))==64 and all(ni+2<=w<ni+2+before for w in ports),'selector output not bound to an existing actual wire'
 # Normalization may make users reference the inverse directly. Cut both
 # polarities so neither body can retain a hidden selector dependence.
 c=Builder(ni+64);free=list(range(2+ni,2+ni+64));cuts={}
 for w,v in zip(ports,free):
  assert w not in cuts;cuts[w]=v
  opposite=b.inverse.get(w)
  if opposite is not None:
   assert opposite not in cuts;cuts[opposite]=c.inv(v)
 wires=list(range(ni+2))
 for i,(_,a,z) in enumerate(b.gates[:before]):
  w=ni+2+i;wires.append(cuts[w] if w in cuts else c.nand(wires[a],wires[z]))
 body=c.finish([wires[v] for v in actual])
 # Reconnect this exact body's new primary ports to this exact selector and
 # compare in one canonical builder with the actual original graph.
 d=Builder(ni);state=list(range(2,2+net.n_state));pins=list(range(2+net.n_state,2+ni))
 od,oo=import_net(d,net,pins,state);_,truth=import_net(d,table,[state[i] for i in addr])
 _,rebuilt=import_net(d,body,list(range(2,2+ni))+truth)
 assert rebuilt==od+oo,'cut/recomposition changed an original D or output'
 return body,dict(actual_positive_cut_wires=ports,actual_existing_output_bindings=64,
  cut_polarities=len(cuts),recomposition_all_D_outputs_identical=True,
  source_state_bits=net.n_state,compared_D_outputs=len(actual),body_metrics=metrics(body))


def pair(old,new,oldtable,newtable,bind,oldbind,producer_states):
 assert old.n_state==new.n_state and bind['kept']==oldbind['kept']
 addr=address_states(bind,producer_states)
 left,l=abstract(old,oldtable,addr);right,r=abstract(new,newtable,addr)
 b=Builder(left.n_in);pins=list(range(2,2+left.n_in))
 _,lo=import_net(b,left,pins);_,ro=import_net(b,right,pins)
 assert lo==ro,'body changed outside the explicitly bound weight cone'
 # A real full-graph output mutation must break the body comparison, not merely
 # the isolated selector theorem or an expected answer in a test fixture.
 changed,_=abstract(flip_output(new),newtable,addr);_,bad=import_net(b,changed,pins)
 assert bad!=lo and sum(x!=y for x,y in zip(lo,bad))==1
 return b.finish(lo),b.finish(ro),dict(cursor_address_state_bits=addr,
  all_retained_state_bits=old.n_state,outputs=old.n_out,selector_output_bits=64,
  previous=l,candidate=r,body_same_canonical_D_outputs=True,
  actual_whole_graph_output_fault_breaks_body=True,
  proof_method='actual selector-wire binding + exact recomposition for each graph + common-body all-input CEC; selector theorem proved separately')
