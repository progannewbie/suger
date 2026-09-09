#!/usr/bin/env python3
"""
圖片 -> 單線筆劃路徑 (畫糖人用)。只需要 pillow + numpy。

糖人是「一條糖絲連續拉出來」的線畫,所以取骨架中心線,不做填色。
輸出 JSON:一串 stroke,每個 stroke 是一段不抬筆的連續 (x, y) mm 座標。

用法:
  python img2path.py 龍.png -o path.json --width 120 --preview preview.png
"""
import argparse, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from svgout import write_svg
from PIL import Image, ImageDraw, ImageFilter

# ---------- 1. 影像 -> 二值圖 ----------
def load_gray(path, max_dim=600, blur=1.0):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA"):                 # 透明 -> 白底
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im = im.convert("L")
    w, h = im.size
    s = max_dim / max(w, h)
    if s < 1:
        im = im.resize((max(int(w*s), 1), max(int(h*s), 1)), Image.LANCZOS)
    if blur > 0:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(im, np.uint8)

def ink_thickness(bw):
    """回傳 (前景比例, 平均半線寬)。半線寬 = 連續侵蝕的存活層數平均"""
    A0 = int(bw.sum())
    if A0 == 0:
        return 0.0, 0.0
    m, tot, k = bw, A0, 0
    while m.any() and k < 40:
        m = np.logical_and.reduce(_shifts(m))
        tot += int(m.sum()); k += 1
    return A0 / bw.size, tot / A0

def auto_thresh(g, rel_jump=1.5, min_ink=0.004, max_ink=0.25):
    """自動挑門檻:描邊吃滿、但還沒灌進色塊。

    門檻由低往高掃,量每一階的平均線寬:
      低門檻只抓到最深的描邊 -> 線寬穩定緩升
      一旦開始把色塊納進來   -> 線寬出現斷崖式跳躍
    取跳躍前最後一階。用「相對於最細那階的倍率」判斷,
    所以粗描邊、細描邊的圖都適用,不必給絕對值。
    回傳 (thresh, ink, half_width)
    """
    base, best = None, None
    for t in range(30, 246, 5):
        ink, half = ink_thickness(g < t)
        if ink < min_ink:
            continue
        if ink > max_ink:
            break
        if base is None:
            base = max(half, 0.5)
        if half > base * rel_jump:
            break                        # 斷崖,色塊開始灌進來了
        best = (t, ink, half)
    if best is None:                     # 沒有線稿特徵,退回 Otsu
        t = otsu(g)
        ink, half = ink_thickness(g < t)
        best = (t, ink, half)
    return best

def _fshift(a, dr, dc):
    p = np.pad(a, 1, mode="edge")
    return p[1+dr:1+dr+a.shape[0], 1+dc:1+dc+a.shape[1]]

def sobel(g):
    f = g.astype(np.float32)
    gx = (-_fshift(f,-1,-1) + _fshift(f,-1,1)
          - 2*_fshift(f,0,-1) + 2*_fshift(f,0,1)
          - _fshift(f,1,-1) + _fshift(f,1,1))
    gy = (-_fshift(f,-1,-1) - 2*_fshift(f,-1,0) - _fshift(f,-1,1)
          + _fshift(f,1,-1) + 2*_fshift(f,1,0) + _fshift(f,1,1))
    return np.hypot(gx, gy)

def edge_binary(g, pct=5.0):
    """沒有描邊可抓時的後備:取梯度最強的前 pct% 當輪廓。
    實心剪影、漸層、照片都靠這條路變成線稿。"""
    m = sobel(g)
    return m >= max(float(np.percentile(m, 100 - pct)), 1e-6)

def needs_invert(g):
    """邊框偏暗 = 黑底白線,要反白。不然會把整片背景當成前景。"""
    b = np.concatenate([g[0], g[-1], g[:, 0], g[:, -1]])
    return float(np.median(b)) < 110

def binarize(g, thresh, invert=False, do_open=False):
    bw = g < thresh                               # 深色 = 線條
    if invert:
        bw = ~bw
    # 細線稿(2-3px)禁止開運算,侵蝕會直接打斷描邊;預設關閉
    return open3(bw) if do_open else bw

