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
import argparse, json, socket, sys, time

# 手冊 90210-1344DE p.1-41:TCP_RECV 每個陣列元素上限 255 字元(對應 E4007)
# 留一點餘裕,避免剛好卡在邊界
MAXLEN = 250

def w2speed(w, vdraw, vmax, quant):
    """糖流量固定 -> 線寬 x 速度 = 常數。要細就跑快。"""
    v = vdraw / max(float(w), 1e-3)
    v = min(max(v, vdraw), vmax)
    return round(round(v / quant) * quant, 1)

def pt_packets(stroke, speeds):
    """把點切成不超過 255 字元的 PT 封包"""
    out, cur = [], "PT"
    for (x, y), v in zip(stroke, speeds):
        piece = f",{x:.2f},{y:.2f},{v:g}"
        if len(cur) + len(piece) > MAXLEN - 2:
            out.append(cur); cur = "PT"
        cur += piece
    if cur != "PT":
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
    ap.add_argument("--dot-ms", type=float, default=120.0, help="糖點停留 ms/mm 直徑")
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
    quant = 10.0

    link = Link(v.host, v.port, v.dry_run)
    t0 = time.time()

    if v.base:
        b = [float(x) for x in v.base.split(",")]
        if len(b) == 3:
            b += [0.0, 180.0, 0.0]            # 工具朝下
        link.send("BASE," + ",".join(f"{x:g}" for x in b))
    link.send(f"SPD,{v.draw_speed:g},{v.travel_speed:g},{v.accuracy:g}")

    for i, s in enumerate(strokes):
        w = widths[i] if widths else [1.0] * len(s)
        sp = [w2speed(x, v.draw_speed, vmax, quant) for x in w]
        print(f"筆劃 {i+1}/{len(strokes)}:{len(s)} 點 "
              f"速度 {min(sp):g}~{max(sp):g} mm/s")
        link.send(f"BEG,{len(s)}")
        for p in pt_packets(s, sp):
            link.send(p)
        link.send("RUN")

    for (x, y, dia) in dots:
        link.send(f"DOT,{x:.2f},{y:.2f},{v.dot_ms*dia/1000:.3f}")

    link.send("HOME")
    link.send("END")
    link.close()
    print(f"\n{len(strokes)} 筆劃 + {len(dots)} 糖點 -> {link.n} 個封包"
          f"{'' if v.dry_run else f'，耗時 {time.time()-t0:.1f}s'}")

if __name__ == "__main__":
    main()
