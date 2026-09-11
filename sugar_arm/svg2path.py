#!/usr/bin/env python3
"""
SVG -> 單線筆劃路徑 (畫糖人用)。只需要 numpy (+ pillow 出預覽)。

每個 <path> = 一筆不抬手。流程:
  貝茲離散化 -> RDP 去冗餘 -> Chaikin 圓角 -> 等弧長重取樣 -> mm 座標

用法:
  python svg2path.py corgi.svg -o path.json --width 120 --step 1.5 --preview p.png
"""
import argparse, json, math, re, sys
import numpy as np

NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
CMD = re.compile(r"([MmLlHhVvCcSsQqTtZz])")

# ---------- 1. 解析 SVG path ----------
def flatten_cubic(p0, p1, p2, p3, n):
    t = np.linspace(0, 1, n)[1:, None]
    return ((1-t)**3 * p0 + 3*(1-t)**2*t * p1 + 3*(1-t)*t**2 * p2 + t**3 * p3)

def flatten_quad(p0, p1, p2, n):
    t = np.linspace(0, 1, n)[1:, None]
    return ((1-t)**2 * p0 + 2*(1-t)*t * p1 + t**2 * p2)

def seg_n(pts, tol=0.4):
    """依控制多邊形長度決定離散點數"""
    L = sum(float(np.hypot(*(pts[i+1] - pts[i]))) for i in range(len(pts)-1))
    return int(min(max(math.ceil(L / max(tol, 1e-6)), 4), 400))

def parse_d(d):
    """回傳 list of polyline (Nx2 ndarray),單位為 SVG user unit"""
    toks = [t for t in CMD.split(d) if t.strip()]
    subs, cur = [], []
    P = np.zeros(2)          # 目前點
    start = np.zeros(2)
    prev_c = None            # 上一個三次控制點 (給 S)
    prev_q = None            # 上一個二次控制點 (給 T)
    i = 0
    while i < len(toks):
        c = toks[i]; i += 1
        args = []
        if i < len(toks) and not CMD.fullmatch(toks[i]):
            args = [float(x) for x in NUM.findall(toks[i])]; i += 1
        rel = c.islower(); C = c.upper()

        def emit(p):
            nonlocal P
            cur.append(p.copy()); P = p.copy()

        if C == "M":
            for k in range(0, len(args), 2):
                p = np.array(args[k:k+2], float)
                p = P + p if (rel and (k or cur)) else p
                if k == 0:
                    if len(cur) > 1: subs.append(np.array(cur))
                    cur = [p.copy()]; P = p.copy(); start = p.copy()
                else:
                    emit(p)
            prev_c = prev_q = None
        elif C == "L":
            for k in range(0, len(args), 2):
                p = np.array(args[k:k+2], float)
                emit(P + p if rel else p)
            prev_c = prev_q = None
        elif C in "HV":
            for a in args:
                p = P.copy()
                j = 0 if C == "H" else 1
                p[j] = P[j] + a if rel else a
                emit(p)
            prev_c = prev_q = None
        elif C == "C":
            for k in range(0, len(args), 6):
                a = np.array(args[k:k+6], float).reshape(3, 2)
                if rel: a = a + P
                pts = np.vstack([P, a])
                for q in flatten_cubic(P, a[0], a[1], a[2], seg_n(pts)):
                    cur.append(q)
                P = a[2].copy(); prev_c = a[1].copy(); prev_q = None
        elif C == "S":
            for k in range(0, len(args), 4):
                a = np.array(args[k:k+4], float).reshape(2, 2)
                if rel: a = a + P
                c1 = 2*P - prev_c if prev_c is not None else P.copy()
                pts = np.vstack([P, c1, a])
                for q in flatten_cubic(P, c1, a[0], a[1], seg_n(pts)):
                    cur.append(q)
                P = a[1].copy(); prev_c = a[0].copy(); prev_q = None
        elif C == "Q":
            for k in range(0, len(args), 4):
                a = np.array(args[k:k+4], float).reshape(2, 2)
                if rel: a = a + P
                pts = np.vstack([P, a])
                for q in flatten_quad(P, a[0], a[1], seg_n(pts)):
                    cur.append(q)
                P = a[1].copy(); prev_q = a[0].copy(); prev_c = None
        elif C == "T":
            for k in range(0, len(args), 2):
                a = np.array(args[k:k+2], float)
                if rel: a = a + P
                c1 = 2*P - prev_q if prev_q is not None else P.copy()
                pts = np.vstack([P, c1, a])
                for q in flatten_quad(P, c1, a, seg_n(pts)):
                    cur.append(q)
                P = a.copy(); prev_q = c1.copy(); prev_c = None
        elif C == "Z":
            if cur:
                cur.append(start.copy()); P = start.copy()
            prev_c = prev_q = None
    if len(cur) > 1:
        subs.append(np.array(cur))
    return subs

def parse_svg(text):
    if re.search(r'\btransform\s*=', text):
        print("警告: SVG 含 transform,本程式不套用,請先把 transform 烘進座標", file=sys.stderr)
    if re.search(r'[Aa]\s*[-\d.]', "".join(re.findall(r'\bd\s*=\s*"([^"]*)"', text))):
        print("警告: path 含圓弧 A 指令,本程式不支援,請改用 C 曲線", file=sys.stderr)
    out = []
    for d in re.findall(r'\bd\s*=\s*"([^"]*)"', text):
        out += parse_d(d)
    return out

