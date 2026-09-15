.PROGRAM pot_server()
; =====================================================================
; Ethernet string receiver for sugar drawing, TEAPOT rig.
; Kawasaki RS07L on an F60 controller.
;
; Derived from sugar_server.as by the smallest possible edit: that one
; loads with 0 errors on real hardware, so only two things changed.
;   - the "sig" branch is gone.  The teapot has no valve; sugar flow is
;     set by how far the pot is tilted, and the tilt is baked into org.
;   - every program was renamed sugar_*/sub_* -> pot_*.  Loading both
;     files into one controller would otherwise overwrite sub_do, and
;     the two versions of it are not the same.
;
; The PC never sends "wait" in teapot mode - sugar is always flowing, so
; any pause leaves a blob.  The handler is kept anyway for manual use.
;
; TOOL must be taught to the SPOUT before running.  Mark the lowest
; point of the pot spout and teach that as the tool centre point.
;
; Receives one motion per line over TCP and executes it immediately.
; Knows nothing about sugar / strokes / brush - the PC decides all of
; that and sends plain strings.  This program never needs to change.
;
; Protocol (one line per action, comma separated, LF between lines):
;   lmove,x,y,z,v      linear move to org shifted by (x,y,z), v mm/s
;   jmove,x,y,z,v      joint move, same
;   ldepart,d,v        retract d mm along tool Z  (pen up, stays normal)
;   wait,t             dwell t seconds
;   brk                wait until motion settles
;   base,x,y,z,o,a,t   set the canvas origin pose "org"
;   acc,n              set accuracy in mm
;   end                finish
; Replies "OK" per packet, "ER" if a command is not recognised.
;
; ---------------------------------------------------------------------
; THREE THINGS THAT COST A LOAD EACH.  Do not undo them.
;
; 1. String variables put the dollar sign at the FRONT of the name:
;    $pkt, $fld, $cmd.  Trailing-dollar BASIC style is rejected on every
;    single line (P0109 invalid statement, P0116 illegal function
;    variable, P0160 specify ON or OFF).  Manual 3.7 p.3-19:
;      $string variable = character string value
;
; 2. ALL COMMENTS MUST STAY ASCII.  Chinese comments got mangled by the
;    editor and ate the line break, swallowing the next statement into
;    the comment.  That silently killed TCP_ACCEPT and one $DECODE.
;
; 3. CASE index must be numeric, not a string (p.6-69), so the command
;    word is dispatched with an IF chain, not CASE.
; ---------------------------------------------------------------------
;
; Verified against the manuals:
;   TCP_LISTEN     ret, port                      90210-1344DE p.1-33
;   TCP_ACCEPT     ret, port, timeout, ipa[0]     p.1-34, ret = socket id
;   TCP_SEND       ret, sock, $sbuf[0], n, tmo    p.1-38
;   TCP_RECV       ret, sock, $rbuf[0], n, tmo, maxchar   p.1-40
;   TCP_CLOSE      ret, sock                      p.1-42
;   TCP_END_LISTEN ret, port                      p.1-43
;   LDEPART        distance                       90209-1025DE p.6-6
;   LEN            (string)                       p.9-11
;   $DECODE        (string var, separator, mode)  p.9-71
;     mode <= 0  take the text before the separator and remove it
;     mode >  0  take the separator itself and remove it
;     Both calls are needed to consume one field.
; Limits: port 8192-65535, timeout 1-60 s, 255 char per element (E4007),
;         4096 byte per call, needs the Ethernet option board (E4054).
; =====================================================================

  port = 10000               ; listen port, must be 8192-65535
  tmo  = 30                  ; comms timeout seconds, manual max 60
  $eol = $CHR(10)            ; line separator inside a packet

  POINT nullpose = TRANS(0,0,0,0,0,0)
  BASE nullpose              ; work in world coordinates
  TOOL to1[1]                ; nozzle TCP, taught elsewhere

; org is the canvas origin and is OWNED BY org_teach.  Do not assign it
; here - that would wipe the taught value every time this starts.
; The PC can still override it at run time with the "base" command.
; Nothing moves until a command arrives; the arm must not jump anywhere
; just because the program was started.

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
    CALL pot_recv
    IF LEN($pkt) == 0 THEN
      GOTO 100
    END
; one packet may hold several lines, peel them off one at a time
110 $line = $DECODE($pkt, $eol, 0)
    $dmy = $DECODE($pkt, $eol, 1)
    IF LEN($line) > 0 THEN
      CALL pot_do
    END
    IF LEN($pkt) > 0 THEN
      GOTO 110
    END
    CALL pot_ok
  END

  ret = 999
  TCP_CLOSE ret, sock
  WAIT (ret<>999)
