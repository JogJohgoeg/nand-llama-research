# klayout -b -r gds_survey.py -rd f=<gds>: cell tree + per-layer shape counts/bbox
import pya
ly=pya.Layout();ly.read(f)
top=ly.top_cell();print('top',top.name,'dbu',ly.dbu,'bbox um',top.dbbox())
for c in ly.each_cell():
    n=sum(1 for _ in c.each_inst())
    print('cell',c.name,'insts',n,'bbox',c.dbbox())
for li in ly.layer_indexes():
    info=ly.get_info(li);cnt=0
    it=top.begin_shapes_rec(li)
    while not it.at_end():cnt+=1;it.next()
    print('layer',info,'shapes',cnt)
