#!/usr/bin/python3
"""Full-flow test: handler -> PartyDeck -> mapper -> gamescope/bwrap arguments.

The pad-keymap harnesses prove the translator works. This proves PARTYDECK wires it up
correctly, which is where every launch failure actually happened:

  * a log line reaching gamescope's --libinput-hold-dev instead of a device path;
  * a dead device path surviving into the command line;
  * one player's device being handed to another because the node list was misaligned;
  * mappers left running afterwards, holding the pads.

It drives `partydeck --dry-run-launch <handler> <pad,pad>`, which builds the REAL launch
commands - handler parsing, mapper spawning, gamescope args, bwrap device masking - and prints
them as JSON instead of starting games. Everything downstream of that is rendering.

A throwaway handler pointing at a stub "game" is created and removed, so nothing in the real
library is touched.

    test-partydeck-flow.py [-v]
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

import evdev
from evdev import UInput, AbsInfo, ecodes as e

PARTYDECK = "/var/home/user/partydeck/partydeck"
HANDLERS = os.path.expanduser("~/.local/share/partydeck/handlers")
TEST_HANDLER = "ZZTestFlow"
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


def make_pad(n):
    return UInput({e.EV_KEY: BUTTONS, e.EV_ABS: AXES}, name=f"Fake Xbox Pad {n}",
                  vendor=0x045E, product=0x028E, version=0x110)


def make_handler(gamedir, keymap_profile):
    d = os.path.join(HANDLERS, TEST_HANDLER)
    os.makedirs(d, exist_ok=True)
    json.dump({
        "name": "ZZ Test Flow", "author": "test", "version": "1.0",
        "info": "Throwaway handler for test-partydeck-flow.py. Safe to delete.",
        "spec_ver": 3,
        "path_gameroot": gamedir,
        "runtime": "", "exec": "fakegame.sh", "args": "", "env": "",
        "sdl2_override": "No", "pause_between_starts": 0.5,
        "use_mangohud": False, "use_goldberg": False, "enable_hidraw": False,
        "pad_keymap": keymap_profile,
        "steam_appid": None, "game_null_paths": [],
    }, open(os.path.join(d, "handler.json"), "w"), indent=2)
    return d


def dry_run(pads):
    """Run PartyDeck's dry-run and return the parsed command list."""
    out = subprocess.run(
        [PARTYDECK, "--dry-run-launch", TEST_HANDLER, ",".join(pads)],
        capture_output=True, text=True, timeout=120)
    body = out.stdout
    # PartyDeck prints log lines before AND after the JSON (the cleanup message, for one), so
    # take exactly the array: from a line that is "[" to the first line that is "]".
    m = re.search(r"^\[$.*?^\]$", body, re.M | re.S)
    if not m:
        log(f"stdout:\n{body[-800:]}\nstderr:\n{out.stderr[-800:]}")
        return None, out
    try:
        return json.loads(m.group(0)), out
    except json.JSONDecodeError as ex:
        log(f"JSON error: {ex}\n{m.group(0)[:400]}")
        return None, out


def main():
    if not os.path.exists(PARTYDECK):
        print(f"  {PARTYDECK} not found", file=sys.stderr)
        return 2
    if not os.access("/dev/uinput", os.W_OK):
        print("  /dev/uinput not writable", file=sys.stderr)
        return 2

    print("\n=== PartyDeck full-flow test ===\n", flush=True)
    subprocess.run(["pkill", "-f", r"/pad-keymap\.py"], capture_output=True)
    time.sleep(0.4)

    gamedir = "/tmp/pd-flow-game"
    os.makedirs(gamedir, exist_ok=True)
    stub = os.path.join(gamedir, "fakegame.sh")
    with open(stub, "w") as f:
        f.write("#!/bin/bash\n# stub game: never actually run by the dry run\nsleep 1\n")
    os.chmod(stub, 0o755)

    pads = []
    try:
        pads = [make_pad(1), make_pad(2)]
        time.sleep(0.8)
        paths = [p.device.path for p in pads]
        log(f"fake pads: {paths}")

        # ---------------------------------------------------------------- with mapping
        make_handler(gamedir, "torchlight2")
        cmds, raw = dry_run(paths)
        if not check("dry run produced launch commands", cmds is not None,
                     "" if cmds else "see -v"):
            return 1
        check("one command per player", len(cmds) == 2, f"{len(cmds)} commands")

        flat = [" ".join(c) for c in cmds]
        holds = [re.search(r"--libinput-hold-dev=([^\s]+)", f) for f in flat]
        nodes = [h.group(1).rstrip(",") if h else "" for h in holds]
        log(f"hold-dev per instance: {nodes}")

        # THE bug that broke every launch: a log line where a device path belongs.
        check("each instance gets a real device path, not a log line",
              all(re.fullmatch(r"/dev/input/event\d+", n) for n in nodes),
              " ".join(n[:40] for n in nodes))
        check("players get different devices", len(set(nodes)) == len(nodes), str(nodes))

        # Isolation: each instance must mask the OTHER player's virtual device.
        ok_mask = True
        for i, f in enumerate(flat):
            other = nodes[1 - i]
            if f"--bind /dev/null {other}" not in f:
                ok_mask = False
                log(f"instance {i} does not mask {other}")
        check("each instance masks the other player's device", ok_mask)

        # And the other player's physical pad.
        ok_pad = all(f"--bind /dev/null {paths[1 - i]}" in f for i, f in enumerate(flat))
        check("each instance masks the other player's pad", ok_pad)

        check("stub game is the executable being launched",
              all("fakegame.sh" in f for f in flat))

        # Cleanup contract: the dry run really started mappers, and must stop them.
        time.sleep(1.0)
        left = subprocess.run(["pgrep", "-cf", r"/pad-keymap\.py"], capture_output=True,
                              text=True).stdout.strip() or "0"
        check("mappers stopped when the run ended", left == "0", f"{left} left")
        still = []
        for p in paths:
            try:
                d = evdev.InputDevice(p)
                d.grab(); d.ungrab(); d.close()
            except OSError:
                still.append(p)
        check("pads released (PartyDeck can assign them again)", not still, str(still))

        # ---------------------------------------------------------- without mapping
        # pad_keymap empty must behave exactly like stock PartyDeck.
        make_handler(gamedir, "")
        cmds2, _ = dry_run(paths)
        if check("dry run works with mapping disabled", cmds2 is not None):
            flat2 = [" ".join(c) for c in cmds2]
            check("no keymap device is injected when disabled",
                  not any("PD Keymap" in f for f in flat2)
                  and all("--libinput-hold-dev" not in f or "event" in f for f in flat2))
            check("pads still masked correctly when disabled",
                  all(f"--bind /dev/null {paths[1 - i]}" in f for i, f in enumerate(flat2)))

    finally:
        subprocess.run(["pkill", "-f", r"/pad-keymap\.py"], capture_output=True)
        shutil.rmtree(os.path.join(HANDLERS, TEST_HANDLER), ignore_errors=True)
        shutil.rmtree(gamedir, ignore_errors=True)
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
