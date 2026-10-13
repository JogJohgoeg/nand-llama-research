# 把 OAS 版图导出成「概览贴图 + 瓦片矩形」两级数据，给 three.js 实例化查看器用。
# 用法: python export3d.py chip.oas outdir [--tiles x0,y0,x1,y1] [--jobs N]
#   瓦片 256 µm；坐标存成相对瓦片原点的 uint16（5 nm 单位）x0,y0,x1,y1，每矩形 8 字节。
import klayout.db as db, numpy as np, json, os, sys, time, argparse
from multiprocessing import Pool

TILE_UM = 256.0
Q_NM = 5                        # 量化单位
OV_UM = 1.5                     # 概览贴图每像素 µm
# 名称, GDS 层/类型, 颜色, z 底(µm), 厚度(µm)  —— sky130 层高（近似，展示用）
LAYERS = [
    ("nwell", (64, 20), "#5b4a8a", -0.40, 0.05),
    ("diff",  (65, 20), "#3fa34d", -0.10, 0.10),
    ("tap",   (65, 44), "#2f7f3a", -0.10, 0.10),
    ("poly",  (66, 20), "#d63b3b",  0.00, 0.18),
    ("licon", (66, 44), "#999999",  0.18, 0.76),
    ("li1",   (67, 20), "#c58bd6",  0.94, 0.10),
    ("mcon",  (67, 44), "#bbbbbb",  1.04, 0.34),
    ("met1",  (68, 20), "#4a7fd6",  1.38, 0.36),
    ("via",   (68, 44), "#dddddd",  1.74, 0.27),
    ("met2",  (69, 20), "#d6a23b",  2.01, 0.36),
    ("via2",  (69, 44), "#dddddd",  2.37, 0.42),
    ("met3",  (70, 20), "#3bc4c4",  2.79, 0.85),
    ("via3",  (70, 44), "#dddddd",  3.64, 0.39),
    ("met4",  (71, 20), "#d65fa0",  4.03, 0.85),
    ("via4",  (71, 44), "#dddddd",  4.88, 0.51),
    ("met5",  (72, 20), "#e8e070",  5.39, 1.26),
]
ROMBIT = ("rombit", None, "#ff3030", 1.74, 0.27)   # ROM 阵列里的 via1 = 权重比特
ROMVIA = 'via'                  # 哪一层算 ROM 比特（落在 rom_bank 宏里的部分）
# IHP SG13G2（--pdk sg13g2）：层号取自 PDK layers_def，层高为 SG13G2 金属堆叠近似值（展示用）
LAYERS_SG13G2 = [
    ("nwell",     (31, 0),  "#5b4a8a", -0.40, 0.05),
    ("activ",     (1, 0),   "#3fa34d", -0.10, 0.10),
    ("gatpoly",   (5, 0),   "#d63b3b",  0.00, 0.16),
    ("cont",      (6, 0),   "#999999",  0.16, 0.48),
    ("metal1",    (8, 0),   "#4a7fd6",  0.64, 0.42),
    ("via1",      (19, 0),  "#dddddd",  1.06, 0.54),
    ("metal2",    (10, 0),  "#d6a23b",  1.60, 0.49),
    ("via2",      (29, 0),  "#dddddd",  2.09, 0.54),
    ("metal3",    (30, 0),  "#3bc4c4",  2.63, 0.49),
    ("via3",      (49, 0),  "#dddddd",  3.12, 0.54),
    ("metal4",    (50, 0),  "#d65fa0",  3.66, 0.49),
    ("via4",      (66, 0),  "#dddddd",  4.15, 0.54),
    ("metal5",    (67, 0),  "#e8e070",  4.69, 0.49),
    ("topvia1",   (125, 0), "#dddddd",  5.18, 0.85),
    ("topmetal1", (126, 0), "#8fd65f",  6.03, 2.00),
    ("topvia2",   (133, 0), "#dddddd",  8.03, 2.80),
    ("topmetal2", (134, 0), "#ff9a5f", 10.83, 3.00),
]

G = {}

def init(oas):
    ly = db.Layout(); ly.read(oas); top = ly.top_cell()
    G['ly'] = ly; G['top'] = top
    G['li'] = {n: ly.find_layer(l, d) for n, (l, d), *_ in LAYERS}
    rom = db.Region()
    for it in top.each_inst():
        c = ly.cell(it.cell_index)
        if c.name.startswith('rom_bank'):
            rom.insert(it.bbox())
    G['rom'] = rom.merged()

def boxes_of(reg):
    # 曼哈顿多边形 → 水平梯形（即矩形）分解
    out = []
    for p in reg.decompose_trapezoids_to_region(1).each():
        b = p.bbox(); out.append((b.left, b.bottom, b.right, b.top))
    return np.array(out, dtype=np.int64).reshape(-1, 4)

