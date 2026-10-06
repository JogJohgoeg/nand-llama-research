#!/usr/bin/env python3
"""Continuous21x128 FF bank with atomic64-bit writes and captured reads.

Same command bit positions as R55, but no idle hold or byte-write protocol.
HOME acknowledges passing row0; it does not stop the circulating bank.
"""
from pathlib import Path
import os,sys,random,json,signal,hashlib
R=Path(os.environ.get('H3_FF_CONTINUOUS_ROOT',str(Path(__file__).resolve().parents[1])))
sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
from nand import Builder,metrics,with_state,verify_state,flip_output
from gate_check import Snapshot
from export import rtl
sha=lambda raw:hashlib.sha256(raw).hexdigest()


def make(rows=21):
 a=(rows-1).bit_length();aw=(2*rows-1).bit_length();nc=aw+4;ns=rows*128+64+a+nc;ni=aw+70
 b=Builder(ns+ni);old=list(range(2,2+ns));pins=list(range(2+ns,2+ns+ni))
 memory=old[:rows*128];payload=old[rows*128:rows*128+64];cursor=old[rows*128+64:rows*128+64+a];cs=old[-nc:]
 phase=cs[:2];address=cs[2:2+aw];mode=cs[-2:]
 reset,start=pins[:2];asked=pins[2:2+aw];command=pins[2+aw:4+aw];data=pins[4+aw:68+aw];enable,read=pins[-2:]
 def eq(bits,n):return b.reduce([v if n>>j&1 else b.inv(v) for j,v in enumerate(bits)],b.land,1)
 def AND(*xs):return b.reduce(xs,b.land,1)
 keep=b.inv(reset);p=[eq(phase,j) for j in range(4)];home=eq(mode,2);writing=eq(mode,1)
 _,invalid=b.add(asked,[((1<<aw)-2*rows)>>j&1 for j in range(aw)])
 begin=AND(keep,start,b.lor(p[0],p[3]),b.lor(eq(command,2),AND(b.inv(command[1]),b.inv(invalid))))
 target=[AND(b.inv(home),v) for v in address[1:]]
 match=b.reduce([b.inv(b.xor(x,y)) for x,y in zip(cursor,target)],b.land,1)
 accept=AND(keep,p[1],match,b.lor(b.inv(writing),enable))
 store=AND(accept,writing);capture=AND(accept,b.inv(b.lor(home,writing)))
 available=AND(keep,p[2],b.inv(writing));consume=AND(available,read)
 ph=phase[:]
 for event,value in [(begin,[1,0]),(accept,[b.lor(home,writing),1]),(consume,[1,1])]:
  ph=[b.mux(event,x,y) for x,y in zip(ph,value)]
 cd=[AND(keep,v) for v in ph]
 cd += [AND(keep,b.mux(begin,x,y)) for x,y in zip(address,asked)]
 cd += [AND(keep,b.mux(begin,x,y)) for x,y in zip(mode,command)]
 source=[b.mux(address[0],x,y) for x,y in zip(memory[:64],memory[64:128])]
 load=AND(begin,eq(command,1));pd=[b.mux(load,x,y) for x,y in zip(payload,data)]
 pd=[b.mux(capture,x,y) for x,y in zip(pd,source)]
 tail=[]
 for half in range(2):
  we=AND(store,address[0] if half else b.inv(address[0]))
  tail += [b.mux(we,x,y) for x,y in zip(memory[half*64:(half+1)*64],payload)]
 md=memory[128:]+tail
 increment=b.add(cursor,[0]*a,1)[0];cd_cursor=[AND(keep,b.inv(eq(cursor,rows-1)),v) for v in increment]
 busy=AND(keep,b.lor(p[1],p[2]));done=AND(keep,p[3],b.inv(begin))
 comb=b.finish(md+pd+cd_cursor+cd+payload+[available,busy,done]);net=with_state(comb,ns)
 return net,comb,dict(rows=rows,values=2*rows,bank_bits=rows*128,payload_bits=64,cursor_bits=a,parent_bits=nc,
  state_order='continuous FF words,captured read/write64,cursor,phase/address/mode',
  protocol='write64 commits atomically on a matching row and enable; read snapshots64, then holds until read; HOME waits to visit row0, does not stop or home an idle bank')


