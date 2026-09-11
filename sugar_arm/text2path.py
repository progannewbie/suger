#!/usr/bin/env python3
"""
中文字串 -> 筆劃路徑 (畫糖人用)。只需要 numpy (+ pillow 出預覽)。

資料來源:makemeahanzi 的 graphics.txt,每個漢字附「每一筆的中心線 median」
和正確筆順。所以完全不用描圖、不用骨架化 —— 拿到就是筆劃。

用法:
  python text2path.py 新年快樂 -o path.json --size 40 --cols 2 --preview p.png
"""
import argparse, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "graphics.txt")
EM = 1024.0          # 字身框
BASE = -124.0        # 字面下緣(makemeahanzi 慣例)

# ---------- 1. 查字 ----------
def load_chars(text, path=DATA):
    """只解析需要的那幾行,不整份 30MB 解析"""
    want = {c for c in text if not c.isspace()}
    out = {}
    if not os.path.exists(path):
        sys.exit(f"找不到字形資料 {path}\n"
                 f"下載: curl -o {path} https://raw.githubusercontent.com/"
                 f"skishore/makemeahanzi/master/graphics.txt")
    with open(path, encoding="utf-8") as f:
        for line in f:
            for c in want:
                if f'"character":"{c}"' in line:
                    d = json.loads(line)
                    out[d["character"]] = d["medians"]
                    break
            if len(out) == len(want):
                break
    return out

# ---------- 2. 平滑 + 重取樣 ----------
def catmull(pts, per_seg=10):
    """median 只有 3~12 點,直接連是折線。用 Catmull-Rom 補成順的曲線。"""
    p = np.asarray(pts, float)
    if len(p) < 3:
        return p
    P = np.vstack([p[0], p, p[-1]])
    out = []
    for i in range(len(P) - 3):
        p0, p1, p2, p3 = P[i], P[i+1], P[i+2], P[i+3]
        t = np.linspace(0, 1, per_seg, endpoint=False)[:, None]
        out.append(0.5 * (2*p1 + (-p0+p2)*t
                          + (2*p0-5*p1+4*p2-p3)*t**2
                          + (-p0+3*p1-3*p2+p3)*t**3))
    out.append(P[-2:-1])
    return np.vstack(out)

def resample(pts, step):
    pts = np.asarray(pts, float)
    d = np.hypot(*(pts[1:] - pts[:-1]).T)
    s = np.concatenate([[0], np.cumsum(d)])
    if s[-1] < 1e-9 or step <= 0:
        return pts
    n = max(int(round(s[-1] / step)), 1)
    t = np.linspace(0, s[-1], n + 1)
    return np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])

# ---------- 3. 毛筆粗細 + 牽絲 ----------
def brush_profile(n, head=0.12, tail=0.28, w0=1.00, wm=0.78, w1=0.30):
    """一筆之內的粗細變化。糖流量固定,粗細靠速度控制,所以這其實是速度曲線的倒數。

    起筆頓一下(最粗) -> 行筆平穩 -> 收筆提速出鋒(最細)
    """
    t = np.linspace(0, 1, max(n, 2))
    w = np.full(len(t), wm)
    h = t < head
    w[h] = w0 + (wm - w0) * (t[h] / max(head, 1e-9))
    q = t > 1 - tail
    u = (t[q] - (1 - tail)) / max(tail, 1e-9)
    w[q] = wm + (w1 - wm) * u**1.5
    return w[:n] if n >= 2 else np.array([wm])

def curvature(pts):
    """每點的曲率 rad/mm。等弧長取樣過,所以轉角除以弧長就是曲率。"""
    p = np.asarray(pts, float)
    n = len(p)
    if n < 3:
        return np.zeros(n)
    d = p[1:] - p[:-1]
    L = np.hypot(d[:, 0], d[:, 1]) + 1e-9
    u = d / L[:, None]
    ang = np.arccos(np.clip((u[:-1] * u[1:]).sum(1), -1.0, 1.0))
    k = np.zeros(n)
    k[1:-1] = ang / np.maximum((L[:-1] + L[1:]) / 2, 1e-9)
    return k

def smooth1d(a, win):
    """頓筆是鋪開幾 mm 的,不是單點,所以曲率要先抹開"""
    if win < 3 or len(a) < 3:
        return a
    win = min(win | 1, len(a) if len(a) % 2 else len(a) - 1)
    if win < 3:
        return a
    pad = win // 2
    return np.convolve(np.pad(a, pad, mode="edge"), np.ones(win)/win, mode="valid")

