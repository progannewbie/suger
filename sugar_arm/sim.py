#!/usr/bin/env python3
"""
手臂模擬器 —— 不接機台,看手臂實際會收到並執行哪些動作。

它扮演手臂,用**跟 sugar_server.as 完全一樣的狀態機**解析 TCP 協定,
把每一道 AS 動作指令展開寫成 trace.as,並估算時間、畫出軌跡圖。

用法 —— 離線,不開網路不接任何東西:
  python sim.py points.json --base 450,0,-120 --trace trace.as --plot sim.png

用法 —— 當假手臂,順便驗證真實的 TCP 收發(兩個終端機):
  python sim.py --serve --port 10555 --trace trace.as
  python stream.py points.json --host 127.0.0.1 --port 10555 --base 450,0,-120
"""
import argparse, json, math, os, socket, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BREAK_SETTLE = 0.05          # BREAK 等待到位的沉降時間估計 s
ACC_PENALTY  = 0.15          # 每次啟停的加減速額外時間估計 s

class Arm:
    """對應 sugar_server.as 的狀態"""
    def __init__(self, zup, sig, vdraw, vtrav, acc):
        self.zup, self.sig = zup, sig
        self.vdraw, self.vtrav, self.acc = vdraw, vtrav, acc
        self.airj = 0
        self.base = (450.0, 0.0, -120.0, 0.0, 180.0, 0.0)
        self.buf = []                       # (x, y, v)
        self.trace, self.path, self.t = [], [], 0.0
        self.here = None                    # 目前 XY,None = 未知
        self.n = {}                         # 指令計數

    def emit(self, line, dt=0.0, kind=None):
        self.trace.append(line)
        self.t += dt
        if kind:
            self.n[kind] = self.n.get(kind, 0) + 1

    def move(self, x, y, v, kind, pen):
        d = 0.0 if self.here is None else math.dist(self.here, (x, y))
        self.here = (x, y)
        self.path.append((x, y, v, pen))
        return d / max(v, 1e-6)

    # ---- 協定指令,對應 AS 的 CASE 分支 ----
    def cmd(self, line):
        f = line.split(",")
        c = f[0]
        if c == "BASE":
            self.base = tuple(float(x) for x in f[1:7])
            self.emit(f"  base = TRANS({','.join(f[1:7])})")
        elif c == "SPD":
            self.vdraw, self.vtrav, self.acc = (float(x) for x in f[1:4])
            self.emit(f"  ACCURACY {self.acc:g} ALWAYS")
        elif c == "AIR":
            self.airj = int(float(f[1]))
        elif c == "BEG":
            self.buf = []
        elif c == "PT":
            n = f[1:]
            for i in range(0, len(n) - 2, 3):
                self.buf.append((float(n[i]), float(n[i+1]), float(n[i+2])))
        elif c == "RUN":
            self.run()
        elif c == "DOT":
            self.dot(float(f[1]), float(f[2]), float(f[3]))
        elif c == "HOME":
            self.emit(f"  SIGNAL -{self.sig}")
            self.emit(f"  SPEED {self.vtrav:g} MM/S ALWAYS")
            self.emit(f"  LDEPART {self.zup:g}", self.zup/self.vtrav, "LDEPART")
            self.emit(f"  JAPPRO SHIFT(base BY 0,0,0), {self.zup:g}",
                      self.move(0, 0, self.vtrav, "JAPPRO", False), "JAPPRO")
            self.emit("  BREAK", BREAK_SETTLE)
        elif c == "END":
            self.emit(f"  SIGNAL -{self.sig}")
            return False
        return True

    def run(self):
        if len(self.buf) < 2:
            return
        x1, y1, _ = self.buf[0]
        appro = "JAPPRO" if self.airj else "LAPPRO"
        self.emit(f"; ---- 筆劃開始,{len(self.buf)} 點 ----")
        self.emit(f"  SPEED {self.vtrav:g} MM/S ALWAYS")
        self.emit(f"  {appro} SHIFT(base BY {x1:.2f},{y1:.2f},0), {self.zup:g}",
                  self.move(x1, y1, self.vtrav, appro, False), appro)
        self.emit(f"  LMOVE SHIFT(base BY {x1:.2f},{y1:.2f},0)",
                  self.zup / self.vtrav + ACC_PENALTY, "LMOVE")
        self.emit("  BREAK", BREAK_SETTLE)
        self.emit(f"  SIGNAL {self.sig}")
        self.emit("  TWAIT 0.15", 0.15)

        last = -1
        for x, y, v in self.buf[1:]:
            if v != last:
                self.emit(f"  SPEED {v:g} MM/S ALWAYS", 0.0, "SPEED")
                last = v
            self.emit(f"  LMOVE SHIFT(base BY {x:.2f},{y:.2f},0)",
                      self.move(x, y, v, "LMOVE", True), "LMOVE")
        self.emit("  BREAK", BREAK_SETTLE)
        self.emit(f"  SIGNAL -{self.sig}")
        self.emit("  TWAIT 0.10", 0.10)
        self.emit(f"  SPEED {self.vtrav:g} MM/S ALWAYS")
        self.emit(f"  LDEPART {self.zup:g}",
                  self.zup/self.vtrav + ACC_PENALTY, "LDEPART")
        self.buf = []

    def dot(self, x, y, dt):
        appro = "JAPPRO" if self.airj else "LAPPRO"
        self.emit(f"  {appro} SHIFT(base BY {x:.2f},{y:.2f},0), {self.zup:g}",
                  self.move(x, y, self.vtrav, appro, False), appro)
        self.emit(f"  LMOVE SHIFT(base BY {x:.2f},{y:.2f},0)",
                  self.zup/self.vtrav, "LMOVE")
        self.emit("  BREAK", BREAK_SETTLE)
        self.emit(f"  SIGNAL {self.sig}")
        self.emit(f"  TWAIT {dt:g}", dt)
        self.emit(f"  SIGNAL -{self.sig}")
        self.emit(f"  LDEPART {self.zup:g}", self.zup/self.vtrav, "LDEPART")

