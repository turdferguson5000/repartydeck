#!/usr/bin/python3
"""Edge cases for the gamepad translation that the main harness does not cover.

The first harness proves one pad works end to end. These are the cases that only appear with
several players, or when something is wrong:

  * CROSS-TALK - two players must not see each other's input. This is the property the whole
    multi-instance design rests on; if it fails, everyone controls everyone.
  * BAD INPUT - a wrong profile or a missing device must fail cleanly and print nothing on
    stdout, because PartyDeck reads stdout as a device path and a stray line there is fatal to
    gamescope.
  * PROFILES - the per-game mappings must actually differ.

    test-pad-keymap-edge.py [-v]
"""

import os
import re
import select
import subprocess
import sys
import threading
import time

import evdev
from evdev import UInput, AbsInfo, ecodes as e

HERE = os.path.dirname(os.path.abspath(__file__))
KEYMAP = os.path.join(HERE, "pad-keymap.py")
VERBOSE = "-v" in sys.argv
results = []

BUTTONS = [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR,
           e.BTN_SELECT, e.BTN_START, e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR]
AXES = [(c, AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0))
        for c in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY)] + \
       [(c, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0))
        for c in (e.ABS_HAT0X, e.ABS_HAT0Y)]


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""),
          flush=True)
    return bool(ok)


def log(msg):
    if VERBOSE:
        print(f"          {msg}", flush=True)


class Collector(threading.Thread):
    def __init__(self, path, seconds):
        super().__init__(daemon=True)
        self.path, self.seconds, self.events = path, seconds, []

    def run(self):
        try:
            dev = evdev.InputDevice(self.path)
        except OSError:
            return
        end = time.monotonic() + self.seconds
        while time.monotonic() < end:
            r, _, _ = select.select([dev.fd], [], [], 0.2)
            if not r:
                continue
            try:
                for ev in dev.read():
                    self.events.append(ev)
            except OSError:
                break
        dev.close()


def make_pad(n):
    return UInput({e.EV_KEY: BUTTONS, e.EV_ABS: AXES}, name=f"Fake Xbox Pad {n}",
                  vendor=0x045E, product=0x028E, version=0x110)


