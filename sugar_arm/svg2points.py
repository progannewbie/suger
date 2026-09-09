#!/usr/bin/env python3
"""
SVG -> 手臂平面座標點位 (Skill 2)。只需要 numpy。

讀 Skill 1 產出的 SVG(只讀 data-role="path" 那層),輸出手臂座標 JSON。

核心問題:多少個點才夠?
  太少 -> 圓弧變多邊形,手臂在每個折點減速,留下一串等距糖疤
  太多 -> 封包爆量、控制器來不及、AS 程式行數失控

判準:**取樣誤差小於糖線寬的四分之一就看不出來** —— 比糖線細的偏差
會被糖本身蓋掉。所以容差綁在糖線寬上,而不是憑感覺給一個點數。

做法是曲率自適應:直線段自動變稀,曲線段自動變密。
再強制保留「速度會變」的點,否則毛筆的粗細變化會被抽掉。

用法:
  python svg2points.py in.svg -o points.json --report
"""
import argparse, json, math, os, re, sys
import numpy as np

NS = "{http://www.w3.org/2000/svg}"

# ---------- 1. 讀 SVG ----------
def parse_svg(path):
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    vb = (root.get("viewBox") or "").split()
    if len(vb) != 4:
        sys.exit("SVG 缺少 viewBox,無法決定尺寸")
    W, H = float(vb[2]), float(vb[3])

    meta = {}
    for e in root.iter():
        if e.tag.startswith(NS + "sugar-"):
            meta[e.tag[len(NS) + 6:]] = (e.text or "").strip()

    groups = [g for g in root.iter(NS + "g") if g.get("data-role") == "path"]
    if not groups:
        sys.exit('SVG 裡找不到 data-role="path" 那層 —— 這不是 Skill 1 的產出?')

    strokes, widths = [], []
    for g in groups:
        for p in g.iter(NS + "path"):
            pts = parse_d(p.get("d") or "")
            if len(pts) < 2:
                continue
            w = p.get("data-widths")
            w = (np.array([float(x) for x in w.split(",")], float)
                 if w else np.ones(len(pts)))
            if len(w) != len(pts):                 # 長度不符就內插補齊
                w = np.interp(np.linspace(0, 1, len(pts)),
                              np.linspace(0, 1, len(w)), w)
            strokes.append(pts); widths.append(w)
    return strokes, widths, W, H, meta

NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")

def parse_d(d):
    """Skill 1 只產 M/L,不用支援曲線指令"""
    if not re.fullmatch(r"[\sMLml0-9.eE+-]*", d):
        sys.exit("path 含非 M/L 指令,請確認來源是 Skill 1 的輸出")
    n = [float(x) for x in NUM.findall(d)]
    return np.array(n, float).reshape(-1, 2) if len(n) >= 4 else np.zeros((0, 2))

# ---------- 2. SVG 座標 -> 手臂平面座標 ----------
def to_arm(pts, W, H):
    """SVG 是左上原點、y 向下;手臂平面是中心原點、y 向上"""
    out = np.empty_like(pts)
    out[:, 0] = pts[:, 0] - W / 2
    out[:, 1] = H / 2 - pts[:, 1]
    return out

# ---------- 3. 曲率自適應取樣 ----------
def rdp_mask(pts, tol):
    """回傳要保留的索引遮罩。RDP 在給定容差下的點數已接近最少。"""
    n = len(pts)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    if n < 3 or tol <= 0:
        keep[:] = True
        return keep
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        L = float(np.hypot(*ab))
        seg = pts[i+1:j] - a
        dist = (np.abs(ab[0]*seg[:, 1] - ab[1]*seg[:, 0]) / L) if L > 1e-9 \
               else np.hypot(seg[:, 0], seg[:, 1])
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return keep

def speed_levels(w, vdraw, vmax, quant):
    """粗細換算成量化後的速度。糖流量固定 -> 線寬 x 速度 = 常數"""
    v = np.clip(vdraw / np.maximum(w, 1e-3), vdraw, vmax)
    return np.round(v / quant) * quant