def otsu(g):
    hist = np.bincount(g.ravel(), minlength=256).astype(float)
    p = hist / hist.sum()
    w0 = np.cumsum(p); w1 = 1 - w0
    mu = np.cumsum(p * np.arange(256)); mut = mu[-1]
    ok = (w0 > 1e-9) & (w1 > 1e-9)
    var = np.zeros(256)
    var[ok] = (mut * w0[ok] - mu[ok]) ** 2 / (w0[ok] * w1[ok])
    return int(np.argmax(var))

def _shifts(a):
    p = np.pad(a, 1, constant_values=False)
    return [p[1+dr:1+dr+a.shape[0], 1+dc:1+dc+a.shape[1]]
            for dr, dc in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,0),(0,1),(1,-1),(1,0),(1,1))]

def open3(a):                                     # 3x3 erode 再 dilate
    e = np.logical_and.reduce(_shifts(a))
    return np.logical_or.reduce(_shifts(e))

# ---------- 2. Zhang-Suen 細線化 -> 1px 骨架 ----------
def skeletonize(a):
    img = a.copy()
    while True:
        changed = False
        for step in (0, 1):
            p = np.pad(img, 1, constant_values=False).astype(np.uint8)
            H, W = img.shape
            def q(dr, dc):
                return p[1+dr:1+dr+H, 1+dc:1+dc+W]
            # P2..P9 = 上, 右上, 右, 右下, 下, 左下, 左, 左上 (順時針)
            P = [q(-1,0), q(-1,1), q(0,1), q(1,1), q(1,0), q(1,-1), q(0,-1), q(-1,-1)]
            B = sum(P)
            seq = P + [P[0]]
            A = sum(((seq[i] == 0) & (seq[i+1] == 1)).astype(np.uint8) for i in range(8))
            c1 = (P[0] * P[2] * P[4] == 0) if step == 0 else (P[0] * P[2] * P[6] == 0)
            c2 = (P[2] * P[4] * P[6] == 0) if step == 0 else (P[0] * P[4] * P[6] == 0)
            kill = img & (B >= 2) & (B <= 6) & (A == 1) & c1 & c2
            if kill.any():
                img = img & ~kill
                changed = True
        if not changed:
            return img


def _crossing(img):
    """回傳 (交叉數 A, 鄰居數 B)。A==1 表示刪掉這點不會改變連通性"""
    p = np.pad(img, 1, constant_values=False).astype(np.uint8)
    H, W = img.shape
    q = lambda dr, dc: p[1+dr:1+dr+H, 1+dc:1+dc+W]
    P = [q(-1,0), q(-1,1), q(0,1), q(1,1), q(1,0), q(1,-1), q(0,-1), q(-1,-1)]
    seq = P + [P[0]]
    A = sum(((seq[i] == 0) & (seq[i+1] == 1)).astype(np.uint8) for i in range(8))
    return A, sum(P)

def prune_redundant(skel, rounds=8):
    """剔除冗餘像素。

    Zhang-Suen 在抗鋸齒邊緣會留下「雙軌」—— 兩條並行的骨架,
    追蹤時就變成一堆假分岔。刪掉 A==1 且鄰居>=3 的點可以收成單軌。
    用棋盤格交錯刪,避免同時刪掉一整段把線打斷。
    """
    img = skel.copy()
    chk = (np.indices(img.shape).sum(0) % 2) == 0
    for _ in range(rounds):
        changed = False
        for par in (chk, ~chk):
            A, B = _crossing(img)
            kill = img & (A == 1) & (B >= 3) & par
            if kill.any():
                img = img & ~kill
                changed = True
        if not changed:
            break
    return img

# ---------- 2b. 實心色塊 -> 螺旋填充 ----------
def components(bw):
    """8-連通元件標記(不用 scipy)"""
    H, W = bw.shape
    lab = np.zeros((H, W), np.int32)
    cur = 0
    for r0, c0 in np.argwhere(bw):
        if lab[r0, c0]:
            continue
        cur += 1
        lab[r0, c0] = cur
        stack = [(int(r0), int(c0))]
        while stack:
            r, c = stack.pop()
            for dr, dc in NBR:
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < W and bw[rr, cc] and not lab[rr, cc]:
                    lab[rr, cc] = cur
                    stack.append((rr, cc))
    return lab, cur

def half_thickness(mask):
    """連續侵蝕到消失的次數 = 半線寬。2px 細線 -> 1;10px 實心圓 -> 5"""
    m = mask.copy(); k = 0
    while m.any() and k <= 60:
        m = np.logical_and.reduce(_shifts(m))
        k += 1
    return k

