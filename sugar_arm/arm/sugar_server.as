; =====================================================================
; sugar_server.as -- 糖畫手臂端常駐接收程式 (Kawasaki F60 / AS Language)
;
; PC 端算好所有點位,透過 TCP 傳字串進來;手臂只負責解析與執行。
;
; 核心設計:先收滿一整筆劃再動,不是收一點動一點。
;   一問一答式的即時執行會變成 stop-and-go,每個停頓就是一坨糖。
;   PT 只填陣列不動作,收到 RUN 才一次連續走完。
;
; ---------------------------------------------------------------------
; 指令簽名全部查證自「F控通訊選項手冊90210-1344DE.pdf」1.6 節:
;
;   TCP_LISTEN     ret, port                          ret=0 成功
;   TCP_ACCEPT     ret, port, timeout, ipa[0]         ret=socket ID(>=0)
;   TCP_SEND       ret, sock, $sbuf[0], 元素數, timeout   ret=0 成功
;   TCP_RECV       ret, sock, $rbuf[0], 元素數變數, timeout, 每元素字元上限
;   TCP_CLOSE      ret, sock                          ret=0 成功
;   TCP_END_LISTEN ret, port                          ret=0 成功
;
; 手冊列出的限制:
;   埠號範圍       8192 - 65535        (p.1-33)
;   timeout        1 - 60 秒,預設 1   (p.1-34)
;   每元素字元上限  1 - 255,預設 255   (p.1-41)  <- 對應 E4007
;   單次收發上限    4096 bytes          (p.1-38/1-40)
;   通訊錯誤不會停止程式,錯誤碼存進 ret;唯獨 TCP_ACCEPT 進行中出錯會停
;
; ret 預設 999 再 WAIT (ret<>999):照 90210-1342DE p.56 範例的寫法,
; 這些指令可能非同步完成,先等它結束比較保險。
; ---------------------------------------------------------------------

.PROGRAM sugar_server()

; ---------- 可調參數 ----------
  port  = 10000                ; 監聽埠。必須落在 8192-65535
  maxpt = 2000                 ; 緩衝點數上限
  sig   = 1                    ; 糖閥輸出訊號 DO 編號
  zup   = 15                   ; 抬筆高度 mm
  vdraw = 60                   ; 基準畫線速度 mm/s
  vtrav = 300                  ; 空走速度 mm/s
  acc   = 3                    ; 精度 mm。放大讓轉角連續走,不要設 0
  tmo   = 30                   ; 通訊逾時 秒。手冊上限 60
  airj  = 0                    ; 空中移動:0=LMOVE(安全) 1=JMOVE(快但路徑不可預測)
  base  = TRANS(450,0,-120,0,180,0)   ; 畫布原點,三點校正後覆蓋

  SIGNAL -sig                  ; 確認糖閥關閉
  SPEED vtrav MM/S ALWAYS
  ACCURACY acc ALWAYS
  npt  = 0
  quit = 0

; ---------- 建立連線 ----------
  ret = 999
  TCP_LISTEN ret, port
  WAIT (ret<>999)
  IF ret <> 0 THEN
    TYPE "TCP_LISTEN failed, ret = ", ret
    GOTO 900
  END

50 sock = 999                  ; ACCEPT 逾時就再等,直到 PC 接進來
  TCP_ACCEPT sock, port, 60, ipa[0]
  WAIT (sock<>999)
  IF sock < 0 THEN
    TYPE "waiting for host..."
    GOTO 50
  END
  TYPE "connected from ", ipa[0], ".", ipa[1], ".", ipa[2], ".", ipa[3]