def silk(a, b, step, bow=0.18):
    """牽絲:兩筆之間的連接線。微微外弧,比直線像手寫。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = float(np.hypot(*(b - a)))
    if L < 1e-6:
        return None
    nrm = np.array([-(b-a)[1], (b-a)[0]]) / L
    ctrl = (a + b) / 2 + nrm * L * bow
    n = max(int(L / max(step, 1e-6)), 4)
    t = np.linspace(0, 1, n + 1)[:, None]
    return (1-t)**2 * a + 2*(1-t)*t * ctrl + t**2 * b

from svgout import write_svg

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="要寫的字,例如 新年快樂。用 / 換行")
    ap.add_argument("-o", "--out", default="path.json")
    ap.add_argument("--size", type=float, default=40.0, help="字高 mm")
    ap.add_argument("--cols", type=int, default=0, help="每行幾字,0=不自動換行")
    ap.add_argument("--gap", type=float, default=0.15, help="字距,字高的倍數")
    ap.add_argument("--line-gap", type=float, default=0.30, help="行距,字高的倍數")
    ap.add_argument("--vertical", action="store_true", help="直式書寫(由上往下)")
    ap.add_argument("--step", type=float, default=1.2, help="等弧長取樣間距 mm")
    ap.add_argument("--bead", type=float, default=2.5, help="糖線寬 mm,只影響預覽")
    ap.add_argument("--brush", action="store_true",
                    help="毛筆粗細:起筆頓、收筆出鋒(靠速度控制)")
    ap.add_argument("--link", choices=["none", "char", "all"], default="none",
                    help="牽絲連接範圍。char=字內連筆(行書) all=整幅一線到底")
    ap.add_argument("--silk-width", type=float, default=0.28, help="牽絲相對粗細")
    ap.add_argument("--curve-gain", type=float, default=0.80,
                    help="轉折處加粗的強度(模擬頓筆)。0=關閉")
    ap.add_argument("--w-max", type=float, default=1.20, help="粗細上限")
    ap.add_argument("--preview", default=None)
    ap.add_argument("--svg", default=None, help="輸出 SVG (Skill 1 的產出格式)")
    v = ap.parse_args()

    lines = [l for l in v.text.split("/") if l.strip() != ""]
    if v.cols:                                   # 依 cols 重新斷行
        flat = "".join(lines)
        lines = [flat[i:i+v.cols] for i in range(0, len(flat), v.cols)]
    glyphs = load_chars("".join(lines))
    missing = sorted({c for l in lines for c in l} - set(glyphs))
    if missing:
        print(f"字形資料庫沒有:{''.join(missing)}(會跳過)")

    sc = v.size / EM
    adv = v.size * (1 + v.gap)                   # 字距
    ladv = v.size * (1 + v.line_gap)             # 行距

    strokes, owner, n_char = [], [], 0
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line):
            if ch not in glyphs:
                continue
            if v.vertical:
                ox, oy = li * -ladv, ci * -adv
            else:
                ox, oy = ci * adv, li * -ladv
            for med in glyphs[ch]:
                if len(med) < 2:
                    continue
                p = catmull(med) * sc            # medians 已是 y 軸朝上,直接用
                p[:, 0] += ox
                p[:, 1] += oy - BASE * sc
                strokes.append(resample(p, v.step))
                owner.append(n_char)
            n_char += 1

    if not strokes:
        sys.exit("沒有可寫的字")

    # --- 粗細:行程曲線 x 曲率加成 ---
    if not v.brush:
        widths = [np.ones(len(s0)) for s0 in strokes]
    else:
        curvs = [smooth1d(curvature(s0), max(int(0.06 * v.size / v.step), 3))
                 for s0 in strokes]
        allk = np.concatenate([c for c in curvs if len(c)]) if curvs else np.array([0.0])
        kref = float(np.percentile(allk, 85)) or 1.0   # 整幅一起正規化,尺度自適應
        widths = []
        for s0, k in zip(strokes, curvs):
            w = brush_profile(len(s0))
            kn = np.clip(k / kref, 0.0, 1.5)            # 轉折越急,按得越重
            widths.append(np.clip(w * (1 + v.curve_gain * kn), 0.05, v.w_max))
        print(f"曲率加成:參考曲率 {kref:.3f} rad/mm,gain {v.curve_gain}")

    # --- 牽絲:照筆順連,不是找最近的。行草的連筆本來就依筆順。 ---
    n_silk, silk_len = 0, 0.0
    if v.link != "none":
        out_s, out_w = [strokes[0]], [widths[0]]
        for i in range(1, len(strokes)):
            same = (owner[i] == owner[i-1])
            if v.link == "all" or (v.link == "char" and same):
                arc = silk(out_s[-1][-1], strokes[i][0], v.step)
                if arc is not None:
                    silk_len += float(np.hypot(*(arc[1:]-arc[:-1]).T).sum())
                    n_silk += 1
                    out_s[-1] = np.vstack([out_s[-1], arc[1:], strokes[i]])
                    out_w[-1] = np.concatenate([
                        out_w[-1],
                        np.full(len(arc)-1, v.silk_width),
                        widths[i]])
                    continue
            out_s.append(strokes[i]); out_w.append(widths[i])
        strokes, widths = out_s, out_w

    allp = np.vstack(strokes)
    x0, y0 = allp.min(0); x1, y1 = allp.max(0)
    cx, cy = (x0+x1)/2, (y0+y1)/2                # 置中
    mm = [[[round(float(p[0]-cx), 3), round(float(p[1]-cy), 3)] for p in s]
          for s in strokes]

    draw = sum(math.dist(s[k-1], s[k]) for s in mm for k in range(1, len(s)))
    travel = sum(math.dist(mm[i-1][-1], mm[i][0]) for i in range(1, len(mm)))
    W, H = round(float(x1-x0), 2), round(float(y1-y0), 2)

    wid = [[round(float(x), 3) for x in w] for w in widths]
    json.dump({"units": "mm", "size_mm": [W, H], "text": v.text,
               "brush": bool(v.brush), "widths": wid,
               "n_chars": n_char, "n_strokes": len(mm), "n_dots": 0, "dots": [],
               "n_points": sum(len(s) for s in mm),
               "draw_len_mm": round(draw, 1), "travel_len_mm": round(travel, 1),
               "strokes": mm}, open(v.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if v.svg:
        write_svg(v.svg,
                  [np.asarray(x, float) for x in mm],
                  widths, W, H,
                  {"text": v.text, "chars": n_char, "strokes": len(mm),
                   "points": sum(len(x) for x in mm),
                   "draw_mm": round(draw, 1), "silk_mm": round(silk_len, 1),
                   "bead_mm": v.bead, "brush": int(bool(v.brush)), "link": v.link,
                   "x0": -W/2, "y1": H/2},
                  v.bead)
        print(f"SVG -> {v.svg}")

    est = draw/80 + travel/300 + len(mm)*0.75
    print(f"{n_char} 字 / 筆劃 {len(mm)} 條 / 座標 {sum(len(s) for s in mm)} 個 "
          f"/ 抬筆 {len(mm)-1} 次")
    print(f"版面 {W:.0f} x {H:.0f} mm,畫線 {draw:.0f} mm,空走 {travel:.0f} mm")
    if n_silk:
        print(f"牽絲 {n_silk} 條,共 {silk_len:.0f} mm (佔畫線 {100*silk_len/max(draw,1e-9):.0f}%)")
    print(f"預估 @80mm/s: 約 {est:.0f} 秒 -> {v.out}")

    if v.preview:
        from PIL import Image, ImageDraw
        PW = 900
        PH = max(int(PW * H / max(W, 1e-9)), 60)
        im = Image.new("RGB", (PW+40, PH+40), "white"); d = ImageDraw.Draw(im)
        bead = max(int(v.bead / W * PW), 1)
        SUG = (168, 96, 32)
        def px(p):
            return (int((p[0]/W + 0.5)*PW)+20, int((0.5 - p[1]/H)*PH)+20)
        for s, w in zip(mm, widths):
            pts = [px(p) for p in s]
            for k in range(1, len(pts)):
                bw_ = max(int(bead * float(w[k])), 1)
                d.line([pts[k-1], pts[k]], fill=SUG, width=bw_)
                r = bw_ // 2
                d.ellipse((pts[k][0]-r, pts[k][1]-r,
                           pts[k][0]+r, pts[k][1]+r), fill=SUG)
        for i, s in enumerate(mm):
            if i:
                d.line([px(mm[i-1][-1]), px(s[0])], fill=(120,140,255), width=1)
        im.save(v.preview)
        print(f"預覽 -> {v.preview}  (糖色=糖線 {v.bead}mm,淡藍=抬筆空走)")

if __name__ == "__main__":
    main()
