.PROGRAM sugar_server()
; =====================================================================
; Ethernet string receiver for sugar drawing.  Kawasaki F60 / AS
;
; Receives one motion per line over TCP and executes it immediately.
; Knows nothing about sugar / strokes / brush - the PC decides all of
; that and sends plain strings.  This program never needs to change.
;
; Protocol (one line per action, comma separated, LF between lines):
;   lmove,x,y,z,v      linear move to org shifted by (x,y,z), v mm/s
;   jmove,x,y,z,v      joint move, same
;   ldepart,d,v        retract d mm along tool Z  (pen up, stays normal)
;   sig,n              n>0 turn ON, n<0 turn OFF  (sugar valve)
;   wait,t             dwell t seconds
;   brk                wait until motion settles
;   base,x,y,z,o,a,t   set the canvas origin pose "org"
;   acc,n              set accuracy in mm
;   end                finish
; Replies "OK" per packet, "ER" if a command is not recognised.
;
; ALL COMMENTS MUST STAY ASCII.
;   Chinese comments got mangled on the way through the editor and ate
;   the line break, swallowing the next statement into the comment.
;   That silently killed TCP_ACCEPT and the $DECODE separator consume.
;
; Verified against the manuals:
;   TCP_LISTEN     ret, port                      90210-1344DE p.1-33
;   TCP_ACCEPT     ret, port, timeout, ipa[0]     p.1-34, ret = socket id
;   TCP_SEND       ret, sock, $sbuf[0], n, tmo    p.1-38
;   TCP_RECV       ret, sock, $rbuf[0], n, tmo, maxchar   p.1-40
;   TCP_CLOSE      ret, sock                      p.1-42
;   TCP_END_LISTEN ret, port                      p.1-43
;   LDEPART        distance                       90209-1025DE p.6-6
; Limits: port 8192-65535, timeout 1-60 s, 255 char per element (E4007),
;         4096 byte per call, needs the Ethernet option board (E4054).
;
; Two syntax traps already hit:
;   - CASE index must be numeric, not a string (p.6-69), so the command
;     word is dispatched with an IF chain.
;   - $DECODE CONSUMES the source string, it does not index fields:
;       $DECODE(s$, ",", 0)  take text before the separator, remove it
;       $DECODE(s$, ",", 1)  take the separator itself, remove it
;     Both calls are needed per field.  (90210-1342DE p.56)
; =====================================================================

  port = 10000               ; listen port, must be 8192-65535
  tmo  = 30                  ; comms timeout seconds, manual max 60
  eol$ = $CHR(10)            ; line separator inside a packet

  POINT nullpose = TRANS(0,0,0,0,0,0)
  BASE nullpose              ; work in world coordinates
  POINT to1[1] = TRANS(0,0,0,0,0,0)
  TOOL to1[1]                ; set the real nozzle TCP here

; org is the canvas origin.  Teach it, or let the PC set it with the
; "base" command.  Nothing moves until a command arrives - the arm must
; not jump anywhere just because the program was started.
  POINT org = TRANS(450,0,-120,0,180,0)

  SPEED 100 MM/S ALWAYS
  ACCURACY 3 ALWAYS
  lastv = -1
  quit  = 0

; ---------- open the connection ----------
  ret = 999
  TCP_LISTEN ret, port
  WAIT (ret<>999)
  IF ret <> 0 THEN
    PRINT "TCP_LISTEN failed, ret = ", ret
    GOTO 900
  END

; TCP_ACCEPT times out after 60 s max, so loop until the host shows up
50 sock = 999
  TCP_ACCEPT sock, port, 60, ipa[0]
  WAIT (sock<>999)
  IF sock < 0 THEN
    PRINT "waiting for host..."
    GOTO 50
  END
  PRINT "connected from ", ipa[0], ipa[1], ipa[2], ipa[3]

; ---------- main loop: receive, then run each line at once ----------
100 WHILE quit == 0 DO
    CALL sub_recv
    IF LEN(pkt$) == 0 THEN
      GOTO 100
    END
; one packet may hold several lines, peel them off one at a time
110 line$ = $DECODE(pkt$, eol$, 0)
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

