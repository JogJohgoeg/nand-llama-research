# romgen.py -- via1-programmed NOR mask-ROM array with built-in bitline precharge, sky130 (tp-2ea.44)
# run: klayout -b -r romgen.py -rd bits=<bits.txt> -rd out=<prefix> [-rd name=<cell>] [-rd tapk=8]
# bits.txt: one line per wordline (word), R characters '0'/'1' (char r = bit on bitline r).
#
# Built in a "build frame" (bitlines horizontal) and transposed (x<->y) on output, so the macro is
# narrow and tall: bitlines vertical met2, wordlines horizontal poly, power on two vertical met4 stripes.
# Build frame: R bitlines (horizontal met2, pitch 0.69 um), C = 2U wordlines (vertical poly). Unit u holds
# wordlines 2u (gate A) and 2u+1 (gate B) on a diffusion strip D_a | A | G | B | D_b in every row; G is
# ground (vertical li strip tied to the met2 ground rails below and above the array); D_a / D_b reach the
# row's bitline through via1 iff the bit is 1. A pfet per row (left of the array, in an nwell with an
# n-tap column) precharges the bitline to VPWR while PRE_N is low.
# Read: PRE_N low (precharge, all wordlines low) -> PRE_N high -> raise one wordline; bitline r goes low
# iff bit(r,w)=1. nfet_01v8 / pfet_01v8, W 0.42 L 0.15.
# Pins (final frame): BL[r] met2 on the top edge; WL even (A) met1 on the right edge, WL odd (B) and
# PRE_N... see LEF. VGND / VPWR met4 stripes over the full height.
import pya
L={'nwell':(64,20),'diff':(65,20),'tap':(65,44),'poly':(66,20),'licon':(66,44),'li':(67,20),'mcon':(67,44),
   'm1':(68,20),'via':(68,44),'m2':(69,20),'via2':(69,44),'m3':(70,20),'via3':(70,44),'m4':(71,20),
   'nsdm':(93,44),'psdm':(94,20),'npc':(95,20),'bnd':(235,4),
   'm1pin':(68,16),'m1lbl':(68,5),'m2pin':(69,16),'m2lbl':(69,5),'m4pin':(71,16),'m4lbl':(71,5)}
rows=[l.strip() for l in open(bits) if l.strip()]
C=len(rows);R=len(rows[0]);assert C%2==0 and all(len(x)==R for x in rows)
U=C//2;TK=int(globals().get('tapk','8'))
UX,PY=1380,690              # unit pitch (x), row pitch (y), nm
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell(globals().get('name','rom_array_%dx%d'%(R,C)))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(x0,y0,x1,y1))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
ux=[];x=0;taps=[]
for u in range(U):
    if u and u%TK==0:taps.append(x);x+=690
    ux.append(x);x+=UX
W=x-270                      # last unit diff ends at ux[-1]+1110
YD=(R-1)*PY+420              # top of the last row's diffusion
yb=-1300;yt=R*PY+600;yv=yt+700   # VGND rails (below/above), VPWR rail (above)
ypb=yb-900;ypt=yt+1300           # poly pad rows (B below, A and PRE above)
XP=-1250                         # precharge pfet diffusion start
NT=XP-690                        # n-tap column start
X0=NT-400                        # left end of the power rails
# ---- rows: bitline, NOR cells, precharge pfet
for r in range(R):
    y=r*PY
    box('m2',XP+300,y+80,W+400,y+340)                    # bitline
    box('m2pin',W+200,y+80,W+400,y+340);label('m2lbl',W+300,y+210,'BL[%d]'%r)
    for u in range(U):
        x=ux[u]
        box('diff',x,y,x+1110,y+420)
        for k,(c0,pad) in enumerate(((40,(-40,210)),(470,None),(900,(900,1150)))):
            box('licon',x+c0,y+125,x+c0+170,y+295)
            if pad is None:continue
            box('li',x+pad[0],y+45,x+pad[1],y+375)
            box('mcon',x+c0,y+125,x+c0+170,y+295)
            cx=x+c0+85;box('m1',cx-160,y+50,cx+160,y+370)
            w=2*u+(0 if k==0 else 1)
            if rows[w][r]=='1':box('via',cx-75,y+135,cx+75,y+285)
    # pfet: S (VPWR strip) | PRE_N gate | D -> bitline
    box('diff',XP,y,XP+680,y+420)
    box('licon',XP+40,y+125,XP+210,y+295);box('licon',XP+470,y+125,XP+640,y+295)
    box('li',XP+470,y+45,XP+720,y+375);box('mcon',XP+470,y+125,XP+640,y+295)
    box('m1',XP+395,y+50,XP+715,y+370);box('via',XP+480,y+135,XP+630,y+285)
    box('licon',NT+125,y+125,NT+295,y+295)              # n-tap contact