def split_blobs(bw, min_thick=3, max_size=24, min_area=10):
    """把「又厚又小」的實心色塊抽出來,回傳 (剩餘 bw, [(cx,cy,R)...])

    骨架化會把實心圓縮成一個點 -> 眼睛鼻子整個消失。
    這種改用螺旋填滿,也正好是糖畫師傅填色塊的畫法。
    """
    lab, n = components(bw)
    keep = bw.copy()
    blobs = []
    for i in range(1, n + 1):
        m = lab == i
        area = int(m.sum())
        if area < min_area:
            continue
        ys, xs = np.nonzero(m)
        h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        if max(h, w) > max_size:
            continue                       # 太大,留給骨架化
        sub = m[ys.min():ys.max()+1, xs.min():xs.max()+1]
        if half_thickness(sub) < min_thick:
            continue                       # 太薄,是線不是塊
        keep &= ~m
        blobs.append((float(xs.mean()), float(ys.mean()),
                      float(math.sqrt(area / math.pi))))
    return keep, blobs

def spiral(cx, cy, R, pitch, per_turn=28):
    """從中心往外螺旋,一筆畫完不抬手。pitch = 糖線寬。

    軌跡半徑要扣掉半條糖線寬(糖是沿著路徑往兩側鋪開),
    而且至少走滿整數圈,不然會留一段缺口變成勾狀。
    """
    r_out = max(R - pitch / 2, 0.0)
    turns = max(math.ceil(r_out / max(pitch, 1e-6)), 1)
    n = int(max(turns * per_turn, 16))
    t = np.linspace(0, 1, n + 1)
    th = t * turns * 2 * math.pi
    r = t * r_out
    return np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)])

# ---------- 3. 骨架 -> 有序像素鏈 ----------
NBR = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]

def trace_skeleton(skel):
    """骨架 -> 有序像素鏈。

    重點:走到分岔點不停手,改選「方向最接近」的分支繼續。
    因為 8-連通骨架在階梯狀轉角會冒出假分岔,遇到就斷會碎成一堆。
    這樣也比較像人畫圖:能順著畫下去就不抬筆。
    """
    pix = {(int(r), int(c)) for r, c in zip(*np.nonzero(skel))}

    # 對角線若能用兩步正交走到,那條對角邊是多餘的。
    # 不砍掉的話,階梯狀的斜線每一階都變成 3 個鄰居 = 假分岔,
    # 追蹤時會沿著對角捷徑再走一次,同一段線被畫兩遍。
    NB = {}
    for r, c in pix:
        lst = []
        for dr, dc in NBR:
            q = (r + dr, c + dc)
            if q not in pix:
                continue
            if dr and dc and ((r + dr, c) in pix or (r, c + dc) in pix):
                continue
            lst.append(q)
        NB[(r, c)] = lst
    used = set()

    def walk(start, nxt):
        chain = [start, nxt]
        used.add(frozenset((start, nxt)))
        prev, cur = start, nxt
        while True:
            cand = [q for q in NB[cur] if frozenset((cur, q)) not in used]
            if not cand:
                break
            d0 = (cur[0] - prev[0], cur[1] - prev[1])
            n0 = math.hypot(*d0) or 1.0
            best, bestcos = None, -2.0
            for q in cand:                      # 選轉彎最小的分支
                d1 = (q[0] - cur[0], q[1] - cur[1])
                cos = (d0[0]*d1[0] + d0[1]*d1[1]) / (n0 * (math.hypot(*d1) or 1.0))
                if cos > bestcos:
                    bestcos, best = cos, q
            used.add(frozenset((cur, best)))
            chain.append(best)
            prev, cur = cur, best
        return chain

    deg = {p: len(NB[p]) for p in pix}
    strokes = []
    for s in ([p for p in pix if deg[p] == 1]      # 端點優先起筆
              + [p for p in pix if deg[p] >= 3]    # 再從分岔起
              + list(pix)):                        # 剩下的純迴圈
        for n in NB[s]:
            if frozenset((s, n)) not in used:
                strokes.append(walk(s, n))
    return [np.array([[c, r] for r, c in s], float) for s in strokes if len(s) >= 2]

