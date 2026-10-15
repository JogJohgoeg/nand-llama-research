# sramgen_sky130.py -- 8T (1R1W) static RAM array, sky130 logic rules (tp-2ea.48.2)
# run: klayout -b -r sramgen_sky130.py -rd rows=<R> -rd cols=<C> -rd out=<prefix> [-rd name=<cell>] [-rd tapk=4]
# writes <prefix>.gds and <prefix>.spice (schematic for LVS).
#
# Cell (build frame, nm; standard-cell style, VGND rail at y=0, VPWR rail at y=CH, odd rows mirrored so row pairs
# share rails and the nwell band). Vertical poly gates on horizontal diffusion strips:
#   ndiff  : c0 WBL | g0 a1 (WWL) | c1 Q | g1 pd1 (QB) | c2 VGND | g2 pd2 (Q) | c3 QB | g3 a2 (WWL) | c4 WBLB
#   pdiff  :                        c1 Q | g1 pu1 (QB) | c2 VPWR | g2 pu2 (Q) | c3 QB
#   read   : R0 VGND | r2 (Q) | X | r1 (RWL) | R1 RBL         -> RBL discharges iff Q=1 (ROM polarity)
# Gap between ndiff and pdiff: QB li track (g1 pad, jogged right) at y_A, Q li track (g2 pad, jogged left) at y_B,
# Q met1 jumper g2 pad -> r2 pad. Bottom zone: WWL (met1, g0/g3 pads) and RWL (met1, r1 pad) tracks.
# Bitlines vertical met2 (WBL/WBLB/RBL). Tap column (p-tap + n-tap) before every TK-th cell.
import pya
L={'nwell':(64,20),'diff':(65,20),'tap':(65,44),'poly':(66,20),'licon':(66,44),'li':(67,20),'mcon':(67,44),
   'm1':(68,20),'via':(68,44),'m2':(69,20),'via2':(69,44),'m3':(70,20),'via3':(70,44),'m4':(71,20),
   'm4pin':(71,16),'m4lbl':(71,5),'nsdm':(93,44),'psdm':(94,20),'npc':(95,20),'bnd':(235,4),
   'm1pin':(68,16),'m1lbl':(68,5),'m2pin':(69,16),'m2lbl':(69,5)}