def do_tile(args):
    tx, ty, write = args
    ly, top = G['ly'], G['top']
    T = int(TILE_UM * 1000)
    tb = db.Box(tx * T, ty * T, (tx + 1) * T, (ty + 1) * T)
    tr = db.Region(tb)
    res = {}
    for n, *_ in LAYERS:
        li = G['li'][n]
        if li is None: continue
        it = top.begin_shapes_rec_touching(li, tb)
        reg = db.Region(it).merged() & tr
        if n == ROMVIA:
            bits = reg & G['rom']; reg = reg - G['rom']
            res['rombit'] = boxes_of(bits)
        res[n] = boxes_of(reg)
    ox, oy = tx * T, ty * T
    blob = bytearray(); index = {}
    for n, b in res.items():
        if len(b) == 0: continue
        q = np.empty_like(b)
        q[:, 0::2] = (b[:, 0::2] - ox) // Q_NM
        q[:, 1::2] = (b[:, 1::2] - oy) // Q_NM
        q = np.clip(q, 0, 65535).astype('<u2')
        index[n] = [len(blob), len(b)]
        blob += q.tobytes()
    if write:
        with open(os.path.join(G['out'], 'tiles', f'{tx}_{ty}.bin'), 'wb') as f: f.write(blob)
    # 概览：返回该瓦片的像素覆盖率（每层一张 uint8）
    px = int(round(TILE_UM / OV_UM))
    ov = {}
    for n, b in res.items():
        if len(b) == 0: continue
        cov = np.zeros((px + 1, px + 1), np.float32)
        s = 1000.0 * OV_UM
        x0 = (b[:, 0] - ox) / s; x1 = (b[:, 2] - ox) / s
        y0 = (b[:, 1] - oy) / s; y1 = (b[:, 3] - oy) / s
        big = ((x1 - x0) >= 1) | ((y1 - y0) >= 1)
        # 大矩形：差分数组按整像素覆盖
        bx0 = np.clip(np.round(x0[big]).astype(int), 0, px); bx1 = np.clip(np.round(x1[big]).astype(int), 0, px)
        by0 = np.clip(np.round(y0[big]).astype(int), 0, px); by1 = np.clip(np.round(y1[big]).astype(int), 0, px)
        D = np.zeros((px + 2, px + 2), np.float32)
        np.add.at(D, (by0, bx0), 1); np.add.at(D, (by0, bx1), -1)
        np.add.at(D, (by1, bx0), -1); np.add.at(D, (by1, bx1), 1)
        cov += np.cumsum(np.cumsum(D, 0), 1)[:px + 1, :px + 1]
        # 小矩形：按面积加到中心像素
        sm = ~big
        cx = np.clip(((x0[sm] + x1[sm]) / 2).astype(int), 0, px)
        cy = np.clip(((y0[sm] + y1[sm]) / 2).astype(int), 0, px)
        np.add.at(cov, (cy, cx), (x1[sm] - x0[sm]) * (y1[sm] - y0[sm]))
        ov[n] = (np.clip(cov[:px, :px], 0, 1) * 255).astype(np.uint8)
    counts = {n: int(len(b)) for n, b in res.items()}
    return tx, ty, index, counts, ov

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('oas'); ap.add_argument('out')
    ap.add_argument('--tiles', default=None, help='tx0,ty0,tx1,ty1 含端点，只给这些瓦片写 .bin')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--pdk', default='sky130', choices=['sky130', 'sg13g2'])
    a = ap.parse_args()
    global LAYERS, ROMVIA, ROMBIT
    if a.pdk == 'sg13g2':
        LAYERS = LAYERS_SG13G2; ROMVIA = 'via1'; ROMBIT = ("rombit", None, "#ff3030", 1.06, 0.54)
    os.makedirs(os.path.join(a.out, 'tiles'), exist_ok=True)
    G['out'] = a.out
    t0 = time.time()
    init(a.oas)
    bb = G['top'].bbox()
    T = int(TILE_UM * 1000)
    nx = -(-bb.right // T); ny = -(-bb.top // T)
    sel = None
    if a.tiles:
        x0, y0, x1, y1 = map(int, a.tiles.split(','))
        sel = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1
    jobs = [(x, y, sel is None or sel(x, y)) for y in range(ny) for x in range(nx)]
    px = int(round(TILE_UM / OV_UM))
    W, H = nx * px, ny * px
    names = [l[0] for l in LAYERS] + ['rombit']
    ovs = {n: np.zeros((H, W), np.uint8) for n in names}
    tiles = {}; total = {}
    with Pool(a.jobs, initializer=init, initargs=(a.oas,)) as pool:
        for i, (tx, ty, index, counts, ov) in enumerate(pool.imap_unordered(do_tile, jobs)):
            for n, c in counts.items(): total[n] = total.get(n, 0) + c
            if index and (sel is None or sel(tx, ty)): tiles[f'{tx}_{ty}'] = index
            for n, img in ov.items():
                ovs[n][H - (ty + 1) * px: H - ty * px, tx * px:(tx + 1) * px] = img[::-1]
            if i % 20 == 0: print(f'{i+1}/{len(jobs)} {time.time()-t0:.0f}s', flush=True)
    from PIL import Image
    os.makedirs(os.path.join(a.out, 'ov'), exist_ok=True)
    cw = int(round(bb.right / 1000 / OV_UM)); ch = int(round(bb.top / 1000 / OV_UM))
    for n in names:
        im = ovs[n][H - ch:, :cw]
        if im.max() == 0: continue
        Image.fromarray(im, 'L').save(os.path.join(a.out, 'ov', f'{n}.png'), optimize=True)
    man = dict(dbu_nm=1, q_nm=Q_NM, tile_um=TILE_UM, ov_um=OV_UM, nx=nx, ny=ny,
               chip_um=[bb.right / 1000, bb.top / 1000], ov_px=[cw, ch],
               layers=[dict(name=n, gds=list(g) if g else None, color=c, z=z, t=t) for n, g, c, z, t in LAYERS + [ROMBIT]],
               ov_layers=[n for n in names if os.path.exists(os.path.join(a.out, 'ov', f'{n}.png'))],
               rom=[[b.left / 1000, b.bottom / 1000, b.right / 1000, b.top / 1000] for b in G['rom'].each()] if False else None,
               totals=total, tiles=tiles)
    json.dump(man, open(os.path.join(a.out, 'manifest.json'), 'w'))
    print('done', f'{time.time()-t0:.0f}s', json.dumps(total))

if __name__ == '__main__':
    main()
