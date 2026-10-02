#!/usr/bin/env python3
"""糖畫線路圖產生器 —— 上傳圖片或輸入中文字,一路跑完 Skill 1 → 2 → 3。

一律茶壺模式:圖片固定一筆到底,文字預設草書(整幅一筆),
動作計畫起點對齊 org、中途不停不抬筆。按「送到手臂」用 stream.py 送出。

  Skill 1  sugar-stroke   ../sugar_arm/img2path.py    圖片 → 單線筆劃 SVG
                          ../sugar_arm/text2path.py   中文字 → 毛筆筆劃 SVG
  Skill 2  sugar-points   ../sugar_arm/svg2points.py  SVG → 手臂座標點位
  Skill 3  sugar-motion   ../sugar_arm/plan.py        點位 → 動作計畫
           離線模擬       ../sugar_arm/sim.py         動作計畫 → 手臂實際軌跡

中文字走字形資料庫(makemeahanzi),每一筆直接有中心線和正確筆順,
品質遠高於把字當圖片描邊。需要 ../sugar_arm/data/graphics.txt。

這裡只負責 UI 與檔案管理。刻意用子程序呼叫而不是 import:
各 skill 的參數與輸出都走 CLI,它們改版時 UI 不用跟著改。

每次執行在 src/out/<圖名或字>_<時間>/ 產出:
  1_stroke.svg    Skill 1 正式產出
  1_stroke.png    用真實糖線寬渲染的預覽
  2_points.json   Skill 2 手臂座標(曲率自適應精簡後)
  2_points.png    點位分佈圖
  3_motion.json   Skill 3 動作計畫,stream.py 直接吃這個送給手臂
  3_trace.as      模擬展開的 AS 動作序列
  3_sim.png       模擬軌跡圖

用法:
  python main.py
"""
import os, re, subprocess, sys, threading, time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

from PIL import Image, ImageTk

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
SA = HERE.parent / "sugar_arm"
SCRIPTS = ("img2path.py", "text2path.py", "svg2points.py", "plan.py", "sim.py")
GLYPHS = SA / "data" / "graphics.txt"
GLYPHS_URL = "https://raw.githubusercontent.com/skishore/makemeahanzi/master/graphics.txt"
ARM_IP = "192.168.5.3"      # 控制器 sugar_main 監聽 20000 埠(10000 在這台 E 控制器會 E4027)