class Model:
 def __init__(self,rows):
  self.rows=rows;self.a=(rows-1).bit_length();self.aw=(2*rows-1).bit_length()
  self.memory=[0]*rows;self.known=[0]*rows;self.payload=self.cursor=self.phase=self.address=self.mode=0
  self.index=0
  self.counts=dict(start=0,complete=0,abort=0,read64=0,write64=0,home=0,rotations=0,write_stalls=0,read_stalls=0,busy_starts=0,invalid_starts=0)
 def tick(self,x):
  aw=self.aw;reset=x&1;start=x>>1&1;address=x>>2&((1<<aw)-1);mode=x>>(2+aw)&3
  data=x>>(4+aw)&((1<<64)-1);enable=x>>(68+aw)&1;read=x>>(69+aw)&1;p=self.phase
  legal=mode==2 or mode<2 and address<2*self.rows;begin=not reset and start and p in (0,3) and legal
  target=0 if self.mode==2 else self.address//2;match=self.cursor==target
  accept=not reset and p==1 and match and (self.mode!=1 or enable)
  available=int(not reset and p==2 and self.mode!=1)
  out=self.payload+(available<<64)+(int(not reset and p in (1,2))<<65)+(int(not reset and p==3 and not begin)<<66)
  mask=(1<<67)-1 if available else 7<<64
  c=self.counts;c['busy_starts']+=int(not reset and start and p in (1,2));c['invalid_starts']+=int(not reset and start and p in (0,3) and not legal)
  c['read_stalls']+=int(available and not read);c['write_stalls']+=int(not reset and p==1 and self.mode==1 and not enable)
  tail=self.memory[0];known=self.known[0]
  if reset:
   c['abort']+=int(p in (1,2));self.phase=self.address=self.mode=0
  elif begin:
   self.phase=1;self.address=address;self.mode=mode;c['start']+=1
   if mode==1:self.payload=data
  elif accept:
   if self.mode==1:
    shift=64*(self.address&1);tail=(tail&~(((1<<64)-1)<<shift))+(self.payload<<shift);known|=1<<(self.address&1)
    self.phase=3;c['write64']+=1;c['complete']+=1
   elif self.mode==2:self.phase=3;c['home']+=1;c['complete']+=1
   else:
    assert known>>(self.address&1)&1
    self.payload=tail>>(64*(self.address&1))&((1<<64)-1);self.phase=2
  elif available and read:self.phase=3;c['read64']+=1;c['complete']+=1
  self.memory=self.memory[1:]+[tail];self.known=self.known[1:]+[known]
  self.cursor=0 if reset or self.cursor==self.rows-1 else (self.cursor+1)&((1<<self.a)-1)
  c['rotations']+=1
  if reset:self.known=[0]*self.rows
  return out,mask


def small_check():
 rows=3;net,comb,_=make(rows);rng=random.Random(260730);xs=[];ys=[];ns=net.n_state
 def state(m):
  v=sum(x<<(128*j) for j,x in enumerate(m.memory));shift=128*m.rows
  v+=m.payload<<shift;shift+=64;v+=m.cursor<<shift;shift+=m.a
  return v+((m.phase+(m.address<<2)+(m.mode<<(2+m.aw)))<<shift)
 assert metrics(net)['nNand']+ns<=4000
 for i in range(1024):
  m=Model(rows);m.memory=[rng.getrandbits(128) for _ in range(rows)];m.known=[3]*rows
  m.payload=rng.getrandbits(64);m.cursor=rng.randrange(1<<m.a);m.phase=i&3;m.mode=i>>2&3;m.address=rng.randrange(1<<m.aw)
  x=rng.getrandbits(net.n_in);old=state(m);y,_=m.tick(x);xs.append(old+(x<<ns));ys.append(state(m)+(y<<ns))
 proof=verify_state(comb,xs,ys,ns);proof.pop('nl_hex')
 m=Model(rows);stim=[];logical=[None]*(2*rows)
 def tick(reset=0,start=0,mode=0,address=0,data=0,enable=0,read=0):
  x=reset+(start<<1)+(address<<2)+(mode<<(2+m.aw))+(data<<(4+m.aw))+(enable<<(68+m.aw))+(read<<(69+m.aw))
  y,mask=m.tick(x);stim.append((x,y,mask));return y
 def command(mode,address,data=0):
  assert m.phase in (0,3)
  tick(start=1,mode=3);tick(start=1,mode=mode,address=address,data=data);value=None
  for _ in range(100):
   if m.phase==3:break
   p=m.phase;take=int(rng.randrange(3)!=0)
   y=tick(start=1,mode=rng.randrange(4),address=rng.randrange(1<<m.aw),data=rng.getrandbits(64),enable=take,read=take)
   if p==2 and take:value=y&((1<<64)-1)
  assert m.phase==3
  if mode==1:logical[address]=data
  if mode==0:assert value==logical[address]
 def fill():
  for i in range(2*rows):command(1,i,rng.getrandbits(64))
 tick(reset=1);fill()
 for _ in range(5):
  for i in rng.sample(list(range(2*rows)),2*rows):
   command(0,i);command(1,i,rng.getrandbits(64));command(0,i)
   for _ in range(rng.randrange(5)):tick()
  command(2,0)
 for mode in (0,1):
  tick(start=1,mode=mode,address=5,data=0);tick(reset=1,start=1,enable=1,read=1);fill()
  for i in range(2*rows):command(0,i)
 def simulate(graph):
  g=Snapshot.decode(graph.encode(),graph.n_in,graph.n_out);state=bytes(ns);wrong=0
  for x,y,mask in stim:
   state,out=g.step(state,bytes(x>>j&1 for j in range(net.n_in)));wrong+=int(bool((sum(v<<j for j,v in enumerate(out))^y)&mask))
  return wrong
 assert simulate(net)==0;wrong=simulate(flip_output(net));assert wrong
 return dict(metrics=metrics(net),arbitrary_state=proof,clocks=len(stim),counts=m.counts,actual_data_gate_mismatches=wrong)


