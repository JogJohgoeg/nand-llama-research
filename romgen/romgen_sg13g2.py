# romgen_sg13g2.py -- via1-programmed NOR mask-ROM array with bitline precharge, IHP SG13G2 (tp-2ea.46.4)
# run: klayout -b -r romgen_sg13g2.py -rd bits=<bits.txt> -rd out=<prefix> [-rd name=<cell>]
# writes <prefix>.gds and <prefix>.spice (schematic: the intended transistors and nets, for LVS).
#
# Same topology as the SKY130 romgen.py, redrawn for SG13G2 rules (no local interconnect: contacts land on
# Metal1). Build frame (transposed x<->y on output, so bitlines end up vertical):
#   R rows (bitlines, horizontal Metal2, pitch 0.51 um), C = 2U wordlines (vertical GatPoly, unit pitch 1.53 um).
#   Unit u: diffusion D_a | gate A (WL 2u) | G | gate B (WL 2u+1) | D_b in every row, W 0.30 L 0.13
#   (sg13_lv_nmos). G is ground: a vertical Metal1 strip over the G contacts, via1 to the VGND Metal2 rails
#   below and above the array. D_a / D_b carry a Metal1 pad; via1 to the row's bitline iff the bit is 1.
#   Precharge: one sg13_lv_pmos per row (W 0.30 L 0.13) left of the array in an NWell with an n-tap column;
#   source on the VPWR Metal1 strip, drain via1 to the bitline, gate = PRE_N (vertical poly).
# Read: PRE_N low precharges every bitline to VPWR (all wordlines low); PRE_N high, raise one wordline;
# bitline r goes low iff bit(r,w) = 1.
# Pins (build frame): BL[r] Metal2 at the right end of row r; WL even (A) Metal1 pads below the array, WL odd
# (B) above, PRE_N above; VGND / VPWR Metal2 rails.
import pya
L={'activ':(1,0),'gatpoly':(5,0),'cont':(6,0),'m1':(8,0),'m2':(10,0),'psd':(14,0),'via1':(19,0),
   'nwell':(31,0),'bnd':(189,4),'m1pin':(8,2),'m1lbl':(8,25),'m2pin':(10,2),'m2lbl':(10,25)}
rows=[l.strip() for l in open(bits) if l.strip()]
C=len(rows);R=len(rows[0]);assert C%2==0 and all(len(x)==R for x in rows)
U=C//2;TK=int(globals().get('tapk','12'))   # p-tap column before every TK-th unit (latch-up LU.b <= 20 um)
UX,PY=1530,510                    # unit pitch (x), row pitch (y), nm
DW=300                            # diffusion width (transistor W)
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell(globals().get('name','rom_array_%dx%d'%(R,C)))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(x0,y0,x1,y1))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
def cont(x,y):box('cont',x,y,x+160,y+160)           # lower-left corner
def via1(cx,cy):box('via1',cx-95,cy-95,cx+95,cy+95)
# ---- x positions
XP=0                              # precharge pfet diffusion start
NT=XP-700                         # n-tap column start (300 wide)
X0=XP+1500                        # first unit diffusion (NWell edge + >= 0.31)
ux=[];taps=[];x=X0
for u in range(U):
    if u%TK==0:taps.append(x+30);x+=720     # tap column: pSD 180 | activ 300 (+30 pSD each side) | 180
    ux.append(x);x+=UX
