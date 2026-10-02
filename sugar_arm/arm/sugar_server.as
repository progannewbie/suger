.PROGRAM sugar_main()
; =====================================================================
; Ethernet string receiver for sugar drawing.  Kawasaki F60 / AS
;
; Program layout:
;   sugar_main   entry point, run this one.  Connection and dispatch.
;   sugar_init   ALL settings: comms, robot BASE frame, TOOL, speed.
;                Edit numbers there, never in sugar_main.
;   sub_*        helpers, not meant to be run on their own.
;
; Run sequence (teapot):
;   wait for PC -> JMOVE star -> LMOVE org -> WAIT SIG(startsig)
;   -> run the PC lines until "end" -> turn pot upright in place
;   -> LMOVE star.
; The PC plans for a teapot: one continuous path that starts at org,
; no sig, no stop in the middle.  sig is kept for a valve, unused now.
;
; Receives one motion per line over TCP and executes it immediately.
; Knows nothing about sugar / strokes / brush - the PC decides all of
; that and sends plain strings.  This program never needs to change.
;
; Protocol (one line per action, comma separated, every line ends with
; LF, an empty line ends the packet - so a packet ends with LF LF.
; Packets may be any length; lines at most maxline chars, see init):
;   lmove,x,y,z,v      linear move to org shifted by (x,y,z), v mm/s
;   jmove,x,y,z,v      joint move, same
;   ldepart,d,v        retract d mm along tool Z  (pen up, stays normal)
;   sig,n              n>0 turn ON, n<0 turn OFF  (sugar valve)
;                      waits for motion to settle first (implicit brk)
;   wait,t             dwell t seconds
;   brk                wait until motion settles
;   base,x,y,z,o,a,t   set the canvas origin pose "org"
;   acc,n              set accuracy in mm
;   end                finish
; Exactly one reply per packet: "OK", or "ER,<cmd>" if a command is not
; recognised (the rest of that packet is then skipped).  "ER,toolong"
; means a line was longer than maxline.
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

  CALL sugar_init

; Nothing moves until the PC is connected; then sub_prep runs the
; teapot start sequence (star -> org -> wait startsig) once.
; runtime state, reset on every start (not settings, so not in init)
  lastv = -1
  quit  = 0
  sigon = 0                  ; valve signal currently ON, 0 = closed
  prepped = 0                ; start sequence done for this run

; ---------- open the listening port once ----------
  ret = 999
  TCP_LISTEN ret, port
  WAIT (ret<>999)
  IF ret <> 0 THEN
    PRINT "TCP_LISTEN failed, ret = ", ret
    GOTO 900
  END

; ---------- serve hosts until one sends "end" ----------
; If the link drops, close the dead socket, make the arm safe and go
; back to TCP_ACCEPT.  The listening port stays open the whole time.
; No GOTO in here - jumping out of a WHILE is asking for trouble.
  WHILE quit == 0 DO
    CALL sub_accept
    lost = 0
; start sequence only on the first connection.  A reconnect mid job
; must not send the arm back to star across the drawing.
    IF prepped == 0 THEN
      CALL sub_prep
      prepped = 1
    END
    WHILE (quit == 0) AND (lost == 0) DO
      CALL sub_packet
    END
    ret = 999
    TCP_CLOSE ret, sock
    WAIT (ret<>999)
    IF lost <> 0 THEN
      CALL sub_safe
    END
  END

; normal end: stand the pot upright in place, then back to star
  IF quit <> 0 THEN
    CALL sub_park
  END

900 ret = 999
  TCP_END_LISTEN ret, port
  WAIT (ret<>999)
.END

.PROGRAM sugar_init()
; =====================================================================
; Every setting of the sugar arm lives here.  sugar_main calls this
; first; it can also be run on its own to put the arm in the same
; BASE / TOOL before teaching org.
;
; org (canvas origin) is NOT set here.  It is OWNED BY org_teach and is
; stored relative to the BASE and TOOL below.  Assigning it here would
; wipe the taught value on every start.  The PC can still override it
; at run time with the "base,x,y,z,o,a,t" command - note that command
; sets org, it does NOT change the robot BASE frame below.
;
; If you change BASE or TOOL here, re-teach org with the same values,
; otherwise the canvas lands somewhere else.
; =====================================================================

