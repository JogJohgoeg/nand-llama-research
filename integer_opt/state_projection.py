"""Remove unobservable feedback state and bind the retained-state projection."""
from golden import Netlist
from nand import Builder,metrics,with_state,flip_output
from export import import_net
from gate_check import verify

def prune(net):
 base=net.n_in+2;ns=net.n_state
 assert all(rec[0]==1 for rec in net.records[:ns]) and all(rec[0]==0 for rec in net.records[ns:])
 todo=list(range(base+len(net.records)-net.n_out,base+len(net.records)));live=set()
 while todo:
  wire=todo.pop()
  if wire<base or wire in live:continue
  live.add(wire);todo.extend(net.records[wire-base][1:])
 ids=[i for i in range(len(net.records)) if base+i in live]
 remap={i:i for i in range(base)};remap.update({base+i:base+j for j,i in enumerate(ids)})
 records=[tuple([net.records[i][0]]+[remap[x] for x in net.records[i][1:]]) for i in ids]
 reduced=Netlist(net.n_in,net.n_out,records);reduced.validate()
 kept=[i for i in range(ns) if base+i in live]
 assert reduced.n_state==len(kept)
 assert ids[-net.n_out:]==list(range(len(net.records)-net.n_out,len(net.records)))
 return reduced,kept


def structural_identity(before,after,kept):
 b=Builder(before.n_state+before.n_in);old=list(range(2,2+before.n_state));p=list(range(2+before.n_state,2+before.n_state+before.n_in))
 bd,bo=import_net(b,before,p,old);ad,ao=import_net(b,after,p,[old[i] for i in kept])
 left=[bd[i] for i in kept]+bo;right=ad+ao
 # Canonical NAND construction binds all retained D and public outputs to
 # identical expressions, with removed old state still arbitrary inputs.
 return b.finish(left),b.finish(right),left==right


def small():
 b=Builder(9);q=list(range(2,8));p=[8,9,10]
 d=[b.xor(q[1],p[0]),q[2],b.nand(q[0],p[1]),b.xor(q[4],p[2]),q[3],p[2]]
 net=with_state(b.finish(d+[q[0],b.inv(p[2])]),6);reduced,kept=prune(net);assert kept==[0,1,2]
 left,right,same=structural_identity(net,reduced,kept);assert same and left.encode()==right.encode()
 assert max(metrics(x)['nNand']+x.n_state for x in [net,reduced,left,right])<=4000
 xs=list(range(512));ys=[]
 for x in xs:
  q=[x>>i&1 for i in range(6)];p=[x>>(6+i)&1 for i in range(3)]
  d=[q[1]^p[0],q[2],1-(q[0]&p[1])];o=q[0]+((1-p[2])<<1)
  ys.append(sum(v<<j for j,v in enumerate(d))+(o<<3))
 checks=dict(before_projected=verify(left,xs,ys),after=verify(right,xs,ys))
 _,_,mutant_same=structural_identity(net,flip_output(reduced),kept);assert not mutant_same
 return dict(before=metrics(net),after=metrics(reduced),kept=kept,removed=[3,4,5],checks=checks,actual_output_mutation_breaks_identity=True)


