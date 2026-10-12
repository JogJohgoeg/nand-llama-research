# romgen_gf180.py -- via1-programmed NOR mask-ROM array with bitline precharge, GF180MCU (3.3 V devices, tp-2ea.46.4)
# run: klayout -b -r romgen_gf180.py -rd bits=<bits.txt> -rd out=<prefix> [-rd name=<cell>] [-rd tapk=8]
# writes <prefix>.gds and <prefix>.spice (schematic: the intended transistors and nets, for LVS).
#
# Same topology and frames as romgen_sg13g2.py, redrawn for GF180MCU rules: nfet_03v3 / pfet_03v3, W 0.36 L 0.28;
# unit pitch 2.24 um, row pitch 0.64 um; explicit Nplus / Pplus implants; contacts land on Metal1.
# Unit u (build frame): COMP D_a | Poly2 gate A (WL 2u) | G | gate B (WL 2u+1) | D_b in every row; G on a vertical
# Metal1 strip tied to the VGND Metal2 rails; D_a / D_b carry a Metal1 pad, via1 to the row's bitline iff the bit
# is 1. A substrate-tap column before every TK-th unit (DF.14: substrate tap within 20 um). Precharge: one
# pfet_03v3 per row in an NWell with an n-tap column, source on the VPWR Metal1 strip, gate = PRE_N.
import pya
L={'comp':(22,0),'poly':(30,0),'cont':(33,0),'m1':(34,0),'m2':(36,0),'via1':(35,0),'nplus':(32,0),'pplus':(31,0),
   'nwell':(21,0),'bnd':(0,0),'m1lbl':(34,10),'m2lbl':(36,10)}
rows=[l.strip() for l in open(bits) if l.strip()]
C=len(rows);R=len(rows[0]);assert C%2==0 and all(len(x)==R for x in rows)
U=C//2;TK=int(globals().get('tapk','8'))
UX,PY,DW=2240,640,360             # unit pitch (x), row pitch (y), diffusion width W (nm)
UD=1960                           # unit diffusion length: 440 | 280 | 520 | 280 | 440
TW=1000                           # substrate-tap column (COMP 360 inside Pplus) inserted before a unit
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell(globals().get('name','rom_array_%dx%d'%(R,C)))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(x0,y0,x1,y1))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
def cont(x,y):box('cont',x,y,x+220,y+220)           # lower-left corner
def via1(cx,cy):box('via1',cx-130,cy-130,cx+130,cy+130)
def pad1(cx,cy):box('m1',cx-200,cy-200,cx+200,cy+200)   # Metal1 pad, area 0.16 (M1.3)
# ---- x positions
XP=0                              # precharge pfet COMP start (S cont | gate | D cont, 1160 long)
NT=XP-900                         # n-tap COMP column start (360 wide)
X0=XP+2100                        # first array column (NWell edge + 0.43, DF.16)
ux=[];taps=[];segs=[];x=X0;s0=None
for u in range(U):
    if u%TK==0:
        if s0 is not None:segs.append((s0,x-280))
        taps.append(x);x+=TW;s0=x
    ux.append(x);x+=UX
segs.append((s0,x-280))
W=x-280                           # right end of the array COMP
YT=(R-1)*PY+DW                    # top of the last row's COMP
ybl0,ybl1=-900,-300               # bottom VGND rail (Metal2)
ytl0,ytl1=YT+300,YT+900           # top VGND rail
yvl0,yvl1=ytl1+300,ytl1+900       # VPWR rail
ypb=ybl0-700;ypt=yvl1+700         # poly pad rows (A below; B and PRE_N above)
sp=[]
def gate_line(x,y0,y1,yc,pin):
    """vertical Poly2 gate x..x+280 from y0 to y1, 0.36 um poly pad centred at (x+140,yc) with a contact and a
    labelled Metal1 pad."""
    box('poly',x,min(y0,yc-180),x+280,max(y1,yc+180))
    cx=x+140;box('poly',cx-180,yc-180,cx+180,yc+180);cont(cx-110,yc-110);pad1(cx,yc);label('m1lbl',cx,yc,pin)
