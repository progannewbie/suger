#!/usr/bin/env python3
"""座標 -> 動作計畫(茶壺版)。只需要標準函式庫。

吃 shapes.py 的 points.json,輸出 motion.json。不碰網路 —— 送出是 stream.py 的事。

與 sugar_arm/plan.py 的差別,全部源自同一句話:
**茶壺沒有閥門,出糖由傾角決定,而傾角本次固定不變。**

由此推出五件事:
  沒有糖閥訊號         -> 不產生 op="sig"
  沒有開關閥的等待     -> 不產生 dwell / cut 的 wait
  傾角鎖死             -> o,a,t 寫進 base,全程不變,--base 必填六個數字
  流量恆定             -> 所有 lmove 同一速度,不看 widths
  全程不抬筆           -> ldepart 與 brk 都只在最後離場出現一次

刻意不 import sugar_arm 的任何模組。兩者物理模型不同,共用會讓任一邊的
調參意外影響另一邊。

用法:
  python3 plan_pot.py out/spiral.json -o out/spiral_motion.json \
      --base -50,-230,50,90,40,-90
"""
import argparse, json, math, os, sys

# 路徑中間絕對不放 BREAK。手臂每停頓一次,糖就積成一坨。
# brk 只在最末端離場前出現一次。


def plan(d, base, speed=60.0, travel_speed=200.0, zup=20.0, approach=None):
    """茶壺模式的動作計畫。

    輸入永遠是單一連續路徑(shapes.py 保證 n_strokes == 1)。
    """
    strokes = d["strokes"]
    if len(strokes) != 1:
        sys.exit(f"錯誤:茶壺模式只接受單一筆劃,收到 {len(strokes)} 條。\n"
                 f"      茶壺沒有閥門,筆劃之間斷不乾淨,必定牽絲。")

    st = strokes[0]
    # 下降那一步後面沒有 brk —— 茶壺沒有閥,糖一路都在流,停一下就是一坨糖。
    # 沒有 brk 就代表控制器會把「垂直下降」和「第一段畫線」blend 起來,
    # 轉角被抹圓。若下降速度遠高於畫線速度,抹圓的半徑會大到壺根本沒碰到紙。
    # 所以下降速度預設就等於畫線速度,讓轉彎半徑小到可以忽略。
    approach = speed if approach is None else approach
    mv = []

    x0, y0 = st[0]
    mv.append({"op": "lmove", "x": x0, "y": y0, "z": zup, "v": travel_speed,
               "why": "移到起點上方"})
    mv.append({"op": "lmove", "x": x0, "y": y0, "z": 0, "v": approach,
               "why": "垂直下降到紙面,速度同畫線以壓小 blend 半徑"})

    # 這裡沒有 brk、沒有開閥、沒有等待。
    # 傾角在 base 裡已經設好,壺一到位糖就在流。
    for (x, y) in st[1:]:
        mv.append({"op": "lmove", "x": x, "y": y, "z": 0, "v": speed})

    # 路徑最後一段是 shapes.py 加的切線外拉尾巴,
    # 走完就在圖案之外,此時抬起、把壺轉正斷流,滴糖落在圖外。
    mv.append({"op": "ldepart", "d": zup, "v": travel_speed,
               "why": "沿工具軸抬起,離場"})
    mv.append({"op": "brk", "why": "離場後才允許停頓"})
    mv.append({"op": "end"})

    info = {"points": len(st), "speed": speed, "approach_speed": approach,
            "start": [x0, y0], "finish": list(st[-1])}
    return mv, info


def estimate(mv):
    """粗估耗時。理想值 —— 沒有模擬加減速曲線,實機會更久。"""
    t, here = 0.0, None
    for m in mv:
        o = m["op"]
        if o == "lmove":
            p = (m["x"], m["y"])
            if here is not None:
                t += math.dist(here, p) / max(m["v"], 1e-6)
            t += abs(m.get("z", 0)) / max(m["v"], 1e-6)
            here = p
        elif o == "ldepart":
            t += m["d"] / max(m["v"], 1e-6)
        elif o == "brk":
            t += 0.20
    return round(t, 1)


def reach_check(mv, base, reach_min, reach_max):
    """離基座的水平距離。靠太近會踩內側死區,而且 J1 角速度會暴增。

    J1 追不上時控制器會自動降速,速度一掉糖就積成一坨 ——
    正好是「等速連續」這個硬需求最怕的事。
    """
    bx, by = base[0], base[1]
    r = [math.hypot(bx + m["x"], by + m["y"]) for m in mv if "x" in m]
    if not r:
        return None
    lo, hi = min(r), max(r)
    ratio = hi / max(lo, 1e-6)
    bad = []
    if lo < reach_min:
        bad.append(f"最近 {lo:.0f} mm < 最小工作半徑 {reach_min:.0f} mm")
    if hi > reach_max:
        bad.append(f"最遠 {hi:.0f} mm > 臂展 {reach_max:.0f} mm")
    return lo, hi, ratio, bad


