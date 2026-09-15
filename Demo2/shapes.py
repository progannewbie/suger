#!/usr/bin/env python3
"""基礎圖形產生器 —— 直接產生手臂座標,取代 Skill 1 + Skill 2。

三種圖形都是一筆到底、不抬筆、不斷流,給固定傾角的茶壺治具用。
輸出格式與 svg2points.py 相同,可直接餵給 plan.py。

    python3 shapes.py spiral -o points.json --preview spiral.png
    python3 shapes.py wave   -o points.json
    python3 shapes.py star   -o points.json

固定傾角 = 糖流量固定 = 線寬固定 = 速度固定,所以 widths 全部為 1.0。
"""
import argparse, json, math, os
import numpy as np

# ---------- 1. 三種圖形的參數式 ----------

def spiral(r0, r1, turns):
    """阿基米德螺旋,由內往外。

    由內往外畫是刻意的:結束時把壺轉正斷流,壺嘴一定會滴糖。
    由外往內畫會滴在正中心最顯眼處;由內往外結束在外圈,可以順勢拉出圖外。
    """
    def f(t):                                   # t: 0..1
        th = t * turns * 2 * math.pi
        r = r0 + (r1 - r0) * t
        return r * np.cos(th), r * np.sin(th)
    return f


def wave(width, height, waves):
    """蛇行波浪。曲率一直換向,用來測轉折處糖會不會積厚。"""
    def f(t):
        x = (t - 0.5) * width
        y = (height / 2) * np.sin(t * waves * 2 * math.pi)
        return x, y
    return f


def star_points(radius, n=5, step=2):
    """五芒星頂點,一筆畫順序(每次跳 2 個頂點),回到起點閉合。"""
    vs = [(radius * math.sin(2 * math.pi * k / n),
           radius * math.cos(2 * math.pi * k / n)) for k in range(n)]
    order = [(i * step) % n for i in range(n)] + [0]
    return np.array([vs[i] for i in order], dtype=float)


# ---------- 2. 曲率自適應取樣 ----------

def sample_curve(f, tol, max_seg, min_seg, dense=8000):
    """沿弧長行走,步長由局部曲率決定。

    弦長 s 對半徑 R 的圓,弓高約 s^2/(8R)。令弓高 = tol 得 s = sqrt(8*R*tol),
    直線段 R 無限大 -> 取 max_seg,急彎 -> 收斂到 min_seg。
    """
    t = np.linspace(0, 1, dense)
    x, y = f(t)
    x = np.asarray(x, float); y = np.asarray(y, float)

    d1x, d1y = np.gradient(x), np.gradient(y)
    d2x, d2y = np.gradient(d1x), np.gradient(d1y)
    num = np.abs(d1x * d2y - d1y * d2x)
    den = (d1x ** 2 + d1y ** 2) ** 1.5
    with np.errstate(divide="ignore", invalid="ignore"):
        R = np.where(num > 1e-12, den / np.maximum(num, 1e-12), np.inf)

    seg = np.clip(np.sqrt(8 * np.maximum(R, 0) * tol), min_seg, max_seg)

    ds = np.hypot(np.diff(x), np.diff(y))
    s = np.concatenate([[0.0], np.cumsum(ds)])
    total = s[-1]

    idx, pos = [0], 0.0
    while pos < total:
        j = int(np.searchsorted(s, pos))
        pos += float(seg[min(j, len(seg) - 1)])
        if pos >= total:
            break
        idx.append(int(np.searchsorted(s, pos)))
    idx.append(len(x) - 1)
    idx = sorted(set(idx))
    return np.stack([x[idx], y[idx]], axis=1)


def sample_polyline(verts, max_seg):
    """折線:頂點一定保留,長邊再切。用於五芒星。"""
    out = [verts[0]]
    for a, b in zip(verts[:-1], verts[1:]):
        L = float(np.hypot(*(b - a)))
        n = max(1, int(math.ceil(L / max_seg)))
        for k in range(1, n + 1):
            out.append(a + (b - a) * (k / n))
    return np.array(out)


def add_tail(pts, tail):
    """沿終點切線方向延伸一小段,讓斷流時的滴糖落在圖外。"""
    if tail <= 0 or len(pts) < 2:
        return pts
    v = pts[-1] - pts[-2]
    L = float(np.hypot(*v))
    if L < 1e-9:
        return pts
    return np.vstack([pts, pts[-1] + v / L * tail])


# ---------- 3. 輸出 ----------

