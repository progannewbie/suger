; =====================================================================
; sugar_test.as -- 手臂自測 (Kawasaki F60 / AS Language)
;
; 只有手臂、沒有治具、沒有噴嘴、沒有畫布也能跑。全程在空中動作。
;
; ---------------------------------------------------------------------
; 執行前一定要做的三件事
;
; 1. 確認 TOOL 設定。沒裝工具就設成 TOOL 0(法蘭中心),
;    不然座標會以上一個工具的 TCP 為準,整個偏掉。
;      >TOOL NULL          或在示教器選 TOOL 0
;
; 2. 用示教器教一個點,取名 tsafe:
;      - 挑一個四周淨空的位置,前後左右上下各留 200mm 以上
;      - 姿態大約垂直朝下即可,不必精準
;      - 本程式所有動作都以這點為原點,只在它的「同一個高度平面」上移動,
;        不會往下降
;
; 3. 把示教器的速度倍率調到 20% 以下。第一次跑一定要有人手按住急停。
;
; ---------------------------------------------------------------------
; 分段執行
;
;   step = 0   只跑範圍檢查(最安全,先跑這個)
;   step = 1   加上方形 + 對角線
;   step = 2   加上短線段計時對照   <- 現階段最有價值的測試
;   step = 3   加上訊號開關時序
;   step = 9   全部
;
; 執行:  >EXECUTE sugar_test
; ---------------------------------------------------------------------

.PROGRAM sugar_test()

  step  = 0                  ; 先從 0 開始,確認沒問題再往上加
  sigon = 0                  ; 1 = 真的送 DO 訊號,0 = 只印訊息
  sig   = 1                  ; 糖閥 DO 編號
  side  = 40                 ; 方形邊長 mm
  vslow = 30                 ; 慢速 mm/s
  vdraw = 60                 ; 畫線速度 mm/s
  vtrav = 150                ; 空走速度 mm/s

  ACCURACY 3 ALWAYS
  IF sigon == 1 THEN
    SIGNAL -sig
  END

  PRINT "==== sugar_test start, step = ", step
  PRINT "all motion stays in the tsafe plane, no descent"

; ---------- step 0:範圍檢查 ----------
  PRINT "-- 0. reach check, slow --"
  SPEED vslow MM/S ALWAYS
  LMOVE tsafe
  BREAK
  PRINT "   at tsafe"
  TWAIT 1.0

  LMOVE SHIFT(tsafe BY -60, -60, 0)
  BREAK
  LMOVE SHIFT(tsafe BY 60, -60, 0)
  BREAK
  LMOVE SHIFT(tsafe BY 60, 60, 0)
  BREAK
  LMOVE SHIFT(tsafe BY -60, 60, 0)
  BREAK
  LMOVE tsafe
  BREAK
  PRINT "   120x120 area reachable"
  IF step < 1 THEN
    GOTO 900
  END

; ---------- step 1:方形 + 對角線 ----------
  PRINT "-- 1. square + diagonal --"
  SPEED vdraw MM/S ALWAYS
  LMOVE SHIFT(tsafe BY 0, 0, 0)
  LMOVE SHIFT(tsafe BY side, 0, 0)
  LMOVE SHIFT(tsafe BY side, side, 0)
  LMOVE SHIFT(tsafe BY 0, side, 0)
  LMOVE SHIFT(tsafe BY 0, 0, 0)
  LMOVE SHIFT(tsafe BY side, side, 0)      ; 對角線
  BREAK
  PRINT "   check corners: sharp or rounded?"
  PRINT "   rounded = ACCURACY too large"
  IF step < 2 THEN
    GOTO 900
  END

; ---------- step 2:短線段計時對照(最重要) ----------
; 手冊 4.5.4.2:FOR 迴圈餵點一定是 Standard motion type,
; 短線段不保證達到設定速度。這段就是量它到底差多少。
  PRINT "-- 2a. one 120mm segment --"
  PRINT "   START TIMING NOW"
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(tsafe BY -60, -30, 0)
  BREAK
  SPEED vdraw MM/S ALWAYS
  LMOVE SHIFT(tsafe BY 60, -30, 0)
  BREAK
  PRINT "   STOP. expected 120/60 = 2.0 s"
  TWAIT 2.0

  PRINT "-- 2b. same 120mm as 40 x 3mm --"
  PRINT "   START TIMING NOW"
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(tsafe BY -60, -45, 0)
  BREAK
  SPEED vdraw MM/S ALWAYS
  FOR i = 1 TO 40
    LMOVE SHIFT(tsafe BY -60 + i*3, -45, 0)
  END
  BREAK
  PRINT "   STOP. also 2.0 s if speed is held"
  PRINT "   much longer -> raise --min-seg on PC"
  TWAIT 2.0

  PRINT "-- 2c. same 120mm as 120 x 1mm --"
  PRINT "   START TIMING NOW"
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(tsafe BY -60, -60, 0)
  BREAK
  SPEED vdraw MM/S ALWAYS
  FOR i = 1 TO 120
    LMOVE SHIFT(tsafe BY -60 + i, -60, 0)
  END
  BREAK
  PRINT "   STOP. this is the worst case"
  IF step < 3 THEN
    GOTO 900
  END

; ---------- step 3:訊號時序 ----------
  PRINT "-- 3. signal timing --"
  FOR i = 1 TO 3
    SPEED vtrav MM/S ALWAYS
    LMOVE SHIFT(tsafe BY -40 + i*20, 50, 0)
    BREAK
    CALL sub_on
    TWAIT i*0.2                            ; 0.2 / 0.4 / 0.6 秒
    CALL sub_off
    TWAIT 0.3
  END
  PRINT "   watch DO", sig, "on the pendant IO monitor"

900 SPEED vslow MM/S ALWAYS
  LMOVE tsafe
  BREAK
  PRINT "==== sugar_test done ===="
.END


.PROGRAM sub_on()
  IF sigon == 1 THEN
    SIGNAL sig
  ELSE
    PRINT "   [dry] valve ON"
  END
.END

.PROGRAM sub_off()
  IF sigon == 1 THEN
    SIGNAL -sig
  ELSE
    PRINT "   [dry] valve OFF"
  END
.END