# ---- ground strips (li) with contacts to the VGND rails at both ends; p-tap columns likewise
def gstrip(x0,x1,cx):
    box('li',x0,yb-200,x1,yt+200)
    for yc in (yb,yt):
        box('mcon',cx-85,yc-85,cx+85,yc+85);box('m1',cx-160,yc-160,cx+160,yc+160);box('via',cx-75,yc-75,cx+75,yc+75)
for u in range(U):gstrip(ux[u]+390,ux[u]+640,ux[u]+555)
for tx in taps:
    box('tap',tx,-50,tx+420,R*PY-200);box('psdm',tx-125,-175,tx+545,R*PY-75)
    for r in range(R):box('licon',tx+125,r*PY+125,tx+295,r*PY+295)
    gstrip(tx+45,tx+295,tx+210)
# ---- precharge column: VPWR li strip over n-tap and pfet sources, n-tap, nwell, implants
box('tap',NT,-50,NT+420,R*PY-200);box('nsdm',NT-125,-175,NT+545,R*PY-75)
box('psdm',XP-125,-125,XP+805,YD+125)
box('nwell',NT-180,-230,XP+860,R*PY-20)
box('li',NT+45,-200,XP+210,yv+200)
box('mcon',NT+125,yv-85,NT+295,yv+85);box('m1',NT+50,yv-160,NT+370,yv+160);box('via',NT+135,yv-75,NT+285,yv+75)
# ---- power rails: met2 (contacts), met3 + met4 stacked over them; via2/via3 every 2.76 um
for yc,net,x0 in ((yb,'VGND',-300),(yt,'VGND',-300),(yv,'VPWR',X0)):
    hw=130 if yc==yt else 300
    box('m2',x0,yc-hw,W+200,yc+hw)
    if yc==yt:continue
    box('m3',x0,yc-300,W+200,yc+300);box('m4',x0,yc-800,W+200,yc+800)   # 1.6 um: room for via4 (0.8 + 2x0.19)
    box('m4pin',x0,yc-800,W+200,yc+800);label('m4lbl',(x0+W)//2,yc,net)
    xs=x0+400
    while xs+200<W:
        box('via2',xs,yc-100,xs+200,yc+100);box('via3',xs,yc-100,xs+200,yc+100);xs+=2760
# implant: nsdm per segment between tap columns
seg=[0]+[tx+690 for tx in taps];ends=[tx for tx in taps]+[W+270]
for s0,s1 in zip(seg,ends):box('nsdm',s0-125,-125,s1-270+125,YD+125)
# ---- poly: wordlines (A up, B down) and PRE_N (up), each with a contacted pad and a met1 pin
def pad(xg,yp,netname):
    cx=xg+75
    box('poly',cx-135,yp,cx+135,yp+330);box('licon',cx-85,yp+80,cx+85,yp+250)
    box('npc',cx-185,yp-20,cx+185,yp+350)
    box('li',cx-85,yp,cx+85,yp+340);box('mcon',cx-85,yp+80,cx+85,yp+250)
    box('m1',cx-160,yp+5,cx+160,yp+325);box('m1pin',cx-160,yp+5,cx+160,yp+325);label('m1lbl',cx,yp+165,netname)
for u in range(U):
    x=ux[u]
    box('poly',x+265,-130,x+415,ypt+330);pad(x+265,ypt,'WL[%d]'%(2*u))
    box('poly',x+695,ypb,x+845,YD+130);pad(x+695,ypb,'WL[%d]'%(2*u+1))
box('poly',XP+265,-130,XP+415,ypt+330);pad(XP+265,ypt,'PRE_N')
# ---- finish: origin at the lower-left corner, then transpose (x<->y)
bb=top.bbox();top.transform(pya.Trans(pya.Vector(-bb.left,-bb.bottom)))
top.transform(pya.Trans(pya.Trans.M45))
bb=top.bbox();box('bnd',bb.left,bb.bottom,bb.right,bb.top)
ly.write(out+'.gds')
# ---- LEF from the final layout: pins = pin-layer shapes named by the label they contain
um=lambda v:'%.3f'%(v/1000.0)
pins={}
for pl,ll,lname in (('m1pin','m1lbl','met1'),('m2pin','m2lbl','met2'),('m4pin','m4lbl','met4')):
    texts=[(s.text.string,s.text.trans.disp) for s in top.shapes(li_[ll]).each() if s.is_text()]
    for s in top.shapes(li_[pl]).each():
        b=s.bbox();nm=[t for t,p in texts if b.contains(pya.Point(p.x,p.y))]
        assert len(nm)==1,(lname,b,nm);pins.setdefault(nm[0],[]).append((lname,b))
lef=['VERSION 5.7 ;','BUSBITCHARS "[]" ;','DIVIDERCHAR "/" ;','MACRO %s'%top.name,'  CLASS BLOCK ;','  ORIGIN 0 0 ;',
     '  FOREIGN %s ;'%top.name,'  SIZE %s BY %s ;'%(um(bb.width()),um(bb.height())),'  SYMMETRY X Y ;']
def rect(b):return 'RECT %s %s %s %s ;'%(um(b.left),um(b.bottom),um(b.right),um(b.top))
for nm in sorted(pins,key=lambda n:(n.split('[')[0],int(n.split('[')[1][:-1]) if '[' in n else 0)):
    d,use=(('INOUT','POWER') if nm=='VPWR' else ('INOUT','GROUND') if nm=='VGND' else ('OUTPUT','SIGNAL') if nm.startswith('BL') else ('INPUT','SIGNAL'))
    lef+=['  PIN %s'%nm,'    DIRECTION %s ;'%d,'    USE %s ;'%use,'    PORT']
    for lname,b in pins[nm]:lef+=['      LAYER %s ;'%lname,'        '+rect(b)]
    lef+=['    END','  END %s'%nm]
# OBS (final frame; build y -> final x, build x -> final y): li1 everywhere; met1 and met2 over the core
# (pin rows/edges left open); met3 over the power rails (met4 rails are pins)
ox=-bb.left  # zero after the shift; kept for clarity
fx=lambda by:by-(ypb-20)   # build y -> final x (bbox bottom in build frame is the B pad npc edge)
def frect(b):return 'RECT %s %s %s %s ;'%(um(b[0]),um(b[1]),um(b[2]),um(b[3]))
core_x0,core_x1=fx(ypb+400),fx(ypt-60)
lef+=['  OBS','    LAYER li1 ;','      '+rect(bb),
      '    LAYER met1 ;','      '+frect((core_x0,0,core_x1,bb.height())),
      '    LAYER met2 ;','      '+frect((core_x0,0,core_x1,bb.height()-(W+400-(W+200)))) ,
      '    LAYER met3 ;']
for s in top.shapes(li_['m3']).each():lef.append('      '+rect(s.bbox()))
lef+=['  END','END %s'%top.name,'END LIBRARY']
open(out+'.lef','w').write('\n'.join(lef)+'\n')
# ---- reference SPICE: every NOR transistor present (unprogrammed drains float); one pfet per bitline
sp=['.subckt %s %s %s PRE_N VPWR VGND'%(top.name,' '.join('BL[%d]'%r for r in range(R)),' '.join('WL[%d]'%w for w in range(C)))]
n=0
for r in range(R):
    for w in range(C):
        d='BL[%d]'%r if rows[w][r]=='1' else 'nf_%d_%d'%(r,w)
        sp.append('XM%d %s WL[%d] VGND VGND sky130_fd_pr__nfet_01v8 w=0.42 l=0.15'%(n,d,w));n+=1
    sp.append('XP%d BL[%d] PRE_N VPWR VPWR sky130_fd_pr__pfet_01v8 w=0.42 l=0.15'%(r,r))
sp.append('.ends');open(out+'.spice','w').write('\n'.join(sp)+'\n')
print('cell',top.name,'bbox um',top.dbbox(),'area um2',round(top.dbbox().area(),1),'bits',R*C,'um2/bit',round(top.dbbox().area()/(R*C),3))