R=int(rows);C=int(cols);TK=int(globals().get('tapk','4'))
CW,CH,TW=3830,3370,690
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell(globals().get('name','sram8t_%dx%d'%(R,C)))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(min(x0,x1),min(y0,y1),max(x0,x1),max(y0,y1)))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
# ---- local geometry (x relative to the cell origin, y local: VGND rail centre 0, VPWR rail centre CH)
YN0,YN1=1140,1560; YP0,YP1=2715,3135          # ndiff / pdiff strips (W 0.42)
YW,YR=495,865                                 # WWL / RWL met1 track centres (bottom zone)
YA,YB=1855,2395                               # QB li track / Q li track + met1 jumper (gap)
GX={'g0':285,'g1':715,'g2':1345,'g3':1775,'r2':2765,'r1':3125}   # gate left edges, L 150
CX={'c0':60,'c1':490,'c2':1020,'c3':1550,'c4':1980,'R0':2540,'R1':3330}  # diff licon left edges
ND=(0,2210);RD=(2480,3560);PD=(430,1780)      # ndiff / read strip / pdiff x extents
sp=[]
def cell(ox,r,c):
    """draw cell (r,c) at x origin ox; y mapping f() handles the mirror of odd rows."""
    y0=r*CH
    f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    def b(k,x0,ya,x1,yb):box(k,ox+x0,f(ya),ox+x1,f(yb))
    # diffusion
    b('diff',ND[0],YN0,ND[1],YN1);b('diff',RD[0],YN0,RD[1],YN1);b('diff',PD[0],YP0,PD[1],YP1)
    # gates (poly) incl. pads; pad = licon centre (px,py), poly 330x330 merged with the gate
    def pad(px,py,lipad=True,xw=False):
        b('poly',px-165,py-165,px+165,py+165);b('licon',px-85,py-85,px+85,py+85)
        b('npc',px-185,py-185,px+185,py+185)
        if lipad and xw:b('li',px-165,py-165,px+165,py+85)   # +80 left/right/bottom (RWL pad: RBL li above)
        elif lipad:b('li',px-165,py-165,px+85,py+165)  # li: +80 top/bottom (Magic li.5) and left (KLayout li.5)
    g=lambda n:GX[n]
    b('poly',g('g0'),YW,g('g0')+150,YN1+130);pad(330,YW)
    b('poly',g('g3'),YW,g('g3')+150,YN1+130);pad(1870,YW)
    b('poly',g('r1'),YR,g('r1')+150,YN1+130);pad(3290,YR,xw=True)
    b('poly',g('g1'),YN0-130,g('g1')+150,YP1+130);pad(970,YA,False)
    b('poly',g('g2'),YN0-130,g('g2')+150,YP1+130);pad(1240,YB,False)
    b('poly',g('r2'),YN0-130,g('r2')+150,YB);pad(2840,YB)
    b('npc',785,YA-185,1425,YB+185)               # merge the two gap npc pads (closer than 0.27)
    # diffusion contacts
    def dc(n,yc):b('licon',CX[n],yc-85,CX[n]+170,yc+85)
    yn,yp=(YN0+YN1)//2,(YP0+YP1)//2
    for n in ('c0','c1','c2','c3','c4','R0','R1'):dc(n,yn)
    for n in ('c1','c2','c3'):dc(n,yp)
    # li: Q (c1 n->p vertical + y_B track to the g2 pad), QB (c3 vertical + y_A track from the g1 pad)
    b('li',CX['c1']-80,yn-165,CX['c1']+170,yp+165)
    b('li',CX['c1']-80,YB-165,1325,YB+165)
    b('li',CX['c3'],yn-165,CX['c3']+170+80,yp+165)
    b('li',885,YA-165,CX['c3']+170,YA+165)
    # VGND: c2 and R0 down to the rail; VPWR: c2(p) up to the rail
    for n in ('c2','R0'):
        b('li',CX[n]-80,-85,CX[n]+170,yn+165);b('mcon',CX[n],-85,CX[n]+170,85)
    b('li',CX['c2']-80,yp-165,CX['c2']+170,CH+85);b('mcon',CX['c2'],CH-85,CX['c2']+170,CH+85)
    # Q met1 jumper g2 pad -> r2 pad
    for px in (1240,2840):b('mcon',px-85,YB-85,px+85,YB+85)
    b('m1',1240-145,YB-115,2840+145,YB+115)        # met1.5: 0.06 past the mcons in x
    # WWL / RWL pads to the met1 tracks
    for px,py in ((330,YW),(1870,YW),(3290,YR)):b('mcon',px-85,py-85,px+85,py+85)
    # bitline contacts: li pad, mcon, met1 pad, via, met2 vertical over the whole cell height
    for n,net in (('c0','WBL'),('c4','WBLB'),('R1','RBL')):
        cx=CX[n]+85
        e0,e1=(0,80) if n=='c4' else (80,0)        # c4 pad grows right (QB vertical li on its left)
        b('li',CX[n]-e0,yn-165,CX[n]+170+e1,yn+165);b('mcon',cx-85,yn-85,cx+85,yn+85)
        b('m1',cx-160,yn-160,cx+160,yn+160);b('via',cx-75,yn-75,cx+75,yn+75)
        b('m2',cx-160,0,cx+160,CH)
    # implants (nsdm per tap segment, drawn at row level)
    if r%2==0:box('psdm',ox+PD[0]-125,y0+YP0-125,ox+PD[1]+125,y0+2*CH-YP0+125)  # merged over the row pair
    # schematic
    t='r%dc%d'%(r,c);Q,QB,X='q_'+t,'qb_'+t,'x_'+t
    wbl,wblb,rbl,wwl,rwl='WBL[%d]'%c,'WBLB[%d]'%c,'RBL[%d]'%c,'WWL[%d]'%r,'RWL[%d]'%r
    N,P='sky130_fd_pr__nfet_01v8','sky130_fd_pr__pfet_01v8';w='W=0.42 L=0.15'
    sp.extend(['Xa1_%s %s %s %s VGND %s %s'%(t,wbl,wwl,Q,N,w),'Xpd1_%s %s %s VGND VGND %s %s'%(t,Q,QB,N,w),
               'Xpd2_%s VGND %s %s VGND %s %s'%(t,Q,QB,N,w),'Xa2_%s %s %s %s VGND %s %s'%(t,QB,wwl,wblb,N,w),
               'Xpu1_%s %s %s VPWR VPWR %s %s'%(t,Q,QB,P,w),'Xpu2_%s VPWR %s %s VPWR %s %s'%(t,Q,QB,P,w),
               'Xr2_%s VGND %s %s VGND %s %s'%(t,Q,X,N,w),'Xr1_%s %s %s %s VGND %s %s'%(t,X,rwl,rbl,N,w)])
def tapcol(tx,r):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    def b(k,x0,ya,x1,yb):box(k,tx+x0,f(ya),tx+x1,f(yb))
    yn,yp=(YN0+YN1)//2,(YP0+YP1)//2
    b('tap',0,YN0,420,YN1);b('psdm',-125,YN0-125,545,YN1+125)
    b('tap',0,YP0,420,YP1)
    if r%2==0:box('nsdm',tx-125,y0+YP0-125,tx+545,y0+2*CH-YP0+125)
    b('licon',125,yn-85,295,yn+85);b('licon',125,yp-85,295,yp+85)
    b('li',45,-85,295,yn+165);b('mcon',125,-85,295,85)
    b('li',45,yp-165,295,CH+85);b('mcon',125,CH-85,295,CH+85)
