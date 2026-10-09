#!/usr/bin/env python3
"""mktb.py <pex.spice> <bits.txt> <lib> <corner> <vdd> <temp> <cload_fF> <w> > tb.spice
One read of wordline w on the extracted array: every bitline starts precharged to VDD (initial condition)
with an extra load cload (wider array + route), WL[w] rises at 5 ns, the rest stay at 0. Measures the
fall time to VDD/2 of a bitline with bit 1 and the lowest voltage of a bitline with bit 0 over 100 ns."""
import sys
pex,bits,lib,corner,vdd,temp,cl,w=sys.argv[1:9];w=int(w);vdd=float(vdd)
rows=[l.strip() for l in open(bits) if l.strip()]
txt=open(pex).read().replace('\n+',' ')
sub=[l for l in txt.splitlines() if l.startswith('.subckt')][0].split();name,ports=sub[1],sub[2:]
r1=rows[w].index('1');r0=rows[w].index('0')
o=['* ROM read, wordline %d'%w,'.lib "%s" %s'%(lib,corner),'.include "%s"'%pex,'.temp %s'%temp,
   'X1 '+' '.join(p.replace('[','_').replace(']','') for p in ports)+' '+name]
for p in ports:
    n=p.replace('[','_').replace(']','')
    if p.startswith('BL'):o.append('C%s %s 0 %sf'%(n,n,cl));o.append('.ic v(%s)=%g'%(n,vdd))
    elif p.startswith('WL'):o.append('V%s %s 0 %s'%(n,n,'pulse(0 %g 5n 0.5n 0.5n 200n 400n)'%vdd if p=='WL[%d]'%w else '0'))
    elif p=='VGND':o.append('VGND VGND 0 0')
o+=['.tran 0.05n 105n uic','.control','run',
    'meas tran tfall when v(BL_%d)=%g fall=1'%(r1,vdd/2),
    'meas tran vmin0 min v(BL_%d) from=5n to=105n'%r0,
    'meas tran v1end find v(BL_%d) at=105n'%r1,
    'print tfall vmin0 v1end','.endc','.end']
print('\n'.join(o))
