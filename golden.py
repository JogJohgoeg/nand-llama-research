"""TapeOut Boolean transition contract. No imports of the existing evaluator.
NAND records are7 bytes; LATCH records are4 bytes. IDs are unsigned24-bit BE.
Only canonical binary inputs/state and validated DAG netlists are in hardware ISA v1.
"""
from dataclasses import dataclass
@dataclass
class Netlist:
 n_in:int
 n_out:int
 records:list
 @classmethod
 def decode(cls,raw,n_in,n_out):
  records=[];i=0
  while i<len(raw):
   op=raw[i];size=7 if op==0 else 4 if op==1 else 0
   if not size or i+size>len(raw):raise ValueError('opcode/truncated record')
   args=[int.from_bytes(raw[j:j+3],'big') for j in range(i+1,i+size,3)];records.append((op,*args));i+=size
  result=cls(n_in,n_out,records);result.validate();return result
 @property
 def n_state(self):return sum(g[0]==1 for g in self.records)
 def validate(self):
  total=2+self.n_in+len(self.records)
  if not 0<=self.n_in<2**24 or not 0<=self.n_out<=len(self.records) or total>2**24:raise ValueError('counts')
  for i,g in enumerate(self.records):
   op=g[0]
   if op not in (0,1) or len(g)!=(3 if op==0 else 2):raise ValueError('record')
   if any(not 0<=w<(2+self.n_in+i if op==0 else total) for w in g[1:]):raise ValueError('wire reference')
 def encode(self):return b''.join(bytes([g[0]])+b''.join(w.to_bytes(3,'big') for w in g[1:]) for g in self.records)
 def step(self,state,inputs):
  inputs=bytes(inputs);state=bytes(state) if state else bytes(self.n_state)
  if len(inputs)!=self.n_in or len(state)!=self.n_state or any(b>1 for b in inputs+state):raise ValueError('binary input/state')
  values=[0,1]+list(inputs);latches=[]
  for g in self.records:
   if g[0]==0:values.append(int(not (values[g[1]] and values[g[2]])))
   else:values.append(state[len(latches)]);latches.append(g[1])
  return bytes(values[d] for d in latches),bytes(values[len(values)-self.n_out:]) if self.n_out else b''
 def step_simd(self,states,inputs):
  """One bit lane per independent transition, NOT one lane per dependent gate."""
  n=len(inputs);mask=(1<<n)-1
  if len(states)!=n:raise ValueError('lanes')
  normalized=[bytes(s) if s else bytes(self.n_state) for s in states]
  for s,x in zip(normalized,inputs):
   if len(s)!=self.n_state or len(x)!=self.n_in or any(b>1 for b in bytes(s)+bytes(x)):raise ValueError('binary lanes')
  v=[0,mask]+[sum(x[j]<<i for i,x in enumerate(inputs)) for j in range(self.n_in)];ds=[]
  for g in self.records:
   if g[0]==0:v.append(mask^(v[g[1]]&v[g[2]]))
   else:v.append(sum(s[len(ds)]<<i for i,s in enumerate(normalized)));ds.append(g[1])
  outs=v[-self.n_out:] if self.n_out else []
  return [(bytes(v[d]>>i&1 for d in ds),bytes(w>>i&1 for w in outs)) for i in range(n)]
