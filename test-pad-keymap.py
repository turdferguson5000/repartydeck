#!/usr/bin/python3
"""End-to-end test for the gamepad -> keyboard/mouse translation.

Every bug in this feature shipped because it was never exercised without a physical controller
in hand:

  * the stdout contract was wrong, so PartyDeck fed a log line to gamescope's
    --libinput-hold-dev and the instance died before the game started;
  * the virtual device was destroyed when its pad blipped, so a later instance found a dead
    path and the launch failed;
  * orphaned mappers kept an exclusive grab on the pads, so PartyDeck could not read a button
    press to assign a controller - "the controller appears then disappears".

Each of those is a test below. The pad is created in-process with uinput so input can actually
be injected, which is what makes real translation testing possible.

    test-pad-keymap.py          # run everything
    test-pad-keymap.py -v       # per-event detail

Needs write access to /dev/uinput (granted on this host by the Sunshine udev rules).
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
    """Read every event from a device in the background."""

    def __init__(self, path, seconds):
        super().__init__(daemon=True)
        self.path, self.seconds, self.events, self.error = path, seconds, [], None

    def run(self):
        try:
            dev = evdev.InputDevice(self.path)
        except OSError as ex:
            self.error = str(ex)
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


def main():
    if not os.access("/dev/uinput", os.W_OK):
        print("  /dev/uinput is not writable - cannot run", file=sys.stderr)
        return 2

    print("\n=== pad-keymap end-to-end test ===\n", flush=True)
    subprocess.run(["pkill", "-f", r"/pad-keymap\.py"], capture_output=True)
    time.sleep(0.4)

    pad = UInput({e.EV_KEY: BUTTONS, e.EV_ABS: AXES}, name="Fake Xbox Pad 1",
                 vendor=0x045E, product=0x028E, version=0x110)
    time.sleep(0.5)
    pad_path = pad.device.path
    check("fake pad created", os.path.exists(pad_path), pad_path)

    mapper = None
    try:
        mapper = subprocess.Popen(
            [KEYMAP, "--device", pad_path, "--profile", "torchlight2",
             "--index", "1", "--print-node"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 1. THE CONTRACT: first stdout line must be a bare device path and nothing else.
        first = mapper.stdout.readline().strip()
        log(f"first stdout line: {first!r}")
        check("stdout line 1 is a bare device path",
              re.fullmatch(r"/dev/input/event\d+", first), first)
        node = first
        time.sleep(0.5)
        check("virtual device exists", os.path.exists(node), node)

        # 2. SLOT CEILING: a sandboxed SDL only scans event0-31.
        num = int(re.search(r"(\d+)$", node).group(1))
        check("virtual device within the event0-31 scan range", num <= 31, f"event{num}")

        # 3. ONE device per pad, not two (slots are scarce).
        names = []
        for p in evdev.list_devices():
            try:
                d = evdev.InputDevice(p)
            except OSError:
                continue
            if d.name.startswith("PD Keymap"):
                names.append(d.name)
            d.close()
        check("exactly one virtual device per pad", len(names) == 1, ", ".join(names))

        # 4. BUTTON TRANSLATION: A -> left click, in the torchlight2 profile.
        col = Collector(node, 2.0)
        col.start()
        time.sleep(0.4)
        pad.write(e.EV_KEY, e.BTN_SOUTH, 1); pad.syn()
        time.sleep(0.15)
        pad.write(e.EV_KEY, e.BTN_SOUTH, 0); pad.syn()
        col.join()
        downs = [v for v in col.events if v.type == e.EV_KEY and v.code == e.BTN_LEFT and v.value == 1]
        ups = [v for v in col.events if v.type == e.EV_KEY and v.code == e.BTN_LEFT and v.value == 0]
        log(f"{len(col.events)} events; BTN_LEFT down={len(downs)} up={len(ups)}")
        check("pad A button becomes a left click", downs and ups,
              f"down={len(downs)} up={len(ups)}")

        # 5. CURSOR: left stick deflection becomes relative pointer motion.
        col = Collector(node, 2.0)
        col.start()
        time.sleep(0.4)
        pad.write(e.EV_ABS, e.ABS_X, 30000); pad.syn()
        time.sleep(1.0)
        pad.write(e.EV_ABS, e.ABS_X, 0); pad.syn()
        col.join()
        rel = [v for v in col.events if v.type == e.EV_REL and v.code == e.REL_X]
        # Track the FURTHEST the cursor got, not the net total. In "direction" mode the cursor
        # parks beside the character and springs back to centre when the stick is released, so
        # the net sum over a press-and-release window is zero BY DESIGN - asserting on the sum
        # would fail a correctly working mode.
        run_sum, peak = 0, 0
        for v in rel:
            run_sum += v.value
            peak = max(peak, abs(run_sum))
        log(f"REL_X events={len(rel)} peak={peak} net={run_sum}")
        check("left stick moves the cursor", len(rel) > 5 and peak > 40,
              f"{len(rel)} events, peak={peak}px")

        # 6. DEADZONE: a centred stick must not drift.
        col = Collector(node, 1.2)
        col.start()
        col.join()
        drift = [v for v in col.events if v.type == e.EV_REL]
        check("centred stick does not drift", len(drift) == 0, f"{len(drift)} stray events")

        # 7. GRAB: the pad is held exclusively, so the desktop stops reacting to it.
        try:
            d = evdev.InputDevice(pad_path); d.grab(); d.ungrab(); d.close()
            grabbed = False
        except OSError:
            grabbed = True
        check("mapper holds an exclusive grab on the pad", grabbed)

        # 8. SURVIVES A PAD BLIP: the virtual device must outlive its pad, or a later
        #    instance finds a dead path and gamescope kills the launch.
        pad.close()
        time.sleep(1.5)
        alive = os.path.exists(node)
        check("virtual device survives the pad disappearing", alive, node)
        pad = UInput({e.EV_KEY: BUTTONS, e.EV_ABS: AXES}, name="Fake Xbox Pad 1",
                     vendor=0x045E, product=0x028E, version=0x110)
        time.sleep(2.5)   # mapper polls every 2s for the pad's return

        # 9. CLEANUP: exiting releases the pad and removes the device.
        mapper.terminate()
        try:
            mapper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mapper.kill(); mapper.wait()
        time.sleep(0.8)
        check("virtual device removed on exit", not os.path.exists(node), node)
        try:
            d = evdev.InputDevice(pad.device.path); d.grab(); d.ungrab(); d.close()
            released = True
        except OSError as ex:
            released = False
            log(f"still grabbed: {ex}")
        check("pad released on exit (PartyDeck can assign it again)", released)
        mapper = None

    finally:
        if mapper is not None:
            mapper.kill(); mapper.wait()
        subprocess.run(["pkill", "-f", r"/pad-keymap\.py"], capture_output=True)
        try:
            pad.close()
        except Exception:
            pass

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n  {len(results) - len(failed)}/{len(results)} passed", flush=True)
    if failed:
        print("  FAILED: " + "; ".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