# ---- columns: x origins, tap columns before every TK-th cell
xs=[];taps=[];x=0
for c in range(C):
    if c%TK==0:taps.append(x);x+=TW
    xs.append(x);x+=CW
taps.append(x);XE=x+TW                        # closing tap column (LU.2: n-diff within 15 um of a p-tap)
for r in range(R):
    for c in range(C):cell(xs[c],r,c)
    for tx in taps:tapcol(tx,r)
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    for k,tx in enumerate(taps):                  # nsdm over the ndiff band of each segment
        if k+1==len(taps):break
        x1=taps[k+1]-CW+RD[1]+125
        box('nsdm',tx+TW-125,f(YN0-125),x1,f(YN1+125))
# ---- row-level shapes: rails, word-line tracks, nwell band (per mirrored row pair), labels/pins
for r in range(R):
    y0=r*CH;f=(lambda y:y0+y) if r%2==0 else (lambda y:y0+CH-y)
    for yc,w,net in ((YW,115,'WWL[%d]'%r),(YR,115,'RWL[%d]'%r)):
        box('m1',-300,f(yc)-w,XE+300,f(yc)+w)
        box('m1pin',XE,f(yc)-w,XE+300,f(yc)+w);label('m1lbl',XE+150,f(yc),net)
for k in range(R+1):                       # rails at y=k*CH: VGND where an even row starts / odd row ends
    net='VGND' if k%2==0 else 'VPWR';yc=k*CH;hw=240 if 0<k<R else 240
    box('m1',-300,yc-hw,XE+300,yc+hw)
    box('m1pin',-300,yc-hw,0,yc+hw);label('m1lbl',-150,yc,net)
for r in range(0,R,2):                     # nwell band around the shared VPWR rail of rows r, r+1
    yb=r*CH+YP0-180;yt=(r+2)*CH-YP0+180 if r+1<R else (r+1)*CH+240
    box('nwell',-300,yb,XE+300,yt)
for c in range(C):
    for n,net in (('c0','WBL'),('c4','WBLB'),('R1','RBL')):
        cx=xs[c]+CX[n]+85
        box('m2',cx-160,-300,cx+160,R*CH+300)
        box('m2pin',cx-160,R*CH,cx+160,R*CH+300);label('m2lbl',cx,R*CH+150,'%s[%d]'%(net,c))
# ---- RBL precharge row below row 0: one pfet (W 0.84) per column, PRE_N poly line (pads + met1 pins at both
# ends), source on a VPWR met1 rail, drain -> RBL met2; n-taps in the tap columns
YD,YG,YS,YV0=-560,-775,-990,-1100          # drain contact, gate, source contact, VPWR rail centres
P0,P1=YS-145,YD+145                        # pdiff y extent
for c in range(C):
    cx=xs[c]+CX['R1']+85
    box('diff',cx-420,P0,cx+420,P1);box('psdm',cx-545,P0-125,cx+545,P1+125)
    for yc in (YD,YS):box('licon',cx-85,yc-85,cx+85,yc+85)
    box('li',cx-165,YD-85,cx+165,YD+165);box('mcon',cx-85,YD-85,cx+85,YD+85)
    box('m1',cx-160,YD-160,cx+160,YD+160);box('via',cx-75,YD-75,cx+75,YD+75)
    box('m2',cx-160,YD-160,cx+160,0)
    box('li',cx-165,YS-165,cx+165,YS+85);box('mcon',cx-85,YS-85,cx+85,YS+85)
    sp.append('Xpc%d RBL[%d] PRE_N VPWR VPWR sky130_fd_pr__pfet_01v8 W=0.84 L=0.15'%(c,c))
box('poly',-1065,YG-75,XE+1065,YG+75)
for px in (-900,XE+900):
    box('poly',px-165,YG-165,px+165,YG+165);box('licon',px-85,YG-85,px+85,YG+85);box('npc',px-185,YG-185,px+185,YG+185)
    box('li',px-165,YG-165,px+85,YG+165);box('mcon',px-85,YG-85,px+85,YG+85)
    box('m1',px-160,YG-160,px+160,YG+160);box('m1pin',px-160,YG-160,px+160,YG+160);label('m1lbl',px,YG,'PRE_N')
for tx in taps:
    box('tap',tx+280,YV0-200,tx+690,YV0+180);box('nsdm',tx+155,YV0-325,tx+815,YV0+325)
    box('licon',tx+400,YV0-85,tx+570,YV0+85);box('li',tx+320,YV0-165,tx+570,YV0+165);box('mcon',tx+400,YV0-85,tx+570,YV0+85)
