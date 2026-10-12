# 把 export3d.py 的瓦片（每个矩形 uint16×4：x0,y0,x1,y1，5 nm 单位）无损压成 .z：
# 每层按 (y0,x0) 排序，存 4 列 uint16：dy0、dx0（同一行内相对前一个，换行时存原值）、宽、高，整体 zlib。
# 用法：python3 pack3d.py 源目录 目标目录   （目标目录里写 manifest.json、ov/、tiles/*.z；自带逐块解码校验）
import json, os, shutil, sys, zlib
import numpy as np
src, dst = sys.argv[1:3]
m = json.load(open(f'{src}/manifest.json'))
os.makedirs(f'{dst}/tiles', exist_ok=True)
shutil.copytree(f'{src}/ov', f'{dst}/ov', dirs_exist_ok=True)

def enc(a):
    a = a[np.lexsort((a[:, 0], a[:, 1]))].astype(np.int64)
    dy = np.diff(a[:, 1], prepend=0)
    dx = a[:, 0].copy(); same = np.r_[False, dy[1:] == 0]
    dx[1:][same[1:]] = np.diff(a[:, 0])[same[1:]]
    return a, [dy, dx, a[:, 2] - a[:, 0], a[:, 3] - a[:, 1]]

def dec(u, cnt):
    dy, dx, w, h = (u[i*cnt:(i+1)*cnt].astype(np.int64) for i in range(4))
    y = np.cumsum(dy); x = dx.copy()
    for i in range(1, cnt):
        if dy[i] == 0: x[i] += x[i-1]
    return np.stack([x, y, x + w, y + h], 1)

raw_tot = z_tot = 0; tiles = {}
for k, lay in m['tiles'].items():
    raw = open(f'{src}/tiles/{k}.bin', 'rb').read(); cols = []; ent = {}; ref = {}
    for n, (off, cnt) in lay.items():
        a = np.frombuffer(raw, np.uint16, cnt * 4, off).reshape(-1, 4)
        s, c = enc(a); ref[n] = s
        for v in c: assert v.min() >= 0 and v.max() < 65536, (k, n)
        cols += [v.astype('<u2').tobytes() for v in c]; ent[n] = cnt
    z = zlib.compress(b''.join(cols), 9)
    u = np.frombuffer(zlib.decompress(z), '<u2'); p = 0
    for n, cnt in ent.items():
        assert np.array_equal(dec(u[p:p + 4*cnt], cnt), ref[n]), (k, n); p += 4 * cnt
    open(f'{dst}/tiles/{k}.z', 'wb').write(z); tiles[k] = ent
    raw_tot += len(raw); z_tot += len(z)
m['tiles'] = tiles; m['codec'] = 'zlib-delta-v1'
json.dump(m, open(f'{dst}/manifest.json', 'w'), separators=(',', ':'))
print(f'{len(tiles)} tiles  {raw_tot/1e6:.1f} MB -> {z_tot/1e6:.1f} MB  (x{raw_tot/z_tot:.1f}), lossless check ok')
