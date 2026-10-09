#!/usr/bin/env python3
"""chartb.py <pex.spice> <bits.txt> <lib> <corner> <vdd> <temp> <w> > deck.spice
Characterisation deck for the array Liberty (ngspice, extracted netlist with all caps):
  phase A (read):      bitlines start at VDD, PRE_N = VDD, WL[w] rises at 5 ns (0.2 ns slew):
                       delay WL[w] 50% -> BL[r1] 50% (fall), BL fall slew 80->20%;
                       charge drawn by the WL[w] source over the edge -> WL input capacitance
  phase B (precharge): WL[w] falls at 40 ns, PRE_N falls at 45 ns: delay PRE_N 50% -> BL[r1] 50% (rise),
                       BL rise slew 20->80%; charge drawn by the PRE_N source -> PRE_N capacitance
Each bitline carries 2 fF (the latch input it drives). r1 is the first bitline with bit 1 on word w."""
import sys
pex,bits,lib,corner,vdd,temp,w=sys.argv[1:8];w=int(w);v=float(vdd)
rows=[l.strip() for l in open(bits) if l.strip()]
txt=open(pex).read().replace('\n+',' ')
sub=[l for l in txt.splitlines() if l.startswith('.subckt')][0].split();name,ports=sub[1],sub[2:]
r1=rows[w].index('1')
n=lambda p:p.replace('[','_').replace(']','')
o=['* array characterisation, word %d bitline %d'%(w,r1),'.lib "%s" %s'%(lib,corner),'.include "%s"'%pex,'.temp %s'%temp,
   '.option method=gear','X1 '+' '.join(n(p) for p in ports)+' '+name]
for p in ports:
    if p.startswith('BL'):o+=['C%s %s 0 2f'%(n(p),n(p)),'.ic v(%s)=%g'%(n(p),v)]
    elif p=='WL[%d]'%w:o.append('VWLX %s 0 pwl(0 0 5n 0 5.2n %g 40n %g 40.2n 0)'%(n(p),v,v))
    elif p.startswith('WL'):o.append('V%s %s 0 0'%(n(p),n(p)))
    elif p=='PRE_N':o.append('VPRE PRE_N 0 pwl(0 %g 45n %g 45.2n 0)'%(v,v))
    elif p=='VPWR':o.append('VPWR VPWR 0 %g'%v)
    elif p=='VGND':o.append('VGND VGND 0 0')
b='v(%s)'%n('BL[%d]'%r1);h=v/2
o+=['.tran 0.01n 60n uic','.control','run',
    'meas tran d_read trig v(%s) val=%g rise=1 targ %s val=%g fall=1'%(n('WL[%d]'%w),h,b,h),
    'meas tran s_fall trig %s val=%g fall=1 targ %s val=%g fall=1'%(b,0.8*v,b,0.2*v),
    'meas tran q_wl integ i(VWLX) from=4.9n to=8n',
    'meas tran d_pre trig v(PRE_N) val=%g fall=1 targ %s val=%g rise=1'%(h,b,h),
    'meas tran s_rise trig %s val=%g rise=1 targ %s val=%g rise=1'%(b,0.2*v,b,0.8*v),
    'meas tran q_pre integ i(VPRE) from=44.9n to=55n',
    'print d_read s_fall q_wl d_pre s_rise q_pre','.endc','.end']
print('\n'.join(o))