W=ux[-1]+1320 if U else X0        # right end of the array diffusion
YT=(R-1)*PY+DW                    # top of the last row's diffusion
# rails / pad rows (build frame y)
ybl0,ybl1=-800,-200               # bottom VGND rail (Metal2)
ytl0,ytl1=YT+310,YT+910           # top VGND rail
yvl0,yvl1=ytl1+300,ytl1+900       # VPWR rail above it
ypb=ybl0-600                      # bottom poly pad row (A pads), pad centre
ypt=yvl1+600                      # top poly pad row (B pads, PRE_N), pad centre
sp=[]                             # schematic lines
PINS={}
def addpin(name,lay,use,dirn,r):PINS.setdefault(name,(lay,use,dirn,[]))[3].append(r)
def gate_line(x,y0,y1,yc,pin):
    """vertical GatPoly line x..x+130 from y0 to y1 with a 0.30 um poly pad centred at (x+65,yc), Cont and
    a Metal1 pin pad on it."""
    box('gatpoly',x,min(y0,yc-150),x+130,max(y1,yc+150))
    cx=x+65;box('gatpoly',cx-150,yc-150,cx+150,yc+150)
    # the Metal1 pin runs from the pad to the macro edge (bottom pads down to ypb-300, top pads up to ypt+300), so the
    # router can enter it straight from outside the macro (IHP top-level GRT found no access point otherwise)
    e0,e1=(ypb-300,yc+150) if yc<0 else (yc-150,ypt+300)
    cont(cx-80,yc-80);box('m1',cx-150,e0,cx+150,e1)
    box('m1pin',cx-150,e0,cx+150,e1);label('m1lbl',cx,yc,pin);addpin(pin,'Metal1',None,'INPUT',(cx-150,e0,cx+150,e1))
# ---- array rows
for r in range(R):
    y=r*PY;cy=y+150
    box('m2',XP+500,cy-100,W+600,cy+100)                       # bitline from the pfet drain to the right edge
    box('m2pin',W+400,cy-100,W+600,cy+100);label('m2lbl',W+500,cy,'BL[%d]'%r);addpin('BL[%d]'%r,'Metal2',None,'OUTPUT',(W+400,cy-100,W+600,cy+100))
    for u in range(U):
        x=ux[u];box('activ',x,y,x+1320,y+DW)
        for k,cx0 in enumerate((70,580,1090)):
            cont(x+cx0,y+70)
            if k==1:continue
            cx=x+cx0+80;box('m1',cx-160,cy-145,cx+160,cy+145)       # drain pad (M1.d area 0.09)
            w=2*u+(0 if k==0 else 1)
            if rows[w][r]=='1':via1(cx,cy)
            sp.append('Mn%d_%d BL[%d] WL[%d] VGND VGND sg13_lv_nmos W=0.3u L=0.13u'%(w,r,r,w) if rows[w][r]=='1' else
                      'Mn%d_%d dn%d_%d WL[%d] VGND VGND sg13_lv_nmos W=0.3u L=0.13u'%(w,r,w,r,w))
    # pfet: S (VPWR strip) | PRE_N gate | D -> bitline
    box('activ',XP,y,XP+810,y+DW);cont(XP+70,y+70);cont(XP+580,y+70)
    box('m1',XP+500,cy-145,XP+820,cy+145);via1(XP+660,cy)
    cont(NT+70,y+70)                                            # n-tap contact (n-tap column below)
    sp.append('Mp%d BL[%d] PRE_N VPWR VPWR sg13_lv_pmos W=0.3u L=0.13u'%(r,r))
