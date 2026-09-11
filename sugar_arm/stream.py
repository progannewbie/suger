#!/usr/bin/env python3
"""
把 motion.json 拆成字串傳給手臂。只需要標準函式庫。

這支程式不做任何決策 —— 走哪裡、多快、什麼時候開閥,全部是 Skill 3
(plan.py)在 motion.json 裡決定好的。這裡只做三件事:
拆成一行一個動作、塞進封包、送出去等回覆。

字串格式(手臂端 sugar_server.as 認的):
  lmove,x,y,z,v / jmove,x,y,z,v / ldepart,d,v
  sig,n / wait,t / brk / base,x,y,z,o,a,t / acc,n / end

用法:
  python stream.py motion.json --host 192.168.0.2
  python stream.py motion.json --dry-run           # 只印字串,不連線
  python stream.py --fake-server                   # 起一個假手臂測協定
"""
import argparse, json, socket, sys, time

# 手冊 90210-1344DE p.1-41:TCP_RECV 每元素上限 255 字元(對應 E4007)
# 留餘裕,避免卡在邊界
MAXLEN = 250

def to_lines(d):
    """motion.json -> 一行一個動作的字串"""
    out = []
    if d.get("base"):
        out.append("base," + ",".join(f"{x:g}" for x in d["base"]))
    if d.get("accuracy") is not None:
        out.append(f"acc,{d['accuracy']:g}")
    for m in d["moves"]:
        o = m["op"]
        if o in ("lmove", "jmove"):
            out.append(f"{o},{m['x']:.2f},{m['y']:.2f},{m['z']:g},{m['v']:g}")
        elif o == "ldepart":
            out.append(f"ldepart,{m['d']:g},{m['v']:g}")
        elif o == "sig":
            out.append(f"sig,{m['n']:g}")
        elif o == "wait":
            out.append(f"wait,{m['t']:g}")
        elif o in ("brk", "end"):
            out.append(o)
        else:
            sys.exit(f"motion.json 裡有不認得的動作:{o}")
    return out

def pack(lines, maxlen=MAXLEN):
    """多行塞進同一個封包,塞滿就換一包"""
    out, cur = [], ""
    for ln in lines:
        add = ln if not cur else "\n" + ln
        if len(cur) + len(add) > maxlen:
            out.append(cur); cur = ln
        else:
            cur += add
    if cur:
        out.append(cur)
    return out

class Link:
    """一問一答:送一包、等一包。

    不加換行當封包結尾 —— AS 端的 TCP_SEND 是把字串原樣送出,不會補 \\n,
    用 readline() 等換行會直接卡死。framing 靠「送完就等回覆」。
    """
    def __init__(self, host, port, dry, timeout=30.0):
        self.dry, self.n, self.s = dry, 0, None
        if not dry:
            self.s = socket.create_connection((host, port), timeout=timeout)
            self.s.settimeout(timeout)

    def send(self, pkt):
        self.n += 1
        if len(pkt) > MAXLEN:
            sys.exit(f"封包 {len(pkt)} 字元,超過上限 {MAXLEN}")
        if self.dry:
            print(pkt)
            return
        self.s.sendall(pkt.encode("ascii"))
        rep = self.s.recv(64).decode("ascii", "replace").strip()
        if not rep.startswith("OK"):
            sys.exit(f"手臂回覆 {rep!r},封包開頭 {pkt[:48]!r}")

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
        npkt = nline = longest = 0
        while True:
            data = c.recv(4096)
            if not data:
                break
            pkt = data.decode("ascii", "replace")
            npkt += 1
            lines = [x for x in pkt.split("\n") if x.strip()]
            nline += len(lines)
            longest = max(longest, len(pkt))
            if npkt % 5 == 0 or len(lines) < 3:
                print(f"  <- [{npkt}] {lines[0][:60]}"
                      f"{f' …+{len(lines)-1} 行' if len(lines) > 1 else ''}",
                      flush=True)
            c.sendall(b"OK")
            if lines and lines[-1].strip() == "end":
                break
        print(f"共 {npkt} 包 / {nline} 個動作,最長封包 {longest} 字元\n", flush=True)
        c.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", nargs="?", help="Skill 3 (plan.py) 的 motion.json")
    ap.add_argument("--host", default="192.168.0.2")
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--dry-run", action="store_true", help="只印字串,不連線")
    ap.add_argument("--fake-server", action="store_true")
    v = ap.parse_args()

    if v.fake_server:
        fake_server(v.port); return
    if not v.json:
        sys.exit("要給 motion.json,或用 --fake-server")

    d = json.load(open(v.json))
    lines = to_lines(d)
    pkts = pack(lines)

    link = Link(v.host, v.port, v.dry_run)
    t0 = time.time()
    for p in pkts:
        link.send(p)
    link.close()

    if not v.dry_run:
        print(f"{len(lines)} 個動作 -> {len(pkts)} 個封包,"
              f"送出耗時 {time.time()-t0:.1f}s")
        print(f"手臂預估執行 {d.get('est_seconds', '?')} 秒")

if __name__ == "__main__":
    main()
