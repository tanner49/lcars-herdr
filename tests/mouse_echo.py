"""Scratch stand-in for an app that asks for the mouse the way Claude Code does (?1000 + ?1006, alt screen).

Prints every mouse report it receives as `EV <press|release> b=<button> col=<c> row=<r>` (1-based). q quits.
Used by tests/mouse_ui.py inside a scratch shell tab.
"""

import os
import re
import sys
import termios
import tty

fd = sys.stdin.fileno()
saved = termios.tcgetattr(fd)
w = sys.stdout.write
w("\x1b[?1049h\x1b[?1000h\x1b[?1006h\x1b[2J\x1b[HMOUSE-ECHO ready\r\n")
sys.stdout.flush()
tty.setraw(fd)
buf, line = b"", 2
try:
    while True:
        chunk = os.read(fd, 1024)
        if not chunk:
            break
        buf += chunk
        for m in re.finditer(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", buf):
            b, c, r, kind = int(m[1]), int(m[2]), int(m[3]), "press" if m[4] == b"M" else "release"
            w(f"\x1b[{line};1HEV {kind} b={b} col={c} row={r}")
            line = 2 + (line - 1) % 20
            sys.stdout.flush()
        buf = re.sub(rb"\x1b\[<[\d;]*[Mm]", b"", buf)
        if b"q" in buf:
            break
        buf = buf[-32:]
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    w("\x1b[?1000l\x1b[?1006l\x1b[?1049l")
    sys.stdout.flush()