def join_strokes(strokes, tol):
    """端點靠得夠近的筆劃接起來 —— 每接一次就少抬一次筆,少一個糖疤"""
    if tol <= 0:
        return strokes
    out = [s for s in strokes]
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(len(out)):
                if i == j or out[i] is None or out[j] is None:
                    continue
                a, b = out[i], out[j]
                for rev_a, rev_b in ((0,0), (0,1), (1,0), (1,1)):
                    A = a[::-1] if rev_a else a
                    B = b[::-1] if rev_b else b
                    if float(np.hypot(*(A[-1] - B[0]))) <= tol:
                        out[i] = np.vstack([A, B])
                        out[j] = None
                        merged = True
                        break
                if merged:
                    break
            if merged:
                break
        out = [s for s in out if s is not None]
    return out

# ---------- 4. RDP 簡化 ----------
def rdp(pts, eps):
    pts = np.asarray(pts, float)
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        L = float(np.hypot(*ab))
        seg = pts[i+1:j] - a
        d = (np.abs(ab[0]*seg[:,1] - ab[1]*seg[:,0]) / L) if L > 1e-9 \
            else np.hypot(seg[:,0], seg[:,1])
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return pts[keep]

# ---------- 5. 排序:少空走 ----------
def order_strokes(strokes):
    if not strokes:
        return []
    remain = [np.asarray(s, float) for s in strokes]
    out = [remain.pop(0)]
    while remain:
        cur = out[-1][-1]
        best, bi, brev = 1e18, 0, False
        for i, s in enumerate(remain):
            d0 = float(np.hypot(*(s[0] - cur)))
            d1 = float(np.hypot(*(s[-1] - cur)))
            if d0 < best: best, bi, brev = d0, i, False
            if d1 < best: best, bi, brev = d1, i, True
        s = remain.pop(bi)
        out.append(s[::-1] if brev else s)
    return out

def two_opt(seq, rounds=6):
    """2-opt 改善筆劃順序。貪心排完常有交叉,反轉一段就能省不少空走。
    筆劃可以反向畫,所以反轉區段時整段順序和方向一起翻。"""
    def cost(q):
        return sum(math.dist(q[i][-1], q[i+1][0]) for i in range(len(q)-1))
    n = len(seq)
    if n < 4:
        return seq
    best = cost(seq)
    for _ in range(rounds):
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                cand = seq[:i] + [x[::-1] for x in reversed(seq[i:j+1])] + seq[j+1:]
                c = cost(cand)
                if c < best - 1e-9:
                    seq, best, improved = cand, c, True
        if not improved:
            break
    return seq


# ---------- 5b. 一線到底 (Eulerian path) ----------
def build_graph(strokes, tol=2.5):
    """筆劃端點 -> 節點,筆劃 -> 邊。骨架分岔處的端點座標本來就相同,會自動併成同一節點。"""
    nodes, edges = [], []
    def nid(p):
        for i, q in enumerate(nodes):
            if math.dist(p, q) <= tol:
                return i
        nodes.append((float(p[0]), float(p[1])))
        return len(nodes) - 1
    for s in strokes:
        edges.append([nid(s[0]), nid(s[-1]), np.asarray(s, float)])
    return nodes, edges

def _connector(nodes, a, b):
    return [a, b, np.array([nodes[a], nodes[b]], float)]

def connect_components(nodes, edges):
    """把分離的連通塊用直線接起來。回傳補上的長度清單。"""
    par = list(range(len(nodes)))
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    def union(a, b):
        par[find(a)] = find(b)
    for a, b, _ in edges:
        union(a, b)
    added = []
    while True:
        grp = {}
        for i in range(len(nodes)):
            grp.setdefault(find(i), []).append(i)
        if len(grp) <= 1:
            break
        ks = list(grp)
        best = None
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                for a in grp[ks[i]]:
                    for b in grp[ks[j]]:
                        d = math.dist(nodes[a], nodes[b])
                        if best is None or d < best[0]:
                            best = (d, a, b)
        d, a, b = best
        edges.append(_connector(nodes, a, b)); added.append(d); union(a, b)
    return added