def thin(pts, w, tol, max_seg, min_seg, lv):
    """RDP 保幾何 + 強制保留速度變化點 + 長段再切"""
    keep = rdp_mask(pts, tol)
    keep[1:] |= (lv[1:] != lv[:-1])            # 速度換檔的點一定要留

    # 太長的段補點,讓速度變化有地方施展、也避免長直線走太久沒校正
    idx = list(np.nonzero(keep)[0])
    extra = []
    for a, b in zip(idx[:-1], idx[1:]):
        L = float(np.hypot(*(pts[b] - pts[a])))
        if L > max_seg:
            n = int(L // max_seg)
            step = max((b - a) // (n + 1), 1)
            extra += list(range(a + step, b, step))
    for i in extra:
        keep[i] = True

    # 過近的點刪掉(手臂走不出來,只是灌封包)
    idx = list(np.nonzero(keep)[0])
    out = [idx[0]]
    for i in idx[1:-1]:
        if float(np.hypot(*(pts[i] - pts[out[-1]]))) >= min_seg:
            out.append(i)
    out.append(idx[-1])
    return np.array(sorted(set(out)))

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("-o", "--out", default="points.json")
    ap.add_argument("--bead", type=float, default=0,
                    help="糖線寬 mm,0=讀 SVG metadata")
    ap.add_argument("--tol", type=float, default=0,
                    help="取樣容差 mm,0=糖線寬/4")
    ap.add_argument("--max-seg", type=float, default=8.0, help="單段長度上限 mm")
    ap.add_argument("--min-seg", type=float, default=0.4, help="單段長度下限 mm")
    ap.add_argument("--draw-speed", type=float, default=60.0, help="基準畫線速度 mm/s")
    ap.add_argument("--max-speed", type=float, default=0, help="0=畫線速度的 4 倍")
    ap.add_argument("--speed-quant", type=float, default=20.0, help="速度量化級距 mm/s")
    ap.add_argument("--report", action="store_true", help="印出每筆的精簡前後")
    v = ap.parse_args()

    strokes, widths, W, H, meta = parse_svg(v.svg)
    bead = v.bead or float(meta.get("bead_mm", 2.5))
    tol = v.tol or bead / 4
    vmax = v.max_speed or v.draw_speed * 4

    out_s, out_w, n0 = [], [], 0
    for i, (p, w) in enumerate(zip(strokes, widths)):
        n0 += len(p)
        a = to_arm(p, W, H)
        lv = speed_levels(w, v.draw_speed, vmax, v.speed_quant)
        idx = thin(a, w, tol, v.max_seg, v.min_seg, lv)
        out_s.append(a[idx]); out_w.append(w[idx])
        if v.report:
            print(f"  筆劃 {i+1}: {len(p)} -> {len(idx)} 點 "
                  f"({100*len(idx)/max(len(p),1):.0f}%)")

    n1 = sum(len(s) for s in out_s)
    draw = sum(float(np.hypot(*(s[1:]-s[:-1]).T).sum()) for s in out_s)
    travel = sum(float(np.hypot(*(out_s[i][0]-out_s[i-1][-1])))
                 for i in range(1, len(out_s)))

    json.dump({"units": "mm", "size_mm": [round(W, 2), round(H, 2)],
               "source_svg": os.path.basename(v.svg),
               "tolerance_mm": round(tol, 3), "bead_mm": bead,
               "n_strokes": len(out_s), "n_points": n1, "n_dots": 0, "dots": [],
               "draw_len_mm": round(draw, 1), "travel_len_mm": round(travel, 1),
               "widths": [[round(float(x), 3) for x in w] for w in out_w],
               "strokes": [[[round(float(p[0]), 3), round(float(p[1]), 3)]
                            for p in s] for s in out_s]},
              open(v.out, "w"), ensure_ascii=False, indent=1)

    print(f"容差 {tol:.2f} mm (糖線寬 {bead} mm 的 1/4)")
    print(f"點位 {n0} -> {n1}  省 {100*(1-n1/max(n0,1)):.0f}%")
    print(f"筆劃 {len(out_s)} 條,畫線 {draw:.0f} mm,空走 {travel:.0f} mm -> {v.out}")

if __name__ == "__main__":
    main()
