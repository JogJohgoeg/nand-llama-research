#!/usr/bin/env python3
"""cell8t_tb.py <lib> <corner> <vdd> <temp> <wpu> <wacc> <wpd> <wrd> [rows] [rbl_fF] > deck.spice  (tp-2ea.48.1)
Schematic-level sizing deck for the 8T 1R1W cell (sky130, nfet/pfet_01v8, L 0.15):
  storage Q/QB (pull-up wpu, pull-down wpd), write access WBL-Q / WBLB-QB (wacc, gate WWL),
  read stack RBL - RWL nfet - Q-gated nfet - GND (wrd): RBL falls iff Q=1 (same polarity as the ROM).
Tests in one transient: (1) write 1 into a cell holding 0, (2) read it (RBL precharged, extra load
rbl_fF), (3) the other rows-1 cells on that RBL all hold 1 with RWL low while a stored-0 cell is read
(worst leakage) -- measured on a second bitline RBL2, (4) write 0 back.
"""
import sys
lib,corner,vdd,temp,wpu,wacc,wpd,wrd=sys.argv[1:9];rows=int(sys.argv[9]) if len(sys.argv)>9 else 64
cl=sys.argv[10] if len(sys.argv)>10 else '10';v=float(vdd)
N='sky130_fd_pr__nfet_01v8';P='sky130_fd_pr__pfet_01v8'
o=['* 8T cell sizing','.lib "%s" %s'%(lib,corner),'.temp %s'%temp,'.param vd=%s'%vdd,
   '.subckt c8 wbl wblb wwl rbl rwl q qb vdd vss',
   'Xpu1 q qb vdd vdd %s w=%s l=0.15'%(P,wpu),'Xpd1 q qb vss vss %s w=%s l=0.15'%(N,wpd),
   'Xpu2 qb q vdd vdd %s w=%s l=0.15'%(P,wpu),'Xpd2 qb q vss vss %s w=%s l=0.15'%(N,wpd),
   'Xa1 wbl wwl q vss %s w=%s l=0.15'%(N,wacc),'Xa2 wblb wwl qb vss %s w=%s l=0.15'%(N,wacc),
   'Xr1 rbl rwl x vss %s w=%s l=0.15'%(N,wrd),'Xr2 x q vss vss %s w=%s l=0.15'%(N,wrd),'.ends',
   'Vdd vdd 0 {vd}','Vss vss 0 0']
# cell under test on RBL: write 1 (5-10 ns), read (precharge until 20 ns, RWL 21-41 ns), write 0 (45-50 ns)
o+=['Vwbl wbl 0 PWL(0 {vd} 44n {vd} 44.1n 0)','Vwblb wblb 0 PWL(0 0 44n 0 44.1n {vd})',
    'Vwwl wwl 0 PWL(0 0 5n 0 5.1n {vd} 10n {vd} 10.1n 0 45n 0 45.1n {vd} 50n {vd} 50.1n 0)',
    'Vrwl rwl 0 PWL(0 0 21n 0 21.1n {vd} 41n {vd} 41.1n 0)',
    'Vpre pre 0 PWL(0 0 20n 0 20.1n {vd})',
    'Xp rbl pre vdd vdd %s w=0.84 l=0.15'%P,'Crbl rbl 0 %sf'%cl,
    'Xc wbl wblb wwl rbl rwl q qb vdd vss c8','.ic v(q)=0 v(qb)={vd}']
# leakage column RBL2: selected cell holds 0, rows-1 unselected cells hold 1 (stack gate on, RWL off)
o+=['Xp2 rbl2 pre vdd vdd %s w=0.84 l=0.15'%P,'Crbl2 rbl2 0 %sf'%cl,
    'Xs vdd 0 0 rbl2 rwl s0 s0b vdd vss c8','.ic v(s0)=0 v(s0b)={vd}']
o+=['Xl%d vdd 0 0 rbl2 0 l%d lb%d vdd vss c8'%(k,k,k) for k in range(rows-1)]
o+=['.ic '+' '.join('v(l%d)={vd} v(lb%d)=0'%(k,k) for k in range(rows-1))]
h=v/2
o+=['.tran 0.01n 55n uic','.control','run',
    'meas tran t_w1 TRIG v(wwl) VAL=%g RISE=1 TARG v(q) VAL=%g RISE=1'%(h,h),
    'meas tran q_end1 FIND v(q) AT=19n',
    'meas tran t_rd TRIG v(rwl) VAL=%g RISE=1 TARG v(rbl) VAL=%g FALL=1'%(h,h),
    'meas tran rbl2_min MIN v(rbl2) FROM=21n TO=41n',
    'meas tran q_hold MIN v(q) FROM=21n TO=41n',
    'meas tran t_w0 TRIG v(wwl) VAL=%g RISE=2 TARG v(q) VAL=%g FALL=1'%(h,h),
    'meas tran q_end0 FIND v(q) AT=54n',
    'echo "RES %s %s %s"'%(corner,vdd,temp),'print t_w1 q_end1 t_rd rbl2_min q_hold t_w0 q_end0','.endc','.end']
print('\n'.join(o))