def main():
    ap = argparse.ArgumentParser(description="points.json -> motion.json(茶壺版)")
    ap.add_argument("json", help="shapes.py 的 points.json")
    ap.add_argument("-o", "--out", default="motion.json")
    ap.add_argument("--base", required=True,
                    help="x,y,z,o,a,t 六個數字。o,a,t 是茶壺傾角,必填")
    ap.add_argument("--speed", type=float, default=60.0, help="畫線速度 mm/s")
    ap.add_argument("--travel-speed", type=float, default=200.0,
                    help="空中移動速度 mm/s。比原版保守,因壺重心外伸")
    ap.add_argument("--zup", type=float, default=20.0, help="離場抬起高度 mm")
    ap.add_argument("--approach-speed", type=float, default=None,
                    help="下降到紙面的速度 mm/s。預設同畫線速度,"
                         "調高會讓起筆轉角被抹圓,壺可能沒碰到紙")
    ap.add_argument("--accuracy", type=float, default=3.0,
                    help="連續 blend 精度 mm。越大轉角越圓滑")
    ap.add_argument("--reach-min", type=float, default=300.0,
                    help="RS07L 最小工作半徑 mm。待現場實測,預設值是保守估計")
    ap.add_argument("--reach-max", type=float, default=730.0,
                    help="RS07L 臂展 mm")
    ap.add_argument("--csv", default=None, help="另外輸出座標 CSV")

    # base 的 x 常常是負數,argparse 會把 "-50,-230,..." 當成另一個選項。
    # 現場不該為了這種事卡住,所以自己把它接成 --base=... 的形式。
    argv = []
    skip = False
    for i, a in enumerate(sys.argv[1:]):
        if skip:
            skip = False
            continue
        if a == "--base" and i + 1 < len(sys.argv) - 1:
            argv.append("--base=" + sys.argv[i + 2])
            skip = True
        else:
            argv.append(a)
    v = ap.parse_args(argv)

    b = [float(x) for x in v.base.split(",")]
    if len(b) != 6:
        sys.exit(f"錯誤:--base 需要六個數字 x,y,z,o,a,t,收到 {len(b)} 個。\n"
                 f"      茶壺的傾角(o,a,t)決定出糖量,不能省略。\n"
                 f"      範例:--base -50,-230,50,90,40,-90")

    d = json.load(open(v.json, encoding="utf-8"))
    mv, info = plan(d, b, v.speed, v.travel_speed, v.zup, v.approach_speed)
    est = estimate(mv)

    kinds = {}
    for m in mv:
        kinds[m["op"]] = kinds.get(m["op"], 0) + 1

    json.dump({"units": "mm", "mode": "teapot",
               "source": os.path.basename(v.json),
               "source_shape": d.get("source_shape"),
               "base": b, "tilt": b[3:], "accuracy": v.accuracy,
               "n_moves": len(mv), "op_counts": kinds,
               "speed": v.speed, "est_seconds": est,
               "stroke": info, "moves": mv},
              open(v.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("動作計畫:" + "  ".join(f"{k} {n}" for k, n in
                                  sorted(kinds.items(), key=lambda x: -x[1])))
    print(f"  {info['points']} 點,全程 {v.speed:g} mm/s 等速"
          f"(下降 {info['approach_speed']:g} mm/s)")
    print(f"  傾角 o={b[3]:g} a={b[4]:g} t={b[5]:g},全程不變")

    xs = [m["x"] for m in mv if "x" in m]
    ys = [m["y"] for m in mv if "y" in m]
    rx = max(abs(min(xs)), abs(max(xs)))
    ry = max(abs(min(ys)), abs(max(ys)))
    print(f"相對原點的最大偏移:X ±{rx:.1f}  Y ±{ry:.1f} mm")

    lo, hi, ratio, bad = reach_check(mv, b, v.reach_min, v.reach_max)
    print(f"離基座水平距離:{lo:.0f} ~ {hi:.0f} mm")
    if bad:
        print("*** 工作範圍警告 ***")
        for x in bad:
            print(f"    {x}")
        print(f"    把 --base 的 y 往外推可解決,圖形檔不用重新產生。")
    if ratio > 1.4:
        print(f"*** J1 角速度警告:內圈需要的角速度是外圈的 {ratio:.1f} 倍")
        print(f"    J1 追不上會自動降速,速度一掉糖就積成一坨。")
        print(f"    把工作區往外推可以壓低這個比值。")

    print(f"共 {len(mv)} 個動作,預估 {est} 秒 -> {v.out}")
    if est > 45:
        print(f"*** 超過 45 秒。茶壺離爐約 60 秒糖就凝固。")

    if v.csv:
        with open(v.csv, "w", encoding="utf-8") as f:
            f.write("no,X,Y,Z,speed\n")
            k = 0
            for m in mv:
                if m["op"] != "lmove":
                    continue
                k += 1
                f.write(f"{k},{m['x']:.2f},{m['y']:.2f},{m['z']:g},{m['v']:g}\n")
        print(f"座標 CSV -> {v.csv}  ({k} 個移動點)")


if __name__ == "__main__":
    main()