# ---------- 2. 幾何處理 ----------
def rdp(pts, eps):
    pts = np.asarray(pts, float)
    if len(pts) < 3 or eps <= 0:
        return pts
    keep = np.zeros(len(pts), bool); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1: continue
        a, b = pts[i], pts[j]; ab = b - a
        L = float(np.hypot(*ab)); seg = pts[i+1:j] - a
        d = (np.abs(ab[0]*seg[:,1] - ab[1]*seg[:,0]) / L) if L > 1e-9 \
            else np.hypot(seg[:,0], seg[:,1])
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k; keep[m] = True; stack += [(i, m), (m, j)]
    return pts[keep]

def chaikin(pts, it=2):
    """轉角切成小圓角,端點保留。糖線最怕尖角(手臂會減速->積糖)"""
    pts = np.asarray(pts, float)
    closed = bool(np.allclose(pts[0], pts[-1]))
    for _ in range(it):
        if len(pts) < 3: break
        a, b = pts[:-1], pts[1:]
        q = a + 0.25 * (b - a)
        r = a + 0.75 * (b - a)
        new = np.empty((len(q) + len(r), 2))
        new[0::2], new[1::2] = q, r
        pts = np.vstack([pts[0], new, pts[-1]]) if not closed \
              else np.vstack([new, new[0]])
    return pts

def resample(pts, step):
    """等弧長重取樣 -> 手臂能等速走,線寬才會均勻"""
    pts = np.asarray(pts, float)
    d = np.hypot(*(pts[1:] - pts[:-1]).T)
    s = np.concatenate([[0], np.cumsum(d)])
    if s[-1] < 1e-9 or step <= 0:
        return pts
    n = max(int(round(s[-1] / step)), 1)
    t = np.linspace(0, s[-1], n + 1)
    return np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])

def order_strokes(ss):
    remain = [np.asarray(s, float) for s in ss]
    out = [remain.pop(0)]
    while remain:
        cur = out[-1][-1]
        best, bi, brev = 1e18, 0, False
        for i, s in enumerate(remain):
            d0 = float(np.hypot(*(s[0]-cur))); d1 = float(np.hypot(*(s[-1]-cur)))
            if d0 < best: best, bi, brev = d0, i, False
            if d1 < best: best, bi, brev = d1, i, True
        s = remain.pop(bi)
        out.append(s[::-1] if brev else s)
    return out

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("-o", "--out", default="path.json")
    ap.add_argument("--width", type=float, default=120.0, help="成品寬度 mm")
    ap.add_argument("--step", type=float, default=1.5, help="等弧長取樣間距 mm")
    ap.add_argument("--eps", type=float, default=0.3, help="RDP 容差 (SVG unit)")
    ap.add_argument("--smooth", type=int, default=2, help="Chaikin 圓角次數")
    ap.add_argument("--min-len", type=float, default=3.0, help="丟掉短於此 mm 的碎筆")
    ap.add_argument("--preview", default=None)
    v = ap.parse_args()

    subs = parse_svg(open(v.svg, encoding="utf-8").read())
    if not subs:
        sys.exit("SVG 裡沒抓到 path d=\"...\"")

    subs = [chaikin(rdp(s, v.eps), v.smooth) for s in subs]
    subs = order_strokes(subs)

    allp = np.vstack(subs)
    x0, y0 = allp.min(0); x1, y1 = allp.max(0)
    scale = v.width / max(x1 - x0, 1e-9)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mmst = [np.column_stack([(s[:,0]-cx)*scale, (cy-s[:,1])*scale]) for s in subs]
    mmst = [resample(s, v.step) for s in mmst]
    mmst = [s for s in mmst
            if np.hypot(*(s[1:]-s[:-1]).T).sum() >= v.min_len]
    if not mmst:
        sys.exit("全部筆劃都太短,調小 --min-len")

    mm = [[[round(float(p[0]),3), round(float(p[1]),3)] for p in s] for s in mmst]
    draw = sum(math.dist(s[k-1], s[k]) for s in mm for k in range(1, len(s)))
    travel = sum(math.dist(mm[i-1][-1], mm[i][0]) for i in range(1, len(mm)))
    h_mm = round(float((y1-y0)*scale), 2)

    json.dump({"units":"mm", "size_mm":[round(v.width,2), h_mm],
               "n_strokes":len(mm), "n_points":sum(len(s) for s in mm),
               "draw_len_mm":round(draw,1), "travel_len_mm":round(travel,1),
               "strokes":mm}, open(v.out,"w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"筆劃 {len(mm)} 條 / 點 {sum(len(s) for s in mm)} 個 / 抬筆 {len(mm)-1} 次")
    print(f"成品 {v.width:.0f} x {h_mm:.0f} mm,畫線 {draw:.0f} mm,空走 {travel:.0f} mm")
    est = draw / 80 + travel / 300 + len(mm) * 0.5
    print(f"預估 @80mm/s: 約 {est:.0f} 秒 -> {v.out}")

    if v.preview:
        from PIL import Image, ImageDraw
        W = 900; H = max(int(W * h_mm / v.width), 60)
        im = Image.new("RGB", (W+40, H+40), "white"); d = ImageDraw.Draw(im)
        def px(p): return (int((p[0]/v.width + 0.5)*W)+20,
                           int((0.5 - p[1]/h_mm)*H)+20)
        for i, s in enumerate(mm):
            if i: d.line([px(mm[i-1][-1]), px(s[0])], fill=(215,215,255), width=1)
            d.line([px(p) for p in s], fill=(20,20,20), width=4, joint="curve")
        im.save(v.preview)
        print(f"預覽 -> {v.preview}  (粗黑=出糖畫線,淡藍=抬筆空走)")

if __name__ == "__main__":
    main()
