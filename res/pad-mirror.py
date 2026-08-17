#!/usr/bin/python3
"""Re-emit one gamepad on a uinput node that outlives the physical device.

WHY THIS EXISTS
---------------
PartyDeck sandboxes each instance with a WHITELIST: /dev/input is a fresh tmpfs and only that
player's devices are bind-mounted back in. That is what stops one pad driving every game at
once. It also means the sandbox is frozen at launch - whatever /dev/input node the pad had when
bwrap started is the only one that instance will ever see.

Wireless pads do not cooperate with that. An Xbox 360 wireless pad powers itself off when idle
(and `xpad` runs with auto_poweroff=Y), so a player who sits through a staggered four-instance
launch can easily have their pad sleep before the game is up. When they wake it, the kernel
DESTROYS the old input device and creates a new one - same /dev/input/eventN name, same minor,
different device. The game's open fd is dead and the sandbox cannot be told about the
replacement, so that player has no controller for the rest of the session.

Measured on 2026-08-16, a four-player Orcs Must Die! 3 session:

    launch 20:36     event24=input257  event29=input259  event30=input260
    20:46:49/50      event29 -> input264, event30 -> input265   (pads woken)
    result           event24 worked; event29 and event30 dead    <- exactly those two

This script breaks that coupling. It grabs the physical pad, mirrors it onto a uinput device
that PartyDeck creates and holds open, and hands the sandbox the MIRROR. When the pad power-
cycles, only this process notices: the mirror node never goes away, so the game keeps its fd
and simply sees a controller that stopped sending events for a moment.

RE-ACQUIRING THE RIGHT PAD
--------------------------
Do NOT match on name/vendor/product/phys. Every pad on an Xbox 360 wireless receiver reports
the identical name, the identical 045e:02a1, the identical phys, and an EMPTY uniq - four
controllers indistinguishable by every field a device normally identifies itself with.

What does distinguish them is the receiver SLOT: each pad lives on its own USB interface
(3-3:1.0, 3-3:1.2, 3-3:1.4, 3-3:1.6) and comes back on the same one. So the key is the USB
interface directory in sysfs, which survives the input device being recreated. Matching on
anything weaker would hand a player someone else's controller - the precise bug the whitelist
was introduced to fix.

Wired pads have their own stable interface path too, so the same rule covers them.

LIMITATION: rumble does not survive the mirror. Force feedback needs UI_FF_UPLOAD handling and
a path back to the physical device; input is forwarded, haptics are not.

    pad-mirror.py --device /dev/input/eventN [--index N] [--print-node]
"""

import argparse
import os
import select
import sys
import time
import traceback

import evdev
from evdev import UInput, ecodes as e


def log(msg):
    """Diagnostics go to stderr - stdout's first line is the node path and nothing else."""
    print(f"[pad-mirror] {msg}", file=sys.stderr, flush=True)


def slot_key(path):
    """The receiver slot / USB interface a pad is plugged into.

    Returns something like 'usb3/3-3/3-3:1.4'. This is the only field that stays put when a
    wireless pad power-cycles: the input device below it is destroyed and recreated, but the
    interface it hangs off does not move.
    """
    node = os.path.basename(path)
    try:
        real = os.path.realpath(f"/sys/class/input/{node}/device")
    except OSError:
        return None
    # .../usb3/3-3/3-3:1.4/input/input264  ->  everything above the input device itself
    marker = "/input/input"
    idx = real.find(marker)
    return real[:idx] if idx != -1 else real


def find_by_slot(key):
    """The current evdev node sitting on a given slot, or None."""
    if not key:
        return None
    for path in sorted(evdev.list_devices()):
        try:
            if slot_key(path) == key:
                return path
        except OSError:
            continue
    return None


def open_pad(path):
    dev = evdev.InputDevice(path)
    # Exclusive: the game must not also be able to read the raw pad, or a player's input would
    # arrive twice and (before the whitelist) in the wrong instances.
    dev.grab()
    return dev


def build_mirror(src, index):
    """A uinput device advertising exactly what the source pad advertises.

    Copying the real capabilities is right here (unlike the Series X|S shim, which had to
    pretend to be a 360 pad because Rewired did not know the newer one): the game already
    supports this controller, so the mirror should be indistinguishable from it.
    """
    caps = src.capabilities(absinfo=True)
    caps.pop(e.EV_SYN, None)
    # Force feedback cannot be forwarded through plain uinput - advertising it would make the
    # game think rumble works and get silence.
    caps.pop(e.EV_FF, None)
    caps.pop(e.EV_FF_STATUS, None)
    info = src.info
    return UInput(caps, name=f"PD Pad {index} ({src.name})",
                  vendor=info.vendor, product=info.product, version=info.version)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--index", type=int, default=1)
    ap.add_argument("--print-node", action="store_true")
    args = ap.parse_args()

    key = slot_key(args.device)
    log(f"pad {args.device} slot={key}")

    try:
        src = open_pad(args.device)
    except OSError as ex:
        log(f"cannot open {args.device}: {ex}")
        return 1

    ui = build_mirror(src, args.index)
    node = ui.device.path

    # FIRST stdout line is the node, and nothing else may precede it: PartyDeck reads exactly
    # one line and feeds it straight into the sandbox. A stray log line here used to take the
    # whole instance down before the game started.
    if args.print_node:
        print(node, flush=True)
    log(f"mirroring {src.name} -> {node}")

    while True:
        try:
            r, _, _ = select.select([src.fd], [], [], 1.0)
            if not r:
                continue
            for ev in src.read():
                if ev.type == e.EV_SYN:
                    continue
                ui.write(ev.type, ev.code, ev.value)
            ui.syn()
        except (OSError, IOError) as ex:
            # The pad went away - almost always it powered off. The mirror STAYS UP; that is
            # the entire point. Sit here until the same slot has a device again.
            log(f"pad lost ({ex}); mirror {node} stays up, waiting for slot {key}")
            try:
                src.ungrab()
            except OSError:
                pass
            try:
                src.close()
            except OSError:
                pass
            src = None
            while src is None:
                time.sleep(1.0)
                path = find_by_slot(key)
                if not path:
                    continue
                try:
                    src = open_pad(path)
                except OSError:
                    # udev is probably still applying permissions; try again next tick.
                    src = None
                    continue
                log(f"pad back on {path}, resumed on {node}")
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
