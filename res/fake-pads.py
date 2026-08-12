#!/usr/bin/python3
"""Create virtual Xbox-style gamepads for testing, and script input into them.

WHY: pad-keymap.py and PartyDeck's controller handling could only ever be tested by picking up
a physical controller, which meant several "fixes" shipped unverified. These fake pads are
indistinguishable from real ones as far as evdev is concerned - same button codes, same axes,
same Microsoft vendor/product - so the whole chain can be exercised headlessly.

    fake-pads.py --count 2                 # create 2 pads, hold them until Ctrl-C
    fake-pads.py --count 1 --press BTN_SOUTH
    fake-pads.py --count 1 --stick 20000,0 --hold 3
"""

import argparse
import sys
import time

from evdev import UInput, AbsInfo, ecodes as e

# The canonical Xbox 360 pad layout: 11 buttons and 8 axes. Advertising exactly this matters -
# CLAUDE.md 10e records a shim that copied a Series pad's capabilities verbatim (including
# KEY_RECORD, which no 360 pad has) and was silently discarded by games that scan for the
# standard layout.
BUTTONS = [
    e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST,
    e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START,
    e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR,
]

AXES = [
    (e.ABS_X,     AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)),
    (e.ABS_Y,     AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)),
    (e.ABS_RX,    AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)),
    (e.ABS_RY,    AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)),
    (e.ABS_Z,     AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)),
    (e.ABS_RZ,    AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)),
    (e.ABS_HAT0X, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
    (e.ABS_HAT0Y, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
]


def make_pad(index):
    return UInput(
        {e.EV_KEY: BUTTONS, e.EV_ABS: AXES},
        name=f"Fake Xbox Pad {index}",
        vendor=0x045E, product=0x028E, version=0x110,   # Microsoft Xbox 360 pad
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--press", action="append", default=[],
                    help="button to click once, e.g. BTN_SOUTH (repeatable)")
    ap.add_argument("--stick", help="left stick as X,Y raw values, e.g. 20000,0")
    ap.add_argument("--hold", type=float, default=0.0,
                    help="seconds to hold the stick / stay alive before exiting")
    ap.add_argument("--print-nodes", action="store_true",
                    help="print each pad's /dev/input/eventN, one per line, then continue")
    args = ap.parse_args()

    pads = [make_pad(i + 1) for i in range(args.count)]
    # uinput devices take a moment to appear; without this a test that opens the node
    # immediately hits a race and reports a false failure.
    time.sleep(0.4)

    if args.print_nodes:
        for p in pads:
            print(p.device.path, flush=True)

    for btn in args.press:
        code = getattr(e, btn)
        for p in pads:
            p.write(e.EV_KEY, code, 1)
            p.syn()
        time.sleep(0.08)
        for p in pads:
            p.write(e.EV_KEY, code, 0)
            p.syn()
        time.sleep(0.08)

    if args.stick:
        x, y = (int(v) for v in args.stick.split(","))
        for p in pads:
            p.write(e.EV_ABS, e.ABS_X, x)
            p.write(e.EV_ABS, e.ABS_Y, y)
            p.syn()

    if args.hold:
        time.sleep(args.hold)
    elif not args.press and not args.stick:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    for p in pads:
        p.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