; ---------- communication ----------
  port = 20000               ; listen port.  10000 gives E4027 on this E controller
  tmo  = 30                  ; comms timeout seconds, manual max 60
  $eol = $CHR(10)            ; line end; an empty line ends the packet
  rmax = 190                 ; chars per TCP_RECV element (1-255)
  maxline = 64               ; longest single line accepted
; rmax + maxline must be <= 255: a line cut at an element edge is glued
; onto the next element and one AS string holds 255 chars at most.
; stream.py MAXLINE must match maxline.

; ---------- robot BASE frame ----------
; Where the work frame sits in world coordinates.
; TRANS(x, y, z, o, a, t)  mm and degrees.  All zero = world frame.
; Named sbase on purpose - "ba" is a pose elsewhere, do not reuse it.
  POINT sbase = TRANS(0, 0, 0, 0, 0, 0)
  BASE sbase

; ---------- TOOL (nozzle tip) ----------
; Nozzle tip relative to the flange, TRANS(x, y, z, o, a, t).
; Must match the TOOL used when org was taught (org_teach uses
; 0, 80, 130 - same as the controller system TOOL in 0911.as).
; Kept as to1[1] because org_teach and other programs use that name.
  POINT to1[1] = TRANS(0, 80, 130, 0, 0, 0)
  TOOL to1[1]

; ---------- teapot start sequence (sub_prep) ----------
; star and org are taught poses, not set here.
  prepv = 100                ; mm/s for star -> org
  startsig = 2026            ; wait for this signal at org before drawing
                             ; ("sugar is pouring").  0 = do not wait.
  parkv = 100                ; mm/s while turning the pot upright at the
                             ; end (sub_park).  Faster = less drip.

; ---------- motion defaults ----------
  SPEED 100 MM/S ALWAYS      ; until the PC sends its own speed
  ACCURACY 3 ALWAYS          ; corner blending, PC can change with acc,n
.END

.PROGRAM sub_do()
; Parse one line and act on it straight away.
; No BREAK between moves - that would stop the arm at every point and
; each stop leaves a blob of sugar.  The PC sends "brk" where it wants
; a real stop.  ACCURACY does the corner blending.
  CALL sub_next
  $cmd = $fld

  IF $cmd == "lmove" THEN
    CALL sub_xyzv
    POINT st = SHIFT(org BY vx, vy, vz)
    LMOVE st
    RETURN
  END

  IF $cmd == "jmove" THEN
    CALL sub_xyzv
    POINT st = SHIFT(org BY vx, vy, vz)
    JMOVE st
    RETURN
  END

  IF $cmd == "ldepart" THEN
    CALL sub_next
    vz = VAL($fld)
    CALL sub_next
    CALL sub_speed
    LDEPART vz
    RETURN
  END

; AS runs ahead of motion: a SIGNAL right after LMOVE fires as soon as
; the move STARTS, not when it arrives.  BREAK first so the valve only
; switches once the arm is really there.  If the PC already sent brk
; there is nothing in motion and this BREAK costs nothing.
  IF $cmd == "sig" THEN
    CALL sub_next
    signum = VAL($fld)
    BREAK
    SIGNAL signum
; remember an open valve so sub_safe can shut it if the link drops
    IF signum > 0 THEN
      sigon = signum
    ELSE
      sigon = 0
    END
    RETURN
  END

  IF $cmd == "wait" THEN
    CALL sub_next
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
    CALL sub_next
    ox = VAL($fld)
    CALL sub_next
    oy = VAL($fld)
    CALL sub_next
    oz = VAL($fld)
    CALL sub_next
    oo = VAL($fld)
    CALL sub_next
    oa = VAL($fld)
    CALL sub_next
    ot = VAL($fld)
    POINT org = TRANS(ox, oy, oz, oo, oa, ot)
    RETURN
  END

  IF $cmd == "acc" THEN
    CALL sub_next
    accv = VAL($fld)
    ACCURACY accv ALWAYS
    RETURN
  END

  IF $cmd == "end" THEN
    quit = 1
    RETURN
  END