; ---------- 主迴圈:收字串 -> 解析 -> 動作 ----------
100 WHILE quit == 0 DO
    CALL sub_recv                        ; 收一行進 line$
    IF LEN(line$) == 0 THEN
      GOTO 100
    END
    CALL sub_next                        ; 砍出指令字 -> fld$

    CASE fld$ OF
      VALUE "BEG":                       ; BEG,<點數>  開始一筆劃
        npt = 0
        CALL sub_ok

      VALUE "PT":                        ; PT,x,y,v,x,y,v,...  直線點
        CALL sub_points
        CALL sub_ok

      VALUE "AR":                        ; AR,中點x,中點y,終點x,終點y,v  圓弧
        CALL sub_arc
        CALL sub_ok

      VALUE "AIR":                       ; AIR,0|1  空中移動的插補方式
        CALL sub_next
        airj = VAL(fld$)
        CALL sub_ok

      VALUE "RUN":                       ; RUN  一次連續走完緩衝區
        CALL sub_run
        CALL sub_ok

      VALUE "DOT":                       ; DOT,x,y,秒
        CALL sub_next
        dx = VAL(fld$)
        CALL sub_next
        dy = VAL(fld$)
        CALL sub_next
        dt = VAL(fld$)
        CALL sub_dot
        CALL sub_ok

      VALUE "BASE":                      ; BASE,x,y,z,o,a,t
        CALL sub_next
        bx = VAL(fld$)
        CALL sub_next
        by = VAL(fld$)
        CALL sub_next
        bz = VAL(fld$)
        CALL sub_next
        bo = VAL(fld$)
        CALL sub_next
        ba = VAL(fld$)
        CALL sub_next
        bt = VAL(fld$)
        base = TRANS(bx, by, bz, bo, ba, bt)
        CALL sub_ok

      VALUE "SPD":                       ; SPD,畫線,空走,精度
        CALL sub_next
        vdraw = VAL(fld$)
        CALL sub_next
        vtrav = VAL(fld$)
        CALL sub_next
        acc = VAL(fld$)
        ACCURACY acc ALWAYS
        CALL sub_ok

      VALUE "HOME":
        SIGNAL -sig
        SPEED vtrav MM/S ALWAYS
        LMOVE SHIFT(base BY 0, 0, zup)
        BREAK
        CALL sub_ok

      VALUE "END":
        quit = 1
        CALL sub_ok

      ANY:
        CALL sub_err
    END
  END

; ---------- 收工 ----------
  SIGNAL -sig
  ret = 999
  TCP_CLOSE ret, sock
  WAIT (ret<>999)
900 ret = 999
  TCP_END_LISTEN ret, port
  WAIT (ret<>999)
.END


; =====================================================================
; sub_next  從 line$ 砍下一個欄位放進 fld$
;
;   $DECODE 會「消耗」原字串,不是照索引取欄位:
;     $DECODE(s$, ",", 0)  取出分隔符前的內容並移除
;     $DECODE(s$, ",", 1)  取出分隔符本身並移除
;   出處:90210-1342DE p.56 的 vision 範例
; =====================================================================
.PROGRAM sub_next()
  fld$ = $DECODE(line$, ",", 0)
  sep$ = $DECODE(line$, ",", 1)
.END


; =====================================================================
; PT,x,y,v,x,y,v,...   把封包裡的點附加到緩衝陣列
;   v 是該點的速度 mm/s。毛筆粗細就是靠它做出來的:
;   糖流量固定 -> 線寬 x 速度 = 常數 -> 走得慢就粗
; =====================================================================
.PROGRAM sub_points()
200 IF npt >= maxpt THEN
    RETURN
  END
  CALL sub_next
  IF LEN(fld$) == 0 THEN                 ; 這包收完了
    RETURN
  END
  vx = VAL(fld$)
  CALL sub_next
  vy = VAL(fld$)
  CALL sub_next
  IF LEN(fld$) == 0 THEN                 ; 欄位不成三組,丟棄
    RETURN
  END
  npt = npt + 1
  px[npt] = vx
  py[npt] = vy
  pv[npt] = VAL(fld$)
  pk[npt] = 0                            ; 0 = LMOVE
  GOTO 200
.END


