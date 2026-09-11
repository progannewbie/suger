#!/usr/bin/env python3
"""
座標 -> 動作計畫 (Skill 3)。只需要標準函式庫。

吃 Skill 2 的 points.json,決定每一步「用什麼方式移動、移動到哪裡、多快」,
輸出 motion.json。這一關不碰網路 —— 送出是傳輸程式(stream.py)的事。

分開的好處:動作計畫可以存檔、審查、版本控制、重播。
出了問題可以直接看 motion.json,不必猜線上送了什麼。

用法:
  python plan.py points.json -o motion.json --base 450,0,-120 --draw-speed 60
"""
import argparse, json, math, os, sys

def w2speed(w, vdraw, vmax, quant):
    """糖流量固定 -> 線寬 x 速度 = 常數。要細就跑快。"""
    v = vdraw / max(float(w), 1e-3)
    v = min(max(v, vdraw), vmax)
    return round(round(v / quant) * quant, 1)

def plan(d, zup=15.0, sig=1, draw_speed=60.0, max_speed=0, travel_speed=300.0,
         speed_quant=20.0, air_move="lmove", dwell=0.15, cut=0.10, dot_ms=120.0):
    """所有糖畫邏輯都在這裡 —— 什麼時候開閥、停多久、怎麼抬筆。

    手臂端完全不知道這些,它只負責照字串動。
    """
    strokes = d["strokes"]
    widths = d.get("widths")
    dots = d.get("dots", [])
    vmax = max_speed or draw_speed * 4
    air = "jmove" if air_move == "jmove" else "lmove"
    mv, info = [], []

    for i, st in enumerate(strokes):
        w = widths[i] if widths else [1.0] * len(st)
        sp = [w2speed(x, draw_speed, vmax, speed_quant) for x in w]
        info.append({"stroke": i + 1, "points": len(st),
                     "v_min": min(sp), "v_max": max(sp)})
        x0, y0 = st[0]
        mv.append({"op": air, "x": x0, "y": y0, "z": zup, "v": travel_speed,
                   "why": "移到起點上方"})
        mv.append({"op": "lmove", "x": x0, "y": y0, "z": 0, "v": travel_speed,
                   "why": "垂直下筆"})
        mv.append({"op": "brk", "why": "確認到位才開閥"})
        mv.append({"op": "sig", "n": sig, "why": "開糖閥"})
        mv.append({"op": "wait", "t": dwell, "why": "等糖絲成形"})
        for (x, y), v in zip(st[1:], sp[1:]):
            mv.append({"op": "lmove", "x": x, "y": y, "z": 0, "v": v})
        mv.append({"op": "brk"})
        mv.append({"op": "sig", "n": -sig, "why": "關糖閥"})
        mv.append({"op": "wait", "t": cut, "why": "等糖絲斷"})
        mv.append({"op": "ldepart", "d": zup, "v": travel_speed,
                   "why": "沿工具軸垂直抬筆"})

    for (x, y, dia) in dots:
        mv.append({"op": air, "x": x, "y": y, "z": zup, "v": travel_speed})
        mv.append({"op": "lmove", "x": x, "y": y, "z": 0, "v": travel_speed})
        mv.append({"op": "brk"})
        mv.append({"op": "sig", "n": sig})
        mv.append({"op": "wait", "t": round(dot_ms * dia / 1000, 3),
                   "why": f"糖點 直徑 {dia}mm,停越久糖越多"})
        mv.append({"op": "sig", "n": -sig})
        mv.append({"op": "wait", "t": cut})
        mv.append({"op": "ldepart", "d": zup, "v": travel_speed})

    mv.append({"op": air, "x": 0, "y": 0, "z": zup, "v": travel_speed,
               "why": "回原點上方"})
    mv.append({"op": "brk"})
    mv.append({"op": "end"})
    return mv, info

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
    ap.add_argument("--zup", type=float, default=15.0, help="抬筆高度 mm")
    ap.add_argument("--signal", type=int, default=1, help="糖閥 DO 編號")
    ap.add_argument("--draw-speed", type=float, default=60.0, help="基準畫線速度 mm/s")
    ap.add_argument("--max-speed", type=float, default=0, help="0 = 畫線速度的 4 倍")
    ap.add_argument("--travel-speed", type=float, default=300.0)
    ap.add_argument("--speed-quant", type=float, default=20.0,
                    help="速度量化級距。必須跟 svg2points.py 用同一個值")
    ap.add_argument("--air-move", choices=["lmove", "jmove"], default="lmove",
                    help="空中移動的插補方式。jmove 較快但笛卡爾路徑不可預測")
    ap.add_argument("--dwell", type=float, default=0.15, help="下筆後等糖絲成形 s")
    ap.add_argument("--cut", type=float, default=0.10, help="關閥後等糖絲斷 s")
    ap.add_argument("--dot-ms", type=float, default=120.0, help="糖點停留 ms/mm 直徑")
    ap.add_argument("--table", default=None,
                    help="另外輸出一份人看的點位表,手動示教時對照用")
    ap.add_argument("--area", type=float, default=0,
                    help="繪圖區邊長 mm。給了就檢查會不會超出")
    v = ap.parse_args()

    d = json.load(open(v.json, encoding="utf-8"))
    mv, info = plan(d, v.zup, v.signal, v.draw_speed, v.max_speed,
                    v.travel_speed, v.speed_quant, v.air_move,
                    v.dwell, v.cut, v.dot_ms)

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

    json.dump({"units": "mm",
               "source": os.path.basename(v.json),
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
            f.write(f"# 座標是相對 org(繪圖區中心)的偏移,單位 mm\n")
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
    if base is None:
        print("提醒:沒給 --base,手臂會用它自己的預設原點")

if __name__ == "__main__":
    main()