; unknown command: only flag it, the main loop sends the single reply
  bad = 1
  $badcmd = $cmd
.END

.PROGRAM sub_xyzv()
; read x,y,z,v and set the speed only when it changed
  CALL sub_next
  vx = VAL($fld)
  CALL sub_next
  vy = VAL($fld)
  CALL sub_next
  vz = VAL($fld)
  CALL sub_next
  CALL sub_speed
.END

.PROGRAM sub_speed()
; $fld holds the speed.  Emitting SPEED on every point would flood the
; controller, so only do it when the value actually changes.
  spdv = VAL($fld)
  IF spdv <> lastv THEN
    SPEED spdv MM/S ALWAYS
    lastv = spdv
  END
.END

.PROGRAM sub_next()
; take one comma separated field from $line into $fld
; both $DECODE calls are required, see the header note
  $fld = $DECODE($line, ",", 0)
  $sep = $DECODE($line, ",", 1)
.END

.PROGRAM sub_accept()
; TCP_ACCEPT times out after 60 s max, so loop until a host shows up
  sock = -1
  WHILE sock < 0 DO
    sock = 999
    TCP_ACCEPT sock, port, 60, ipa[0]
    WAIT (sock<>999)
    IF sock < 0 THEN
      PRINT "waiting for host..."
    END
  END
  PRINT "connected from ", ipa[0], ipa[1], ipa[2], ipa[3]
.END

.PROGRAM sub_packet()
; Read and run ONE packet, then send exactly one reply.
;
; Wire format: every line ends with LF and the packet ends with an
; empty line, so a packet always finishes with LF LF.  That end mark
; is the only thing that says "packet complete":
;  - TCP is a stream, one packet may arrive in several TCP_RECV calls.
;  - TCP_RECV cuts the data into elements of rmax chars, so a packet
;    longer than one string (255 max) spans $rbuf[0], $rbuf[1], ...
;    A line cut at an element edge is carried in $rest and glued to
;    the next element.  rmax + maxline must stay <= 255.
;
; Exactly ONE reply per packet, sent after the end mark: the PC sends
; one and waits for one, an extra reply knocks it out of step.  After a
; bad line the rest of the packet is still READ (to find the end mark)
; but not run - the PC aborts on ER anyway.
;
; $DECODE(.., $eol, 1) takes ALL consecutive LFs, so the end mark LF LF
; comes back as one 2-char separator.  If the two LFs land in different
; elements the second shows up as an empty line instead.  Both mean end.
  bad = 0
  eop = 0
  $rest = ""
  WHILE (eop == 0) AND (lost == 0) DO
    CALL sub_recv
    ie = 0
    WHILE (ie < rcnt) AND (eop == 0) AND (lost == 0) DO
      $chunk = $rest + $rbuf[ie]
      $rest = ""
      ie = ie + 1
      WHILE (LEN($chunk) > 0) AND (eop == 0) DO
        $line = $DECODE($chunk, $eol, 0)
        $sep = $DECODE($chunk, $eol, 1)
; sub_do eats $line and reuses $sep, so keep their lengths first
        nline = LEN($line)
        nsep = LEN($sep)
        IF nsep == 0 THEN
; no LF yet: the line continues in the next element
          IF nline > maxline THEN
            bad = 1
            $badcmd = "toolong"
          ELSE
            $rest = $line
          END
        ELSE
          IF (nline > 0) AND (bad == 0) THEN
            CALL sub_do
          END
          IF (nsep >= 2) OR (nline == 0) THEN
            eop = 1
          END
        END
      END
    END
  END
  IF lost == 0 THEN
    IF bad <> 0 THEN
      CALL sub_err
    ELSE
      CALL sub_ok
    END
  END
.END