IMAGE_TYPES = [("圖片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("所有檔案", "*.*")]

# 中文要能正常顯示。Consolas 沒有中文字,Tk 會自動補字型,
# 常補到 "@微軟正黑體" 這類直書字型,中文就整個躺平轉 90 度。
CJK_FONTS = ("Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans TC",
             "Microsoft YaHei UI", "PingFang TC", "Noto Sans CJK TC")

# 書體 = text2path 的 --link
LINKS = {"楷書(筆劃分離)": "none", "行書(字內連筆)": "char", "草書(整幅一筆)": "all"}

# 右側分頁:(key, 分頁名稱, 圖說)
TABS = (("s1", "1 線路圖", "糖色 = 實際糖線寬,淡藍 = 抬筆空走"),
        ("s2", "2 點位", "藍點 = 送給手臂的座標點,直線段稀、曲線段密"),
        ("s3", "3 模擬軌跡", "手臂實際會走的路徑。糖色越淺 = 跑越快(線越細),藍線 = 空中移動"))


def pick_font(candidates, size):
    have = set(font.families())
    for name in candidates:
        if name in have:
            return (name, size)
    return ("TkFixedFont", size)


def parse_base(text):
    """畫布原點:空白 = 不指定;否則 x,y,z 或 x,y,z,o,a,t。格式錯誤丟 ValueError。"""
    text = text.strip().replace("，", ",")        # 全形逗號也收
    if not text:
        return None
    vals = [float(x) for x in text.split(",")]
    if len(vals) not in (3, 6):
        raise ValueError("要 3 個數字 (x,y,z) 或 6 個數字 (x,y,z,o,a,t)")
    return ",".join(f"{x:g}" for x in vals)


def safe_name(text, limit=12):
    """字串 → 可當資料夾名稱的片段"""
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")
    return name[:limit] or "text"


def run_script(name, *args):
    """跑 sugar_arm 裡的一支程式,回傳 (成功與否, 輸出文字)。"""
    cmd = [sys.executable, str(SA / name), *map(str, args)]
    # Windows 主控台預設 cp950,不指定的話中文輸出會變亂碼
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=SA)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def stroke_stage(kind, source, f, opt):
    """Skill 1:圖片走描線,文字走字形資料庫。回傳 (成功與否, 輸出文字)。"""
    out = ["--svg", f("1_stroke.svg"), "--preview", f("1_stroke.png")]
    if kind == "text":
        args = [source, "--size", opt["size"], "--cols", opt["cols"],
                "--link", opt["link"], *out]
        if opt["vertical"]:
            args.append("--vertical")
        return run_script("text2path.py", *args)
    # 茶壺模式:糖一直在流,只能一筆到底
    args = [source, "--width", opt["width"], "--one-stroke", *out]
    if opt["invert"]:
        args.append("--invert")
    return run_script("img2path.py", *args)


def pipeline(kind, source, run_dir, opt):
    """依序產生每個階段的 (key, 標題, 成功與否, 輸出文字, 圖檔)。前一段失敗就停。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    f = lambda name: run_dir / name

    ok, log = stroke_stage(kind, source, f, opt)
    yield "s1", "Skill 1 線路圖", ok, log, f("1_stroke.png")
    if not ok:
        return

    ok, log = run_script("svg2points.py", f("1_stroke.svg"),
                         "-o", f("2_points.json"), "--preview", f("2_points.png"),
                         "--draw-speed", opt["speed"])
    yield "s2", "Skill 2 座標點位", ok, log, f("2_points.png")
    if not ok:
        return

    args = [f("2_points.json"), "-o", f("3_motion.json"), "--draw-speed", opt["speed"]]
    if opt["base"]:
        args += ["--base", opt["base"]]
    ok, log = run_script("plan.py", *args)
    if ok:
        ok2, log2 = run_script("sim.py", f("3_motion.json"),
                               "--trace", f("3_trace.as"), "--plot", f("3_sim.png"))
        ok, log = ok2, f"{log}\n\n[離線模擬]\n{log2}"
    yield "s3", "Skill 3 動作計畫", ok, log, f("3_sim.png")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("糖畫線路圖產生器")
        self.geometry("1280x840")
        self.minsize(960, 580)
        self.source = None              # ("image", Path) 或 ("text", str)
        self.run_dir = None
        self._photos = {}               # 持有 PhotoImage 參照,否則會被回收而變空白
        self.cjk = pick_font(CJK_FONTS, 9)[0]

        self._build_controls()
        self._build_views()
        self._build_log()

        missing = [s for s in SCRIPTS if not (SA / s).exists()]
        if missing:
            messagebox.showerror("找不到轉換程式", f"{SA} 裡缺少:\n" + "\n".join(missing))

    # ---------- 版面 ----------
    def _build_controls(self):
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="上傳圖片…", command=self.pick_image).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(bar, text="輸入文字").pack(side="left")
        self.text_var = tk.StringVar()
        ent = tk.Entry(bar, textvariable=self.text_var, width=18, font=(self.cjk, 12))
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self.write_text())
        ttk.Button(bar, text="寫字", command=self.write_text).pack(side="left")
        ttk.Label(bar, text="(用 / 換行)", foreground="#777").pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        self.run_btn = ttk.Button(bar, text="重新產生", command=self.convert,
                                  state="disabled")
        self.run_btn.pack(side="left")
        ttk.Button(bar, text="開啟輸出資料夾", command=self.open_out_dir).pack(side="right")

        opts = ttk.Frame(self, padding=(8, 0, 8, 4))
        opts.pack(fill="x")

        g1 = ttk.LabelFrame(opts, text="圖片", padding=(8, 2))
        g1.pack(side="left", padx=(0, 8))
        ttk.Label(g1, text="成品寬度 mm").pack(side="left")
        self.width_var = tk.StringVar(value="110")
        ttk.Spinbox(g1, from_=20, to=400, increment=10, width=5,
                    textvariable=self.width_var).pack(side="left", padx=(4, 10))
        self.invert_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(g1, text="黑白反轉", variable=self.invert_var).pack(side="left")

        g2 = ttk.LabelFrame(opts, text="文字", padding=(8, 2))
        g2.pack(side="left", padx=(0, 8))
        ttk.Label(g2, text="字高 mm").pack(side="left")
        self.size_var = tk.StringVar(value="45")
        ttk.Spinbox(g2, from_=15, to=200, increment=5, width=5,
                    textvariable=self.size_var).pack(side="left", padx=(4, 10))
        ttk.Label(g2, text="每行字數").pack(side="left")
        self.cols_var = tk.StringVar(value="0")
        ttk.Spinbox(g2, from_=0, to=20, increment=1, width=4,
                    textvariable=self.cols_var).pack(side="left", padx=(4, 10))
        # 茶壺模式預設整幅一筆;選其他書體時,筆劃之間的接線也會畫出來
        self.link_var = tk.StringVar(value="草書(整幅一筆)")
        ttk.Combobox(g2, textvariable=self.link_var, values=list(LINKS),
                     state="readonly", width=14).pack(side="left", padx=(0, 10))
        self.vertical_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(g2, text="直書", variable=self.vertical_var).pack(side="left")

        g3 = ttk.LabelFrame(opts, text="手臂", padding=(8, 2))
        g3.pack(side="left")
        ttk.Label(g3, text="畫線速度 mm/s").pack(side="left")
        self.speed_var = tk.StringVar(value="60")
        ttk.Spinbox(g3, from_=10, to=300, increment=10, width=5,
                    textvariable=self.speed_var).pack(side="left", padx=(4, 10))
        ttk.Label(g3, text="畫布原點 x,y,z[,o,a,t]").pack(side="left")
        self.base_var = tk.StringVar(value="")
        ttk.Entry(g3, textvariable=self.base_var, width=22).pack(side="left", padx=(4, 10))
        ttk.Label(g3, text="手臂 IP").pack(side="left")
        self.ip_var = tk.StringVar(value=ARM_IP)
        ttk.Entry(g3, textvariable=self.ip_var, width=13).pack(side="left", padx=(4, 6))
        self.send_btn = ttk.Button(g3, text="送到手臂", command=self.send_to_arm,
                                   state="disabled")
        self.send_btn.pack(side="left")

        self.status = tk.StringVar(value="上傳圖片,或輸入中文字後按「寫字」")
        ttk.Label(self, textvariable=self.status, padding=(8, 0)).pack(fill="x")

    def _build_views(self):
        pane = ttk.Frame(self, padding=8)
        pane.pack(fill="both", expand=True)
        pane.columnconfigure((0, 1), weight=1, uniform="v")
        pane.rowconfigure(1, weight=1)

        ttk.Label(pane, text="輸入").grid(row=0, column=0, sticky="sw")
        self.views = {}
        self.views["src"] = [self._canvas(pane, "src"), None]
        self.views["src"][0].grid(row=1, column=0, sticky="nsew", padx=(0, 6))

        self.tabs = ttk.Notebook(pane)
        self.tabs.grid(row=0, column=1, rowspan=2, sticky="nsew")
        for key, name, caption in TABS:
            frm = ttk.Frame(self.tabs)
            ttk.Label(frm, text=caption, padding=(2, 2)).pack(fill="x")
            c = self._canvas(frm, key)
            c.pack(fill="both", expand=True)
            self.views[key] = [c, None]          # [canvas, 原始 PIL 圖 或 文字]
            self.tabs.add(frm, text=name)

    def _canvas(self, parent, key):
        c = tk.Canvas(parent, bg="white", highlightthickness=1, highlightbackground="#bbb")
        c.bind("<Configure>", lambda e: self._redraw(key))
        return c

    def _build_log(self):
        frm = ttk.Frame(self, padding=(8, 0, 8, 8))
        frm.pack(fill="x")
        sb = ttk.Scrollbar(frm, orient="vertical")
        self.log = tk.Text(frm, height=9, wrap="word", font=(self.cjk, 9),
                           yscrollcommand=sb.set)
        sb.config(command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="x", expand=True)

    # ---------- 顯示 ----------
    def _show(self, key, path):
        with Image.open(path) as im:
            self.views[key][1] = im.convert("RGB")
        self._redraw(key)

    def _show_text(self, text):
        self.views["src"][1] = text
        self._redraw("src")

    def _clear(self, key):
        self.views[key][1] = None
        self._redraw(key)

    def _redraw(self, key):
        canvas, item = self.views[key]
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if item is None or cw < 10 or ch < 10:
            return
        if isinstance(item, str):                # 文字模式:把字直接排出來
            rows = item.split("/")
            n = max(max(len(r) for r in rows), 1)
            size = int(min(cw / n * 0.8, ch / len(rows) * 0.7, 160))
            canvas.create_text(cw // 2, ch // 2, text="\n".join(rows),
                               font=(self.cjk, -max(size, 12)), justify="center")
            return
        s = min(cw / item.width, ch / item.height)
        fit = item.resize((max(int(item.width * s), 1), max(int(item.height * s), 1)),
                          Image.LANCZOS)
        self._photos[key] = ImageTk.PhotoImage(fit)
        canvas.create_image(cw // 2, ch // 2, image=self._photos[key])

    def _append_log(self, title, text):
        self.log.insert("end", f"── {title} ──\n{text}\n\n")
        self.log.see("end")

    # ---------- 動作 ----------
    def pick_image(self):
        path = filedialog.askopenfilename(title="選擇圖片", filetypes=IMAGE_TYPES)
        if not path:
            return
        try:
            self._show("src", path)
        except Exception as e:
            messagebox.showerror("無法開啟圖片", str(e))
            return
        self.source = ("image", Path(path))
        self.run_btn.config(state="normal")
        self.convert()                           # 上傳完直接跑

    def write_text(self):
        text = self.text_var.get().strip()
        if not text.replace("/", "").strip():
            messagebox.showinfo("輸入文字", "請先輸入要寫的中文字")
            return
        if not GLYPHS.exists():
            messagebox.showerror(
                "缺少字形資料",
                f"文字模式需要字形資料庫(約 29MB),目前找不到:\n{GLYPHS}\n\n"
                f"請下載後放到上面的位置:\n{GLYPHS_URL}")
            return
        self._show_text(text)
        self.source = ("text", text)
        self.run_btn.config(state="normal")
        self.convert()

    def _read_options(self):
        try:
            width = float(self.width_var.get())
            size = float(self.size_var.get())
            speed = float(self.speed_var.get())
            cols = int(self.cols_var.get())
        except ValueError:
            raise ValueError("寬度、字高、速度必須是數字,每行字數必須是整數")
        if min(width, size, speed) <= 0 or cols < 0:
            raise ValueError("寬度、字高、速度必須大於 0,每行字數不可為負")
        try:
            base = parse_base(self.base_var.get())
        except ValueError as e:
            raise ValueError(f"畫布原點格式錯誤:{e}")
        return {"width": f"{width:g}", "size": f"{size:g}", "speed": f"{speed:g}",
                "cols": cols, "link": LINKS[self.link_var.get()],
                "vertical": self.vertical_var.get(), "base": base,
                "invert": self.invert_var.get()}

    def convert(self):
        if not self.source:
            return
        try:
            opt = self._read_options()
        except ValueError as e:
            messagebox.showerror("參數錯誤", str(e))
            return
        kind, src = self.source
        stem = src.stem if kind == "image" else safe_name(src)
        self.run_dir = OUT_DIR / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}"
        for key, *_ in TABS:
            self._clear(key)
        self.log.delete("1.0", "end")
        self.tabs.select(0)
        self.run_btn.config(state="disabled")
        self.send_btn.config(state="disabled")
        self.status.set("Skill 1 線路圖 處理中…")
        # 三段加起來可能要十幾秒,放背景執行緒才不會把視窗卡住
        threading.Thread(target=self._worker, args=(kind, src, self.run_dir, opt),
                         daemon=True).start()

    def _worker(self, kind, src, run_dir, opt):
        done = 0
        try:
            for stage in pipeline(kind, src, run_dir, opt):
                self.after(0, self._stage_done, *stage)
                if not stage[2]:
                    break
                done += 1
        except Exception as e:
            self.after(0, self._append_log, "執行失敗", str(e))
        self.after(0, self._finished, kind, done)

    def _stage_done(self, key, title, ok, log, png):
        self._append_log(title, log)
        if ok and png.exists():
            self._show(key, png)
        nxt = [t for k, t, _ in TABS if k > key]
        if ok and nxt:
            self.status.set(f"{title} 完成,{nxt[0][2:]} 處理中…")

    def _finished(self, kind, done):
        self.run_btn.config(state="normal")
        if done == len(TABS):
            self.send_btn.config(state="normal")
            self.status.set(f"全部完成,已存到 {self.run_dir}  "
                            "(確認無誤後按「送到手臂」)")
        elif done == 0 and kind == "image":
            self.status.set("第 1 步失敗,請看下方訊息(白線黑底可勾「黑白反轉」)")
        else:
            self.status.set(f"第 {done + 1} 步失敗,請看下方訊息")

    # ---------- 送手臂 ----------
    def send_to_arm(self):
        motion = self.run_dir / "3_motion.json" if self.run_dir else None
        if not motion or not motion.exists():
            messagebox.showinfo("送到手臂", "請先產生動作計畫")
            return
        ip = self.ip_var.get().strip()
        if not messagebox.askokcancel(
                "送到手臂",
                f"手臂會開始動作。\n\n"
                f"確認:\n"
                f"  1. 控制器已在執行 sugar_main\n"
                f"  2. 手臂在 star 附近、周圍淨空\n\n"
                f"送出後手臂會走 star → org,等訊號 2026(巧克力倒出)才開始畫,\n"
                f"畫完抬起並回 star 把壺轉正。\n"
                f"送往 {ip}"):
            return
        self.send_btn.config(state="disabled")
        self.run_btn.config(state="disabled")
        self._append_log("送到手臂", f"{motion}\n-> {ip}")
        self.status.set("連線手臂中…")
        threading.Thread(target=self._send_worker, args=(motion, ip), daemon=True).start()

    def _send_worker(self, motion, ip):
        # 不用 run_script:stream.py 要等開始訊號,可能很久,輸出要邊跑邊顯示
        cmd = [sys.executable, "-u", str(SA / "stream.py"), str(motion), "--host", ip]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace",
                                 env=env, cwd=SA)
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    self.after(0, self._send_line, line)
            ok = p.wait() == 0
        except Exception as e:
            self.after(0, self._send_line, f"執行失敗:{e}")
            ok = False
        self.after(0, self._send_done, ok)

    def _send_line(self, line):
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.status.set(line)

    def _send_done(self, ok):
        self.log.insert("end", "\n")
        self.send_btn.config(state="normal")
        self.run_btn.config(state="normal")
        self.status.set("已全部送給手臂" if ok else "送出失敗,請看下方訊息")

    def open_out_dir(self):
        target = self.run_dir if self.run_dir and self.run_dir.exists() else OUT_DIR
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(target)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(target)])
        else:
            subprocess.run(["xdg-open", str(target)])


if __name__ == "__main__":
    App().mainloop()
