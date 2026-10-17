# sramgen_gf180.py -- 8T (1R1W) static RAM array, GF180MCU 3.3 V devices (tp-2ea.48.5), same topology as
# sramgen_sg13g2.py; rules taken from romgen_gf180.py (which passes the GF180 DRC/LVS).
# run: klayout -b -r sramgen_gf180.py -rd rows=<R> -rd cols=<C> -rd out=<prefix> [-rd name=<cell>] [-rd tapk=4]
# writes <prefix>.gds, <prefix>.spice (schematic for LVS) and <prefix>.lef.
#
# All transistors nfet_03v3 / pfet_03v3 W 0.36 L 0.28 (precharge pfet W 0.72). Source/drain lengths: 0.44 at a
# strip end (0.07 COMP enclosure + 0.22 contact + 0.15 to gate), 0.52 between two gates with a contact, 0.36
# between two gates without one. Explicit Nplus / Pplus implants. Word lines on Metal2 (horizontal tracks below
# the nfets), bit lines on Metal3 (vertical), Q jumper g -> r2 on Metal2, rails VGND (y=0) / VPWR (y=CH) on Metal1,
# odd rows mirrored. Cell (x, nm):
#   ndiff : c0 WBL 0..440 | g0 a1 (WWL) | c1 Q | g1 pd1 (QB) | c2 VGND | g2 pd2 (Q) | c3 QB | g3 a2 (WWL) | c4 WBLB ..3560
#   pdiff :                        c1 Q | g1 pu1 (QB) | c2 VPWR | g2 pu2 (Q) | c3 QB            800..2760
#   read  : R0 VGND 3840 | r2 (Q) | X | r1 (RWL) | R1 RBL ..5640        -> RBL discharges iff Q=1
import pya
L={'comp':(22,0),'poly':(30,0),'cont':(33,0),'m1':(34,0),'m2':(36,0),'m3':(42,0),'m4':(46,0),
   'via1':(35,0),'via2':(38,0),'via3':(40,0),'nplus':(32,0),'pplus':(31,0),'nwell':(21,0),'bnd':(0,0),
   'm1lbl':(34,10),'m2lbl':(36,10),'m3lbl':(42,10),'m4lbl':(46,10)}