.PROGRAM sub_recv()
; Fills $rbuf[0 .. rcnt-1], rmax chars per element.
; The PC sends the next packet the moment it gets OK, so the line is
; never idle while a job runs.  Any error, timeout or empty read means
; the host is gone: set lost and let the main loop re-accept.
  ret = 999
  rcnt = 0
  TCP_RECV ret, sock, $rbuf[0], rcnt, tmo, rmax
  WAIT (ret<>999)
  IF ret == 0 THEN
    IF rcnt < 1 THEN
      PRINT "host closed the connection"
      lost = 1
    END
  ELSE
    PRINT "TCP_RECV failed, ret = ", ret
    lost = 1
  END
.END

.PROGRAM sub_send()
  $sbuf[0] = $tx
  ret = 999
  TCP_SEND ret, sock, $sbuf[0], 1, tmo
  WAIT (ret<>999)
  IF ret <> 0 THEN
    PRINT "TCP_SEND failed, ret = ", ret
    lost = 1
  END
.END

.PROGRAM sub_prep()
; Teapot start sequence, once per run, AFTER the PC has connected:
;   star -> org -> wait for startsig -> return, drawing starts.
; startsig means "chocolate is pouring", so drawing must begin the
; moment it comes.  That is why this runs only once the PC is already
; connected: its first packet is waiting in the buffer and is read
; straight away.  The PC must allow a long wait for that first OK.
  SPEED prepv MM/S ALWAYS
  JMOVE star
  LMOVE org
  BREAK                      ; really at org before watching the signal
  IF startsig <> 0 THEN
    PRINT "at org, waiting for signal ", startsig
    WAIT SIG(startsig)
    PRINT "signal ", startsig, " on, start drawing"
  END
  lastv = -1                 ; force the PC speed on the next move
.END

.PROGRAM sub_park()
; The PC sent "end" after lifting with ldepart.  A tilted teapot keeps
; pouring, so FIRST stand it upright where it is, THEN leave.  Going
; straight to star would tilt back while moving and drip a trail.
;
; Upright = star's orientation (o, a, t).  Keep the current x, y, z and
; take o, a, t from star: the TCP (spout) stays put, only the pot turns.
  BREAK
  SPEED parkv MM/S ALWAYS
  POINT pkcur = HERE
  DECOMPOSE pkc[0] = pkcur
  DECOMPOSE pks[0] = star
  POINT pkup = TRANS(pkc[0], pkc[1], pkc[2], pks[3], pks[4], pks[5])
  LMOVE pkup
  BREAK                      ; really upright before moving away
  PRINT "pot upright, going back to star"
  SPEED prepv MM/S ALWAYS
  LMOVE star
  BREAK
  PRINT "done, back at star"
.END

.PROGRAM sub_safe()
; Link dropped mid job.  Let the queued motion finish, then shut the
; sugar valve so it does not keep pouring.  The arm is NOT moved: the
; PC knows the geometry and decides how to lift away on reconnect.
  BREAK
  IF sigon > 0 THEN
    SIGNAL -sigon
    PRINT "link lost, valve ", sigon, " closed"
    sigon = 0
  END
.END

.PROGRAM sub_ok()
  $tx = "OK"
  CALL sub_send
.END

.PROGRAM sub_err()
; name the offending command so the PC can print it
  $tx = "ER," + $badcmd
  CALL sub_send
.END

.PROGRAM Comment___ () ; Comments for IDE. Do not use.
	; @@@ PROJECT @@@
	; @@@ PROJECTNAME @@@
	; sugar_main
	; @@@ HISTORY @@@
	; @@@ INSPECTION @@@
	; @@@ CONNECTION @@@
	; 
	; 
	; 
	; @@@ PROGRAM @@@
	; 0:sugar_main:F
	; 0:sugar_init:F
	; 0:sub_do:F
	; 0:sub_xyzv:F
	; 0:sub_speed:F
	; 0:sub_next:F
	; 0:sub_accept:F
	; 0:sub_packet:F
	; 0:sub_recv:F
	; 0:sub_send:F
	; 0:sub_ok:F
	; 0:sub_err:F
	; 0:sub_prep:F
	; 0:sub_park:F
	; 0:sub_safe:F
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