# ---- array rows
for r in range(R):
    y=r*PY;cy=y+180
    box('m2',XP+700,cy-140,W+600,cy+140);label('m2lbl',W+450,cy,'BL[%d]'%r)
    for u in range(U):
        x=ux[u];box('comp',x,y,x+UD,y+DW)
        for k,cx0 in enumerate((70,870,1670)):
            cont(x+cx0,y+70)
            if k==1:continue
            cx=x+cx0+110;pad1(cx,cy);w=2*u+(0 if k==0 else 1)
            if rows[w][r]=='1':via1(cx,cy)
            sp.append('Mn%d_%d %s WL[%d] VGND VGND nfet_03v3 W=0.36u L=0.28u'%(w,r,'BL[%d]'%r if rows[w][r]=='1' else 'dn%d_%d'%(w,r),w))
    box('comp',XP,y,XP+1160,y+DW);cont(XP+70,y+70);cont(XP+870,y+70);pad1(XP+980,cy);via1(XP+980,cy)
    cont(NT+70,y+70)
    sp.append('Mp%d BL[%d] PRE_N VPWR VPWR pfet_03v3 W=0.36u L=0.28u'%(r,r))
# ---- ground strips over the G contacts and substrate-tap columns, via1 to both VGND rails
for gx in [ux[u]+980 for u in range(U)]+[tx+360 for tx in taps]:
    box('m1',gx-115,ybl0+100,gx+115,ytl1-100)
    for yc in ((ybl0+ybl1)//2,(ytl0+ytl1)//2):via1(gx,yc);pad1(gx,yc)
for tx in taps:
    box('comp',tx+180,-70,tx+540,YT+70);box('pplus',tx-50,-300,tx+770,YT+300)
    for r in range(R):cont(tx+250,r*PY+70)
for a,b in segs:box('nplus',a-230,-300,b+230,YT+300)
# ---- wordline gates: A (even) pads below, B (odd) pads above; PRE_N above
for u in range(U):
    gate_line(ux[u]+440,-220,YT+220,ypb,'WL[%d]'%(2*u))
    gate_line(ux[u]+1240,-220,YT+220,ypt,'WL[%d]'%(2*u+1))
gate_line(XP+440,-220,YT+220,ypt,'PRE_N')
# ---- precharge column: n-tap, NWell, implants, VPWR Metal1 strip
box('comp',NT,-70,NT+360,YT+70);box('nplus',NT-230,-300,NT+590,YT+300)
box('pplus',XP-230,-300,XP+1390,YT+300)
box('nwell',NT-430,-500,XP+1590,YT+500)
box('m1',NT+50,-150,XP+310,yvl1-100)
for vx in range(NT+200,XP+200,520):via1(vx,(yvl0+yvl1)//2)
# ---- rails (Metal2) with labels
for (y0,y1),net,xa in (((ybl0,ybl1),'VGND',X0-300),((ytl0,ytl1),'VGND',X0-300),((yvl0,yvl1),'VPWR',NT-300)):
    box('m2',xa,y0,W+600,y1);label('m2lbl',W+450,(y0+y1)//2,net)
box('bnd',NT-600,ypb-400,W+600,ypt+400)
top.transform(pya.Trans(pya.Trans.M45))
ly.write(out+'.gds')
with open(out+'.spice','w') as f:
    pins=' '.join(['BL[%d]'%r for r in range(R)]+['WL[%d]'%w for w in range(C)]+['PRE_N','VPWR','VGND'])
    f.write('.SUBCKT %s %s\n%s\n.ENDS\n'%(top.name,pins,'\n'.join(sp)))
b=top.bbox();print('cell',top.name,'R',R,'C',C,'size_um',b.width()/1000,b.height()/1000,
                   'um2_per_bit',round(b.width()*b.height()/1e6/(R*C),4))
