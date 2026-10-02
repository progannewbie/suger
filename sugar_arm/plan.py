#!/usr/bin/env python3
"""
座標 -> 動作計畫 (Skill 3)。只需要標準函式庫。

吃 Skill 2 的 points.json,決定每一步「用什麼方式移動、移動到哪裡、多快」,
輸出 motion.json。這一關不碰網路 —— 送出是傳輸程式(stream.py)的事。

分開的好處:動作計畫可以存檔、審查、版本控制、重播。
出了問題可以直接看 motion.json,不必猜線上送了什麼。

茶壺模式(唯一模式)。茶壺沒有閥,壺一傾斜糖就一直流,由此推出:
  不產生 sig        沒有閥可以開關
  路徑中間沒有 brk / wait   手臂停一下就是一坨糖
  不抬筆、不空中移動        抬起來糖照樣流,只會滴一路
  多筆劃 -> 首尾相接成一筆  接線一樣會畫出來,會印出接了多長
  起點對齊 org      手臂端先走 star -> org、等訊號 2026 確認糖倒得出來才開始畫,
                    那一攤糖就留在 org。讓圖的第一點就是 org,那攤糖當作起筆,
                    不必從 org 拖一條線到起點。(--keep-center 改成維持圖置中)
  收尾              ldepart 抬起;手臂端收到 end 後回 star 把壺轉正斷流

流量固定,線寬 x 速度 = 常數,所以粗細仍然靠速度控制。

用法:
  python plan.py points.json -o motion.json --draw-speed 60
"""
import argparse, json, math, os, sys

def w2speed(w, vdraw, vmax, quant):
    """糖流量固定 -> 線寬 x 速度 = 常數。要細就跑快。"""
    v = vdraw / max(float(w), 1e-3)
    v = min(max(v, vdraw), vmax)
    return round(round(v / quant) * quant, 1)

def plan(d, zup=15.0, draw_speed=60.0, max_speed=0, travel_speed=300.0,
         speed_quant=20.0, keep_center=False):
    """所有糖畫邏輯都在這裡 —— 怎麼走、多快、什麼時候抬。

    手臂端完全不知道這些,它只負責照字串動。
    """
    strokes = [st for st in d["strokes"] if st]
    widths = d.get("widths")
    if not strokes:
        sys.exit("錯誤:points.json 裡沒有任何筆劃")
    vmax = max_speed or draw_speed * 4

    # 全部筆劃首尾相接成一條路徑。接線用下一筆起點的速度畫出來。
    pts, sp, info, joins = [], [], [], []
    for i, st in enumerate(strokes):
        w = widths[i] if widths else [1.0] * len(st)
        s = [w2speed(x, draw_speed, vmax, speed_quant) for x in w]
        info.append({"stroke": i + 1, "points": len(st),
                     "v_min": min(s), "v_max": max(s)})
        if pts:
            joins.append(round(math.dist(pts[-1], st[0]), 1))
        pts += [tuple(p) for p in st]
        sp += s

    shift = (0.0, 0.0)
    if not keep_center:
        shift = (-pts[0][0], -pts[0][1])
        pts = [(x + shift[0], y + shift[1]) for x, y in pts]

    mv = []
    if keep_center:
        # 手臂停在 org(0,0) 倒糖,畫到起點的這段也會留下糖線
        mv.append({"op": "lmove", "x": pts[0][0], "y": pts[0][1], "z": 0,
                   "v": draw_speed, "why": "從 org 拉到起點(會畫出來)"})
    for (x, y), v in zip(pts[1:], sp[1:]):
        mv.append({"op": "lmove", "x": round(x, 3), "y": round(y, 3), "z": 0, "v": v})
    mv.append({"op": "ldepart", "d": zup, "v": travel_speed, "why": "畫完抬起"})
    mv.append({"op": "brk", "why": "整條路徑唯一的停頓,離場後才停"})
    mv.append({"op": "end", "why": "手臂端回 star 把壺轉正"})

    extra = {"shift": [round(shift[0], 3), round(shift[1], 3)],
             "joins_mm": joins, "dots_ignored": len(d.get("dots", []))}
    return mv, info, extra

