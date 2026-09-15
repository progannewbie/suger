#!/usr/bin/env python3
"""檢查 motion.json 沒有違反茶壺模式的規則。

現場改參數後重跑,一秒確認新產出沒有破壞下面這幾條。
任一項 FAIL 就以非零碼結束。
"""
import json, math, sys, glob, os

RULES = """規則來源:Demo2/doc/技術規格.md 第 3.2 節"""

def check(path):
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
    return out, (min(r), max(r)) if r else None


def main():
    files = sys.argv[1:] or sorted(glob.glob("Demo2/out/*_motion.json"))
    if not files:
        sys.exit("找不到 motion.json")
    bad = 0
    for f in files:
        out, r = check(f)
        fails = [x for x in out if not x[1]]
        print(f"\n{os.path.basename(f)}")
        for name, ok, note in out:
            mark = "PASS" if ok else "FAIL"
            print(f"  {mark}  {name:14} {note if not ok else ''}")
        if r:
            print(f"        離基座 {r[0]:.0f} ~ {r[1]:.0f} mm"
                  f"  內外圈角速度比 {r[1]/r[0]:.2f}")
        bad += len(fails)
    print(f"\n{'PASS —— 全部通過' if not bad else f'FAIL —— {bad} 項未通過'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
