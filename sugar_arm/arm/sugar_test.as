; =====================================================================
; sugar_test.as -- 手臂自測 (Kawasaki F60 / AS Language)
;
; 不需要 PC、不需要治具。在裝噴嘴之前先驗證手臂本身。
; 建議先用簽字筆綁在法蘭上,或乾跑(sigon = 0)看動作。
;
; 測三件事:
;   1. 方形 + 對角線 —— 座標方向、行程範圍、轉角行為
;   2. 短線段直線   —— 這段最重要,見下方說明
;   3. 糖閥訊號     —— 開關時機(sigon = 1 才會真的送訊號)
;
; ---------------------------------------------------------------------
; 為什麼要測「短線段」
;
; 手冊 4.5.4.2:Motion Type 2 在精度放大且姿態不變時,即使兩點很近也能
; 達到設定速度;Standard motion type 則不保證。而用 FOR 迴圈餵點一定是
; Standard(迴圈的 END 介於兩個移動指令之間),手冊自己的「沿指定路徑
; 運動」範例也一樣。
;
; 所以實際畫糖時,太短的線段可能達不到設定速度 -> 走得比預期慢 ->
; 糖流量固定的情況下線會變粗。第 2 段測試就是用來看這個:
; 同樣 120mm 的直線,一次用 1 大段走完,一次切成 40 個 3mm 小段走完,
; 用碼表量兩者耗時。如果後者明顯久很多,代表要把 PC 端的 --min-seg 調大。
;
; 執行:  >EXECUTE sugar_test
; ---------------------------------------------------------------------

.PROGRAM sugar_test()

  sigon = 0                  ; 1 = 真的開糖閥,0 = 乾跑只走路徑
  sig   = 1                  ; 糖閥 DO 編號
  zup   = 30                 ; 安全高度 mm
  vdraw = 60                 ; 畫線速度 mm/s
  vtrav = 200                ; 空走速度 mm/s
  side  = 40                 ; 方形邊長 mm

; cvbase 由 sugar_calib 算出。沒跑過校正就用下面這個暫時值。
; 註解掉這行才會用校正結果。
  POINT cvbase = TRANS(450,0,-120,0,180,0)

  ACCURACY 3 ALWAYS
  SIGNAL -sig

  PRINT "==== sugar_test start ===="

; ---------- 測試 1:方形 + 對角線 ----------
  PRINT "-- 1. square + diagonal --"
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(cvbase BY 0, 0, zup)
  LMOVE SHIFT(cvbase BY 0, 0, 0)
  BREAK
  CALL sub_on

  SPEED vdraw MM/S ALWAYS
  LMOVE SHIFT(cvbase BY side, 0, 0)
  LMOVE SHIFT(cvbase BY side, side, 0)
  LMOVE SHIFT(cvbase BY 0, side, 0)
  LMOVE SHIFT(cvbase BY 0, 0, 0)
  LMOVE SHIFT(cvbase BY side, side, 0)   ; 對角線
  BREAK
  CALL sub_off
  SPEED vtrav MM/S ALWAYS
  LDEPART zup
  BREAK

; ---------- 測試 2:一大段 vs 四十小段,量時間 ----------
  PRINT "-- 2a. one long 120mm segment, time it --"
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(cvbase BY -60, -30, zup)
  LMOVE SHIFT(cvbase BY -60, -30, 0)
  BREAK
  CALL sub_on
  SPEED vdraw MM/S ALWAYS
  LMOVE SHIFT(cvbase BY 60, -30, 0)      ; 120mm 一次走完
  BREAK
  CALL sub_off
  SPEED vtrav MM/S ALWAYS
  LDEPART zup
  BREAK
  PRINT "   expected 120/60 = 2.0 s"

  PRINT "-- 2b. same 120mm as 40 x 3mm segments --"
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(cvbase BY -60, -45, zup)
  LMOVE SHIFT(cvbase BY -60, -45, 0)
  BREAK
  CALL sub_on
  SPEED vdraw MM/S ALWAYS
  FOR i = 1 TO 40
    LMOVE SHIFT(cvbase BY -60 + i*3, -45, 0)
  END
  BREAK
  CALL sub_off
  SPEED vtrav MM/S ALWAYS
  LDEPART zup
  BREAK
  PRINT "   if 2b takes much longer than 2a,"
  PRINT "   raise --min-seg on the PC side"

; ---------- 測試 3:糖閥時機 ----------
  PRINT "-- 3. valve timing, 3 dots --"
  FOR i = 1 TO 3
    SPEED vtrav MM/S ALWAYS
    LMOVE SHIFT(cvbase BY -40 + i*20, 50, zup)
    LMOVE SHIFT(cvbase BY -40 + i*20, 50, 0)
    BREAK
    CALL sub_on
    TWAIT i*0.2                          ; 0.2 / 0.4 / 0.6 秒,看糖點大小差異
    CALL sub_off
    TWAIT 0.1
    LDEPART zup
    BREAK
  END

  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(cvbase BY 0, 0, zup)
  BREAK
  PRINT "==== sugar_test done ===="
.END


.PROGRAM sub_on()
  IF sigon == 1 THEN
    SIGNAL sig
    TWAIT 0.15
  END
.END

.PROGRAM sub_off()
  IF sigon == 1 THEN
    SIGNAL -sig
    TWAIT 0.10
  END
.END
