; =====================================================================
; sugar_calib.as -- 畫布三點校正 (Kawasaki F60 / AS Language)
;
; 不需要 PC。用示教器教三個點,算出畫布座標系,之後所有路徑都以它為基準。
; 石板挪動或換一塊板,重教三點就好,路徑檔完全不用重算。
;
; ---------------------------------------------------------------------
; 操作步驟
;
; 前提:要有一個實體平面當基準(畫布、石板、或暫時用一塊平整的板)。
; 還沒有畫布的話,這支先不用跑 —— 先跑 sugar_test 就好。
;
; 1. 用示教器在平面上教三個點(工具要用實際的噴嘴,TCP 必須已經校好;
;    還沒裝工具就先設 TOOL 0 用法蘭中心,之後裝了噴嘴要重教):
;      cvo   畫布原點        —— 圖形會以這裡為中心
;      cvx   原點往 +X 方向的任一點
;      cvy   +Y 那半邊的任一點(不必垂直,程式會自己正交化)
;
;    三點要盡量分開,太近會放大角度誤差。建議間距 100mm 以上。
;
; 2. 執行本程式:
;      >EXECUTE sugar_calib
;
; 3. 讀出結果:
;      >POINT cvbase
;    把顯示的 X Y Z O A T 六個值填進 PC 端的 --base 參數。
;
; 4. 驗證(本程式最後會做):手臂會移到原點上方,再沿 +X 走 50mm。
;    看它是不是貼著畫布邊緣走,方向對不對。
;
; ---------------------------------------------------------------------
; FRAME(p1, p2, p3, p4) 回傳一個相對座標系(手冊 90209-1025DE p.9-49):
;   p1 -> p2  決定 X 軸正方向
;   p3        決定 XY 平面,且位於 +Y 側
;   p4        決定原點
; 只取三個點的位置分量,姿態不影響。
; =====================================================================

.PROGRAM sugar_calib()

  zup   = 30                 ; 驗證時的安全高度 mm
  vchk  = 100                ; 驗證動作速度 mm/s

; ---------- 算出畫布座標系 ----------
  POINT cvbase = FRAME(cvo, cvx, cvy, cvo)

  PRINT "---- canvas frame built ----"
  PRINT "run >POINT cvbase to read X Y Z O A T"
  PRINT "then put those 6 numbers into PC --base"

; ---------- 驗證:沿 +X 走 50mm ----------
  SPEED vchk MM/S ALWAYS
  ACCURACY 1 ALWAYS

  LMOVE SHIFT(cvbase BY 0, 0, zup)       ; 原點上方
  BREAK
  PRINT "at canvas origin, height ", zup
  TWAIT 1.0

  LMOVE SHIFT(cvbase BY 50, 0, zup)      ; 沿 +X 走 50mm
  BREAK
  PRINT "moved +X 50mm"
  TWAIT 1.0

  LMOVE SHIFT(cvbase BY 50, 50, zup)     ; 再沿 +Y 走 50mm
  BREAK
  PRINT "moved +Y 50mm"
  TWAIT 1.0

  LMOVE SHIFT(cvbase BY 0, 0, zup)       ; 回原點上方
  BREAK
  PRINT "---- done. check the square corners ----"
.END
