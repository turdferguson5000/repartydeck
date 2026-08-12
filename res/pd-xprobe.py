#!/usr/bin/python3
# Runs INSIDE gamescope. Creates a REAL mapped window and counts input delivered to it.
#
# An earlier version selected events on the root window with no window of its own and saw
# nothing - gamescope forwards stolen input to the focused client SURFACE, so a client with no
# window is not a valid test of whether input arrives.
import sys, time
from Xlib import X, display
try:
    d = display.Display()
except Exception as ex:
    print(f"XPROBE ERROR {ex}", flush=True); sys.exit(1)

scr = d.screen()
win = scr.root.create_window(0, 0, 1280, 720, 0, scr.root_depth,
                             X.InputOutput, X.CopyFromParent,
                             background_pixel=scr.black_pixel,
                             event_mask=(X.PointerMotionMask | X.ButtonPressMask |
                                         X.ButtonReleaseMask | X.KeyPressMask |
                                         X.KeyReleaseMask | X.StructureNotifyMask |
                                         X.FocusChangeMask | X.ExposureMask))
win.set_wm_name("pd-xprobe")
win.map()
d.sync()
try:
    win.set_input_focus(X.RevertToParent, X.CurrentTime)
    d.sync()
except Exception:
    pass
print(f"XPROBE READY win={win.id}", flush=True)

motion = buttons = keys = 0
end = time.time() + 25
nxt = time.time() + 1.0
while time.time() < end:
    while d.pending_events():
        ev = d.next_event()
        if ev.type == X.MotionNotify: motion += 1
        elif ev.type in (X.ButtonPress, X.ButtonRelease): buttons += 1
        elif ev.type in (X.KeyPress, X.KeyRelease): keys += 1
    if time.time() >= nxt:
        print(f"XPROBE RESULT motion={motion} buttons={buttons} keys={keys}", flush=True)
        nxt = time.time() + 1.0
    time.sleep(0.05)
