#!/usr/bin/env python3
"""
path.json -> Kawasaki AS Language 程式

座標用 SHIFT(base BY dx,dy,dz) 相對畫布原點,想移動整幅畫只改 base 一行。
糖閥用一個 DO 訊號控制:SIGNAL n = 開,SIGNAL -n = 關。

用法:
  python path2as.py path.json -o sugar.as --base 400,0,-100 --speed 120
"""
import argparse, json, math

HDR = """; ================================================
; 糖人繪圖程式 (由 path2as.py 產生)
; 筆劃 {n_strokes} 條 / 點 {n_points} 個 / 畫線 {draw:.0f} mm
; 畫布尺寸 {w} x {h} mm
; 糖閥訊號 = DO{sig} , 抬筆高度 = {zup} mm
; ================================================
.PROGRAM {name}()
  SIGNAL -{sig}                  ; 先確認糖閥關
  SPEED {travel_speed} MM/S ALWAYS
  ACCURACY {acc} ALWAYS          ; 小值=轉角準,大值=走得順
  base = TRANS({bx},{by},{bz},{o},{a},{t})   ; 畫布原點/姿態
  LMOVE SHIFT(base BY 0,0,{zup})
  BREAK
"""

FTR = """  SIGNAL -{sig}
  SPEED {travel_speed} MM/S ALWAYS
  LMOVE SHIFT(base BY 0,0,{zup})
  BREAK
.END
"""

def emit_dots(dots, cfg):
    """糖點:下筆 -> 開閥停留 -> 關閥 -> 抬筆。直徑靠停留時間控制"""
    L = []
    for i, (x, y, dia) in enumerate(dots):
        hold = round(cfg["dot_ms"] * dia / 1000.0, 3)
        L += [f"  ; --- 糖點 {i+1}/{len(dots)} (直徑 {dia} mm) ---",
              f"  SPEED {cfg['travel_speed']} MM/S ALWAYS",
              f"  LMOVE SHIFT(base BY {x},{y},{cfg['zup']})",
              f"  LMOVE SHIFT(base BY {x},{y},0)",
              f"  BREAK",
              f"  SIGNAL {cfg['sig']}",
              f"  TWAIT {hold}",
              f"  SIGNAL -{cfg['sig']}",
              f"  TWAIT {cfg['cut']}",
              f"  LMOVE SHIFT(base BY {x},{y},{cfg['zup']})"]
    return L

def w2speed(w, cfg):
    """糖流量固定,線寬 x 速度 = 常數。所以要細就跑快。
    量化成 QUANT 的倍數,不然每個點都會冒一行 SPEED。"""
    v = cfg["draw_speed"] / max(float(w), 1e-3)
    v = min(max(v, cfg["draw_speed"]), cfg["max_speed"])
    q = cfg["quant"]
    return round(round(v / q) * q, 1)