# ---- ground strips (Metal1) over the G contacts, via1 to both VGND rails
for u in range(U):
    x=ux[u]+580;box('m1',x,ybl0+130,x+160,ytl1-130)
    for yc in ((ybl0+ybl1)//2,(ytl0+ytl1)//2):via1(x+80,yc);box('m1',x-25,yc-145,x+185,yc+145)
# ---- p-tap columns (pSD activ, contacts every row, Metal1 strip to the VGND rails)
for tx in taps:
    box('activ',tx+180,-60,tx+480,YT+60);box('psd',tx+150,-90,tx+510,YT+90)
    for r in range(R):cont(tx+250,r*PY+70)
    box('m1',tx+250,ybl0+130,tx+410,ytl1-130)
    for yc in ((ybl0+ybl1)//2,(ytl0+ytl1)//2):via1(tx+330,yc);box('m1',tx+225,yc-145,tx+435,yc+145)
# ---- wordline gates: A (even) pads below, B (odd) pads above
for u in range(U):
    gate_line(ux[u]+340,-180,YT+180,ypb,'WL[%d]'%(2*u))
    gate_line(ux[u]+850,-180,YT+180,ypt,'WL[%d]'%(2*u+1))
# ---- precharge column: PRE_N poly, VPWR Metal1 strip over n-tap and pfet sources, n-tap, NWell, pSD
gate_line(XP+340,-180,YT+180,ypt,'PRE_N')
box('activ',NT,-60,NT+300,YT+60)                                # n-tap stripe (no pSD)
box('psd',XP-180,-300,XP+990,YT+300)          # pSD.i: 0.30 beyond the pfet gate ends
box('nwell',NT-310,-370,XP+1120,YT+370)
box('m1',NT+45,-120,XP+230,yvl1-130)
for x in range(NT+150,XP+100,420):via1(x+95,(yvl0+yvl1)//2)
box('m1',NT+45,yvl0+20,XP+230,yvl1-20)
# ---- rails (Metal2) with pins
for (y0,y1),net,xa in (((ybl0,ybl1),'VGND',X0-300),((ytl0,ytl1),'VGND',X0-300),((yvl0,yvl1),'VPWR',NT-300)):
    box('m2',xa,y0,W+600,y1);box('m2pin',W+300,y0,W+600,y1);label('m2lbl',W+450,(y0+y1)//2,net);addpin(net,'Metal2','POWER' if net=='VPWR' else 'GROUND','INOUT',(xa,y0,W+600,y1))
box('bnd',NT-400,ypb-300,W+600,ypt+300)
# ---- transpose to the final frame (bitlines vertical) and write
top.transform(pya.Trans(pya.Trans.M45))
def write_lef(path,cell,b,pins,obs_layers):
    """LEF abstract in the final (transposed) frame: pin rectangles recorded in the build frame are swapped x<->y;
    OBS: see below (core blocked, edge band = real metal minus pins)."""
    s=lambda v:'%.3f'%(v/1000)
    o=['VERSION 5.7 ;','BUSBITCHARS "[]" ;','DIVIDERCHAR "/" ;','MACRO %s'%cell,'  CLASS BLOCK ;',
       '  ORIGIN %s %s ;'%(s(-b.left),s(-b.bottom)),'  SIZE %s BY %s ;'%(s(b.width()),s(b.height())),'  SYMMETRY X Y ;']
    for name,(lay,use,dirn,rects) in pins.items():
        o+=['  PIN %s'%name,'    DIRECTION %s ;'%dirn]+(['    USE %s ;'%use] if use else [])+['    PORT','      LAYER %s ;'%lay]
        o+=['        RECT %s %s %s %s ;'%(s(y0),s(x0),s(y1),s(x1)) for x0,y0,x1,y1 in rects]+['    END','  END %s'%name]
    # OBS: the macro core (all but a 1.5 um edge band) is one blocked rectangle; inside the edge band only the real
    # metal is blocked, minus the pin shapes, so the router can reach the WL / PRE_N (Metal1) and BL (Metal2) pins
    # from outside and drop vias on them (a whole-macro OBS left no access; IHP top-level DRT could not converge)
    E=1500;core=pya.Region(pya.Box(b.left+E,b.bottom+E,b.right-E,b.top-E));band=pya.Region(b)-core
    o+=['  OBS']
    for lef,key in obs_layers:
        pr=pya.Region()
        for lay,use,dirn,rects in pins.values():
            if lay==lef:
                for x0,y0,x1,y1 in rects:pr.insert(pya.Box(y0,x0,y1,x1))
        r=((core+(pya.Region(top.begin_shapes_rec(li_[key]))&band))-pr).merged()
        o.append('    LAYER %s ;'%lef)
        for p in r.each():
            for t in p.decompose_trapezoids():
                q=t.bbox();o.append('      RECT %s %s %s %s ;'%(s(q.left),s(q.bottom),s(q.right),s(q.top)))
    o+=['  END','END %s'%cell,'END LIBRARY']
    open(path,'w').write('\n'.join(o)+'\n')
ly.write(out+'.gds')
write_lef(out+'.lef',top.name,top.bbox(),PINS,[('Metal1','m1'),('Metal2','m2')])
with open(out+'.spice','w') as f:
    pins=' '.join(['BL[%d]'%r for r in range(R)]+['WL[%d]'%w for w in range(C)]+['PRE_N','VPWR','VGND'])
    f.write('.SUBCKT %s %s\n%s\n.ENDS\n'%(top.name,pins,'\n'.join(sp)))
b=top.bbox();print('cell',top.name,'R',R,'C',C,'size_um',b.width()/1000,b.height()/1000,
                   'um2_per_bit',round(b.width()*b.height()/1e6/(R*C),4))