R=int(rows);C=int(cols);TK=int(globals().get('tapk','4'))
CW,CH,TW=5920,4520,1600
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell(globals().get('name','sram8t_gf_%dx%d'%(R,C)))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(min(x0,x1),min(y0,y1),max(x0,x1),max(y0,y1)))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
PINS={}
def addpin(name,lay,q):PINS.setdefault(name,[]).append((lay,q))
YW,YR=640,1200                                 # WWL / RWL Metal2 track centres (and their gate pads)
YN,YP=1880,3880                                # ndiff+read / pdiff strip centres (W 0.36: +-180)
YA,YB=2600,3240                                # QB Metal1 track (g1 pad) / Q Metal1 track (g2 pad, r2 pad, Q jumper)
NW0=2700                                       # NWell bottom edge in a row (>= 0.43 from ndiff, <= pdiff - 0.43)
GX={'g0':440,'g1':1240,'g2':2040,'g3':2840,'r2':4280,'r1':4920}         # gate left edges (L 0.28)
CX={'c0':70,'c1':870,'c2':1670,'c3':2470,'c4':3270,'R0':3910,'R1':5350}  # contact left edges (0.22)
ND=(0,3560);RD=(3840,5640);PD=(800,2760)
sp=[]
def cell(ox,r,c):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    def b(k,x0,ya,x1,yb):box(k,ox+x0,f(ya),ox+x1,f(yb))
    for a,z in (ND,RD):b('comp',a,YN-180,z,YN+180)
    b('comp',PD[0],YP-180,PD[1],YP+180)
    b('nplus',ND[0]-230,YN-410,RD[1]+230,YN+410)
    def pad(px,py):                              # 0.36 poly pad with a contact and a 0.40 Metal1 pad
        b('poly',px-180,py-180,px+180,py+180);b('cont',px-110,py-110,px+110,py+110);b('m1',px-200,py-200,px+200,py+200)
    g=lambda n:GX[n]
    # access gates (WWL): pad below on the WWL track; read gate r1 (RWL): pad on the RWL track
    for n in ('g0','g3'):b('poly',g(n),YW,g(n)+280,YN+180+220);pad(g(n)+140,YW)
    b('poly',g('r1'),YR,g('r1')+280,YN+180+220);pad(g('r1')+140,YR)
    # inverter gates across both strips; g1 pad right of the gate on the QB track, g2 pad left of it on the Q track
    for n in ('g1','g2'):b('poly',g(n),YN-180-220,g(n)+280,YP+180+220)
    pad(1580,YA);pad(1980,YB)
    # read gate r2 (= Q): from the read strip up to its pad on the Q track
    b('poly',g('r2'),YN-180-220,g('r2')+280,YB);pad(g('r2')+140,YB)
    def dc(n,yc):b('cont',CX[n],yc-110,CX[n]+220,yc+110)
    for n in ('c0','c1','c2','c3','c4','R0','R1'):dc(n,YN)
    for n in ('c1','c2','c3'):dc(n,YP)
    # Metal1: Q strip (c1, ndiff to pdiff) + Q track to the g2 pad; QB strip (c3) + QB track from the g1 pad
    b('m1',CX['c1']-30,YN-170,CX['c1']+250,YP+170);b('m1',CX['c1']-30,YB-140,1980,YB+140)
    b('m1',CX['c3']-30,YN-170,CX['c3']+250,YP+170);b('m1',1580,YA-140,CX['c3']+250,YA+140)
    # VGND: c2 and R0 down to the rail; VPWR: c2 (pdiff) up to the rail
    for n in ('c2','R0'):b('m1',CX[n]-30,0,CX[n]+250,YN+170)
    b('m1',CX['c2']-30,YP-170,CX['c2']+250,CH)
    # Q jumper on Metal2: via1 on the Q track at x 1300 -> r2 pad
    for vx in (1300,g('r2')+140):b('via1',vx-130,YB-130,vx+130,YB+130)
    b('m2',1300-200,YB-200,g('r2')+140+200,YB+200)
    # word-line pads: via1 onto the Metal2 tracks (tracks drawn at row level)
    for px,py in ((g('g0')+140,YW),(g('g3')+140,YW),(g('r1')+140,YR)):b('via1',px-130,py-130,px+130,py+130)
    # bit-line contacts: Metal1 pad, via1, Metal2 pad, via2 onto the Metal3 line
    for n in ('c0','c4','R1'):
        cx=CX[n]+110;b('m1',cx-200,YN-200,cx+200,YN+200);b('via1',cx-130,YN-130,cx+130,YN+130)
        b('m2',cx-140,YN-190,cx+140,YN+410);b('via2',cx-130,YN-130,cx+130,YN+130)   # 0.28 wide (M2.2a to the next cell), ends 0.06
    t='r%dc%d'%(r,c);Q,QB,X='q_'+t,'qb_'+t,'x_'+t
    wbl,wblb,rbl,wwl,rwl='WBL[%d]'%c,'WBLB[%d]'%c,'RBL[%d]'%c,'WWL[%d]'%r,'RWL[%d]'%r
    N,P='nfet_03v3','pfet_03v3';w='W=0.36u L=0.28u'
    sp.extend(['Ma1_%s %s %s %s VGND %s %s'%(t,wbl,wwl,Q,N,w),'Mpd1_%s %s %s VGND VGND %s %s'%(t,Q,QB,N,w),
               'Mpd2_%s VGND %s %s VGND %s %s'%(t,Q,QB,N,w),'Ma2_%s %s %s %s VGND %s %s'%(t,QB,wwl,wblb,N,w),
               'Mpu1_%s %s %s VPWR VPWR %s %s'%(t,Q,QB,P,w),'Mpu2_%s VPWR %s %s VPWR %s %s'%(t,Q,QB,P,w),
               'Mr2_%s VGND %s %s VGND %s %s'%(t,Q,X,N,w),'Mr1_%s %s %s %s VGND %s %s'%(t,X,rwl,rbl,N,w)])
