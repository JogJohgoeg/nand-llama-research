#!/usr/bin/env python3
"""sram_char.py <pex.spice> <col> <row> <lib> <corner> <vdd> <temp> <rbl_load_fF> [hold_ns] > deck.spice  (tp-2ea.48.3)
Characterisation deck for the 8T array macro from the full-cap extraction (path-reduced, like romgen/reduce.py):
kept devices = every transistor on RBL[col] (64 read-access fets + the precharge pfet), the read-stack fets
below them, every fet gated by RWL[row], WWL[row] or PRE_N (gate loads; their other terminals not kept go to 0),
every fet touching WBL[col] / WBLB[col] (junction loads). Storage nodes are pinned by sources: the read-stack
gate of the selected cell (row, col) = VQS, those of the other 63 cells of the column = VDD (worst leakage).
Other nets are tied to ground; capacitors to them become capacitors to ground.
  phase A: PRE_N low until 5 ns, RWL[row] rises at 10 ns with VQS = VDD -> d_read (RWL 50% -> RBL 50% fall), s_fall
  phase B: RWL falls at 40 ns, PRE_N falls at 45 ns -> d_pre (PRE_N 50% -> RBL 50% rise), s_rise
  phase C: PRE_N high at 60 ns, VQS = 0, RWL[row] high from 65 ns for hold_ns -> rbl_min (read-0 droop)
  charges: RWL, PRE_N, WWL (pulse at 2 ns) and WBL (step at 1 ns) sources -> input capacitances."""
import sys
pex,col,row,lib,corner,vdd,temp,cl=sys.argv[1:9];col,row=int(col),int(row);v=float(vdd)
hold=float(sys.argv[9]) if len(sys.argv)>9 else 100.0
txt=open(pex).read().replace('\n+',' ').splitlines()
sub=[l for l in txt if l.lower().startswith('.subckt')][0].split();name,ports=sub[1],sub[2:]
RBL,RWL,WWL,WBL,WBLB='RBL[%d]'%col,'RWL[%d]'%row,'WWL[%d]'%row,'WBL[%d]'%col,'WBLB[%d]'%col
dev=[l.split() for l in txt if l[:1] and l[0] in 'Xx' and not l.lower().startswith('.')]
sd=lambda p:(p[1],p[3])
acc=[p for p in dev if RBL in sd(p)]
xs={t for p in acc for t in sd(p) if t not in (RBL,'VPWR','VGND')}
stack=[p for p in dev if p not in acc and set(sd(p))&xs]
sel=[p for p in acc if p[2]==RWL];assert len(sel)==1,len(sel)
xsel=[t for t in sd(sel[0]) if t!=RBL][0]
qnets={p[2] for p in stack};qsel=[p[2] for p in stack if xsel in sd(p)];assert len(qsel)==1
qsel=qsel[0];qoth=qnets-{qsel}
loads=[p for p in dev if p not in acc and p not in stack and (p[2] in (RWL,WWL,'PRE_N') or WBL in sd(p) or WBLB in sd(p))]
keep={RBL,RWL,WWL,WBL,WBLB,'PRE_N','VPWR','VGND'}|xs|qnets
m=lambda x:x if x in keep else '0'
out=['* sram8t characterisation col %d row %d (%d access, %d stack, %d load fets)'%(col,row,len(acc),len(stack),len(loads)),
     '.lib "%s" %s'%(lib,corner),'.temp %s'%temp,'.option method=gear']
for p in acc+stack+loads:out.append(' '.join([p[0]]+[m(x) for x in p[1:5]]+p[5:]))
unit={'f':1e-15,'p':1e-12,'a':1e-18,'n':1e-9}
cap={}
for l in txt:
    if not l or l[0] not in 'Cc':continue
    p=l.split();a,b=m(p[1]),m(p[2])
    if a==b:continue
    s=p[3];val=float(s[:-1])*unit[s[-1]] if s[-1] in unit else float(s)
    k=tuple(sorted((a,b)));cap[k]=cap.get(k,0)+val
out+=['CR%d %s %s %.6gf'%(n,k[0],k[1],c*1e15) for n,(k,c) in enumerate(sorted(cap.items()))]
nn=lambda s:s.replace('[','_').replace(']','')
out=[l if l.startswith('*') or l.startswith('.') else ' '.join(nn(t) for t in l.split()) for l in out]
r1,rw,ww,wb,wbb=nn(RBL),nn(RWL),nn(WWL),nn(WBL),nn(WBLB)
t2=65+hold
out+=['VPWR VPWR 0 %g'%v,'VGND VGND 0 0','CRL %s 0 %sf'%(r1,cl),'.ic v(%s)=%g'%(r1,v),
      'VPRE PRE_N 0 pwl(0 0 5n 0 5.2n %g 45n %g 45.2n 0 60n 0 60.2n %g)'%(v,v,v),
      'VRWL %s 0 pwl(0 0 10n 0 10.2n %g 40n %g 40.2n 0 65n 0 65.2n %g %gn %g %gn 0)'%(rw,v,v,v,t2,v,t2+0.2),
      'VWWL %s 0 pwl(0 0 2n 0 2.2n %g 4n %g 4.2n 0)'%(ww,v,v),
      'VWBL %s 0 pwl(0 0 1n 0 1.2n %g)'%(wb,v),'VWBLB %s 0 0'%wbb,
      'VQS %s 0 pwl(0 %g 50n %g 50.2n 0)'%(nn(qsel),v,v)]
out+=['VQ%d %s 0 %g'%(k,nn(q),v) for k,q in enumerate(sorted(qoth))]
h=v/2
out+=['.tran 0.01n %gn uic'%(t2+5),'.control','run',
      'meas tran d_read trig v(%s) val=%g rise=1 targ v(%s) val=%g fall=1'%(rw,h,r1,h),
      'meas tran s_fall trig v(%s) val=%g fall=1 targ v(%s) val=%g fall=1'%(r1,0.8*v,r1,0.2*v),
      'meas tran d_pre trig v(PRE_N) val=%g fall=1 targ v(%s) val=%g rise=1'%(h,r1,h),
      'meas tran s_rise trig v(%s) val=%g rise=1 targ v(%s) val=%g rise=1'%(r1,0.2*v,r1,0.8*v),
      'meas tran rbl_min min v(%s) from=66n to=%gn'%(r1,t2),
      'meas tran q_rwl integ i(VRWL) from=9.9n to=14n','meas tran q_pre integ i(VPRE) from=44.9n to=50n',
      'meas tran q_wwl integ i(VWWL) from=1.9n to=3.5n','meas tran q_wbl integ i(VWBL) from=0.9n to=1.9n',
      'echo "RES %s %s %s cl=%s"'%(corner,vdd,temp,cl),'print d_read s_fall d_pre s_rise rbl_min q_rwl q_pre q_wwl q_wbl','.endc','.end']
print('\n'.join(out))