def eulerize(nodes, edges):
    """奇點兩兩配對補邊,留 2 個當起訖點。回傳補上的長度清單。"""
    deg = {}
    for a, b, _ in edges:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    odd = [n for n, d in deg.items() if d % 2 == 1]
    added = []
    while len(odd) > 2:
        best = None
        for i in range(len(odd)):
            for j in range(i + 1, len(odd)):
                d = math.dist(nodes[odd[i]], nodes[odd[j]])
                if best is None or d < best[0]:
                    best = (d, i, j)
        d, i, j = best
        edges.append(_connector(nodes, odd[i], odd[j])); added.append(d)
        odd = [x for k, x in enumerate(odd) if k not in (i, j)]
    return added, odd

def euler_path(nodes, edges, start):
    """Hierholzer:走完每條邊剛好一次"""
    adj = {}
    for i, (a, b, _) in enumerate(edges):
        adj.setdefault(a, []).append((i, b))
        adj.setdefault(b, []).append((i, a))
    used = [False] * len(edges)
    stack, out = [(start, -1)], []
    while stack:
        v, _e = stack[-1]
        moved = False
        while adj.get(v):
            i, w = adj[v].pop()
            if used[i]:
                continue
            used[i] = True
            stack.append((w, i)); moved = True
            break
        if not moved:
            out.append(stack.pop())
    out.reverse()

    pts = []
    for k in range(1, len(out)):
        a_prev = out[k-1][0]
        seg = edges[out[k][1]][2]
        if math.dist(seg[0], nodes[a_prev]) > math.dist(seg[-1], nodes[a_prev]):
            seg = seg[::-1]
        pts.extend(seg[1:] if pts else seg)
    return np.asarray(pts, float) if pts else None

