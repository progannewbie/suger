; =====================================================================
; sugar_server.as -- 通用動作序列執行器 (Kawasaki F60 / AS Language)
;
; 這支程式是「固定的」—— 載進控制器之後就不用再改。
; 它完全不知道什麼是糖畫、什麼是筆劃、什麼是毛筆。
; 它只做三件事:收字串、存進緩衝、照順序執行。
;
; 所有決策(走哪裡、多快、什麼時候開糖閥、停多久)都在 PC 端算好,
; 用字串送進來。要換成畫別的東西,改 PC 端就好,這支不用動。
;
; ---------------------------------------------------------------------
; 協定:一行一個動作,自帶型態。多個動作可以用換行塞在同一個封包裡。
;
;   緩衝型(存起來,收到 run 才一起執行):
;     lmove,x,y,z,v      直線插補到 base 偏移 (x,y,z),速度 v mm/s
;     jmove,x,y,z,v      關節插補,同上
;     ldepart,d,v        沿「工具 Z 軸」退開 d mm(抬筆用,保證垂直)
;     sig,n              n 正數=開,負數=關。糖閥就是這個
;     wait,t             停留 t 秒
;     brk                等動作完全到位
;
;   立即型(不進緩衝,馬上做):
;     base,x,y,z,o,a,t   設定畫布原點
;     acc,n              設定精度 mm
;     run                一次連續執行緩衝區,然後清空
;     clr                清空緩衝
;     end                收工
;
; 每收一行回 "OK" 或 "ER"。
;
; 為什麼要緩衝:一問一答式的即時執行會變成 stop-and-go,
; 每個往返停頓都是一坨糖。所以先收滿一整筆劃,收到 run 才連續走完。
; ---------------------------------------------------------------------
;
; 指令簽名查證自你的手冊:
;   TCP_LISTEN     ret, port                    F控通訊選項手冊 90210-1344DE p.1-33
;   TCP_ACCEPT     ret, port, timeout, ipa[0]   同上 p.1-34,ret=socket ID
;   TCP_SEND       ret, sock, $sbuf[0], 元素數, timeout    同上 p.1-38
;   TCP_RECV       ret, sock, $rbuf[0], 元素數變數, timeout, 字元上限  同上 p.1-40
;   TCP_CLOSE      ret, sock                    同上 p.1-42
;   TCP_END_LISTEN ret, port                    同上 p.1-43
;   LDEPART        distance                     AS 語言參考手冊 90209-1025DE p.6-6
;
; 限制:埠號 8192-65535、timeout 1-60 秒、每元素上限 255 字元(E4007)、
;       單次收發 4096 bytes、需要 Ethernet 選購板卡(否則 E4054)
;
; $DECODE 會「消耗」原字串,不是照索引取欄位:
;   $DECODE(s$, ",", 0)  取出分隔符前的內容並移除
;   $DECODE(s$, ",", 1)  取出分隔符本身並移除
; 出處:F控傳送裝置同步功能 90210-1342DE p.56
; =====================================================================

.PROGRAM sugar_server()

  port  = 10000              ; 監聽埠。必須落在 8192-65535
  maxop = 3000               ; 緩衝動作數上限
  tmo   = 30                 ; 通訊逾時 秒。手冊上限 60
  base  = TRANS(450,0,-120,0,180,0)   ; 畫布原點,PC 端會用 base 指令覆蓋

  SPEED 100 MM/S ALWAYS
  ACCURACY 3 ALWAYS
  nop  = 0
  quit = 0
  eol$ = $CHR(10)            ; 封包內的動作分隔符。控制器若不吃換行,改成 ";"

; ---------- 建立連線 ----------
  ret = 999
  TCP_LISTEN ret, port
  WAIT (ret<>999)
  IF ret <> 0 THEN
    TYPE "TCP_LISTEN failed, ret = ", ret
    GOTO 900
  END

50 sock = 999                ; ACCEPT 逾時上限 60 秒,逾時就再等
  TCP_ACCEPT sock, port, 60, ipa[0]
  WAIT (sock<>999)
  IF sock < 0 THEN
    TYPE "waiting for host..."
    GOTO 50
  END
  TYPE "connected from ", ipa[0], ".", ipa[1], ".", ipa[2], ".", ipa[3]

