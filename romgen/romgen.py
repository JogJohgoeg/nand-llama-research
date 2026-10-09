# romgen.py -- via1-programmed NOR mask-ROM array for sky130 (tp-2ea.44 prototype)
# run: klayout -b -r romgen.py -rd bits=<bits.txt> -rd out=<prefix> [-rd tapk=8]
# bits.txt: one line per wordline (word), R characters '0'/'1' (char r = bit on bitline r).
# Array: R bitlines (horizontal met2), C = 2U wordlines (vertical poly). Unit u holds wordlines 2u (gate A)
# and 2u+1 (gate B) on a diffusion strip D_a | A | G | B | D_b in every row; G is ground (vertical li strip,
# tied to met2 ground rails above and below the array); D_a / D_b reach the row's bitline through via1 iff
# the bit is 1. Read: precharge bitlines high, raise one wordline, bitline r goes low iff bit(r,w)=1.
# Even wordlines are pinned (met1) on the top edge, odd ones on the bottom edge. nfet_01v8 W=0.42 L=0.15.
import pya
L={'diff':(65,20),'tap':(65,44),'poly':(66,20),'licon':(66,44),'li':(67,20),'mcon':(67,44),'m1':(68,20),
   'via':(68,44),'m2':(69,20),'nsdm':(93,44),'psdm':(94,20),'npc':(95,20),'bnd':(235,4),
   'm1pin':(68,16),'m1lbl':(68,5),'m2pin':(69,16),'m2lbl':(69,5)}
rows=[l.strip() for l in open(bits) if l.strip()]
C=len(rows);R=len(rows[0]);assert C%2==0 and all(len(x)==R for x in rows)
U=C//2;TK=int(globals().get('tapk','8'))
UX,PY=1380,690              # unit pitch (x), row pitch (y), nm
ly=pya.Layout();ly.dbu=0.001;top=ly.create_cell('rom_array_%dx%d'%(R,C))
li_={k:ly.layer(*v) for k,v in L.items()}
def box(k,x0,y0,x1,y1):top.shapes(li_[k]).insert(pya.Box(x0,y0,x1,y1))
def label(k,x,y,t):top.shapes(li_[k]).insert(pya.Text(t,pya.Trans(x,y)))
# x origin of each unit, with a tap column (690 nm) after every TK units
ux=[];x=0;taps=[]
for u in range(U):
    if u and u%TK==0:taps.append(x);x+=690
    ux.append(x);x+=UX
W=x-270                      # last unit diff ends at ux[-1]+1110
yb=-1300;yt=R*PY+600         # ground rail centres (below / above the rows)
ypb=yb-900;ypt=yt+600        # poly pad rows
# rows
for r in range(R):
    y=r*PY
    box('m2',-200,y+80,W+200,y+340)                     # bitline
    box('m2pin',-200,y+80,0,y+340);label('m2lbl',-100,y+210,'BL[%d]'%r)
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
# G strips (li) + ground contacts at both ends, tap columns likewise
def gstrip(x0,x1,cx):
    box('li',x0,yb-200,x1,yt+200)
    for yc in (yb,yt):
        box('mcon',cx-85,yc-85,cx+85,yc+85);box('m1',cx-160,yc-160,cx+160,yc+160);box('via',cx-75,yc-75,cx+75,yc+75)
for u in range(U):gstrip(ux[u]+390,ux[u]+640,ux[u]+555)
for tx in taps:
    box('tap',tx,-50,tx+420,R*PY-200);box('psdm',tx-125,-175,tx+545,R*PY-75)
    for r in range(R):box('licon',tx+125,r*PY+125,tx+295,r*PY+295)
    gstrip(tx+45,tx+295,tx+210)
for yc in (yb,yt):
    box('m2',-200,yc-130,W+200,yc+130);box('m2pin',-200,yc-130,0,yc+130);label('m2lbl',-100,yc,'VGND')
# implant: nsdm per segment between tap columns
seg=[0]+[tx+690 for tx in taps];ends=[tx for tx in taps]+[W+270]
for s0,s1 in zip(seg,ends):box('nsdm',s0-125,-125,s1-270+125,(R-1)*PY+420+125)
# poly wordlines + pads (A on top, B on bottom)
for u in range(U):
    x=ux[u]
    for xg,up,w in ((x+265,True,2*u),(x+695,False,2*u+1)):
        yp=ypt if up else ypb
        if up:box('poly',xg,-130,xg+150,yp+330)
        else:box('poly',xg,yp,xg+150,(R-1)*PY+420+130)
        cx=xg+75
        box('poly',cx-135,yp,cx+135,yp+330);box('licon',cx-85,yp+80,cx+85,yp+250)
        box('npc',cx-185,yp-20,cx+185,yp+350)
        box('li',cx-85,yp,cx+85,yp+340);box('mcon',cx-85,yp+80,cx+85,yp+250)
        box('m1',cx-160,yp+5,cx+160,yp+325);box('m1pin',cx-160,yp+5,cx+160,yp+325);label('m1lbl',cx,yp+165,'WL[%d]'%w)
bb=top.bbox();box('bnd',bb.left,bb.bottom,bb.right,bb.top)
ly.write(out+'.gds')
# reference SPICE: every transistor present; unprogrammed drains float on their own nets
sp=['.subckt %s %s %s VGND'%(top.name,' '.join('BL[%d]'%r for r in range(R)),' '.join('WL[%d]'%w for w in range(C)))]
n=0
for r in range(R):
    for w in range(C):
        d='BL[%d]'%r if rows[w][r]=='1' else 'nf_%d_%d'%(r,w)
        sp.append('XM%d %s WL[%d] VGND VGND sky130_fd_pr__nfet_01v8 w=0.42 l=0.15'%(n,d,w));n+=1
sp.append('.ends');open(out+'.spice','w').write('\n'.join(sp)+'\n')
print('cell',top.name,'bbox um',top.dbbox(),'area um2',round(top.dbbox().area(),1),'bits',R*C,'um2/bit',round(top.dbbox().area()/(R*C),3))
