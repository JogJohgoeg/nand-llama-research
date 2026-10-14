# sramgen_sg13g2.py -- 8T (1R1W) static RAM array, IHP SG13G2 (tp-2ea.48), same topology as sramgen_sky130.py
# run: klayout -b -r sramgen_sg13g2.py -rd rows=<R> -rd cols=<C> -rd out=<prefix> [-rd name=<cell>] [-rd tapk=4]
# writes <prefix>.gds, <prefix>.spice (schematic for LVS) and <prefix>.lef.
#
# No local interconnect in SG13G2: local wiring on Metal1, word lines on Metal2 (horizontal tracks in the bottom
# zone, via1 from Metal1 pads on the poly contacts), bit lines on Metal3 (vertical, via1/via2 stacks on the
# diffusion contacts), Q jumper g2 -> r2 on Metal2 in the gap. Rails VGND (y=0) / VPWR (y=CH) on Metal1, odd rows
# mirrored. All transistors W 0.30 L 0.13 (sg13_lv_nmos / sg13_lv_pmos).
#   ndiff : c0 WBL | g0 a1 (WWL) | c1 Q | g1 pd1 (QB) | c2 VGND | g2 pd2 (Q) | c3 QB | g3 a2 (WWL) | c4 WBLB
#   pdiff :                        c1 Q | g1 pu1 (QB) | c2 VPWR | g2 pu2 (Q) | c3 QB
#   read  : R0 VGND | r2 (Q) | X | r1 (RWL) | R1 RBL        -> RBL discharges iff Q=1
import pya
L={'activ':(1,0),'gatpoly':(5,0),'cont':(6,0),'m1':(8,0),'m2':(10,0),'m3':(30,0),'m4':(50,0),'psd':(14,0),
   'via1':(19,0),'via2':(29,0),'via3':(49,0),'nwell':(31,0),'bnd':(189,4),
   'm1pin':(8,2),'m1lbl':(8,25),'m2pin':(10,2),'m2lbl':(10,25),'m3pin':(30,2),'m3lbl':(30,25),'m4pin':(50,2),'m4lbl':(50,25)}
