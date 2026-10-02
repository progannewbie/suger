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
  python stream.py motion.json --host 192.168.5.3
  python stream.py motion.json --dry-run           # 只印字串,不連線
  python stream.py --fake-server                   # 起一個假手臂測協定
"""
import argparse, json, socket, sys, time

# 封包格式:每行結尾 \n,封包結尾多一個空行 —— 整包以 "\n\n" 收尾。
# 手臂端 sub_packet 看到這個結尾才回覆,所以封包可以超過 255 字:
# TCP_RECV 會切成多個元素,跨元素的行由手臂端接回去。
# 但「單行」不能超過 MAXLINE —— 要跟手臂端 sugar_init 的 maxline 一致
# (rmax 190 + maxline 64 <= 255,AS 字串上限)。
EOP = "\n\n"
MAXLINE = 64
# 預設仍是 250:每包都落在單一 $rbuf 元素內,手臂端「跨元素接行」那段
# 還沒上機驗證,先不走到它。實機確認過後可用 --maxlen 加大;
# 一包越大,手臂執行完才回 OK 越久,PC 等回覆的逾時是 30 秒。
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
    for ln in out:
        if len(ln) > MAXLINE:
            sys.exit(f"單行 {len(ln)} 字元,超過手臂端上限 {MAXLINE}:{ln}")
    return out

def pack(lines, maxlen=MAXLEN):
    """多行塞進同一個封包,塞滿就換一包。每包以 EOP 收尾。"""
    out, cur = [], ""
    for ln in lines:
        add = ln + "\n"
        if cur and len(cur) + len(add) + 1 > maxlen:
            out.append(cur + "\n"); cur = add
        else:
            cur += add
    if cur:
        out.append(cur + "\n")
    return out

def unpack(buf):
    """假手臂 / 模擬器用:從累積的資料切出一個完整封包,沒收完回 None"""
    if EOP not in buf:
        return None, buf
    pkt, rest = buf.split(EOP, 1)
    return pkt, rest

class Link:
    """一問一答:送一包、等一包。

    送出的封包以 "\\n\\n" 收尾,手臂端靠它判斷一包收完了。
    回覆方向沒有結尾 —— AS 端的 TCP_SEND 把字串原樣送出,不會補 \\n,
    用 readline() 等換行會直接卡死。回覆的 framing 靠「送完就等回覆」。
    """
    def __init__(self, host, port, dry, timeout=30.0, start_timeout=600.0):
        self.dry, self.n, self.s = dry, 0, None
        self.timeout, self.start_timeout = timeout, start_timeout
        if not dry:
            self.s = socket.create_connection((host, port), timeout=timeout)

    def send(self, pkt):
        self.n += 1
        if self.dry:
            print(pkt)
            return
        # 第一包的 OK 要等手臂走 star -> org、再等開始訊號(倒出巧克力),
        # 可能等很久;之後每包照常 30 秒。
        first = self.n == 1
        if first:
            print(f"等手臂走到 org 並收到開始訊號(最多 {self.start_timeout:g} 秒)…",
                  flush=True)
        self.s.settimeout(self.start_timeout if first else self.timeout)
        self.s.sendall(pkt.encode("ascii"))
        try:
            rep = self.s.recv(64).decode("ascii", "replace").strip()
        except socket.timeout:
            sys.exit("手臂沒有回覆" + ("(沒等到開始訊號?)" if first else ""))
        if first:
            print("開始畫", flush=True)
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
        buf = ""
        while True:
            pkt, buf = unpack(buf)
            if pkt is None:
                data = c.recv(4096)
                if not data:
                    break
                buf += data.decode("ascii", "replace")
                continue
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
    ap.add_argument("--host", default="192.168.5.3")
    ap.add_argument("--port", type=int, default=10000)
    ap.add_argument("--dry-run", action="store_true", help="只印字串,不連線")
    ap.add_argument("--fake-server", action="store_true")
    ap.add_argument("--maxlen", type=int, default=MAXLEN,
                    help=f"每包字元上限,預設 {MAXLEN}(實機驗證過再加大)")
    ap.add_argument("--start-timeout", type=float, default=600.0,
                    help="第一包等 OK 的秒數:手臂走到 org 並等開始訊號 2026")
    v = ap.parse_args()

    if v.fake_server:
        fake_server(v.port); return
    if not v.json:
        sys.exit("要給 motion.json,或用 --fake-server")

    d = json.load(open(v.json, encoding="utf-8"))
    lines = to_lines(d)
    pkts = pack(lines, v.maxlen)

    link = Link(v.host, v.port, v.dry_run, start_timeout=v.start_timeout)
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