def reference(rows=21):
 a=(rows-1).bit_length();aw=(2*rows-1).bit_length();nc=aw+4;s=rows*128;ns=s+64+a+nc;ni=aw+70
 return f'''module top(input [{ns+ni-1}:0] din,output [{ns+66}:0] dout);
wire [{s-1}:0] memory=din[{s-1}:0];
wire [63:0] payload=din[{s+63}:{s}];
wire [{a-1}:0] cursor=din[{s+64+a-1}:{s+64}];
wire [1:0] phase=din[{ns-nc+1}:{ns-nc}];
wire [{aw-1}:0] address=din[{ns-3}:{ns-aw-2}];
wire [1:0] mode=din[{ns-1}:{ns-2}];
wire [{ni-1}:0] pins=din[{ns+ni-1}:{ns}];
wire reset=pins[0],start=pins[1],enable=pins[{ni-2}],read=pins[{ni-1}];
wire [{aw-1}:0] wanted=pins[{aw+1}:2];wire [1:0] command=pins[{aw+3}:{aw+2}];
wire [63:0] data=pins[{aw+67}:{aw+4}];
wire legal=command==2 || (command<2 && wanted<{2*rows});
wire begin_command=!reset && start && (phase==0 || phase==3) && legal;
wire [{a-1}:0] target=mode==2 ? {a}'d0 : address[{aw-1}:1];
wire accept=!reset && phase==1 && cursor==target && (mode!=1 || enable);
wire store=accept && mode==1,capture=accept && mode!=1 && mode!=2;
wire available=!reset && phase==2 && mode!=1;
reg [1:0] next_phase;reg [{aw-1}:0] next_address;reg [1:0] next_mode;
always @* begin
 next_phase=phase;next_address=address;next_mode=mode;
 if(reset)begin next_phase=0;next_address=0;next_mode=0;end
 else if(begin_command)begin next_phase=1;next_address=wanted;next_mode=command;end
 else if(accept)next_phase=(mode==1 || mode==2)?3:2;
 else if(available && read)next_phase=3;
end
reg [127:0] tail;wire [6:0] offset={{address[0],6'd0}};
always @* begin
 tail=memory[127:0];
 if(store)tail[offset +: 64]=payload;
end
assign dout[{s-1}:0]={{tail,memory[{s-1}:128]}};
assign dout[{s+63}:{s}]=begin_command && command==1?data:capture?(address[0]?memory[127:64]:memory[63:0]):payload;
assign dout[{s+64+a-1}:{s+64}]=reset || cursor=={a}'d{rows-1}?{a}'d0:cursor+{a}'d1;
assign dout[{ns-1}:{ns-nc}]={{next_mode,next_address,next_phase}};
assign dout[{ns+63}:{ns}]=payload;
assign dout[{ns+64}]=available;
assign dout[{ns+65}]=!reset && (phase==1 || phase==2);
assign dout[{ns+66}]=!reset && phase==3 && !begin_command;
endmodule
'''


if __name__=='__main__':
 signal.alarm(55);print(json.dumps(small_check(),indent=2));print(metrics(make()[0]))
