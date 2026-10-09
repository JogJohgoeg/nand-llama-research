# klayout -b -r gds_window.py -rd f=<gds> -rd w=x0,y0,x1,y1 (um): list shapes overlapping window, per layer
import pya
ly=pya.Layout();ly.read(f);top=ly.top_cell()
x0,y0,x1,y1=[float(v) for v in w.split(',')]
box=pya.Box(int(x0/ly.dbu),int(y0/ly.dbu),int(x1/ly.dbu),int(y1/ly.dbu))
for li in ly.layer_indexes():
    it=top.begin_shapes_rec_overlapping(li,box);out=[]
    while not it.at_end():
        b=it.shape().bbox().transformed(it.trans());out.append((round(b.left*ly.dbu,3),round(b.bottom*ly.dbu,3),round(b.right*ly.dbu,3),round(b.top*ly.dbu,3)));it.next()
    if out:print(ly.get_info(li),sorted(out))
