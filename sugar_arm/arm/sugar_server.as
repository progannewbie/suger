; =====================================================================
; sugar_server.as -- Ethernet 字串接收執行器 (Kawasaki F60 / AS Language)
;
; 這支程式做的事只有一件:
;
;       收 Ethernet 字串 -> 照字串講的去動
;
; 它不知道什麼是糖畫、什麼是筆劃、什麼是毛筆。
; 走哪裡、多快、什麼時候開糖閥、停多久,全部由 PC 端算好用字串送進來。
; 載進控制器之後就不用再改 —— 要換成畫別的東西,改 PC 端就好。
;
; ---------------------------------------------------------------------
; 字串格式:一行一個動作,逗號分隔。一個封包可以用換行塞多行。
;
;   lmove,x,y,z,v      直線插補到 base 偏移 (x,y,z),速度 v mm/s
;   jmove,x,y,z,v      關節插補,同上
;   ldepart,d,v        沿工具 Z 軸退開 d mm(抬筆用,保證垂直)
;   sig,n              n 正數=開,負數=關。糖閥就是這個
;   wait,t             停留 t 秒
;   brk                等動作完全到位才繼續
;   base,x,y,z,o,a,t   設定畫布原點
;   acc,n              設定精度 mm
;   end                收工
;
; 每收一個封包回 "OK";指令不認得回 "ER"。
;
; ---------------------------------------------------------------------
; 為什麼可以「收到就動」而不會頓
;
; AS 在執行移動指令時不會卡住程式 —— 控制器會把移動指令排進自己的
; 佇列,程式繼續往下跑(手冊 4.5.5:「AS can perform non-motion
; instructions while the robot is moving」)。所以只要網路餵得夠快,
; 手臂就會連續走完,不會一點一停。
;
; 以 60mm/s、點距 1mm 計,每秒只需要 60 個點;區域網路往返約 1~5ms,
; 每秒可以送 200~1000 個點,餵得過來。
;
; 唯一風險:網路突然卡住時,控制器的佇列會清空,手臂停在半途 ->
; 糖會積一坨。PC 端要避免在畫線途中做耗時的事。
;
; ---------------------------------------------------------------------
; 指令簽名查證自手冊:
;   TCP_LISTEN     ret, port                    通訊選項手冊 90210-1344DE p.1-33
;   TCP_ACCEPT     ret, port, timeout, ipa[0]   同上 p.1-34,ret = socket ID
;   TCP_SEND       ret, sock, $sbuf[0], 元素數, timeout        同上 p.1-38
;   TCP_RECV       ret, sock, $rbuf[0], 元素數變數, timeout, 字元上限  同上 p.1-40
;   TCP_CLOSE      ret, sock                    同上 p.1-42
;   TCP_END_LISTEN ret, port                    同上 p.1-43
;   LDEPART        distance                     AS 語言參考手冊 p.6-6
;
; 限制:埠號 8192-65535、timeout 1-60 秒、每元素上限 255 字元(E4007)、
;       單次收發 4096 bytes、需要 Ethernet 選購板卡(否則 E4054)
;
; 兩個語法地雷(都踩過):
;   - CASE 的索引必須是數值,不能是字串(手冊 p.6-69)。
;     所以指令字只能用 IF 鏈分派。
;   - $DECODE 會「消耗」原字串,不是照索引取欄位:
;       $DECODE(s$, ",", 0)  取出分隔符前的內容並移除
;       $DECODE(s$, ",", 1)  取出分隔符本身並移除
;     出處:傳送裝置同步功能手冊 90210-1342DE p.56
; =====================================================================

.PROGRAM sugar_server()

  port = 10000               ; 監聽埠。必須落在 8192-65535
  tmo  = 30                  ; 通訊逾時 秒。手冊上限 60
  eol$ = $CHR(10)            ; 封包內的換行。控制器若不吃,改成 ";"
  base = TRANS(450,0,-120,0,180,0)    ; 預設值,PC 端會用 base 指令覆蓋

  SPEED 100 MM/S ALWAYS
  ACCURACY 3 ALWAYS
  lastv = -1
  quit  = 0

; ---------- 建立連線 ----------
  ret = 999
  TCP_LISTEN ret, port
  WAIT (ret<>999)
  IF ret <> 0 THEN
    PRINT "TCP_LISTEN failed, ret = ", ret
    GOTO 900
  END