box('nwell',-300,P0-345,XE+300,P1+180)
box('m1',-300,YV0-240,XE+300,YV0+240);box('m1pin',-300,YV0-240,0,YV0+240);label('m1lbl',-150,YV0,'VPWR')
# ---- power: met4 stripes (1.6 um) over the tap columns, VGND / VPWR alternating, via stacks m1->m4 at the rails
YTOP=R*CH+300;YBOT=P0-345
for i,tx in enumerate(taps):
    net='VGND' if i%2==0 else 'VPWR';xc=tx+210
    box('m4',xc-800,YBOT,xc+800,YTOP);box('m4pin',xc-800,YBOT,xc+800,YTOP);label('m4lbl',xc,YTOP-800,net)   # whole stripe = pin (PDN drops vias on pin shapes only)
    ys=[k*CH for k in range(R+1) if (k%2==0)==(net=='VGND')]+([YV0] if net=='VPWR' else [])
    for yc in ys:
        box('via',xc-75,yc-75,xc+75,yc+75);box('m2',xc-185,yc-160,xc+185,yc+160)
        box('via2',xc-100,yc-100,xc+100,yc+100);box('m3',xc-250,yc-250,xc+250,yc+250)
        box('via3',xc-100,yc-100,xc+100,yc+100)
bb=top.bbox();box('bnd',bb.left,bb.bottom,bb.right,bb.top)
ly.write(out+'.gds')
# ---- LEF: pins = pin-layer shapes named by the label inside; OBS li1/met1/met2 over the core, met3 pads
um=lambda v:'%.3f'%((v)/1000.0)
def rect(q):return 'RECT %s %s %s %s ;'%(um(q.left-bb.left),um(q.bottom-bb.bottom),um(q.right-bb.left),um(q.top-bb.bottom))
pins={}
for pl,ll,lname in (('m1pin','m1lbl','met1'),('m2pin','m2lbl','met2'),('m4pin','m4lbl','met4')):
    texts=[(t.text.string,t.text.trans.disp) for t in top.shapes(li_[ll]).each() if t.is_text()]
    for t in top.shapes(li_[pl]).each():
        q=t.bbox();nm=[n for n,pt in texts if q.contains(pya.Point(pt.x,pt.y))]
        assert len(nm)==1,(lname,q,nm)
        if nm[0] in ('VPWR','VGND') and lname=='met1':continue   # rail labels stay in the GDS (LVS); power pins are the Metal4 stripes only
        pins.setdefault(nm[0],[]).append((lname,q))
lef=['VERSION 5.7 ;','BUSBITCHARS "[]" ;','DIVIDERCHAR "/" ;','MACRO %s'%top.name,'  CLASS BLOCK ;','  ORIGIN 0 0 ;',
     '  FOREIGN %s ;'%top.name,'  SIZE %s BY %s ;'%(um(bb.width()),um(bb.height())),'  SYMMETRY X Y ;']
key=lambda n:(n.split('[')[0],int(n.split('[')[1][:-1]) if '[' in n else 0)
for nm in sorted(pins,key=key):
    d,use=(('INOUT','POWER') if nm=='VPWR' else ('INOUT','GROUND') if nm=='VGND' else ('OUTPUT','SIGNAL') if nm.startswith('RBL') else ('INPUT','SIGNAL'))
    lef+=['  PIN %s'%nm,'    DIRECTION %s ;'%d,'    USE %s ;'%use,'    PORT']
    for lname,q in pins[nm]:lef+=['      LAYER %s ;'%lname,'        '+rect(q)]
    lef+=['    END','  END %s'%nm]
core=pya.Box(bb.left+1500,bb.bottom+1500,bb.right-1500,bb.top-1500)
lef+=['  OBS','    LAYER li1 ;','      '+rect(bb),'    LAYER met1 ;','      '+rect(core),'    LAYER met2 ;','      '+rect(core),'    LAYER met3 ;']
for t in top.shapes(li_['m3']).each():lef.append('      '+rect(t.bbox()))
lef+=['  END','END %s'%top.name,'END LIBRARY']
open(out+'.lef','w').write('\n'.join(lef)+'\n')
with open(out+'.spice','w') as fsp:
    pins=' '.join(['WBL[%d] WBLB[%d] RBL[%d]'%(c,c,c) for c in range(C)]+['WWL[%d] RWL[%d]'%(r,r) for r in range(R)]+['PRE_N','VPWR','VGND'])
    fsp.write('.SUBCKT %s %s\n%s\n.ENDS\n'%(top.name,pins,'\n'.join(sp)))
print('cell',top.name,'R',R,'C',C,'size_um',bb.width()/1000,bb.height()/1000,'um2_per_bit',round(bb.width()*bb.height()/1e6/(R*C),3))