R=int(rows);C=int(cols);TK=int(globals().get('tapk','4'))
CW,CH,TW=3880,3230,1020
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell(globals().get('name','sram8t_%dx%d'%(R,C)))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(min(x0,x1),min(y0,y1),max(x0,x1),max(y0,y1)))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
YN,YP=1510,2770                                # ndiff / pdiff strip centres (W 0.30)
YW,YR=530,1030                                 # WWL / RWL Metal2 track centres
YA,YB=1900,2380                                # QB Metal1 track / Q Metal1 track (+ Metal2 jumper)
GX={'g0':340,'g1':850,'g2':1360,'g3':1870,'r2':2890,'r1':3200}
CX={'c0':70,'c1':580,'c2':1090,'c3':1600,'c4':2110,'R0':2620,'R1':3440}
ND=(0,2340);RD=(2550,3670);PD=(510,1830)
sp=[]
def cell(ox,r,c):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    def b(k,x0,ya,x1,yb):box(k,ox+x0,f(ya),ox+x1,f(yb))
    b('activ',ND[0],YN-150,ND[1],YN+150);b('activ',RD[0],YN-150,RD[1],YN+150);b('activ',PD[0],YP-150,PD[1],YP+150)
    def pad(px,py):                              # 0.30 poly pad with a contact
        b('gatpoly',px-150,py-150,px+150,py+150);b('cont',px-80,py-80,px+80,py+80)
    g=lambda n:GX[n]
    for n,px in (('g0',405),('g3',1935)):b('gatpoly',g(n),YW,g(n)+130,YN+150+180);pad(px,YW)
    b('gatpoly',g('r1'),YR,g('r1')+130,YN+150+180);pad(3350,YR)
    b('gatpoly',g('g1'),YN-150-180,g('g1')+130,YP+150+180);pad(1030,YA)
    b('gatpoly',g('g2'),YN-150-180,g('g2')+130,YP+150+180);pad(1310,YB)
    b('gatpoly',g('r2'),YN-150-180,g('r2')+130,YB);pad(2955,YB)
    def dc(n,yc):b('cont',CX[n],yc-80,CX[n]+160,yc+80)
    for n in ('c0','c1','c2','c3','c4','R0','R1'):dc(n,YN)
    for n in ('c1','c2','c3'):dc(n,YP)
    # Metal1: Q (c1 vertical + y_B track to the g2 pad), QB (c3 vertical + y_A track from the g1 pad)
    b('m1',CX['c1'],YN-130,CX['c1']+160,YP+130);b('m1',CX['c1'],YB-130,1390,YB+130)
    b('m1',CX['c3'],YN-130,CX['c3']+160,YP+130);b('m1',950,YA-130,CX['c3']+160,YA+130)
    # VGND: c2, R0 down to the rail; VPWR: c2 (p) up to the rail
    for n in ('c2','R0'):b('m1',CX[n]-50,-200,CX[n]+210,YN+80)     # 0.26 wide: M1.c1 enclosure in x
    b('m1',CX['c2']-50,YP-80,CX['c2']+210,CH+200)
    # Q jumper on Metal2: via1 on the Q track at x 1000 -> r2 pad
    for vx in (1000,2955):b('via1',vx-95,YB-95,vx+95,YB+95)
    b('m1',2955-150,YB-150,2955+150,YB+150);b('m2',1000-145,YB-145,2955+145,YB+145)
    # word-line pads: Metal1 pad, via1, onto the Metal2 tracks (drawn at row level)
    for px,py in ((405,YW),(1935,YW),(3350,YR)):b('m1',px-150,py-150,px+150,py+150);b('via1',px-95,py-95,px+95,py+95)
    # bit-line contacts: Metal1 pad, via1, Metal2 pad, via2, Metal3 vertical line
    for n in ('c0','c4','R1'):
        cx=CX[n]+80;b('m1',cx-150,YN-150,cx+150,YN+150);b('via1',cx-95,YN-95,cx+95,YN+95)
        b('m2',cx-145,YN-100,cx+145,YN+400);b('via2',cx-95,YN-95,cx+95,YN+95)   # M2.d area, clear of the RWL track
    t='r%dc%d'%(r,c);Q,QB,X='q_'+t,'qb_'+t,'x_'+t
    wbl,wblb,rbl,wwl,rwl='WBL[%d]'%c,'WBLB[%d]'%c,'RBL[%d]'%c,'WWL[%d]'%r,'RWL[%d]'%r
    N,P='sg13_lv_nmos','sg13_lv_pmos';w='W=0.3u L=0.13u'
    sp.extend(['Ma1_%s %s %s %s VGND %s %s'%(t,wbl,wwl,Q,N,w),'Mpd1_%s %s %s VGND VGND %s %s'%(t,Q,QB,N,w),
               'Mpd2_%s VGND %s %s VGND %s %s'%(t,Q,QB,N,w),'Ma2_%s %s %s %s VGND %s %s'%(t,QB,wwl,wblb,N,w),
               'Mpu1_%s %s %s VPWR VPWR %s %s'%(t,Q,QB,P,w),'Mpu2_%s VPWR %s %s VPWR %s %s'%(t,Q,QB,P,w),
               'Mr2_%s VGND %s %s VGND %s %s'%(t,Q,X,N,w),'Mr1_%s %s %s %s VGND %s %s'%(t,X,rwl,rbl,N,w)])
def tapcol(tx,r):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    def b(k,x0,ya,x1,yb):box(k,tx+x0,f(ya),tx+x1,f(yb))
    b('activ',0,YN-210,300,YN+210);b('psd',-180,YN-390,480,YN+390)   # p-tap (pSD encloses it by 0.18)
    b('activ',0,YP-210,300,YP+210)                                    # n-tap (no pSD, inside the NWell band)
    b('cont',70,YN-80,230,YN+80);b('cont',70,YP-80,230,YP+80)
    b('m1',20,-150,280,YN+130);b('m1',20,YP-130,280,CH+150)