def estimate(mv):
    """粗估耗時。理想值 —— 沒有模擬加減速曲線,實機會更久。"""
    t, here = 0.0, None
    for m in mv:
        o = m["op"]
        if o in ("lmove", "jmove"):
            p = (m["x"], m["y"])
            if here is not None:
                t += math.dist(here, p) / max(m["v"], 1e-6)
            t += abs(m.get("z", 0)) / max(m["v"], 1e-6)
            here = p
        elif o == "ldepart":
            t += m["d"] / max(m["v"], 1e-6)
        elif o == "wait":
            t += m["t"]
        elif o == "brk":
            t += 0.20                    # 沉降 + 啟停的粗估
    return round(t, 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", help="Skill 2 的 points.json")
    ap.add_argument("-o", "--out", default="motion.json")
    ap.add_argument("--base", default=None, help="畫布原點 x,y,z 或 x,y,z,o,a,t")
    ap.add_argument("--accuracy", type=float, default=3.0,
                    help="精度 mm。設太小(尤其 0)控制器會逐點停,糖會積")
    ap.add_argument("--zup", type=float, default=15.0, help="畫完抬起高度 mm")
    ap.add_argument("--draw-speed", type=float, default=60.0, help="基準畫線速度 mm/s")
    ap.add_argument("--max-speed", type=float, default=0, help="0 = 畫線速度的 4 倍")
    ap.add_argument("--travel-speed", type=float, default=300.0, help="抬起速度 mm/s")
    ap.add_argument("--speed-quant", type=float, default=20.0,
                    help="速度量化級距。必須跟 svg2points.py 用同一個值")
    ap.add_argument("--keep-center", action="store_true",
                    help="圖維持置中在 org,從 org 拉一條線到起點;預設是起點對齊 org")
    ap.add_argument("--table", default=None,
                    help="另外輸出一份人看的點位表,手動示教時對照用")
    ap.add_argument("--csv", default=None,
                    help="只輸出移動點的座標 CSV,給試算表或手動輸入用")
    ap.add_argument("--area", type=float, default=0,
                    help="繪圖區邊長 mm。給了就檢查會不會超出")
    v = ap.parse_args()

    d = json.load(open(v.json, encoding="utf-8"))
    mv, info, extra = plan(d, v.zup, v.draw_speed, v.max_speed,
                           v.travel_speed, v.speed_quant, v.keep_center)

    base = None
    if v.base:
        b = [float(x) for x in v.base.split(",")]
        if len(b) == 3:
            b += [0.0, 180.0, 0.0]        # 工具朝下
        base = b

    kinds = {}
    for m in mv:
        kinds[m["op"]] = kinds.get(m["op"], 0) + 1
    speeds = [m["v"] for m in mv if m["op"] in ("lmove", "jmove")]
    est = estimate(mv)

    json.dump({"units": "mm", "mode": "teapot",
               "source": os.path.basename(v.json),
               **extra,
               "base": base, "accuracy": v.accuracy,
               "n_moves": len(mv), "op_counts": kinds,
               "speed_range": [min(speeds), max(speeds)] if speeds else None,
               "est_seconds": est,
               "strokes": info,
               "moves": mv},
              open(v.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("動作計畫:" + "  ".join(f"{k} {n}" for k, n in
                                  sorted(kinds.items(), key=lambda x: -x[1])))
    for s in info:
        print(f"  筆劃 {s['stroke']}:{s['points']} 點  "
              f"速度 {s['v_min']:g}~{s['v_max']:g} mm/s")
    print("茶壺模式:一筆到底、中途不停、不抬筆")
    if extra["joins_mm"]:
        j = extra["joins_mm"]
        print(f"*** {len(info)} 筆劃首尾相接,接線 {len(j)} 段共 {sum(j):.0f} mm 也會畫出來"
              f"(最長 {max(j):.0f} mm)。要乾淨請用「一筆到底」/「草書」")
    if extra["dots_ignored"]:
        print(f"*** 茶壺做不出單獨的糖點,略過 {extra['dots_ignored']} 個")
    if any(extra["shift"]):
        print(f"起點對齊 org:整張圖平移 X {extra['shift'][0]:+.1f}  Y {extra['shift'][1]:+.1f} mm")
    else:
        print("起點對齊 org" if not v.keep_center else "圖置中在 org,從 org 拉線到起點")
    # 範圍檢查
    xs = [m["x"] for m in mv if "x" in m]
    ys = [m["y"] for m in mv if "y" in m]
    if xs:
        rx, ry = max(abs(min(xs)), abs(max(xs))), max(abs(min(ys)), abs(max(ys)))
        print(f"相對 org 的最大偏移:X ±{rx:.1f}  Y ±{ry:.1f} mm")
        if v.area:
            half = v.area / 2
            ok = "在範圍內" if rx <= half and ry <= half else ">>> 超出範圍 <<<"
            print(f"繪圖區 {v.area:.0f}x{v.area:.0f}(半邊 {half:.0f}mm):{ok}")

    print(f"共 {len(mv)} 個動作,預估 {est} 秒 -> {v.out}")

    if v.table:
        with open(v.table, "w", encoding="utf-8") as f:
            f.write(f"# 點位表 —— 手動示教對照用\n")
            f.write(f"# 來源 {os.path.basename(v.json)}\n")
            f.write(f"# 座標是相對 org 的偏移,單位 mm(茶壺模式起點 = org)\n")
            f.write(f"# Z 為 0 表示貼著畫布,正值是抬起來\n")
            if base:
                f.write(f"# org = TRANS({','.join(f'{x:g}' for x in base)})\n")
            f.write("#\n")
            f.write(f"{'#':>4} {'動作':8} {'X':>9} {'Y':>9} {'Z':>7} "
                    f"{'速度':>6}  說明\n")
            f.write("-" * 62 + "\n")
            for i, m in enumerate(mv, 1):
                o = m["op"]
                if o in ("lmove", "jmove"):
                    f.write(f"{i:4d} {o:8} {m['x']:9.2f} {m['y']:9.2f} "
                            f"{m['z']:7.1f} {m['v']:6.0f}  {m.get('why','')}\n")
                elif o == "ldepart":
                    f.write(f"{i:4d} {o:8} {'':9} {'':9} {m['d']:7.1f} "
                            f"{m['v']:6.0f}  {m.get('why','沿工具軸退開')}\n")
                elif o == "sig":
                    f.write(f"{i:4d} {o:8} {'':9} {'':9} {'':7} "
                            f"{'':6}  DO {'ON' if m['n']>0 else 'OFF'}"
                            f"  {m.get('why','')}\n")
                elif o == "wait":
                    f.write(f"{i:4d} {o:8} {'':9} {'':9} {'':7} "
                            f"{'':6}  停 {m['t']}s  {m.get('why','')}\n")
                else:
                    f.write(f"{i:4d} {o:8} {'':9} {'':9} {'':7} {'':6}  "
                            f"{m.get('why','')}\n")
        print(f"點位表 -> {v.table}")

    if v.csv:
        with open(v.csv, "w", encoding="utf-8") as f:
            f.write("no,op,X,Y,Z,speed\n")
            k = 0
            for m in mv:
                if m["op"] not in ("lmove", "jmove"):
                    continue
                k += 1
                f.write(f"{k},{m['op']},{m['x']:.2f},{m['y']:.2f},"
                        f"{m['z']:g},{m['v']:g}\n")
        print(f"座標 CSV -> {v.csv}  ({k} 個移動點)")
    if base is None:
        print("沒給 --base:用手臂上示教的 org(跟倒糖確認的位置一致)")
    else:
        print("*** 給了 --base:手臂會在示教的 org 倒糖確認,卻在這個新原點作畫,兩處不一致")

if __name__ == "__main__":
    main()