def tapcol(tx,r):                                # p-tap at the ndiff height, n-tap (in the NWell) at the pdiff height
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    def b(k,x0,ya,x1,yb):box(k,tx+x0,f(ya),tx+x1,f(yb))
    b('comp',500,YN-180,1100,YN+180);b('pplus',270,YN-410,1330,YN+410)    # 0.60 x 0.36 (DF.9 min COMP area)
    b('comp',500,YP-180,1100,YP+180);b('nplus',270,YP-410,1330,YP+410)
    b('cont',690,YN-110,910,YN+110);b('cont',690,YP-110,910,YP+110)
    b('m1',660,0,940,YN+170);b('m1',660,YP-170,940,CH)
xs=[];taps=[];x=0
for c in range(C):
    if c%TK==0:taps.append(x);x+=TW
    xs.append(x);x+=CW
taps.append(x);XE=x+TW
for r in range(R):
    for c in range(C):cell(xs[c],r,c)
    for tx in taps:tapcol(tx,r)
    if r%2==0:                                   # Pplus over the pdiff of the row pair (merged), per cell
        y0=r*CH
        for c in range(C):box('pplus',xs[c]+PD[0]-230,y0+YP-410,xs[c]+PD[1]+230,y0+2*CH-YP+410)
for r in range(R):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    for yc,net in ((YW,'WWL[%d]'%r),(YR,'RWL[%d]'%r)):
        q=pya.Box(-300,f(yc)-140,XE+300,f(yc)+140);box('m2',q.left,q.bottom,q.right,q.top)
        label('m2lbl',XE+150,f(yc),net);addpin(net,'Metal2',pya.Box(XE,q.bottom,XE+300,q.top))
for k in range(R+1):
    net='VGND' if k%2==0 else 'VPWR';yc=k*CH
    box('m1',-300,yc-200,XE+300,yc+200);label('m1lbl',-150,yc,net)
for r in range(0,R,2):
    yb=r*CH+NW0;yt=(r+2)*CH-NW0 if r+1<R else r*CH+YP+180+430
    box('nwell',-300,yb,XE+300,yt)
for c in range(C):
    for n,net in (('c0','WBL'),('c4','WBLB'),('R1','RBL')):
        cx=xs[c]+CX[n]+110
        box('m3',cx-180,-300 if n!='R1' else -1100,cx+180,R*CH+300)
        label('m3lbl',cx,R*CH+150,'%s[%d]'%(net,c));addpin('%s[%d]'%(net,c),'Metal3',pya.Box(cx-180,R*CH,cx+180,R*CH+300))
# ---- RBL precharge row below row 0: one vertical pfet (W 0.72) per column, PRE_N poly line (L 0.28) across;
# drain -> Metal3 RBL, source -> VPWR Metal1 rail; n-taps in the tap columns under the rail
YD,YG,YS,YV0=-900,-1300,-1700,-2500
for c in range(C):
    cx=xs[c]+CX['R1']+110
    box('comp',cx-360,YS-180,cx+360,YD+180);box('pplus',cx-590,YS-410,cx+590,YD+410)
    for yc in (YD,YS):box('cont',cx-110,yc-110,cx+110,yc+110)
    box('m1',cx-200,YD-200,cx+200,YD+200);box('via1',cx-130,YD-130,cx+130,YD+130)
    box('m2',cx-200,YD-200,cx+200,YD+200);box('via2',cx-130,YD-130,cx+130,YD+130)
    box('m1',cx-140,YV0,cx+140,YS+170)
    sp.append('Mpc%d RBL[%d] PRE_N VPWR VPWR pfet_03v3 W=0.72u L=0.28u'%(c,c))
box('poly',-1200,YG-140,XE+1200,YG+140)
for px in (-1000,XE+1000):
    box('poly',px-180,YG-180,px+180,YG+180);box('cont',px-110,YG-110,px+110,YG+110)
    box('m1',px-200,YG-200,px+200,YG+200);label('m1lbl',px,YG,'PRE_N');addpin('PRE_N','Metal1',pya.Box(px-200,YG-200,px+200,YG+200))