def write_json(path, pts, shape, speed, tail):
    draw = float(np.hypot(*np.diff(pts, axis=0).T).sum())
    W = float(pts[:, 0].max() - pts[:, 0].min())
    H = float(pts[:, 1].max() - pts[:, 1].min())
    json.dump({"units": "mm", "size_mm": [round(W, 2), round(H, 2)],
               "source_shape": shape, "tail_mm": tail,
               "tolerance_mm": None, "bead_mm": 0,
               "n_strokes": 1, "n_points": len(pts), "n_dots": 0, "dots": [],
               "draw_len_mm": round(draw, 1), "travel_len_mm": 0.0,
               "widths": [[1.0] * len(pts)],
               "strokes": [[[round(float(p[0]), 3), round(float(p[1]), 3)]
                            for p in pts]]},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return draw, W, H


def preview(path, pts, size=900, margin=40):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (size, size), "white")
    dr = ImageDraw.Draw(im)
    lo = pts.min(axis=0); hi = pts.max(axis=0)
    span = max(float((hi - lo).max()), 1e-6)
    k = (size - 2 * margin) / span
    cx, cy = (lo + hi) / 2

    def px(p):
        return (size / 2 + (p[0] - cx) * k, size / 2 - (p[1] - cy) * k)

    dr.line([px(p) for p in pts], fill=(40, 70, 55), width=6, joint="curve")
    for p in pts:
        x, y = px(p)
        dr.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(200, 60, 60))
    x, y = px(pts[0]); dr.ellipse([x - 7, y - 7, x + 7, y + 7], outline=(0, 120, 220), width=3)
    x, y = px(pts[-1]); dr.ellipse([x - 7, y - 7, x + 7, y + 7], outline=(220, 120, 0), width=3)
    im.save(path)


# ---------- 4. CLI ----------

def main():
    ap = argparse.ArgumentParser(description="基礎圖形 -> 手臂座標 points.json")
    ap.add_argument("shape", choices=["spiral", "wave", "star"])
    ap.add_argument("-o", "--out", default="points.json")
    ap.add_argument("--preview", default=None, help="輸出預覽 PNG")

    ap.add_argument("--tol", type=float, default=0.3, help="取樣容差 mm")
    ap.add_argument("--max-seg", type=float, default=8.0, help="單段長度上限 mm")
    ap.add_argument("--min-seg", type=float, default=0.8, help="單段長度下限 mm")
    ap.add_argument("--tail", type=float, default=10.0,
                    help="收尾沿切線外拉長度 mm,讓斷流滴糖落在圖外")

    ap.add_argument("--speed", type=float, default=60.0, help="畫線速度 mm/s")
    ap.add_argument("--limit", type=float, default=45.0,
                    help="畫圖時間上限 s;茶壺離爐約 60 s 就會凝固")

    ap.add_argument("--r0", type=float, default=8.0, help="螺旋內半徑 mm")
    ap.add_argument("--r1", type=float, default=50.0, help="螺旋外半徑 mm")
    ap.add_argument("--turns", type=float, default=5.0, help="螺旋圈數")

    ap.add_argument("--width", type=float, default=100.0, help="波浪寬 mm")
    ap.add_argument("--height", type=float, default=60.0, help="波浪高 mm")
    ap.add_argument("--waves", type=float, default=4.0, help="波浪週期數")

    ap.add_argument("--radius", type=float, default=50.0, help="五芒星外接圓半徑 mm")
    v = ap.parse_args()

    if v.shape == "spiral":
        pts = sample_curve(spiral(v.r0, v.r1, v.turns), v.tol, v.max_seg, v.min_seg)
        desc = f"內徑 {v.r0} 外徑 {v.r1} {v.turns:g} 圈,線距 {2*(v.r1-v.r0)/v.turns/2:.1f} mm"
    elif v.shape == "wave":
        pts = sample_curve(wave(v.width, v.height, v.waves), v.tol, v.max_seg, v.min_seg)
        desc = f"寬 {v.width} 高 {v.height} {v.waves:g} 個週期"
    else:
        pts = sample_polyline(star_points(v.radius), v.max_seg)
        desc = f"外接圓半徑 {v.radius}"

    pts = add_tail(pts, v.tail)
    draw, W, H = write_json(v.out, pts, v.shape, v.speed, v.tail)
    t = draw / v.speed

    print(f"{v.shape}: {desc}")
    print(f"範圍 {W:.1f} x {H:.1f} mm,路徑長 {draw:.0f} mm,{len(pts)} 點 -> {v.out}")
    print(f"預估畫圖時間 {t:.1f} s (速度 {v.speed:g} mm/s)")
    if t > v.limit:
        print(f"*** 超過上限 {v.limit:g} s —— 糖會在畫完前凝固。")
        print(f"    把速度提到 {draw/v.limit:.0f} mm/s 以上,或把圖縮小。")
    else:
        print(f"    上限 {v.limit:g} s,還剩 {v.limit - t:.1f} s 緩衝。")

    if v.preview:
        preview(v.preview, pts)
        print(f"預覽 -> {v.preview}  (藍圈=起點 橘圈=終點 紅點=實際送出的座標)")


if __name__ == "__main__":
    main()
