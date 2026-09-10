# suger — 機械手臂糖畫 / 糖畫寫字

用 Kawasaki F60 機械手臂寫毛筆字、畫糖畫。
輸入一個中文字或一張圖,輸出手臂能連續走完的路徑,透過 Ethernet 即時串流給手臂。

```
輸入(字 / 圖)
  │
  ├─ Skill 1  sugar-stroke   → 一筆到底的毛筆 SVG(中心線 + 每點粗細)
  ├─ Skill 2  sugar-points   → 手臂平面座標(曲率自適應,點數砍 60%)
  └─ Skill 3  sugar-motion   → 動作型態 + 速度 + 停留時間 + TCP 串流
                             ↓ TCP
                   arm/sugar_server.as(手臂端常駐程式)
```

## 核心概念

**糖流量固定,所以線寬 × 速度 = 常數。**
毛筆的粗細變化不是靠壓力,是靠速度 —— 走得慢就粗,走得快就細。
起筆頓一下(慢)、收筆出鋒(快)、牽絲最細(最快)。

**等速連續是硬需求。** 手臂每停頓一次,糖就積成一坨。所以:

- 筆劃線段之間絕對不放 `BREAK`,靠 `ACCURACY ... ALWAYS` 讓控制器連續 blend
- 通訊採「先收滿一整筆劃再動」,不是收一點動一點

**中文字用筆劃資料庫,不用描圖。**
[makemeahanzi](https://github.com/skishore/makemeahanzi) 提供 9574 個漢字每一筆的
中心線(median)和正確筆順,品質遠高於把字渲染成點陣圖再骨架化。

## 安裝

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 中文字形資料 (29MB,不在 repo 裡)
mkdir -p sugar_arm/data
curl -o sugar_arm/data/graphics.txt \
  https://raw.githubusercontent.com/skishore/makemeahanzi/master/graphics.txt
```

只依賴 numpy + pillow。

## 用法

### 寫字(主線)

```bash
PY=.venv/bin/python

# Skill 1: 字 → 一筆到底的毛筆 SVG
$PY sugar_arm/text2path.py 台灣尚勇 --size 45 --cols 2 --brush --link char \
    --svg out.svg -o out.json --preview out.png

# Skill 2: SVG → 手臂座標
$PY sugar_arm/svg2points.py out.svg -o points.json --report

# Skill 3: 座標 → 通訊字串(先 dry-run 看封包)
$PY sugar_arm/stream.py points.json --dry-run --base 450,0,-120 --draw-speed 60

# 實際送給手臂
$PY sugar_arm/stream.py points.json --host 192.168.0.2 --base 450,0,-120
```

`--link` 決定書體:

| 值 | 書體 | 抬筆次數(4 字) |
|---|---|---|
| `none` | 楷書 | ~50 |
| `char` | 行書(字內連筆) | **3** |
| `all` | 草書(整幅一筆) | **0** |

### 畫圖

```bash
$PY sugar_arm/img2path.py 圖.jpg --width 110 --svg out.svg -o out.json --preview out.png
```

參數(門檻、簡化容差、碎筆下限)全部從量到的線寬自動推導,不用手調。
描邊乾淨的卡通線稿效果好;行草書法、照片、粗筆相連的圖會糊掉。

### 不接手臂測協定

```bash
$PY sugar_arm/stream.py --fake-server --port 10123      # 一個終端機
$PY sugar_arm/stream.py points.json --host 127.0.0.1 --port 10123   # 另一個
```

## 手臂端

`sugar_arm/arm/sugar_server.as` 載進控制器常駐執行,開 TCP 監聽,
收字串 → 解析 → 填緩衝陣列 → 一次連續走完。

TCP 指令簽名查證自 **F控通訊選項手冊 90210-1344DE** 1.6 節:

```
TCP_LISTEN     ret, port                          ret=0 成功
TCP_ACCEPT     ret, port, timeout, ipa[0]         ret=socket ID
TCP_SEND       ret, sock, $sbuf[0], 元素數, timeout
TCP_RECV       ret, sock, $rbuf[0], 元素數變數, timeout, 每元素字元上限
TCP_CLOSE      ret, sock
TCP_END_LISTEN ret, port
```

手冊列出的限制:

| 項目 | 範圍 |
|---|---|
| 埠號 | 8192–65535 |
| timeout | 1–60 秒 |
| 每元素字元上限 | 1–255(對應錯誤碼 E4007) |
| 單次收發 | ≤4096 bytes |

**需要 Ethernet 選購板卡**,沒裝會報 E4054。

### 通訊協定

```
BEG,<點數>              宣告一筆劃
PT,x,y,v,x,y,v,...      直線點,每點帶速度。單封包 ≤250 字元
RUN                     一次連續走完緩衝區
DOT,x,y,秒              糖點
BASE,x,y,z,o,a,t        畫布原點
SPD,畫線,空走,精度
AIR,0|1                 空中移動 0=LAPPRO 1=JAPPRO
HOME / END
```

手臂每收一行回 `OK` 或 `ER`。兩邊都不加換行 —— AS 的 `TCP_SEND` 不會補 `\n`,
framing 靠「送一行就等回覆」。

## 檔案

| 檔案 | 用途 |
|---|---|
| `sugar_arm/text2path.py` | 中文字 → 筆劃(Skill 1) |
| `sugar_arm/img2path.py` | 圖片 → 筆劃(Skill 1) |
| `sugar_arm/svgout.py` | 共用 SVG 輸出格式 |
| `sugar_arm/svg2points.py` | SVG → 手臂座標(Skill 2) |
| `sugar_arm/stream.py` | 座標 → 通訊字串(Skill 3) |
| `sugar_arm/arm/sugar_server.as` | 手臂端常駐接收程式 |
| `sugar_arm/path2as.py` | 舊路線:直接產 .as 檔上傳 |
| `sugar_arm/svg2path.py` | 備援:手繪 SVG → 座標 |
| `.claude/skills/` | Claude Code skill 定義(判斷規則) |

## 動作型態

只用兩種插補:直線畫圖,關節趕路。

| 情況 | 指令 |
|---|---|
| 筆劃內的每一段 | `LMOVE` |
| 移到下一筆起點上方 | `LAPPRO`(預設) / `JAPPRO`(`--air-move jmove`) |
| 下筆 | `LMOVE` |
| 抬筆 | `LDEPART` |

`APPRO` / `DEPART` 沿的是**工具 Z 軸**不是 base Z,所以退刀保證沿噴嘴軸垂直,
不會斜著刮到剛畫好的糖。

**圓弧插補(C1MOVE/C2MOVE)實測後放棄。** 圓弧段內速度不能變,而毛筆粗細
正是靠速度做的 —— 本質衝突。四字實測:開圓弧指令數 −16% 但封包數 +125%,
兩者都不是瓶頸。

## 已知限制

- **字形是楷書骨架**,加牽絲只能做到「連筆楷書」,做不出真正的行草字形。
  沒有開放的行草中心線資料庫。
- **實心大區塊只畫外框**,內部填不了。糖畫本來也填不了大面積。
- **粗細對比上限被手臂速度範圍綁死**。預設 4 倍(60→240mm/s),
  實測後用 `--max-speed` 調整。
- **飛白做不出來** —— 糖是連續流體。

## 授權

字形資料來自 [makemeahanzi](https://github.com/skishore/makemeahanzi)
(LGPL / Arphic Public License),不隨本 repo 散布,請照上面指令自行下載。
