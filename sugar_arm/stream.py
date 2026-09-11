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

def w2speed(w, vdraw, vmax, quant):
    """糖流量固定 -> 線寬 x 速度 = 常數。要細就跑快。"""
    v = vdraw / max(float(w), 1e-3)
    v = min(max(v, vdraw), vmax)
    return round(round(v / quant) * quant, 1)

def _unused_pt_packets(stroke, speeds):
    """把點切成不超過 250 字元的 PT 封包(手冊上限 255,留餘裕)"""
    out, cur = [], "PT"
    for (x, y), v in zip(stroke, speeds):
        piece = f",{x:.2f},{y:.2f},{v:g}"
        if len(cur) + len(piece) > MAXLEN - 2:
            out.append(cur); cur = "PT"
        cur += piece
    if cur != "PT":
        out.append(cur)
    return out

def build_ops(d, zup=15.0, sig=1, draw_speed=60.0, max_speed=0,
              travel_speed=300.0, speed_quant=20.0, air_move="lmove",
              dwell=0.15, cut=0.10, dot_ms=120.0):
    """把座標變成一串動作指令。每行一個動作,自帶型態。

    所有糖畫邏輯都在這裡 —— 什麼時候開閥、停多久、怎麼抬筆。
    手臂端收到一行就執行一行,完全不知道自己在畫糖。

    不用擔心「一行一往返會頓」:AS 執行移動指令時不會卡住程式,
    控制器會把移動排進自己的佇列(手冊 4.5.5)。60mm/s、點距 1mm
    只需要每秒 60 個點,區網往返 1~5ms 餵得過來。
    """
    strokes = d["strokes"]
    widths = d.get("widths")
    dots = d.get("dots", [])
    vmax = max_speed or draw_speed * 4
    air = "jmove" if air_move == "jmove" else "lmove"
    ops, info = [], []

    for i, st in enumerate(strokes):
        w = widths[i] if widths else [1.0] * len(st)
        sp = [w2speed(x, draw_speed, vmax, speed_quant) for x in w]
        info.append((len(st), min(sp), max(sp)))
        x0, y0 = st[0]
        ops.append(f"{air},{x0:.2f},{y0:.2f},{zup:g},{travel_speed:g}")  # 移到起點上方
        ops.append(f"lmove,{x0:.2f},{y0:.2f},0,{travel_speed:g}")        # 垂直下筆
        ops.append("brk")                       # 確認到位才開閥
        ops.append(f"sig,{sig}")                # 開糖閥
        ops.append(f"wait,{dwell:g}")           # 等糖絲成形
        for (x, y), v in zip(st[1:], sp[1:]):
            ops.append(f"lmove,{x:.2f},{y:.2f},0,{v:g}")
        ops.append("brk")
        ops.append(f"sig,-{sig}")               # 關糖閥
        ops.append(f"wait,{cut:g}")             # 等糖絲斷
        ops.append(f"ldepart,{zup:g},{travel_speed:g}")   # 沿工具軸垂直抬筆

    for (x, y, dia) in dots:
        ops.append(f"{air},{x:.2f},{y:.2f},{zup:g},{travel_speed:g}")
        ops.append(f"lmove,{x:.2f},{y:.2f},0,{travel_speed:g}")
        ops.append("brk")
        ops.append(f"sig,{sig}")
        ops.append(f"wait,{dot_ms*dia/1000:.3f}")        # 停越久糖越多
        ops.append(f"sig,-{sig}")
        ops.append(f"wait,{cut:g}")
        ops.append(f"ldepart,{zup:g},{travel_speed:g}")

    ops.append(f"{air},0,0,{zup:g},{travel_speed:g}")     # 回原點上方
    ops.append("brk")
    ops.append("end")
    return ops, info

def build_commands(d, base=None, accuracy=3.0, **kw):
    """完整指令串:設定 + 動作序列"""
    head = []
    if base:
        b = [float(x) for x in base.split(",")] if isinstance(base, str) else list(base)
        if len(b) == 3:
            b += [0.0, 180.0, 0.0]            # 工具朝下
        head.append("base," + ",".join(f"{x:g}" for x in b))
    head.append(f"acc,{accuracy:g}")
    ops, info = build_ops(d, **kw)
    return head + ops, info

def pack(ops, maxlen=MAXLEN):
    """多個動作用換行塞進同一個封包,塞滿 250 字元為止"""
    out, cur = [], ""
    for o in ops:
        add = o if not cur else "\n" + o
        if len(cur) + len(add) > maxlen:
            out.append(cur); cur = o
        else:
            cur += add
    if cur:
        out.append(cur)
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
    ap.add_argument("--zup", type=float, default=15.0, help="抬筆高度 mm")
    ap.add_argument("--signal", type=int, default=1, help="糖閥 DO 編號")
    ap.add_argument("--dwell", type=float, default=0.15, help="下筆後等糖絲成形 s")
    ap.add_argument("--cut", type=float, default=0.10, help="關閥後等糖絲斷 s")
    ap.add_argument("--dot-ms", type=float, default=120.0, help="糖點停留 ms/mm 直徑")
    ap.add_argument("--speed-quant", type=float, default=20.0,
                    help="速度量化級距 mm/s。必須跟 svg2points.py 用同一個值,"
                         "否則 Skill 2 保留的速度換檔點會對不上")
    ap.add_argument("--air-move", choices=["lmove", "jmove"], default="lmove",
                    help="空中移動的插補方式。lmove=LAPPRO 直線,路徑可預測;"
                         "jmove=JAPPRO 關節插補較快,但笛卡爾路徑不可預測,"
                         "抬筆高度要夠,否則可能中途下沉刮到成品")
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

    cmds, info = build_commands(
        d, v.base, v.accuracy, zup=v.zup, sig=v.signal,
        draw_speed=v.draw_speed, max_speed=v.max_speed,
        travel_speed=v.travel_speed, speed_quant=v.speed_quant,
        air_move=v.air_move, dwell=v.dwell, cut=v.cut, dot_ms=v.dot_ms)
    pkts = pack(cmds)

    link = Link(v.host, v.port, v.dry_run)
    t0 = time.time()
    for pk in pkts:
        link.send(pk)
    link.close()

    kinds = {}
    for c in cmds:
        kinds[c.split(",")[0]] = kinds.get(c.split(",")[0], 0) + 1
    print("動作序列:" + "  ".join(f"{k} {n}" for k, n in
                                   sorted(kinds.items(), key=lambda x: -x[1])))
    for si, (n, lo, hi) in enumerate(info):
        print(f"  筆劃 {si+1}/{len(strokes)}:{n} 點  速度 {lo:g}~{hi:g} mm/s")
    print(f"共 {len(cmds)} 個動作 -> {link.n} 個封包"
          f"{'' if v.dry_run else f'，載入耗時 {time.time()-t0:.1f}s'}")

if __name__ == "__main__":
    main()