def emit(strokes, cfg, name, widths=None):
    L = []
    for i, s in enumerate(strokes):
        w = widths[i] if widths and i < len(widths) else None
        x0, y0 = s[0]
        L.append(f"  ; --- 筆劃 {i+1}/{len(strokes)} ({len(s)} 點) ---")
        L.append(f"  SPEED {cfg['travel_speed']} MM/S ALWAYS")
        L.append(f"  LMOVE SHIFT(base BY {x0},{y0},{cfg['zup']})")   # 移到起點上方
        L.append(f"  LMOVE SHIFT(base BY {x0},{y0},0)")              # 下筆
        L.append(f"  BREAK")
        L.append(f"  SIGNAL {cfg['sig']}")                            # 開糖閥
        if cfg["dwell"] > 0:
            L.append(f"  TWAIT {cfg['dwell']}")                       # 等糖絲成形
        cur = None
        for k, (x, y) in enumerate(s[1:], start=1):
            sp = w2speed(w[k], cfg) if w else cfg["draw_speed"]
            if sp != cur:                       # 只有速度變了才寫 SPEED
                L.append(f"  SPEED {sp} MM/S ALWAYS")
                cur = sp
            L.append(f"  LMOVE SHIFT(base BY {x},{y},0)")
        L.append(f"  BREAK")
        L.append(f"  SIGNAL -{cfg['sig']}")                           # 關糖閥
        if cfg["cut"] > 0:
            L.append(f"  TWAIT {cfg['cut']}")                         # 等糖絲斷
        L.append(f"  LMOVE SHIFT(base BY {x},{y},{cfg['zup']})")      # 抬筆
    return L

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("-o", "--out", default="sugar.as")
    ap.add_argument("--name", default="sugar")
    ap.add_argument("--base", default="400,0,-100", help="畫布原點 X,Y,Z mm")
    ap.add_argument("--pose", default="0,180,0", help="姿態 O,A,T deg (工具朝下)")
    ap.add_argument("--zup", type=float, default=15.0, help="抬筆高度 mm")
    ap.add_argument("--draw-speed", type=float, default=80.0, help="畫線速度 mm/s")
    ap.add_argument("--travel-speed", type=float, default=300.0, help="空走速度 mm/s")
    ap.add_argument("--accuracy", type=float, default=1.0)
    ap.add_argument("--signal", type=int, default=1, help="糖閥 DO 編號")
    ap.add_argument("--dwell", type=float, default=0.15, help="下筆後等待 s")
    ap.add_argument("--cut", type=float, default=0.10, help="關閥後等待 s")
    ap.add_argument("--max-speed", type=float, default=0,
                    help="毛筆最細處的速度上限 mm/s,0=畫線速度的 4 倍")
    ap.add_argument("--speed-quant", type=float, default=20.0,
                    help="速度量化級距 mm/s,越小越細膩但行數越多")
    ap.add_argument("--dot-ms", type=float, default=120.0,
                    help="糖點停留 ms/mm 直徑,越久點越大")
    ap.add_argument("--max-steps", type=int, default=2500, help="單一程式最大行數,超過就拆")
    v = ap.parse_args()

    d = json.load(open(v.json, encoding="utf-8"))
    strokes = d["strokes"]
    bx, by, bz = [float(x) for x in v.base.split(",")]
    o, a, t = [float(x) for x in v.pose.split(",")]
    cfg = dict(sig=v.signal, zup=v.zup, draw_speed=v.draw_speed,
               travel_speed=v.travel_speed, dwell=v.dwell, cut=v.cut,
               dot_ms=v.dot_ms, quant=v.speed_quant,
               max_speed=v.max_speed or v.draw_speed * 4)
    dots = d.get("dots", [])
    widths = d.get("widths") or None
    if widths:
        print(f"毛筆模式:速度 {v.draw_speed:.0f} ~ {cfg['max_speed']:.0f} mm/s "
              f"(級距 {v.speed_quant:.0f})")

    # 依行數把筆劃分批,避免單一程式過長
    batches, cur, cnt = [], [], 0
    wb, wcur = [], []
    for i, s in enumerate(strokes):
        n = len(s) * (2 if widths else 1) + 12
        if cur and cnt + n > v.max_steps:
            batches.append(cur); wb.append(wcur); cur, wcur, cnt = [], [], 0
        cur.append(s); wcur.append(widths[i] if widths else None); cnt += n
    if cur:
        batches.append(cur); wb.append(wcur)

    out = []
    for bi, b in enumerate(batches):
        nm = v.name if len(batches) == 1 else f"{v.name}{bi+1}"
        out.append(HDR.format(name=nm, n_strokes=len(b),
                              n_points=sum(len(s) for s in b),
                              draw=sum(math.dist(s[k-1], s[k]) for s in b
                                       for k in range(1, len(s))),
                              w=d["size_mm"][0], h=d["size_mm"][1],
                              sig=v.signal, zup=v.zup, acc=v.accuracy,
                              travel_speed=v.travel_speed,
                              bx=bx, by=by, bz=bz, o=o, a=a, t=t))
        out += emit(b, cfg, nm, wb[bi] if widths else None)
        if bi == len(batches) - 1 and dots:
            out += emit_dots(dots, cfg)
        out.append(FTR.format(sig=v.signal, zup=v.zup, travel_speed=v.travel_speed))

    if len(batches) > 1:      # 主程式依序呼叫
        out.append(f".PROGRAM {v.name}()")
        for bi in range(len(batches)):
            out.append(f"  CALL {v.name}{bi+1}")
        out.append(".END\n")

    open(v.out, "w", encoding="utf-8").write("\n".join(out))
    print(f"{len(strokes)} 筆劃 + {len(dots)} 糖點 -> {len(batches)} 個程式, "
          f"{len(out)} 行 -> {v.out}")

if __name__ == "__main__":
    main()
