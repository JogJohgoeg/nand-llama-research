# klayout -b -r gds_pitch.py -rd f=<gds>: shape size histograms and pitches per layer (array study)
import pya,collections
ly=pya.Layout();ly.read(f);top=ly.top_cell()
for li in ly.layer_indexes():
    info=ly.get_info(li);sz=collections.Counter();xs=collections.Counter();ys=collections.Counter()
    it=top.begin_shapes_rec(li)
    while not it.at_end():
        b=it.shape().bbox().transformed(it.trans());sz[(b.width(),b.height())]+=1;xs[b.left]+=1;ys[b.bottom]+=1;it.next()
    def pitch(c):
        k=sorted(v for v,n in c.items() if n>=8);d=collections.Counter(b-a for a,b in zip(k,k[1:]));return d.most_common(3)
    print(info,'sizes',sz.most_common(4),'xpitch',pitch(xs),'ypitch',pitch(ys))