50 sock = 999                ; ACCEPT 逾時上限 60 秒,逾時就再等
  TCP_ACCEPT sock, port, 60, ipa[0]
  WAIT (sock<>999)
  IF sock < 0 THEN
    PRINT "waiting for host..."
    GOTO 50
  END
  PRINT "connected from ", ipa[0], ipa[1], ipa[2], ipa[3]

; ---------- 主迴圈:收字串 -> 立刻執行 ----------
100 WHILE quit == 0 DO
    CALL sub_recv                      ; 收一包進 pkt$
    IF LEN(pkt$) == 0 THEN
      GOTO 100
    END
110 line$ = $DECODE(pkt$, eol$, 0)     ; 一包可能有多行,逐行做
    dmy$ = $DECODE(pkt$, eol$, 1)
    IF LEN(line$) > 0 THEN
      CALL sub_do
    END
    IF LEN(pkt$) > 0 THEN
      GOTO 110
    END
    CALL sub_ok
  END

  ret = 999
  TCP_CLOSE ret, sock
  WAIT (ret<>999)
900 ret = 999
  TCP_END_LISTEN ret, port
  WAIT (ret<>999)
.END


; =====================================================================
; sub_do  解析一行,立刻執行
;
;   移動指令之間不下 BREAK —— 下了控制器會逐點停,
;   在糖畫的場合每個停頓都是一坨糖。要等到位由 PC 端明確送 brk。
;   靠 ACCURACY 讓控制器把轉角連續 blend 過去。
; =====================================================================
.PROGRAM sub_do()
  CALL sub_next
  cmd$ = fld$

  IF cmd$ == "lmove" THEN
    CALL sub_xyzv
    LMOVE SHIFT(base BY vx, vy, vz)
    RETURN
  END

  IF cmd$ == "jmove" THEN
    CALL sub_xyzv
    JMOVE SHIFT(base BY vx, vy, vz)
    RETURN
  END

  IF cmd$ == "ldepart" THEN            ; ldepart,d,v
    CALL sub_next
    vz = VAL(fld$)
    CALL sub_next
    CALL sub_speed
    LDEPART vz
    RETURN
  END

  IF cmd$ == "sig" THEN                ; sig,n   正開負關
    CALL sub_next
    SIGNAL VAL(fld$)
    RETURN
  END

  IF cmd$ == "wait" THEN               ; wait,t
    CALL sub_next
    TWAIT VAL(fld$)
    RETURN
  END

  IF cmd$ == "brk" THEN
    BREAK
    RETURN
  END

  IF cmd$ == "base" THEN               ; base,x,y,z,o,a,t
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
    RETURN
  END

  IF cmd$ == "acc" THEN                ; acc,n
    CALL sub_next
    accv = VAL(fld$)
    ACCURACY accv ALWAYS
    RETURN
  END

  IF cmd$ == "end" THEN
    quit = 1
    RETURN
  END

  CALL sub_err
.END


; =====================================================================
; sub_xyzv  讀 x,y,z,v 四個欄位,並在速度有變時才下 SPEED
; =====================================================================
.PROGRAM sub_xyzv()
  CALL sub_next
  vx = VAL(fld$)
  CALL sub_next
  vy = VAL(fld$)
  CALL sub_next
  vz = VAL(fld$)
  CALL sub_next
  CALL sub_speed
.END


; =====================================================================
; sub_speed  fld$ 是速度。只有跟上次不同才下指令,不然行數會爆
; =====================================================================
.PROGRAM sub_speed()
  spdv = VAL(fld$)
  IF spdv <> lastv THEN
    SPEED spdv MM/S ALWAYS
    lastv = spdv
  END
.END


; =====================================================================
; sub_next  從 line$ 砍下一個逗號分隔的欄位放進 fld$
; =====================================================================
.PROGRAM sub_next()
  fld$ = $DECODE(line$, ",", 0)
  sep$ = $DECODE(line$, ",", 1)
.END


; =====================================================================
; 通訊底層。收發都不加換行當結尾,framing 靠「送一包就等回覆」。
; =====================================================================
.PROGRAM sub_recv()
  pkt$ = ""
  ret = 999
  rcnt = 0
  TCP_RECV ret, sock, $rbuf[0], rcnt, tmo, 255
  WAIT (ret<>999)
  IF ret == 0 THEN
    IF rcnt >= 1 THEN
      pkt$ = $rbuf[0]
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