def start_mapper(dev_path, profile, index):
    p = subprocess.Popen(
        [KEYMAP, "--device", dev_path, "--profile", profile, "--index", str(index),
         "--print-node"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = p.stdout.readline().strip()
    return p, line


def main():
    if not os.access("/dev/uinput", os.W_OK):
        print("  /dev/uinput not writable", file=sys.stderr)
        return 2

    print("\n=== pad-keymap edge cases ===\n", flush=True)
    subprocess.run(["pkill", "-f", r"/pad-keymap\.py"], capture_output=True)
    time.sleep(0.4)

    procs, pads = [], []
    try:
        # ---------------------------------------------------------- two players
        pads = [make_pad(1), make_pad(2)]
        time.sleep(0.6)
        p1, n1 = start_mapper(pads[0].device.path, "torchlight2", 1)
        p2, n2 = start_mapper(pads[1].device.path, "torchlight2", 2)
        procs += [p1, p2]
        time.sleep(0.6)
        log(f"player1 -> {n1}   player2 -> {n2}")

        check("two mappers produce two distinct devices", n1 != n2 and n1 and n2, f"{n1} / {n2}")
        # Slot numbers are reported, NOT asserted. The event0-31 ceiling in CLAUDE.md 10e
        # applies to SDL scanning for controllers inside a sandbox; these devices are handed to
        # gamescope by explicit path via --libinput-hold-dev and are never discovered by a
        # numeric scan. What actually has to hold is that the path opens - a path gamescope
        # cannot open is fatal to the instance, which is the failure we really hit.
        nums = [int(re.search(r"(\d+)$", n).group(1)) for n in (n1, n2) if re.search(r"\d+$", n)]
        high = [x for x in nums if x > 31]
        if high:
            print(f"          INFO devices above event31: {high} - fine for the explicit-path "
                  f"handoff, but a game scanning for pads would not see them", flush=True)
        openable = []
        for n in (n1, n2):
            try:
                fd = os.open(n, os.O_RDONLY)
                os.close(fd)
                openable.append(True)
            except OSError:
                openable.append(False)
        check("both devices openable by path (what gamescope needs)", all(openable), str(nums))

        # THE isolation property: player 1 pressing A must produce nothing on player 2's device.
        c1, c2 = Collector(n1, 2.0), Collector(n2, 2.0)
        c1.start(); c2.start()
        time.sleep(0.4)
        pads[0].write(e.EV_KEY, e.BTN_SOUTH, 1); pads[0].syn()
        time.sleep(0.15)
        pads[0].write(e.EV_KEY, e.BTN_SOUTH, 0); pads[0].syn()
        c1.join(); c2.join()
        on1 = [v for v in c1.events if v.type == e.EV_KEY and v.code == e.BTN_LEFT]
        on2 = [v for v in c2.events if v.type == e.EV_KEY]
        log(f"player1 device saw {len(on1)} clicks; player2 device saw {len(on2)} key events")
        check("player 1's press reaches player 1", len(on1) >= 2, f"{len(on1)} events")
        check("player 1's press does NOT reach player 2", len(on2) == 0, f"{len(on2)} events")

        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
        procs = []
        time.sleep(0.6)

        # ---------------------------------------------------------- bad input
        # A wrong profile must fail cleanly: argparse rejects it, nothing on stdout.
        bad = subprocess.run([KEYMAP, "--device", pads[0].device.path,
                              "--profile", "not-a-real-profile", "--print-node"],
                             capture_output=True, text=True, timeout=20)
        check("invalid profile exits non-zero", bad.returncode != 0, f"rc={bad.returncode}")
        check("invalid profile prints nothing on stdout", bad.stdout.strip() == "",
              repr(bad.stdout[:40]))

        # A missing device must do the same - PartyDeck would otherwise pass junk to gamescope.
        missing = subprocess.run([KEYMAP, "--device", "/dev/input/event9999",
                                  "--profile", "nwn", "--print-node"],
                                 capture_output=True, text=True, timeout=20)
        check("missing device exits non-zero", missing.returncode != 0, f"rc={missing.returncode}")
        check("missing device prints nothing on stdout", missing.stdout.strip() == "",
              repr(missing.stdout[:40]))

        # ---------------------------------------------------------- profiles differ
        # nwn maps X to a RIGHT click (its radial menu); torchlight2 maps X to right click too,
        # but B differs: TL2 sends KEY_1 (potion), NWN sends KEY_TAB (highlight).
        p3, n3 = start_mapper(pads[0].device.path, "nwn", 1)
        procs.append(p3)
        time.sleep(0.6)
        c3 = Collector(n3, 2.0)
        c3.start()
        time.sleep(0.4)
        pads[0].write(e.EV_KEY, e.BTN_EAST, 1); pads[0].syn()
        time.sleep(0.15)
        pads[0].write(e.EV_KEY, e.BTN_EAST, 0); pads[0].syn()
        c3.join()
        tabs = [v for v in c3.events if v.type == e.EV_KEY and v.code == e.KEY_TAB]
        ones = [v for v in c3.events if v.type == e.EV_KEY and v.code == e.KEY_1]
        log(f"nwn profile: KEY_TAB={len(tabs)} KEY_1={len(ones)}")
        check("nwn profile maps B to Tab, not the TL2 potion key",
              len(tabs) >= 2 and len(ones) == 0, f"tab={len(tabs)} key1={len(ones)}")

        # ---------------------------------------------------------- d-pad and wheel
        # The d-pad is an ABS hat, not buttons, and is handled by separate code that has to
        # release the previous direction before pressing the new one - easy to get wrong.
        c4 = Collector(n3, 2.0)
        c4.start()
        time.sleep(0.4)
        pads[0].write(e.EV_ABS, e.ABS_HAT0X, 1); pads[0].syn()   # d-pad right
        time.sleep(0.2)
        pads[0].write(e.EV_ABS, e.ABS_HAT0X, 0); pads[0].syn()   # release
        c4.join()
        # nwn profile: d-pad right -> KEY_4
        d_down = [v for v in c4.events if v.type == e.EV_KEY and v.code == e.KEY_4 and v.value == 1]
        d_up = [v for v in c4.events if v.type == e.EV_KEY and v.code == e.KEY_4 and v.value == 0]
        log(f"dpad right: down={len(d_down)} up={len(d_up)}")
        check("d-pad press and release both emitted", d_down and d_up,
              f"down={len(d_down)} up={len(d_up)}")

        c5 = Collector(n3, 2.0)
        c5.start()
        time.sleep(0.4)
        pads[0].write(e.EV_ABS, e.ABS_RY, -30000); pads[0].syn()  # right stick up
        time.sleep(1.0)
        pads[0].write(e.EV_ABS, e.ABS_RY, 0); pads[0].syn()
        c5.join()
        wheel = [v for v in c5.events if v.type == e.EV_REL and v.code == e.REL_WHEEL]
        log(f"wheel events={len(wheel)}")
        check("right stick emits scroll wheel", len(wheel) >= 3, f"{len(wheel)} events")

    finally:
        for p in procs:
            try:
                p.kill(); p.wait()
            except Exception:
                pass
        subprocess.run(["pkill", "-f", r"/pad-keymap\.py"], capture_output=True)
        for p in pads:
            try:
                p.close()
            except Exception:
                pass

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n  {len(results) - len(failed)}/{len(results)} passed", flush=True)
    if failed:
        print("  FAILED: " + "; ".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
