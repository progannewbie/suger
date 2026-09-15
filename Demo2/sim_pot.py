#!/usr/bin/env python3
"""茶壺模式離線模擬器 —— 不接機台,看手臂實際會收到並執行什麼。

為什麼不直接用 sugar_arm/sim.py:
那支判斷「有沒有在出糖」是看糖閥訊號。茶壺沒有閥,訊號永遠不會來,
它會認為整張圖都沒在畫糖 —— 軌跡圖是空的,凡是跟出糖狀態有關的邏輯都失效。

茶壺模式的出糖狀態只由高度決定:
    z == 0  壺嘴貼著紙面,糖在流
    z >  0  壺抬起來了,離場中
沒有中間狀態,因為沒有閥可以關。

用法:
  python3 sim_pot.py out/spiral_motion.json --trace out/spiral_trace.as \
      --plot out/spiral_sim.png
"""
import argparse, json, math, os, sys

BEAD_MM = 2.5                # 糖線寬估計,只影響畫圖粗細。實測後再調


class Arm:
    """對應 Demo2/arm/pot_server.as:收一行就執行一行,沒有緩衝。"""

    def __init__(self):
        self.trace, self.path = [], []
        self.t = 0.0
        self.here = None
        self.lastv = -1
        self.n = {}
        self.drawing = False

    def emit(self, line, kind=None):
        self.trace.append(line)
        if kind:
            self.n[kind] = self.n.get(kind, 0) + 1

    def speed(self, v):
        if v != self.lastv:
            self.emit(f"  SPEED {v:g} MM/S ALWAYS", "SPEED")
            self.lastv = v

    def do(self, line):
        f = line.split(",")
        c = f[0]

        if c == "base":
            o = [float(x) for x in f[1:7]]
            self.emit(f"  POINT org = TRANS({','.join(f'{x:g}' for x in o)})")
            return True

        if c == "acc":
            self.emit(f"  ACCURACY {float(f[1]):g} ALWAYS")
            return True

        if c in ("lmove", "jmove"):
            x, y, z, v = (float(q) for q in f[1:5])
            self.speed(v)
            self.emit(f"  POINT st = SHIFT(org BY {x:g}, {y:g}, {z:g})")
            self.emit(f"  {c.upper()} st", c.upper())
            # 壺嘴貼紙面 = 糖在流。沒有閥,高度就是唯一的開關。
            self.drawing = (z == 0)
            d = 0.0 if self.here is None else math.dist(self.here, (x, y))
            self.t += (d + abs(z)) / max(v, 1e-6)
            self.here = (x, y)
            self.path.append((x, y, v, self.drawing))
            return True

        if c == "ldepart":
            d, v = float(f[1]), float(f[2])
            self.speed(v)
            self.emit(f"  LDEPART {d:g}", "LDEPART")
            self.t += d / max(v, 1e-6)
            self.drawing = False
            return True

        if c == "brk":
            self.emit("  BREAK", "BREAK")
            self.t += 0.05
            return True

        if c == "end":
            return False

        self.emit(f"  ; ?? unknown: {line}", "UNKNOWN")
        return True


def to_lines(d):
    """與 sugar_arm/stream.py 的 to_lines 相同格式,刻意不 import 它。"""
    out = []
    if d.get("base"):
        out.append("base," + ",".join(f"{x:g}" for x in d["base"]))
    if d.get("accuracy"):
        out.append(f"acc,{d['accuracy']:g}")
    for m in d["moves"]:
        o = m["op"]
        if o in ("lmove", "jmove"):
            out.append(f"{o},{m['x']:.2f},{m['y']:.2f},{m['z']:g},{m['v']:g}")
        elif o == "ldepart":
            out.append(f"ldepart,{m['d']:g},{m['v']:g}")
        elif o in ("brk", "end"):
            out.append(o)
    return out


def plot(path, out, bead=BEAD_MM):
    from PIL import Image, ImageDraw
    ink = [p for p in path if p[3]]
    if not ink:
        print("警告:沒有任何出糖動作,不畫圖")
        return
    xs = [p[0] for p in ink]
    ys = [p[1] for p in ink]
    W = (max(xs) - min(xs)) or 1.0
    H = (max(ys) - min(ys)) or 1.0
    PW = 900
    PH = max(int(PW * H / W), 80)
    im = Image.new("RGB", (PW + 40, PH + 40), "white")
    dr = ImageDraw.Draw(im)

    def px(x, y):
        return (int((x - min(xs)) / W * PW) + 20,
                int((max(ys) - y) / H * PH) + 20)

    w = max(int(bead / W * PW), 2)
    prev = None
    for x, y, v, ink_on in path:
        q = px(x, y)
        if prev is not None:
            if ink_on:
                # 傾角固定 -> 流量固定 -> 速度固定 -> 線寬均一,所以不做深淺變化
                dr.line([prev, q], fill=(150, 95, 40), width=w)
            else:
                dr.line([prev, q], fill=(120, 150, 255), width=1)
                dr.ellipse((q[0]-3, q[1]-3, q[0]+3, q[1]+3), outline=(60, 90, 220))
        prev = q
    im.save(out)


def main():
    ap = argparse.ArgumentParser(description="茶壺模式離線模擬器")
    ap.add_argument("json", help="plan_pot.py 的 motion.json")
    ap.add_argument("--trace", default=None, help="輸出展開後的 AS 動作序列")
    ap.add_argument("--plot", default=None, help="輸出軌跡圖 PNG")
    ap.add_argument("--bead", type=float, default=BEAD_MM,
                    help="糖線寬 mm,只影響畫圖粗細")
    ap.add_argument("--lines", default=None, help="另存送給手臂的原始字串")
    v = ap.parse_args()

    d = json.load(open(v.json, encoding="utf-8"))
    lines = to_lines(d)

    arm = Arm()
    for ln in lines:
        if not arm.do(ln):
            break

    print(f"{len(lines)} 個動作(離線,完全沒用到網路)")
    print(f"展開成 {len(arm.trace)} 行 AS:")
    for k, n in sorted(arm.n.items(), key=lambda x: -x[1]):
        print(f"    {k:10} {n:4d}")
    drawn = sum(1 for p in arm.path if p[3])
    print(f"出糖點 {drawn} / {len(arm.path)},預估 {arm.t:.1f} 秒")
    if "UNKNOWN" in arm.n:
        print(f"*** 有 {arm.n['UNKNOWN']} 個手臂看不懂的指令,它會回 ER")

    if v.lines:
        open(v.lines, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print(f"原始字串 -> {v.lines}")
    if v.trace:
        with open(v.trace, "w", encoding="utf-8") as f:
            f.write(f"; {os.path.basename(v.json)} 展開後手臂實際執行的動作\n")
            f.write("; 由 Demo2/sim_pot.py 產生,離線,沒有連過任何機台\n")
            f.write("\n".join(arm.trace) + "\n")
        print(f"AS 動作序列 -> {v.trace}")
    if v.plot:
        plot(arm.path, v.plot, v.bead)
        print(f"軌跡圖 -> {v.plot}  (棕線=出糖,藍線=空中移動)")


if __name__ == "__main__":
    main()
