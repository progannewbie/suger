#!/usr/bin/env python3
"""
手臂模擬器 —— 不接機台,看手臂實際會收到並執行哪些動作。

它扮演手臂,用**跟 sugar_server.as 完全一樣的狀態機**解析 TCP 協定,
把每一道 AS 動作指令展開寫成 trace.as,並估算時間、畫出軌跡圖。

用法 —— 離線,不開網路不接任何東西:
  python sim.py motion.json --trace trace.as --plot sim.png

用法 —— 當假手臂,順便驗證真實的 TCP 收發(兩個終端機):
  python sim.py --serve --port 10555 --trace trace.as
  python stream.py motion.json --host 127.0.0.1 --port 10555
"""
import argparse, json, math, os, socket, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BREAK_SETTLE = 0.05          # BREAK 等待到位的沉降時間估計 s
ACC_PENALTY  = 0.15          # 每次啟停的加減速額外時間估計 s

class Arm:
    """對應 sugar_server.as:收一行就執行一行,沒有緩衝。"""
    def __init__(self, zup, sig, vdraw, vtrav, acc):
        self.zup, self.sig, self.acc = zup, sig, acc
        self.vtrav = vtrav
        self.base = (450.0, 0.0, -120.0, 0.0, 180.0, 0.0)
        self.trace, self.path, self.t = [], [], 0.0
        self.here = None
        self.pen = False
        self.lastv = -1
        self.n = {}

    def emit(self, line, dt=0.0, kind=None):
        self.trace.append(line)
        self.t += dt
        if kind:
            self.n[kind] = self.n.get(kind, 0) + 1

    def _go(self, x, y, v):
        d = 0.0 if self.here is None else math.dist(self.here, (x, y))
        self.here = (x, y)
        self.path.append((x, y, v, self.pen))
        return d / max(v, 1e-6)

    def _speed(self, v):
        if v != self.lastv:
            self.emit(f"  SPEED {v:g} MM/S ALWAYS", 0.0, "SPEED")
            self.lastv = v

    def cmd(self, line):
        for one in line.split("\n"):
            one = one.strip()
            if one and not self._one(one):
                return False
        return True

    def _one(self, line):
        f = line.split(",")
        c = f[0]
        if c in ("lmove", "jmove"):
            x, y, z, v = (float(q) for q in f[1:5])
            self._speed(v)
            AS = "LMOVE" if c == "lmove" else "JMOVE"
            dt = self._go(x, y, v) + (abs(z) / v if z else 0.0)
            self.emit(f"  {AS} SHIFT(base BY {x:.2f},{y:.2f},{z:g})", dt, AS)
        elif c == "ldepart":
            dist, v = float(f[1]), float(f[2])
            self._speed(v)
            self.pen = False
            self.emit(f"  LDEPART {dist:g}", dist / v, "LDEPART")
        elif c == "sig":
            n = int(float(f[1]))
            self.pen = n > 0
            self.emit(f"  SIGNAL {n}", 0.0, "SIGNAL")
        elif c == "wait":
            t = float(f[1])
            self.emit(f"  TWAIT {t:g}", t, "TWAIT")
        elif c == "brk":
            self.emit("  BREAK", BREAK_SETTLE + ACC_PENALTY, "BREAK")
        elif c == "base":
            self.base = tuple(float(x) for x in f[1:7])
            self.emit(f"  base = TRANS({','.join(f[1:7])})")
        elif c == "acc":
            self.acc = float(f[1])
            self.emit(f"  ACCURACY {self.acc:g} ALWAYS")
        elif c == "end":
            return False
        return True

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
    ap.add_argument("json", nargs="?", help="Skill 3 的 motion.json(離線模式)")
    ap.add_argument("--serve", action="store_true", help="改當假手臂,等 TCP 連線")
    ap.add_argument("--port", type=int, default=10555)
    ap.add_argument("--trace", default="trace.as", help="輸出 AS 動作序列")
    ap.add_argument("--plot", default=None, help="輸出軌跡圖 PNG")
    ap.add_argument("--zup", type=float, default=15.0)
    ap.add_argument("--signal", type=int, default=1)
    ap.add_argument("--bead", type=float, default=2.5)
    v = ap.parse_args()

    if not v.serve and not v.json:
        sys.exit("要給 motion.json,或加 --serve 當假手臂")

    arm = Arm(v.zup, v.signal, 60, 300, 3)
    arm.emit(".PROGRAM sugar_replay()")

    if v.serve:
        n = run_server(arm, v.port)
        src = f"收到 {n} 個封包"
    else:
        import stream
        d = json.load(open(v.json, encoding="utf-8"))
        lines = stream.to_lines(d)
        n = run_offline(arm, lines)
        src = f"{n} 個動作(離線,完全沒用到網路)"

    arm.emit(".END")
    open(v.trace, "w", encoding="utf-8").write("\n".join(arm.trace) + "\n")

    tot = sum(arm.n.values())
    print(f"\n{src}")
    print(f"展開成 {len(arm.trace)} 行 AS,其中動作指令 {tot} 個:")
    for k in sorted(arm.n, key=lambda x: -arm.n[x]):
        print(f"    {k:8s} {arm.n[k]:5d}")
    print(f"\n預估時間 {arm.t:.1f} 秒 (抬筆 {arm.n.get('LDEPART', 0)} 次)")
    print(f"AS 動作序列 -> {v.trace}")
    if v.plot:
        plot(arm.path, v.plot, v.bead)
        print(f"軌跡圖 -> {v.plot}  (糖色深淺=速度,藍線=空中移動)")


if __name__ == "__main__":
    main()