def one_stroke(strokes, tol=2.5):
    """把多筆合成一筆。回傳 (單一筆劃, 補線總長px, 補線條數)"""
    if len(strokes) < 2:
        return strokes[0] if strokes else None, 0.0, 0
    nodes, edges = build_graph(strokes, tol)
    add1 = connect_components(nodes, edges)
    add2, odd = eulerize(nodes, edges)
    start = odd[0] if odd else edges[0][0]
    path = euler_path(nodes, edges, start)
    return path, sum(add1) + sum(add2), len(add1) + len(add2)

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("-o", "--out", default="path.json")
    ap.add_argument("--width", type=float, default=120.0, help="成品寬度 mm")
    ap.add_argument("--max-dim", type=int, default=500, help="處理解析度 px")
    ap.add_argument("--eps", type=float, default=None, help="簡化容差 px (預設自動)")
    ap.add_argument("--min-len", type=float, default=None, help="碎筆劃長度下限 px (預設自動)")
    ap.add_argument("--join", type=float, default=None, help="端點合併距離 px (預設自動)")
    ap.add_argument("--bead", type=float, default=2.5, help="糖線寬 mm,決定螺旋間距")
    ap.add_argument("--blob-thick", type=int, default=None, help="色塊半線寬門檻 (預設自動)")
    ap.add_argument("--blob-max", type=int, default=None, help="色塊最大邊長 px (預設自動)")
    ap.add_argument("--no-blob", action="store_true", help="關閉色塊螺旋填充")
    ap.add_argument("--thresh", type=int, default=None, help="固定門檻 0-255 (預設自動)")
    ap.add_argument("--mode", choices=["auto", "line", "edge"], default="auto",
                    help="line=抓描邊(卡通線稿) edge=梯度輪廓(剪影/照片/漸層)")
    ap.add_argument("--edge-pct", type=float, default=5.0,
                    help="edge 模式保留梯度最強的前幾 %%")
    ap.add_argument("--one-stroke", action="store_true",
                    help="一線到底:補連接線讓整張圖一筆畫完(連接線也會出糖)")
    ap.add_argument("--max-strokes", type=int, default=60,
                    help="筆劃數上限,超過自動加大 min-len 收斂。0=不限")
    ap.add_argument("--invert", action="store_true", help="黑白反轉")
    ap.add_argument("--open", action="store_true",
                    help="開運算去雜點。線寬<4px 千萬別開,會打斷描邊")
    ap.add_argument("--preview", default=None, help="輸出預覽 PNG")
    ap.add_argument("--svg", default=None, help="輸出 raw.svg (原始像素座標,給 AI 精簡用)")
    v = ap.parse_args()

    g = load_gray(v.image, v.max_dim)

    # --- 黑底白線自動反白 ---
    flipped = False
    if v.mode != "edge" and not v.invert and needs_invert(g):
        g = 255 - g
        flipped = True

    # --- 選模式:有描邊走線稿,沒有就走邊緣偵測 ---
    mode = v.mode
    if mode == "auto" and v.thresh is not None:
        mode = "line"                    # 手動給了門檻就是要走線稿
    if mode == "auto":
        thr, ink, half = auto_thresh(g)
        mode = "line" if (ink <= 0.18 and half <= 4.0) else "edge"
    if mode == "line":
        if v.thresh is not None:
            thr = v.thresh
        bw = binarize(g, thr, v.invert, v.open)
        ink, half = ink_thickness(bw)
        src = f"線稿 門檻 {thr}"
    else:
        bw = edge_binary(g, v.edge_pct)
        thr = None
        ink, half = ink_thickness(bw)
        src = f"邊緣偵測 前 {v.edge_pct}%"
    if flipped:
        src += " (已自動反白)"
    if not bw.any():
        sys.exit("二值化後一片空白,試試 --invert 或指定 --thresh")

    # --- 所有像素參數都用「量到的線寬」推導,換圖才不用重調 ---
    lw = max(2.0 * half, 1.5)                      # 實測線寬 px
    eps       = v.eps        if v.eps        is not None else round(0.40*lw, 2)
    min_len   = v.min_len    if v.min_len    is not None else round(4.0*lw, 1)
    join      = v.join       if v.join       is not None else round(3.0*lw, 1)
    blob_thk  = v.blob_thick if v.blob_thick is not None else max(int(round(1.8*half))+1, 3)
    blob_max  = v.blob_max   if v.blob_max   is not None else int(round(10*lw))
    print(f"{src} / 前景 {ink*100:.1f}% / "
          f"線寬 {lw:.1f}px  ->  eps {eps} min-len {min_len} join {join} "
          f"blob-thick {blob_thk} blob-max {blob_max}")

    # 比例尺:螺旋間距要換算成 px
    ys_, xs_ = np.nonzero(bw)
    mm_per_px = v.width / max(int(xs_.max() - xs_.min()), 1)
    pitch_px = max(v.bead / mm_per_px, 1.0)

    blobs = []
    if not v.no_blob:
        bw, blobs = split_blobs(bw, blob_thk, blob_max)

    # 色塊比糖線還細 -> 點一下就填滿,畫螺旋反而溢出
    def is_dot(R):
        return (R - pitch_px / 2) <= pitch_px * 0.25
    dots_px = [(cx, cy, 2*R) for cx, cy, R in blobs if is_dot(R)]
    spirals = [spiral(cx, cy, R, pitch_px) for cx, cy, R in blobs if not is_dot(R)]

    raw = trace_skeleton(skeletonize(bw))
    n0 = len(raw)

    # 先殺毛刺再合併。順序反了的話,join 會把一堆 1-3px 的毛刺
    # 黏成「夠長」的假筆劃 —— 一個圓會變成 8 筆。
    spur = max(1.5 * lw, 4.0)
    raw = [x for x in raw if np.hypot(*(x[1:]-x[:-1]).T).sum() >= spur]
    raw = join_strokes(raw, join)
    print(f"追蹤 {n0} 條 -> 去毛刺(<{spur:.0f}px) {len(raw)} 條")
    lens = np.array([np.hypot(*(x[1:]-x[:-1]).T).sum() for x in raw]) if raw else np.array([])

    # --- 筆劃數上限:加大 min-len 直到收斂,保證任何圖都出得了東西 ---
    ml = min_len
    while True:
        keep = [x for x, L in zip(raw, lens) if L >= ml]
        if not v.max_strokes or len(keep) + len(spirals) <= v.max_strokes or ml > 400:
            break
        ml *= 1.35
    if ml != min_len:
        print(f"筆劃數超過上限 {v.max_strokes},min-len 自動提高到 {ml:.0f}px")

    strokes = [rdp(x, eps) for x in keep] + spirals
    add_len_px, add_n = 0.0, 0
    if v.one_stroke:
        single, add_len_px, add_n = one_stroke(strokes, tol=max(2.5, lw))
        if single is not None:
            strokes = [single]
    else:
        strokes = two_opt(order_strokes(strokes))
    if not strokes:
        sys.exit("沒抓到線條。這張圖可能沒有明顯描邊,試試 --thresh 手動指定或 --invert")

    allp = np.vstack(strokes + [np.array([[d[0], d[1]] for d in dots_px])]
                     if dots_px else strokes)
    x0, y0 = allp.min(0); x1, y1 = allp.max(0)
    scale = v.width / max(x1 - x0, 1e-9)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mm = [[[round(float((p[0]-cx)*scale), 3), round(float((cy-p[1])*scale), 3)]
           for p in s] for s in strokes]          # 置中 + Y 翻正

    dots = [[round(float((d[0]-cx)*scale), 3), round(float((cy-d[1])*scale), 3),
             round(float(d[2]*scale), 2)] for d in dots_px]
    dots.sort(key=lambda d: (d[1], d[0]))

    draw = sum(math.dist(s[k-1], s[k]) for s in mm for k in range(1, len(s)))
    travel = sum(math.dist(mm[i-1][-1], mm[i][0]) for i in range(1, len(mm)))
    h_mm = round(float((y1 - y0) * scale), 2)

    json.dump({"units": "mm", "size_mm": [round(v.width, 2), h_mm],
               "n_strokes": len(mm), "n_dots": len(dots),
               "n_points": sum(len(s) for s in mm), "dots": dots,
               "draw_len_mm": round(draw, 1), "travel_len_mm": round(travel, 1),
               "strokes": mm},
              open(v.out, "w"), ensure_ascii=False, indent=1)

    print(f"筆劃 {len(mm)} 條 (含 {len(spirals)} 個螺旋色塊) + 糖點 {len(dots)} 個 / "
          f"座標 {sum(len(s) for s in mm)} 個 / 抬筆 {len(mm)+len(dots)-1} 次")
    print(f"成品 {v.width:.0f} x {h_mm:.0f} mm,畫線 {draw:.0f} mm,空走 {travel:.0f} mm")
    if v.one_stroke:
        print(f"一線到底:補了 {add_n} 條連接線,共 {add_len_px*scale:.0f} mm "
              f"(佔總畫線 {100*add_len_px*scale/max(draw,1e-9):.0f}%)")
    est = draw/80 + travel/300 + (len(mm) + len(dots)) * 0.75   # 每次抬筆約 0.75s
    print(f"預估 @80mm/s: 約 {est:.0f} 秒 (其中抬筆佔 {(len(mm)+len(dots))*0.75:.0f} 秒)")
    print(f"-> {v.out}")

    if v.svg:
        write_svg(v.svg, [np.asarray(x, float) for x in mm],
                  [np.ones(len(x)) for x in mm], v.width, h_mm,
                  {"source": os.path.basename(v.image), "mode": mode,
                   "strokes": len(mm), "points": sum(len(x) for x in mm),
                   "draw_mm": round(draw, 1), "bead_mm": v.bead,
                   "brush": 0, "link": "all" if v.one_stroke else "none",
                   "x0": -v.width/2, "y1": h_mm/2},
                  v.bead)
        print(f"SVG -> {v.svg}  ({len(mm)} 條)")

    if v.preview:
        W = 900
        H = max(int(W * h_mm / v.width), 60)
        im = Image.new("RGB", (W + 40, H + 40), "white")
        d = ImageDraw.Draw(im)
        bead = max(int(v.bead / v.width * W), 1)   # 用真實糖線寬畫,才看得出會不會糊
        def px(p):
            return (int((p[0] / v.width + 0.5) * W) + 20,
                    int((0.5 - p[1] / h_mm) * H) + 20)
        SUG = (168, 96, 32)
        for i, s in enumerate(mm):                 # 先鋪糖線
            pts = [px(p) for p in s]
            d.line(pts, fill=SUG, width=bead, joint="curve")
            r = bead // 2
            for q in (pts[0], pts[-1]):            # 圓頭收尾
                d.ellipse((q[0]-r, q[1]-r, q[0]+r, q[1]+r), fill=SUG)
        for X, Y, dia in dots:                     # 糖點
            x, y = px((X, Y)); r = max(int(dia / v.width * W / 2), bead // 2)
            d.ellipse((x-r, y-r, x+r, y+r), fill=SUG)
        for i, s in enumerate(mm):                 # 空走線畫在最上層
            if i:
                d.line([px(mm[i-1][-1]), px(s[0])], fill=(120, 140, 255), width=1)
        im.save(v.preview)
        print(f"預覽 -> {v.preview}  (糖色=實際糖線 {v.bead}mm 寬,淡藍=抬筆空走)")

if __name__ == "__main__":
    main()