for tx in taps:
    box('comp',tx+500,YV0-180,tx+1100,YV0+180);box('nplus',tx+270,YV0-410,tx+1330,YV0+410)
    box('cont',tx+690,YV0-110,tx+910,YV0+110)
box('nwell',-300,YV0-180-430,XE+300,YD+180+430)
box('m1',-300,YV0-200,XE+300,YV0+200);label('m1lbl',-150,YV0,'VPWR')
# ---- power: Metal4 stripes (1.6 um) over the tap columns, VGND / VPWR alternating, via1-via3 at the rails
YTOP=R*CH+300;YBOT=YV0-600
for i,tx in enumerate(taps):
    net='VGND' if i%2==0 else 'VPWR';xc=tx+800
    box('m4',xc-800,YBOT,xc+800,YTOP);label('m4lbl',xc,YTOP-800,net);addpin(net,'Metal4',pya.Box(xc-800,YBOT,xc+800,YTOP))
    ys=[k*CH for k in range(R+1) if (k%2==0)==(net=='VGND')]+([YV0] if net=='VPWR' else [])
    for yc in ys:
        box('via1',xc-130,yc-130,xc+130,yc+130);box('m2',xc-250,yc-200,xc+250,yc+200)
        box('via2',xc-130,yc-130,xc+130,yc+130);box('m3',xc-250,yc-200,xc+250,yc+200)
        box('via3',xc-130,yc-130,xc+130,yc+130)
bb=top.bbox();box('bnd',bb.left,bb.bottom,bb.right,bb.top)
ly.write(out+'.gds')
um=lambda v:'%.3f'%(v/1000.0)
def rect(q):return 'RECT %s %s %s %s ;'%(um(q.left-bb.left),um(q.bottom-bb.bottom),um(q.right-bb.left),um(q.top-bb.bottom))
lef=['VERSION 5.7 ;','BUSBITCHARS "[]" ;','DIVIDERCHAR "/" ;','MACRO %s'%top.name,'  CLASS BLOCK ;','  ORIGIN 0 0 ;',
     '  FOREIGN %s ;'%top.name,'  SIZE %s BY %s ;'%(um(bb.width()),um(bb.height())),'  SYMMETRY X Y ;']
key=lambda n:(n.split('[')[0],int(n.split('[')[1][:-1]) if '[' in n else 0)
for nm in sorted(PINS,key=key):
    d,use=(('INOUT','POWER') if nm=='VPWR' else ('INOUT','GROUND') if nm=='VGND' else ('OUTPUT','SIGNAL') if nm.startswith('RBL') else ('INPUT','SIGNAL'))
    lef+=['  PIN %s'%nm,'    DIRECTION %s ;'%d,'    USE %s ;'%use,'    PORT']
    for lname,q in PINS[nm]:lef+=['      LAYER %s ;'%lname,'        '+rect(q)]
    lef+=['    END','  END %s'%nm]
core=pya.Box(bb.left+1500,bb.bottom+1500,bb.right-1500,bb.top-1500)
lef+=['  OBS']+['    LAYER %s ;'%l+'\n      '+rect(core) for l in ('Metal1','Metal2','Metal3')]+['  END','END %s'%top.name,'END LIBRARY']
open(out+'.lef','w').write('\n'.join(lef)+'\n')
with open(out+'.spice','w') as fsp:
    pn=' '.join(['WBL[%d] WBLB[%d] RBL[%d]'%(c,c,c) for c in range(C)]+['WWL[%d] RWL[%d]'%(r,r) for r in range(R)]+['PRE_N','VPWR','VGND'])
    fsp.write('.SUBCKT %s %s\n%s\n.ENDS\n'%(top.name,pn,'\n'.join(sp)))
print('cell',top.name,'R',R,'C',C,'size_um',bb.width()/1000,bb.height()/1000,'um2_per_bit',round(bb.width()*bb.height()/1e6/(R*C),3))