900 ret = 999
  TCP_END_LISTEN ret, port
  WAIT (ret<>999)
.END

.PROGRAM pot_do()
; Parse one line and act on it straight away.
; No BREAK between moves - that would stop the arm at every point and
; each stop leaves a blob of sugar.  The PC sends "brk" where it wants
; a real stop.  ACCURACY does the corner blending.
  CALL pot_next
  $cmd = $fld

  IF $cmd == "lmove" THEN
    CALL pot_xyzv
    POINT st = SHIFT(org BY vx, vy, vz)
    LMOVE st
    RETURN
  END

  IF $cmd == "jmove" THEN
    CALL pot_xyzv
    POINT st = SHIFT(org BY vx, vy, vz)
    JMOVE st
    RETURN
  END

  IF $cmd == "ldepart" THEN
    CALL pot_next
    vz = VAL($fld)
    CALL pot_next
    CALL pot_speed
    LDEPART vz
    RETURN
  END

  IF $cmd == "wait" THEN
    CALL pot_next
    waitt = VAL($fld)
    TWAIT waitt
    RETURN
  END

  IF $cmd == "brk" THEN
    BREAK
    RETURN
  END

; base,x,y,z,o,a,t  sets the canvas origin pose.
; Names are ox oy oz oo oa ot on purpose - do not reuse "ba", that is a
; pose variable elsewhere and assigning a real to it breaks it.
  IF $cmd == "base" THEN
    CALL pot_next
    ox = VAL($fld)
    CALL pot_next
    oy = VAL($fld)
    CALL pot_next
    oz = VAL($fld)
    CALL pot_next
    oo = VAL($fld)
    CALL pot_next
    oa = VAL($fld)
    CALL pot_next
    ot = VAL($fld)
    POINT org = TRANS(ox, oy, oz, oo, oa, ot)
    RETURN
  END

  IF $cmd == "acc" THEN
    CALL pot_next
    accv = VAL($fld)
    ACCURACY accv ALWAYS
    RETURN
  END

  IF $cmd == "end" THEN
    quit = 1
    RETURN
  END

  CALL pot_err
.END

.PROGRAM pot_xyzv()
; read x,y,z,v and set the speed only when it changed
  CALL pot_next
  vx = VAL($fld)
  CALL pot_next
  vy = VAL($fld)
  CALL pot_next
  vz = VAL($fld)
  CALL pot_next
  CALL pot_speed
.END

.PROGRAM pot_speed()
; $fld holds the speed.  Emitting SPEED on every point would flood the
; controller, so only do it when the value actually changes.
  spdv = VAL($fld)
  IF spdv <> lastv THEN
    SPEED spdv MM/S ALWAYS
    lastv = spdv
  END
.END

.PROGRAM pot_next()
; take one comma separated field from $line into $fld
; both $DECODE calls are required, see the header note
  $fld = $DECODE($line, ",", 0)
  $sep = $DECODE($line, ",", 1)
.END

.PROGRAM pot_recv()
; no trailing newline on the wire, framing is send-one-wait-one
  $pkt = ""
  ret = 999
  rcnt = 0
  TCP_RECV ret, sock, $rbuf[0], rcnt, tmo, 255
  WAIT (ret<>999)
  IF ret == 0 THEN
    IF rcnt >= 1 THEN
      $pkt = $rbuf[0]
    END
  END
.END

.PROGRAM pot_send()
  $sbuf[0] = $tx
  ret = 999
  TCP_SEND ret, sock, $sbuf[0], 1, tmo
  WAIT (ret<>999)
.END

.PROGRAM pot_ok()
  $tx = "OK"
  CALL pot_send
.END

.PROGRAM pot_err()
  $tx = "ER"
  CALL pot_send
.END

.PROGRAM Comment___ () ; Comments for IDE. Do not use.
	; @@@ PROJECT @@@
	; @@@ PROJECTNAME @@@
	; pot_server
	; @@@ HISTORY @@@
	; @@@ INSPECTION @@@
	; @@@ CONNECTION @@@
	; 
	; 
	; 
	; @@@ PROGRAM @@@
	; 0:pot_server:F
	; 0:pot_do:F
	; 0:pot_xyzv:F
	; 0:pot_speed:F
	; 0:pot_next:F
	; 0:pot_recv:F
	; 0:pot_send:F
	; 0:pot_ok:F
	; 0:pot_err:F
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