.PROGRAM sub_do()
; Parse one line and act on it straight away.
; No BREAK between moves - that would stop the arm at every point and
; each stop leaves a blob of sugar.  The PC sends "brk" where it wants
; a real stop.  ACCURACY does the corner blending.
  CALL sub_next
  cmd$ = fld$

  IF cmd$ == "lmove" THEN
    CALL sub_xyzv
    POINT st = SHIFT(org BY vx, vy, vz)
    LMOVE st
    RETURN
  END

  IF cmd$ == "jmove" THEN
    CALL sub_xyzv
    POINT st = SHIFT(org BY vx, vy, vz)
    JMOVE st
    RETURN
  END

  IF cmd$ == "ldepart" THEN
    CALL sub_next
    vz = VAL(fld$)
    CALL sub_next
    CALL sub_speed
    LDEPART vz
    RETURN
  END

  IF cmd$ == "sig" THEN
    CALL sub_next
    signum = VAL(fld$)
    SIGNAL signum
    RETURN
  END

  IF cmd$ == "wait" THEN
    CALL sub_next
    waitt = VAL(fld$)
    TWAIT waitt
    RETURN
  END

  IF cmd$ == "brk" THEN
    BREAK
    RETURN
  END

; base,x,y,z,o,a,t  sets the canvas origin pose.
; Note the variable names: ox oy oz oo oa ot.  Do NOT reuse "ba" here,
; that name is a pose elsewhere and assigning a real to it breaks it.
  IF cmd$ == "base" THEN
    CALL sub_next
    ox = VAL(fld$)
    CALL sub_next
    oy = VAL(fld$)
    CALL sub_next
    oz = VAL(fld$)
    CALL sub_next
    oo = VAL(fld$)
    CALL sub_next
    oa = VAL(fld$)
    CALL sub_next
    ot = VAL(fld$)
    POINT org = TRANS(ox, oy, oz, oo, oa, ot)
    RETURN
  END

  IF cmd$ == "acc" THEN
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

.PROGRAM sub_xyzv()
; read x,y,z,v and set the speed only when it changed
  CALL sub_next
  vx = VAL(fld$)
  CALL sub_next
  vy = VAL(fld$)
  CALL sub_next
  vz = VAL(fld$)
  CALL sub_next
  CALL sub_speed
.END

.PROGRAM sub_speed()
; fld$ holds the speed.  Emitting SPEED on every point would flood the
; controller, so only do it when the value actually changes.
  spdv = VAL(fld$)
  IF spdv <> lastv THEN
    SPEED spdv MM/S ALWAYS
    lastv = spdv
  END
.END

.PROGRAM sub_next()
; take one comma separated field from line$ into fld$
; both $DECODE calls are required, see the header note
  fld$ = $DECODE(line$, ",", 0)
  sep$ = $DECODE(line$, ",", 1)
.END

.PROGRAM sub_recv()
; no trailing newline on the wire, framing is send-one-wait-one
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

.PROGRAM Comment___ () ; Comments for IDE. Do not use.
	; @@@ PROJECT @@@
	; @@@ PROJECTNAME @@@
	; sugar_server
	; @@@ HISTORY @@@
	; @@@ INSPECTION @@@
	; @@@ CONNECTION @@@
	; 
	; 
	; 
	; @@@ PROGRAM @@@
	; 0:sugar_server:F
	; 0:sub_do:F
	; 0:sub_xyzv:F
	; 0:sub_speed:F
	; 0:sub_next:F
	; 0:sub_recv:F
	; 0:sub_send:F
	; 0:sub_ok:F
	; 0:sub_err:F
	; @@@ TRANS @@@
	; @@@ JOINTS @@@
	; @@@ REALS @@@
	; @@@ STRINGS @@@
	; @@@ INTEGER @@@
	; @@@ SIGNALS @@@
	; @@@ TOOLS @@@
	; @@@ BASE @@@
	; @@@ FRAME @@@
	; @@@ BOOL @@@
	; @@@ DEFAULTS @@@
	; BASE: NULL
	; TOOL: NULL
.END