; ---------- 主迴圈 ----------
100 WHILE quit == 0 DO
    CALL sub_recv                      ; 收一包進 pkt$
    IF LEN(pkt$) == 0 THEN
      GOTO 100
    END
110 line$ = $DECODE(pkt$, eol$, 0)     ; 一包可能有多行,逐行處理
    dmy$ = $DECODE(pkt$, eol$, 1)
    IF LEN(line$) > 0 THEN
      CALL sub_exec
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
; sub_exec  解析一行並處理
; =====================================================================
.PROGRAM sub_exec()
  CALL sub_next
  cmd$ = fld$

  CASE cmd$ OF
    VALUE "lmove":                     ; lmove,x,y,z,v
      CALL sub_push
      op[nop] = 1

    VALUE "jmove":                     ; jmove,x,y,z,v
      CALL sub_push
      op[nop] = 2

    VALUE "ldepart":                   ; ldepart,d,v
      IF nop < maxop THEN
        nop = nop + 1
        CALL sub_next
        pz[nop] = VAL(fld$)            ; 退開距離
        CALL sub_next
        pv[nop] = VAL(fld$)
        op[nop] = 3
      END

    VALUE "sig":                       ; sig,n   正開負關
      IF nop < maxop THEN
        nop = nop + 1
        CALL sub_next
        pv[nop] = VAL(fld$)
        op[nop] = 4
      END

    VALUE "wait":                      ; wait,t
      IF nop < maxop THEN
        nop = nop + 1
        CALL sub_next
        pv[nop] = VAL(fld$)
        op[nop] = 5
      END

    VALUE "brk":                       ; brk
      IF nop < maxop THEN
        nop = nop + 1
        op[nop] = 6
      END

    VALUE "base":                      ; base,x,y,z,o,a,t  立即生效
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

    VALUE "acc":                       ; acc,n  立即生效
      CALL sub_next
      ACCURACY VAL(fld$) ALWAYS

    VALUE "run":                       ; run  執行緩衝
      CALL sub_run
      nop = 0

    VALUE "clr":
      nop = 0

    VALUE "end":
      quit = 1

    ANY:
      CALL sub_err
  END
.END


; =====================================================================
; sub_push  讀 x,y,z,v 存進緩衝
; =====================================================================
.PROGRAM sub_push()
  IF nop >= maxop THEN
    RETURN
  END
  nop = nop + 1
  CALL sub_next
  px[nop] = VAL(fld$)
  CALL sub_next
  py[nop] = VAL(fld$)
  CALL sub_next
  pz[nop] = VAL(fld$)
  CALL sub_next
  pv[nop] = VAL(fld$)
.END


; =====================================================================
; sub_next  從 line$ 砍下一個逗號分隔的欄位放進 fld$
; =====================================================================
.PROGRAM sub_next()
  fld$ = $DECODE(line$, ",", 0)
  sep$ = $DECODE(line$, ",", 1)
.END


; =====================================================================
; sub_run  照順序執行緩衝區裡的動作
;
;   移動指令之間「絕對不放 BREAK」—— 放了控制器會逐點停,
;   在糖畫的場合每個停頓都是一坨糖。要等到位就由 PC 端明確送 brk。
;   靠 ACCURACY + ALWAYS 讓控制器把轉角連續 blend 過去。
; =====================================================================
.PROGRAM sub_run()
  lastv = -1
  FOR i = 1 TO nop
    CASE op[i] OF
      VALUE 1:                         ; lmove
        IF pv[i] <> lastv THEN
          SPEED pv[i] MM/S ALWAYS
          lastv = pv[i]
        END
        LMOVE SHIFT(base BY px[i], py[i], pz[i])

      VALUE 2:                         ; jmove
        IF pv[i] <> lastv THEN
          SPEED pv[i] MM/S ALWAYS
          lastv = pv[i]
        END
        JMOVE SHIFT(base BY px[i], py[i], pz[i])

      VALUE 3:                         ; ldepart 沿工具 Z 退開
        IF pv[i] <> lastv THEN
          SPEED pv[i] MM/S ALWAYS
          lastv = pv[i]
        END
        LDEPART pz[i]

      VALUE 4:                         ; sig
        SIGNAL pv[i]

      VALUE 5:                         ; wait
        TWAIT pv[i]

      VALUE 6:                         ; brk
        BREAK
    END
  END
.END


; =====================================================================
; 通訊底層
;   收發都不加換行字元。framing 靠「送一包就等回覆」。
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
