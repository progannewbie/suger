#!/usr/bin/env python3
"""檢查 motion.json 沒有違反茶壺模式的規則。

現場改參數後重跑,一秒確認新產出沒有破壞下面這幾條。
任一項 FAIL 就以非零碼結束。
"""
import json, math, sys, glob, os

# 糖線寬估計 mm。明天量到實際值後改這裡,或用 --bead 覆寫。
BEAD_MM = 2.5

# 自我交叉的圖形,線一定會碰,不適用沾黏檢查
SELF_CROSSING = {"star"}

RULES = """規則來源:Demo2/doc/技術規格.md 第 3.2 節"""

def clearance(pts, min_gap_mm=15.0):
    """最近的非相鄰線距。路徑上相隔夠遠、空間上卻很近的兩點,就是會沾黏的地方。

    只用標準函式庫,所以取樣點數壓在能接受的範圍。
    """
    seg = [0.0]
    for a, b in zip(pts[:-1], pts[1:]):
        seg.append(seg[-1] + math.dist(a, b))
    total = seg[-1]
    n = 800
    q, tt = [], []
    for k in range(n):
        t = total * k / (n - 1)
        i = min(range(len(seg)), key=lambda j: abs(seg[j] - t))
        q.append(pts[i]); tt.append(seg[i])
    best = float("inf")
    for i in range(n):
        for j in range(i + 1, n):
            if abs(tt[i] - tt[j]) <= min_gap_mm:
                continue
            d = math.dist(q[i], q[j])
            if d < best:
                best = d
    return best


def check(path, bead=BEAD_MM):
    raw = open(path, encoding="utf-8").read()
    d = json.loads(raw)
    c = d["op_counts"]
    mv = d["moves"]
    draw = [m for m in mv if m["op"] == "lmove" and m.get("z") == 0][1:]
    b = d["base"]
    r = [math.hypot(b[0] + m["x"], b[1] + m["y"]) for m in mv if "x" in m]

    out = []
    out.append(("無糖閥訊號", '"sig"' not in raw,
                "茶壺沒有閥,發訊號手臂也不知道要做什麼"))
    out.append(("無等待動作", "wait" not in c,
                "糖一路在流,停一下就是一坨糖"))
    out.append(("BREAK 只一次", c.get("brk") == 1,
                f"實際 {c.get('brk')} 次。中途停頓會讓糖積起來"))
    out.append(("抬筆只一次", c.get("ldepart") == 1,
                f"實際 {c.get('ldepart')} 次。本次全程不抬筆"))
    out.append(("傾角固定", len(d["tilt"]) == 3,
                f"傾角 {d['tilt']}"))
    out.append(("畫線速度單一", len({m["v"] for m in draw}) == 1,
                f"實際有 {len({m['v'] for m in draw})} 種速度。"
                f"流量固定就該速度固定"))
    out.append(("起筆不被抹圓", mv[1]["v"] == draw[0]["v"],
                f"下降 {mv[1]['v']:g} vs 畫線 {draw[0]['v']:g} mm/s。"
                f"差太多會讓起筆轉角抹圓,壺沒碰到紙"))
    out.append(("時間在上限內", d["est_seconds"] <= 45,
                f"預估 {d['est_seconds']} 秒,上限 45 秒"))
    out.append(("離基座距離已知", bool(r),
                f"{min(r):.0f} ~ {max(r):.0f} mm"))

    shape = d.get("source_shape") or ""
    pts = [(m["x"], m["y"]) for m in mv if m["op"] == "lmove" and m.get("z") == 0]
    gap = None
    if shape in SELF_CROSSING:
        out.append((f"圈間不沾黏", True,
                    f"{shape} 本來就自我交叉,不適用"))
    elif len(pts) > 3:
        gap = clearance(pts)
        out.append(("圈間不沾黏", gap > bead,
                    f"最近線距 {gap:.1f} mm,糖線寬估 {bead:g} mm。"
                    f"線寬超過 {gap:.1f} mm 就會黏在一起"))
    return out, ((min(r), max(r)) if r else None), gap


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--bead")]
    bead = BEAD_MM
    for a in sys.argv[1:]:
        if a.startswith("--bead="):
            bead = float(a.split("=", 1)[1])
    files = args or sorted(glob.glob("Demo2/out/*_motion.json"))
    if not files:
        sys.exit("找不到 motion.json")
    bad = 0
    for f in files:
        out, r, gap = check(f, bead)
        fails = [x for x in out if not x[1]]
        print(f"\n{os.path.basename(f)}")
        for name, ok, note in out:
            mark = "PASS" if ok else "FAIL"
            print(f"  {mark}  {name:14} {note if not ok else ''}")
        if r:
            print(f"        離基座 {r[0]:.0f} ~ {r[1]:.0f} mm"
                  f"  內外圈角速度比 {r[1]/r[0]:.2f}"
                  + (f"  線距餘裕 {gap:.1f} mm" if gap else ""))
        bad += len(fails)
    print(f"\n{'PASS —— 全部通過' if not bad else f'FAIL —— {bad} 項未通過'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