xs=[];taps=[];x=0
for c in range(C):
    if c%TK==0:taps.append(x);x+=TW
    xs.append(x);x+=CW
taps.append(x);XE=x+TW
for r in range(R):
    for c in range(C):cell(xs[c],r,c)
    for tx in taps:tapcol(tx+360,r)
    y0=r*CH
    if r%2==0:                                   # pSD over the pdiff of the row pair (merged), per cell
        for c in range(C):box('psd',xs[c]+PD[0]-300,y0+YP-450,xs[c]+PD[1]+300,y0+2*CH-YP+450)
for r in range(R):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    for yc,net in ((YW,'WWL[%d]'%r),(YR,'RWL[%d]'%r)):
        box('m2',-300,f(yc)-145,XE+300,f(yc)+145)
        box('m2pin',XE,f(yc)-145,XE+300,f(yc)+145);label('m2lbl',XE+150,f(yc),net)
for k in range(R+1):
    net='VGND' if k%2==0 else 'VPWR';yc=k*CH
    box('m1',-300,yc-150,XE+300,yc+150);box('m1pin',-300,yc-150,0,yc+150);label('m1lbl',-150,yc,net)
for r in range(0,R,2):
    yb=r*CH+YP-150-310;yt=(r+2)*CH-YP+150+310 if r+1<R else r*CH+YP+150+310
    box('nwell',-300,yb,XE+300,yt)
for c in range(C):
    for n,net in (('c0','WBL'),('c4','WBLB'),('R1','RBL')):
        cx=xs[c]+CX[n]+80
        box('m3',cx-145,-300,cx+145,R*CH+300)
        box('m3pin',cx-145,R*CH,cx+145,R*CH+300);label('m3lbl',cx,R*CH+150,'%s[%d]'%(net,c))
# ---- RBL precharge row below row 0: one pfet (W 0.60) per column; PRE_N poly line with pads at both ends;
# source -> VPWR Metal1 rail, drain -> Metal3 RBL; n-taps in the tap columns
YD,YG,YS,YV0=-700,-1000,-1300,-1450
for c in range(C):
    cx=xs[c]+CX['R1']+80
    box('activ',cx-300,YS-150,cx+300,YD+150);box('psd',cx-600,YS-450,cx+600,YD+450)
    for yc in (YD,YS):box('cont',cx-80,yc-80,cx+80,yc+80)
    box('m1',cx-150,YD-150,cx+150,YD+150);box('via1',cx-95,YD-95,cx+95,YD+95)
    box('m2',cx-190,YD-190,cx+190,YD+190);box('via2',cx-95,YD-95,cx+95,YD+95);box('m3',cx-145,YD-145,cx+145,0)
    box('m1',cx-80,YV0,cx+80,YS+130)
    sp.append('Mpc%d RBL[%d] PRE_N VPWR VPWR sg13_lv_pmos W=0.6u L=0.13u'%(c,c))
box('gatpoly',-1050,YG-65,XE+1050,YG+65)
for px in (-900,XE+900):
    box('gatpoly',px-150,YG-150,px+150,YG+150);box('cont',px-80,YG-80,px+80,YG+80)
    box('m1',px-150,YG-150,px+150,YG+150);box('m1pin',px-150,YG-150,px+150,YG+150);label('m1lbl',px,YG,'PRE_N')
for tx in taps:
    box('activ',tx+600,YV0-210,tx+900,YV0+210);box('cont',tx+670,YV0-80,tx+830,YV0+80);box('m1',tx+600,YV0-150,tx+900,YV0+150)
