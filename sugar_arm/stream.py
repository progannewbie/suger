#!/usr/bin/env python3
"""
PC 端:把 path.json 串流給手臂 (Kawasaki F60, TCP)

配合 arm/sugar_server.as。協定是純文字,每行一個指令:

  BEG,<點數>              宣告一筆劃有幾點
  PT,x,y,v,x,y,v,...      填點(含每點速度),單封包 <=255 字元
  RUN                     一次連續走完緩衝區
  DOT,x,y,秒              糖點
  BASE,x,y,z,o,a,t        設定畫布原點
  SPD,畫線,空走,精度       設定速度
  HOME / END
手臂每收一行回 OK 或 ER。

用法:
  python stream.py path.json --host 192.168.0.2 --draw-speed 60
  python stream.py path.json --dry-run          # 不連線,只印封包
  python stream.py --fake-server                # 起一個假手臂,測協定用
"""
import argparse, json, math, socket, sys, time

# 手冊 90210-1344DE p.1-41:TCP_RECV 每個陣列元素上限 255 字元(對應 E4007)
# 留一點餘裕,避免剛好卡在邊界
MAXLEN = 250

# ---------- 圓弧擬合 (C1MOVE + C2MOVE) ----------
def circle_err(P):
    """三點定圓,回傳其餘點到該圓的最大偏差。共線回傳 inf"""
    a, b, c = P[0], P[len(P)//2], P[-1]
    d0 = 2*(a[0]*(b[1]-c[1]) + b[0]*(c[1]-a[1]) + c[0]*(a[1]-b[1]))
    if abs(d0) < 1e-9:
        return float("inf")
    s1, s2, s3 = a[0]**2+a[1]**2, b[0]**2+b[1]**2, c[0]**2+c[1]**2
    ux = (s1*(b[1]-c[1]) + s2*(c[1]-a[1]) + s3*(a[1]-b[1])) / d0
    uy = (s1*(c[0]-b[0]) + s2*(a[0]-c[0]) + s3*(b[0]-a[0])) / d0
    R = math.dist(a, (ux, uy))
    return max(abs(math.dist(q, (ux, uy)) - R) for q in P)

def plan_motion(stroke, speeds, tol, use_arc, min_pts=4):
    """回傳 [(kind, x, y, v), ...],kind: 'L' 直線 / 'C1' 圓弧中點 / 'C2' 圓弧終點

    圓弧只能在「速度相同」的連續段裡找 —— C1MOVE 和 C2MOVE 之間沒有
    中間點可以改速度,而毛筆的粗細正是靠速度做的。所以毛筆開得越細膩,
    能用圓弧的地方越少。這是本質衝突,不是實作問題。
    """
    n = len(stroke)
    out = [("L", stroke[0][0], stroke[0][1], speeds[0])]
    i = 0                                  # 已經吐出的最後一個點
    while i < n - 1:
        made_arc = False
        if use_arc and n - i >= min_pts:
            v = speeds[i + 1]
            run = i + 1                    # 同速段的結尾(不含)
            while run < n and speeds[run] == v:
                run += 1
            best = i
            for k in range(i + min_pts - 1, run):
                if circle_err(stroke[i:k+1]) > tol:
                    break
                best = k                   # 貪心地把圓弧拉到最長
            if best - i >= min_pts - 1:
                mid = (i + best) // 2
                out.append(("C1", stroke[mid][0], stroke[mid][1], v))
                out.append(("C2", stroke[best][0], stroke[best][1], v))
                i = best
                made_arc = True
        if not made_arc:                   # 沒圓弧就走一步直線,保證每點都吐出
            i += 1
            out.append(("L", stroke[i][0], stroke[i][1], speeds[i]))
    return out

def w2speed(w, vdraw, vmax, quant):
    """糖流量固定 -> 線寬 x 速度 = 常數。要細就跑快。"""
    v = vdraw / max(float(w), 1e-3)
    v = min(max(v, vdraw), vmax)
    return round(round(v / quant) * quant, 1)

def packets(plan):
    """把動作計畫切成封包。

    同型態的連續動作併在同一包 —— 圓弧如果每段自己一包,封包數會爆炸
    (實測 40 -> 119),把 PT 的批次優勢整個吃掉。
    """
    out, cur, kind = [], "", None

    def flush():
        nonlocal cur, kind
        if kind and cur != kind:
            out.append(cur)
        cur, kind = "", None

    i = 0
    while i < len(plan):
        k, x, y, v = plan[i]
        if k == "C1" and i + 1 < len(plan) and plan[i+1][0] == "C2":
            _, xe, ye, _ = plan[i+1]
            piece = f",{x:.2f},{y:.2f},{xe:.2f},{ye:.2f},{v:g}"
            head = "AR"
            i += 2
        else:
            piece = f",{x:.2f},{y:.2f},{v:g}"
            head = "PT"
            i += 1
        if kind != head or len(cur) + len(piece) > MAXLEN - 2:
            flush()
            kind, cur = head, head
        cur += piece
    flush()
    return out

class Link:
    """一問一答:送一行、等一行。

    不加換行字元 —— AS 端的 TCP_SEND 是把字串陣列原樣送出,不會補 \n,
    所以這邊不能用 readline() 等換行,會直接卡死。
    framing 靠「送完就等回覆」保證每次 TCP_RECV 剛好拿到一整行。
    """
    def __init__(self, host, port, dry, timeout=30.0):
        self.dry = dry
        self.n = 0
        self.s = None
        if not dry:
            self.s = socket.create_connection((host, port), timeout=timeout)
            self.s.settimeout(timeout)

    def send(self, line):
        self.n += 1
        if len(line) > MAXLEN:
            sys.exit(f"封包 {len(line)} 字元,超過上限 {MAXLEN}")
        if self.dry:
            print(f"  {line}")
            return "OK"
        self.s.sendall(line.encode("ascii"))
        rep = self.s.recv(64).decode("ascii", "replace").strip()
        if not rep.startswith("OK"):
            sys.exit(f"手臂回覆 {rep!r},指令 {line[:48]}…")
        return rep

    def close(self):
        if self.s:
            self.s.close()

def fake_server(port):
    """假手臂:收什麼都回 OK,用來測協定和封包切法"""
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port)); srv.listen(1)
    print(f"假手臂監聽 :{port}  (Ctrl-C 結束)", flush=True)
    while True:
        c, a = srv.accept()
        print(f"連線來自 {a}", flush=True)
        n, longest = 0, 0
        while True:
            data = c.recv(4096)
            if not data:
                break
            line = data.decode("ascii", "replace").strip()
            n += 1
            longest = max(longest, len(line))
            if not line.startswith("PT") or n % 10 == 0:
                print(f"  <- {line[:72]}", flush=True)
            c.sendall(b"OK")
            if line.startswith("END"):
                break
        print(f"共收 {n} 行,最長 {longest} 字元\n", flush=True)
        c.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", help="text2path.py / img2path.py 的輸出")
    ap.add_argument("--host", default="192.168.0.2")
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--draw-speed", type=float, default=60.0, help="基準畫線速度 mm/s")
    ap.add_argument("--max-speed", type=float, default=0, help="最細處速度上限,0=4倍")
    ap.add_argument("--travel-speed", type=float, default=300.0)
    ap.add_argument("--accuracy", type=float, default=3.0, help="精度 mm,放大才連續")
    ap.add_argument("--base", default=None, help="畫布原點 x,y,z 或 x,y,z,o,a,t")
    ap.add_argument("--dot-ms", type=float, default=120.0, help="糖點停留 ms/mm 直徑")
    ap.add_argument("--speed-quant", type=float, default=20.0,
                    help="速度量化級距 mm/s。必須跟 svg2points.py 用同一個值,"
                         "否則 Skill 2 保留的速度換檔點會對不上")
    ap.add_argument("--arc", action="store_true",
                    help="等速段用 C1MOVE/C2MOVE 圓弧取代直線點,省指令數")
    ap.add_argument("--air-move", choices=["lmove", "jmove"], default="lmove",
                    help="空中移動的插補方式。jmove 較快但笛卡爾路徑不可預測,"
                         "抬筆高度要夠,否則可能中途下沉撞到成品")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake-server", action="store_true")
    v = ap.parse_args()

    if v.fake_server:
        fake_server(v.port); return
    if not v.json:
        sys.exit("要給 path.json,或用 --fake-server")

    d = json.load(open(v.json))
    strokes = d["strokes"]
    widths = d.get("widths")
    dots = d.get("dots", [])
    vmax = v.max_speed or v.draw_speed * 4
    quant = v.speed_quant

    link = Link(v.host, v.port, v.dry_run)
    t0 = time.time()

    if v.base:
        b = [float(x) for x in v.base.split(",")]
        if len(b) == 3:
            b += [0.0, 180.0, 0.0]            # 工具朝下
        link.send("BASE," + ",".join(f"{x:g}" for x in b))
    link.send(f"SPD,{v.draw_speed:g},{v.travel_speed:g},{v.accuracy:g}")
    link.send(f"AIR,{1 if v.air_move == 'jmove' else 0}")
    tol = float(d.get("tolerance_mm", d.get("bead_mm", 2.5) / 4))
    n_l = n_arc = 0

    for i, s in enumerate(strokes):
        w = widths[i] if widths else [1.0] * len(s)
        sp = [w2speed(x, v.draw_speed, vmax, quant) for x in w]
        pl = plan_motion(s, sp, tol, v.arc)
        na = sum(1 for k, *_ in pl if k == "C1")
        nl = sum(1 for k, *_ in pl if k == "L")
        n_l += nl; n_arc += na
        print(f"筆劃 {i+1}/{len(strokes)}:{len(s)} 點 -> "
              f"LMOVE {nl} + 圓弧 {na} 段  速度 {min(sp):g}~{max(sp):g} mm/s")
        link.send(f"BEG,{len(pl)}")
        for p in packets(pl):
            link.send(p)
        link.send("RUN")

    for (x, y, dia) in dots:
        link.send(f"DOT,{x:.2f},{y:.2f},{v.dot_ms*dia/1000:.3f}")

    link.send("HOME")
    link.send("END")
    link.close()
    cmds = n_l + 2 * n_arc
    print(f"\n動作:LMOVE {n_l} + C1/C2MOVE {n_arc} 段 = {cmds} 個指令"
          f"{' (--arc 關閉)' if not v.arc else ''}")
    print(f"空中移動:{v.air_move.upper()}")
    print(f"{len(strokes)} 筆劃 + {len(dots)} 糖點 -> {link.n} 個封包"
          f"{'' if v.dry_run else f'，耗時 {time.time()-t0:.1f}s'}")

if __name__ == "__main__":
    main()
