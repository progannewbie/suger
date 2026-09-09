#!/usr/bin/env python3
"""共用的 SVG 輸出 —— Skill 1 的產出格式。text2path 和 img2path 都用這支。"""
import numpy as np

def write_svg(path, strokes, widths, W, H, meta, bead):
    """輸出兩層:

      data-role="path"    權威資料 —— 中心線 + 每點粗細,給 Skill 2 讀
      data-role="preview" 視覺呈現 —— 逐段變寬的線,瀏覽器打開就看得到毛筆效果

    座標用標準 SVG 慣例(左上原點、y 軸向下、單位 mm)。
    Skill 2 要轉成手臂座標時翻 y、置中即可。
    """
    def sx(p): return p[0] - meta["x0"]
    def sy(p): return meta["y1"] - p[1]          # y 翻正成 SVG 向下

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {W:.2f} {H:.2f}" width="{W:.2f}mm" height="{H:.2f}mm">',
           f'  <!-- 糖畫路徑 by text2path.py。'
           f'單位 mm,y 軸向下(標準 SVG)。糖線寬 {bead}mm -->',
           f'  <metadata>']
    for k, v in meta.items():
        if k not in ("x0", "y1"):
            out.append(f'    <sugar-{k}>{v}</sugar-{k}>')
    out.append('  </metadata>')

    # --- 視覺層:逐段變寬,打開瀏覽器就看得到毛筆 ---
    out.append('  <g data-role="preview" fill="none" stroke="#1a1a1a" '
               'stroke-linecap="round" stroke-linejoin="round">')
    for s0, w0 in zip(strokes, widths):
        for k in range(1, len(s0)):
            lw = max(float(w0[k]) * bead, 0.05)
            out.append(f'    <path d="M {sx(s0[k-1]):.2f} {sy(s0[k-1]):.2f} '
                       f'L {sx(s0[k]):.2f} {sy(s0[k]):.2f}" stroke-width="{lw:.2f}"/>')
    out.append('  </g>')

    # --- 權威層:中心線 + 粗細序列 ---
    out.append('  <g data-role="path" fill="none" stroke="none">')
    for i, (s0, w0) in enumerate(zip(strokes, widths)):
        d = "M " + " L ".join(f"{sx(p):.3f} {sy(p):.3f}" for p in s0)
        ws = ",".join(f"{float(x):.3f}" for x in w0)
        out.append(f'    <path id="stroke{i+1}" d="{d}" data-widths="{ws}"/>')
    out.append('  </g>\n</svg>')
    open(path, "w", encoding="utf-8").write("\n".join(out))