box('nwell',-300,YV0-150-310,XE+300,YD+150+310)
box('m1',-300,YV0-200,XE+300,YV0+200);box('m1pin',-300,YV0-200,0,YV0+200);label('m1lbl',-150,YV0,'VPWR')
# ---- power: Metal4 stripes (1.6 um) over the tap columns, VGND / VPWR alternating, via1-via3 at the rails
YTOP=R*CH+300;YBOT=YV0-600
for i,tx in enumerate(taps):
    net='VGND' if i%2==0 else 'VPWR';xc=tx+510
    box('m4',xc-800,YBOT,xc+800,YTOP);box('m4pin',xc-800,YTOP-1600,xc+800,YTOP);label('m4lbl',xc,YTOP-800,net)
    ys=[k*CH for k in range(R+1) if (k%2==0)==(net=='VGND')]+([YV0] if net=='VPWR' else [])
    for yc in ys:
        box('via1',xc-95,yc-95,xc+95,yc+95);box('m2',xc-250,yc-145,xc+250,yc+145)
        box('via2',xc-95,yc-95,xc+95,yc+95);box('m3',xc-250,yc-145,xc+250,yc+145)
        box('via3',xc-95,yc-95,xc+95,yc+95)
bb=top.bbox();box('bnd',bb.left,bb.bottom,bb.right,bb.top)
ly.write(out+'.gds')
um=lambda v:'%.3f'%(v/1000.0)
def rect(q):return 'RECT %s %s %s %s ;'%(um(q.left-bb.left),um(q.bottom-bb.bottom),um(q.right-bb.left),um(q.top-bb.bottom))
pins={}
for pl,ll,lname in (('m1pin','m1lbl','Metal1'),('m2pin','m2lbl','Metal2'),('m3pin','m3lbl','Metal3'),('m4pin','m4lbl','Metal4')):
    texts=[(t.text.string,t.text.trans.disp) for t in top.shapes(li_[ll]).each() if t.is_text()]
    for t in top.shapes(li_[pl]).each():
        q=t.bbox();nm=[n for n,pt in texts if q.contains(pya.Point(pt.x,pt.y))]
        assert len(nm)==1,(lname,q,nm);pins.setdefault(nm[0],[]).append((lname,q))
lef=['VERSION 5.7 ;','BUSBITCHARS "[]" ;','DIVIDERCHAR "/" ;','MACRO %s'%top.name,'  CLASS BLOCK ;','  ORIGIN 0 0 ;',
     '  FOREIGN %s ;'%top.name,'  SIZE %s BY %s ;'%(um(bb.width()),um(bb.height())),'  SYMMETRY X Y ;']
key=lambda n:(n.split('[')[0],int(n.split('[')[1][:-1]) if '[' in n else 0)
for nm in sorted(pins,key=key):
    d,use=(('INOUT','POWER') if nm=='VPWR' else ('INOUT','GROUND') if nm=='VGND' else ('OUTPUT','SIGNAL') if nm.startswith('RBL') else ('INPUT','SIGNAL'))
    lef+=['  PIN %s'%nm,'    DIRECTION %s ;'%d,'    USE %s ;'%use,'    PORT']
    for lname,q in pins[nm]:lef+=['      LAYER %s ;'%lname,'        '+rect(q)]
    lef+=['    END','  END %s'%nm]
core=pya.Box(bb.left+1500,bb.bottom+1500,bb.right-1500,bb.top-1500)
lef+=['  OBS']+['    LAYER %s ;'%l+'\n      '+rect(core) for l in ('Metal1','Metal2','Metal3')]+['  END','END %s'%top.name,'END LIBRARY']
open(out+'.lef','w').write('\n'.join(lef)+'\n')
with open(out+'.spice','w') as fsp:
    pn=' '.join(['WBL[%d] WBLB[%d] RBL[%d]'%(c,c,c) for c in range(C)]+['WWL[%d] RWL[%d]'%(r,r) for r in range(R)]+['PRE_N','VPWR','VGND'])
    fsp.write('.SUBCKT %s %s\n%s\n.ENDS\n'%(top.name,pn,'\n'.join(sp)))
print('cell',top.name,'R',R,'C',C,'size_um',bb.width()/1000,bb.height()/1000,'um2_per_bit',round(bb.width()*bb.height()/1e6/(R*C),3))