def plot(path, out, bead):
    from PIL import Image, ImageDraw
    pen = [p for p in path if p[3]]
    if not pen:
        return
    xs = [p[0] for p in pen]; ys = [p[1] for p in pen]
    W = max(xs)-min(xs) or 1; H = max(ys)-min(ys) or 1
    PW = 900; PH = max(int(PW*H/W), 80)
    im = Image.new("RGB", (PW+40, PH+40), "white"); d = ImageDraw.Draw(im)
    def px(x, y):
        return (int((x-min(xs))/W*PW)+20, int((max(ys)-y)/H*PH)+20)
    vs = [p[2] for p in pen]; vlo, vhi = min(vs), max(vs)
    prev = None
    for x, y, v, p in path:
        q = px(x, y)
        if prev is not None:
            if p:                                    # 出糖:速度越快顏色越淺、線越細
                t = (v-vlo)/max(vhi-vlo, 1e-9)
                col = (int(120+110*t), int(60+90*t), int(20+40*t))
                w = max(int(bead/W*PW*(1-0.6*t)), 1)
                d.line([prev, q], fill=col, width=w)
            else:                                    # 空中移動
                d.line([prev, q], fill=(120, 150, 255), width=1)
                d.ellipse((q[0]-3, q[1]-3, q[0]+3, q[1]+3), outline=(60,90,220))
        prev = q
    im.save(out)

def run_offline(arm, cmds):
    for line in cmds:
        if not arm.cmd(line):
            break
    return len(cmds)

def run_server(arm, port):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port)); srv.listen(1)
    print(f"模擬手臂監聽 :{port}  等 stream.py 連進來…", flush=True)
    c, a = srv.accept()
    print(f"連線來自 {a}", flush=True)
    n = 0
    while True:
        data = c.recv(4096)
        if not data:
            break
        n += 1
        alive = arm.cmd(data.decode("ascii", "replace").strip())
        c.sendall(b"OK")
        if not alive:
            break
    c.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", help="Skill 2 的 points.json(離線模式)")
    ap.add_argument("--serve", action="store_true", help="改當假手臂,等 TCP 連線")
    ap.add_argument("--port", type=int, default=10555)
    ap.add_argument("--base", default=None, help="畫布原點 x,y,z 或 x,y,z,o,a,t")
    ap.add_argument("--draw-speed", type=float, default=60.0)
    ap.add_argument("--max-speed", type=float, default=0)
    ap.add_argument("--travel-speed", type=float, default=300.0)
    ap.add_argument("--accuracy", type=float, default=3.0)
    ap.add_argument("--speed-quant", type=float, default=20.0)
    ap.add_argument("--air-move", choices=["lmove", "jmove"], default="lmove")
    ap.add_argument("--dot-ms", type=float, default=120.0)
    ap.add_argument("--trace", default="trace.as", help="輸出 AS 動作序列")
    ap.add_argument("--plot", default=None, help="輸出軌跡圖 PNG")
    ap.add_argument("--zup", type=float, default=15.0)
    ap.add_argument("--signal", type=int, default=1)
    ap.add_argument("--bead", type=float, default=2.5)
    v = ap.parse_args()

    if not v.serve and not v.json:
        sys.exit("要給 points.json,或加 --serve 當假手臂")

    arm = Arm(v.zup, v.signal, v.draw_speed, v.travel_speed, v.accuracy)
    arm.emit(".PROGRAM sugar_replay()")
    arm.emit(f"  SIGNAL -{v.signal}")

    if v.serve:
        n = run_server(arm, v.port)
        src = f"收到 {n} 個封包"
    else:
        import stream
        d = json.load(open(v.json))
        cmds, _ = stream.build_commands(
            d, v.base, v.draw_speed, v.max_speed, v.travel_speed,
            v.accuracy, v.speed_quant, v.air_move, v.dot_ms)
        n = run_offline(arm, cmds)
        src = f"{n} 個封包(離線,完全沒用到網路)"

    arm.emit(".END")
    open(v.trace, "w").write("\n".join(arm.trace) + "\n")

    tot = sum(arm.n.values())
    print(f"\n{src}")
    print(f"展開成 {len(arm.trace)} 行 AS,其中動作指令 {tot} 個:")
    for k in sorted(arm.n, key=lambda x: -arm.n[x]):
        print(f"    {k:8s} {arm.n[k]:5d}")
    lifts = arm.n.get("LAPPRO", 0) + arm.n.get("JAPPRO", 0)
    print(f"\n預估時間 {arm.t:.1f} 秒 (抬筆落筆 {lifts} 次)")
    print(f"AS 動作序列 -> {v.trace}")
    if v.plot:
        plot(arm.path, v.plot, v.bead)
        print(f"軌跡圖 -> {v.plot}  (糖色深淺=速度,藍線=空中移動)")


if __name__ == "__main__":
    main()