; =====================================================================
; AR,中點x,中點y,終點x,終點y,v   一段圓弧
;
;   Kawasaki 的圓弧要兩道指令:C1MOVE 走到弧上中點,C2MOVE 走到終點。
;   圓由「前一個動作的位置 + C1MOVE 的點 + C2MOVE 的點」三點決定。
;   出處:F控AS語言參考手冊 90209-1025DE p.6-14
; =====================================================================
.PROGRAM sub_arc()
300 IF npt >= maxpt - 2 THEN
    RETURN
  END
  CALL sub_next
  IF LEN(fld$) == 0 THEN                 ; 這包收完了
    RETURN
  END
  mx = VAL(fld$)
  CALL sub_next
  my = VAL(fld$)
  CALL sub_next
  ex = VAL(fld$)
  CALL sub_next
  ey = VAL(fld$)
  CALL sub_next
  IF LEN(fld$) == 0 THEN                 ; 欄位不成五個,丟棄
    RETURN
  END
  av = VAL(fld$)
  npt = npt + 1
  px[npt] = mx
  py[npt] = my
  pv[npt] = av
  pk[npt] = 1                            ; 1 = C1MOVE 弧上中點
  npt = npt + 1
  px[npt] = ex
  py[npt] = ey
  pv[npt] = av
  pk[npt] = 2                            ; 2 = C2MOVE 弧的終點
  GOTO 300
.END


; =====================================================================
; RUN  執行緩衝區裡的整條筆劃
;
;   線段之間「絕對不放 BREAK」—— 放了控制器會逐點停,
;   糖在每個停頓點積成一坨。靠 ACCURACY + ALWAYS 讓它連續 blend。
;   只有下筆前和抬筆前才 BREAK。
; =====================================================================
.PROGRAM sub_run()
  IF npt < 2 THEN
    RETURN
  END

  SPEED vtrav MM/S ALWAYS
  IF airj == 1 THEN
    JMOVE SHIFT(base BY px[1], py[1], zup)   ; 關節插補較快
  ELSE
    LMOVE SHIFT(base BY px[1], py[1], zup)   ; 直線插補,路徑可預測
  END
  LMOVE SHIFT(base BY px[1], py[1], 0)       ; 下筆一定要垂直,不能用 JMOVE
  BREAK                                  ; 確認真的到位才開閥

  SIGNAL sig                             ; 開糖閥
  TWAIT 0.15                             ; 等糖絲成形

  lastv = -1
  FOR i = 2 TO npt
    IF pv[i] <> lastv THEN
      SPEED pv[i] MM/S ALWAYS            ; 只有變速才下指令
      lastv = pv[i]
    END
    CASE pk[i] OF
      VALUE 1:
        C1MOVE SHIFT(base BY px[i], py[i], 0)
      VALUE 2:
        C2MOVE SHIFT(base BY px[i], py[i], 0)
      ANY:
        LMOVE SHIFT(base BY px[i], py[i], 0)
    END
  END

  BREAK
  SIGNAL -sig                            ; 關糖閥
  TWAIT 0.10                             ; 等糖絲斷
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(base BY px[npt], py[npt], zup)
  npt = 0
.END


; =====================================================================
; DOT  糖點:停在原地開閥,直徑靠停留時間控制
; =====================================================================
.PROGRAM sub_dot()
  SPEED vtrav MM/S ALWAYS
  LMOVE SHIFT(base BY dx, dy, zup)
  LMOVE SHIFT(base BY dx, dy, 0)
  BREAK
  SIGNAL sig
  TWAIT dt
  SIGNAL -sig
  TWAIT 0.10
  LMOVE SHIFT(base BY dx, dy, zup)
.END


; =====================================================================
; 通訊底層
;   收發都不加換行:靠「一問一答」framing,PC 送一行就等回覆,
;   所以每次 TCP_RECV 剛好拿到一整行。
; =====================================================================
.PROGRAM sub_recv()
  line$ = ""
  ret = 999
  rcnt = 0
  TCP_RECV ret, sock, $rbuf[0], rcnt, tmo, 255
  WAIT (ret<>999)
  IF ret == 0 THEN
    IF rcnt >= 1 THEN
      line$ = $rbuf[0]
    END
  END
.END

.PROGRAM sub_send()
  $sbuf[0] = tx$
  ret = 999
  TCP_SEND ret, sock, $sbuf[0], 1, tmo
  WAIT (ret<>999)
.END

.PROGRAM sub_ok()
  tx$ = "OK"
  CALL sub_send
.END

.PROGRAM sub_err()
  tx$ = "ER"
  CALL sub_send
.END
