#!/usr/bin/python3
"""Drive keyboard/mouse-only games with a gamepad, one virtual pair per player.

WHY THIS EXISTS
Some games have no controller support at all - Torchlight II and Neverwinter Nights are
click-to-move RPGs built entirely around a mouse cursor and hotkeys. Steam Input would
normally paper over that, but PartyDeck does not use Steam Input: its `NoSteamInput` setting
is a *filter* that excludes Valve's virtual pads (vendor 0x28de), and under Goldberg there is
no Steam client for an Input API to talk to anyway. So the translation has to happen below the
game, at the evdev layer.

HOW IT FITS PARTYDECK
For each physical pad this creates TWO uinput devices:

    PD Keymap <n> Keyboard      key events
    PD Keymap <n> Mouse         REL_X/REL_Y, buttons, wheel

PartyDeck enumerates evdev devices and lets you assign several to one instance, so you assign
that player's virtual keyboard AND virtual mouse to their instance. Because PartyDeck binds
/dev/null over every other instance's devices inside the sandbox, each player's translated
input reaches only their own window. That is the whole trick - a single global uinput keyboard
would send keystrokes to whichever window had focus and break multi-instance entirely.

Do NOT also assign the physical pad to the instance: this script takes an exclusive grab on it
(EVIOCGRAB) so the desktop and Steam stop reacting to it, and a grabbed device delivers nothing
to the game anyway.

USAGE
    pad-keymap.py --profile torchlight2            # all pads found
    pad-keymap.py --profile nwn --device /dev/input/event5
    pad-keymap.py --list                           # show pads and profiles, create nothing
"""

import argparse
import errno
import os
import re
import select
import signal
import subprocess
import sys
import time

import evdev
from evdev import UInput, AbsInfo, ecodes as e

# ---------------------------------------------------------------------------
# Profiles
#
# Each profile maps a physical pad button to either a keyboard key ("KEY_...") or a mouse
# button ("BTN_LEFT"/"BTN_RIGHT"/"BTN_MIDDLE"). Sticks are handled separately below.
# ---------------------------------------------------------------------------
PROFILES = {
    # Torchlight II: click-to-move ARPG. Left click moves/attacks, right click is the
    # secondary skill, 1-0 are potions and skills, I is inventory, Esc the menu.
    "torchlight2": {
        e.BTN_SOUTH: "BTN_LEFT",      # A  - move / attack
        e.BTN_WEST:  "BTN_RIGHT",     # X  - secondary skill
        e.BTN_EAST:  "KEY_1",         # B  - health potion
        e.BTN_NORTH: "KEY_2",         # Y  - mana potion
        e.BTN_TL:    "KEY_3",
        e.BTN_TR:    "KEY_4",
        e.BTN_SELECT: "KEY_I",        # inventory
        e.BTN_START: "KEY_ESC",       # menu
        e.BTN_THUMBL: "KEY_SPACE",
        e.BTN_THUMBR: "KEY_T",        # town portal
    },
    # Neverwinter Nights: also click-to-move, but party-based with its own panel keys.
    "nwn": {
        e.BTN_SOUTH: "BTN_LEFT",      # A  - select / move
        e.BTN_WEST:  "BTN_RIGHT",     # X  - radial menu (how NWN does actions)
        e.BTN_EAST:  "KEY_TAB",       # B  - highlight interactables
        e.BTN_NORTH: "KEY_M",         # Y  - map
        e.BTN_TL:    "KEY_I",         # inventory
        e.BTN_TR:    "KEY_J",         # journal
        e.BTN_SELECT: "KEY_C",        # character sheet
        e.BTN_START: "KEY_ESC",
        e.BTN_THUMBL: "KEY_SPACE",    # pause
        e.BTN_THUMBR: "KEY_L",        # rest
    },
    # Neutral fallback: cursor + clicks + Esc, nothing game-specific.
    "generic": {
        e.BTN_SOUTH: "BTN_LEFT",
        e.BTN_WEST:  "BTN_RIGHT",
        e.BTN_EAST:  "KEY_ENTER",
        e.BTN_NORTH: "KEY_SPACE",
        e.BTN_START: "KEY_ESC",
        e.BTN_SELECT: "KEY_TAB",
    },
}

# Per-profile cursor behaviour.
#
# "pointer" - the stick moves a free-floating cursor, like a mouse. Fine for menus, poor for
#             click-to-move combat: the cursor drifts away from the character and you spend the
#             fight hunting for it.
# "direction" - the stick is a DIRECTION. The cursor is held at a fixed radius from screen
#             centre on the side you are pushing, and springs back to centre when you let go.
#             Because the character is always drawn at screen centre in these games, pushing
#             right and holding A walks right - which is how console ARPG ports handle exactly
#             this problem. Radius is in pixels from centre: far enough to be outside the
#             character, close enough that enemies near you are under the cursor.
CURSOR_MODES = {
    "torchlight2": ("direction", 230),
    "nwn":         ("direction", 260),
    "generic":     ("pointer", 0),
}

# D-pad is an ABS hat on Xbox pads, not buttons, so it is mapped separately.
DPAD = {
    "torchlight2": {"up": "KEY_5", "down": "KEY_6", "left": "KEY_7", "right": "KEY_8"},
    "nwn":         {"up": "KEY_1", "down": "KEY_2", "left": "KEY_3", "right": "KEY_4"},
    "generic":     {"up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT"},
}

# Cursor feel. The stick is a *velocity* control: deflection sets speed, and the loop below
# emits small relative movements at a fixed rate. A deadzone is essential - analogue sticks
# rest a few units off centre and the cursor would otherwise drift forever.
DEADZONE = 6000        # of 32767
# How fast the cursor travels, in pixels per tick (~125 ticks/sec).
#
# 22 was the original and proved far too fast - a small stick nudge threw the cursor across the
# screen. 9 was that reduced ~60%, and still too quick in play. 6.3 is a further 30% off.
# In "direction" mode this governs how quickly the cursor eases out to its parked position and
# back, NOT how far it parks - that is the per-profile radius. Override with --cursor-speed.
CURSOR_MAX_SPEED = 6.3
TICK = 0.008           # ~125 Hz, matches a typical mouse polling rate
PRINT_NODE = False   # set from --print-node; makes stdout machine-readable
CURSOR_MODE_OVERRIDE = ""   # --cursor-mode
CURSOR_RADIUS_OVERRIDE = 0  # --cursor-radius
SCREEN_W, SCREEN_H = 1920, 1080   # --screen WxH; only used by "direction" mode
SCROLL_INTERVAL = 0.12 # seconds between wheel clicks while the right stick is held


def _event_num(path):
    """event number from /dev/input/eventN, or -1 if it does not parse."""
    m = re.search(r"event(\d+)$", path or "")
    return int(m.group(1)) if m else -1

def is_gamepad(dev):
    """A real pad has BTN_SOUTH *and* an analogue stick.

    BTN_SOUTH alone is not enough: virtual keyboards created by other tools (RustDesk's
    uinput device on this machine) advertise a sweeping key range that includes the gamepad
    button codes, and were being picked up as controllers. Requiring ABS_X as well excludes
    anything without a stick, and rejecting devices that also carry KEY_A excludes real
    keyboards outright.
    """
    caps = dev.capabilities()
    keys = caps.get(e.EV_KEY, [])
    axes = [a for a, _ in caps.get(e.EV_ABS, [])]
    if e.BTN_SOUTH not in keys or e.ABS_X not in axes:
        return False
    return e.KEY_A not in keys


def find_pads():
    pads = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if is_gamepad(d):
            pads.append(d)
        else:
            d.close()
    # Stable order so player numbering does not shuffle between runs.
    pads.sort(key=lambda d: int(d.path.rsplit("event", 1)[1]))
    return pads


def make_virtual(index, profile):
    """Create ONE device per player carrying both the keys and the pointer.

    This was originally two devices (a keyboard and a mouse), which is tidier but costs twice
    as many event slots - and slots are the scarce resource here. A sandboxed SDL only scans
    /dev/input/event0..31 (CLAUDE.md 10e), every one of those 32 is already occupied on this
    machine, and the second device promptly landed at event256 where nothing could see it.

    One device advertising EV_KEY (keyboard keys *and* mouse buttons) plus EV_REL is legal
    evdev - plenty of real keyboards with trackpoints look exactly like this - and it halves
    the pressure. PartyDeck classifies it as a Mouse because it carries BTN_LEFT; that only
    affects the label in its device list, and assigning it hands the instance both halves.
    """
    keys = set()
    for target in list(PROFILES[profile].values()) + list(DPAD[profile].values()):
        keys.add(getattr(e, target))
    for extra in ("KEY_LEFTSHIFT", "KEY_LEFTCTRL", "KEY_ENTER", "KEY_ESC",
                  "BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE"):
        keys.add(getattr(e, extra))

    dev = UInput(
        {e.EV_KEY: sorted(keys), e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]},
        name=f"PD Keymap {index}",
        version=1,
    )
    # Both halves are the same device now; returning a pair keeps the call sites unchanged.
    return dev, dev


def scale(value, lo=-32768, hi=32767):
    """Raw stick axis -> -1.0..1.0 with the deadzone removed and the remainder rescaled."""
    if abs(value) < DEADZONE:
        return 0.0
    span = hi - DEADZONE if value > 0 else abs(lo) - DEADZONE
    out = (abs(value) - DEADZONE) / span
    out = min(out, 1.0) ** 1.6      # curve: fine control near centre, fast at the edge
    return out if value > 0 else -out


# Select+Start opens the on-screen keyboard.
#
# gamepad-osk.service watches for this chord so the host can be typed on with no keyboard, but
# it cannot see it while a game runs: this mapper takes an EXCLUSIVE grab on the pad, and the
# per-game profiles map Start and Select to keys of their own. So the chord is handled here and
# the same toggle script is invoked.
#
# Both buttons are emitted on a short DELAY rather than immediately. Firing at once and then
# retracting would flash Esc into the game (which opens its menu) every time the keyboard is
# summoned. Waiting ~150ms lets a chord be recognised before either key is sent; a button
# pressed alone still works, just with that much delay - only on these two buttons.
OSK_CHORD = {e.BTN_SELECT, e.BTN_START}
OSK_TOGGLE = os.environ.get("OSK_TOGGLE", "/usr/local/bin/osk-toggle.sh")
CHORD_WINDOW = 0.15


def _fire_osk(log):
    try:
        subprocess.Popen([OSK_TOGGLE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("[pad-keymap]   Select+Start -> on-screen keyboard")
    except Exception as ex:
        log(f"[pad-keymap]   could not run {OSK_TOGGLE}: {ex}")

def _wait_for_pad(path, index, timeout=None):
    """Re-open a pad that went away, without tearing down the virtual device.

    Tries the original node first, then any other real pad, because a wireless pad often comes
    back on a DIFFERENT event number after the dongle re-enumerates (CLAUDE.md 10e).
    """
    waited = 0.0
    while timeout is None or waited < timeout:
        try:
            d = evdev.InputDevice(path)
            if is_gamepad(d):
                return d
            d.close()
        except OSError:
            pass
        for p in evdev.list_devices():
            try:
                d = evdev.InputDevice(p)
            except OSError:
                continue
            if is_gamepad(d):
                return d
            d.close()
        time.sleep(2.0)
        waited += 2.0
    return None

def run(pad, index, profile, grab):
    mapping = PROFILES[profile]
    dpad = DPAD[profile]
    mode, radius = CURSOR_MODES.get(profile, ("pointer", 0))
    if CURSOR_MODE_OVERRIDE:
        mode = CURSOR_MODE_OVERRIDE
    if CURSOR_RADIUS_OVERRIDE:
        radius = CURSOR_RADIUS_OVERRIDE
    screen_w, screen_h = SCREEN_W, SCREEN_H
    cx0, cy0 = screen_w / 2.0, screen_h / 2.0
    est_x, est_y = cx0, cy0
    log_mode = f"cursor mode: {mode}" + (f", radius {radius}px" if mode == "direction" else "")

    kb, mouse = make_virtual(index, profile)
    node = getattr(kb, "device", None)
    node = node.path if node is not None else "?"

    # CONTRACT WITH PARTYDECK: in --print-node mode the FIRST line of stdout is the bare
    # device path and nothing else, so every human-readable line goes to stderr instead.
    # Getting this wrong is not a cosmetic problem - PartyDeck feeds that first line straight
    # to gamescope's --libinput-hold-dev, so a stray log line there kills the instance before
    # the game starts.
    if PRINT_NODE:
        print(node, flush=True)

    def log(msg):
        print(msg, file=sys.stderr if PRINT_NODE else sys.stdout, flush=True)

    log(f"[pad-keymap] player {index}: {pad.name} ({pad.path}) -> profile '{profile}'")
    warn = "  <-- ABOVE event31, GAME WILL NOT SEE IT" if _event_num(node) > 31 else ""
    log(f"[pad-keymap]   created: PD Keymap {index} at {node}{warn}")
    log(f"[pad-keymap]   {log_mode}")

    if grab:
        try:
            pad.grab()
            log(f"[pad-keymap]   grabbed {pad.path} exclusively")
        except OSError as ex:
            # Not fatal: without a grab the desktop also sees the pad, which is untidy but
            # does not stop the translation working.
            log(f"[pad-keymap]   WARNING could not grab: {ex}")

    lx = ly = rx = ry = 0.0
    last_scroll = 0.0
    hat_x = hat_y = 0
    pending = {}        # chord buttons held but not yet emitted
    emitted = set()     # chord buttons whose key was sent (held down)
    chord_fired = False


    while True:
        # Wait for pad events, but never longer than one tick, so cursor motion stays smooth
        # while the stick is held at a constant deflection (which emits no new events).
        r, _, _ = select.select([pad.fd], [], [], TICK)
        if r:
            try:
                for ev in pad.read():
                    if ev.type == e.EV_KEY and ev.code in OSK_CHORD:
                        # Held together -> on-screen keyboard, and neither key is sent on.
                        if ev.value:
                            pending[ev.code] = time.monotonic()
                            if OSK_CHORD.issubset(pending.keys()):
                                pending.clear()
                                chord_fired = True
                                _fire_osk(log)
                        else:
                            if ev.code in emitted:
                                t = mapping.get(ev.code)
                                if t:
                                    dev = mouse if t.startswith("BTN_") else kb
                                    dev.write(e.EV_KEY, getattr(e, t), 0); dev.syn()
                                emitted.discard(ev.code)
                            elif ev.code in pending and not chord_fired:
                                # Released before the window elapsed and alone: a real tap.
                                t = mapping.get(ev.code)
                                if t:
                                    dev = mouse if t.startswith("BTN_") else kb
                                    dev.write(e.EV_KEY, getattr(e, t), 1); dev.syn()
                                    time.sleep(0.02)
                                    dev.write(e.EV_KEY, getattr(e, t), 0); dev.syn()
                            pending.pop(ev.code, None)
                            if not pending:
                                chord_fired = False
                    elif ev.type == e.EV_KEY and ev.code in mapping:
                        target = mapping[ev.code]
                        code = getattr(e, target)
                        dev = mouse if target.startswith("BTN_") else kb
                        dev.write(e.EV_KEY, code, 1 if ev.value else 0)
                        dev.syn()
                    elif ev.type == e.EV_ABS:
                        if ev.code == e.ABS_X:    lx = scale(ev.value)
                        elif ev.code == e.ABS_Y:  ly = scale(ev.value)
                        elif ev.code == e.ABS_RX: rx = scale(ev.value)
                        elif ev.code == e.ABS_RY: ry = scale(ev.value)
                        elif ev.code == e.ABS_HAT0X:
                            # Release the previous direction before pressing the new one.
                            if hat_x and ev.value != hat_x:
                                kb.write(e.EV_KEY, getattr(e, dpad["right"] if hat_x > 0 else dpad["left"]), 0)
                            if ev.value:
                                kb.write(e.EV_KEY, getattr(e, dpad["right"] if ev.value > 0 else dpad["left"]), 1)
                            hat_x = ev.value
                            kb.syn()
                        elif ev.code == e.ABS_HAT0Y:
                            if hat_y and ev.value != hat_y:
                                kb.write(e.EV_KEY, getattr(e, dpad["down"] if hat_y > 0 else dpad["up"]), 0)
                            if ev.value:
                                kb.write(e.EV_KEY, getattr(e, dpad["down"] if ev.value > 0 else dpad["up"]), 1)
                            hat_y = ev.value
                            kb.syn()
            except OSError as ex:
                if ex.errno == errno.ENODEV:
                    # The pad went away (switched off, dongle re-enumerated, battery died).
                    # DO NOT exit: the uinput device must outlive the pad. PartyDeck hands
                    # gamescope this node at launch, and with pause_between_starts at 30s a
                    # later instance can start well after a blip - if the device vanished,
                    # gamescope dies with "Invalid path /dev/input/eventN" and the whole
                    # launch fails. So keep the virtual device alive and wait for the pad.
                    log(f"[pad-keymap] player {index}: pad gone, keeping {node} alive "
                        f"and waiting for it to return")
                    pad = _wait_for_pad(pad.path, index)
                    if pad is None:
                        return
                    if grab:
                        try:
                            pad.grab()
                        except OSError:
                            pass
                    log(f"[pad-keymap] player {index}: pad back on {pad.path}")
                    continue
                raise

        # A chord button held longer than the window was pressed on its own: send it now.
        if pending and not chord_fired:
            now = time.monotonic()
            for code, t0 in list(pending.items()):
                if now - t0 >= CHORD_WINDOW:
                    target = mapping.get(code)
                    if target:
                        dev = mouse if target.startswith("BTN_") else kb
                        dev.write(e.EV_KEY, getattr(e, target), 1); dev.syn()
                        emitted.add(code)
                    pending.pop(code, None)

        # Cursor motion from the left stick.
        if mode == "direction":
            # Where the cursor SHOULD be: centre plus the stick's deflection scaled to radius.
            # Only relative motion can be emitted, so the cursor position is tracked by dead
            # reckoning from an assumed centred start and clamped to the screen. Small drift is
            # harmless here - the cursor is pulled back to a computed target every tick, so
            # errors are corrected rather than accumulated.
            tx = cx0 + lx * radius
            ty = cy0 + ly * radius
            dx = tx - est_x
            dy = ty - est_y
            step = max(1.0, CURSOR_MAX_SPEED)
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 0.5:
                if dist > step:
                    dx *= step / dist
                    dy *= step / dist
                ix, iy = int(round(dx)), int(round(dy))
                if ix or iy:
                    if ix: mouse.write(e.EV_REL, e.REL_X, ix)
                    if iy: mouse.write(e.EV_REL, e.REL_Y, iy)
                    mouse.syn()
                    est_x = min(max(est_x + ix, 0.0), float(screen_w))
                    est_y = min(max(est_y + iy, 0.0), float(screen_h))
        elif lx or ly:
            dx = int(round(lx * CURSOR_MAX_SPEED))
            dy = int(round(ly * CURSOR_MAX_SPEED))
            if dx: mouse.write(e.EV_REL, e.REL_X, dx)
            if dy: mouse.write(e.EV_REL, e.REL_Y, dy)
            mouse.syn()

        # Right stick scrolls (zoom in these games), rate-limited so one nudge is one click.
        if ry:
            now = time.monotonic()
            if now - last_scroll >= SCROLL_INTERVAL:
                mouse.write(e.EV_REL, e.REL_WHEEL, -1 if ry > 0 else 1)
                mouse.syn()
                last_scroll = now


def main():
    # Declared up front: Python requires `global` BEFORE any use of the name in the scope, and
    # CURSOR_MAX_SPEED is read below as an argparse default.
    global PRINT_NODE, CURSOR_MAX_SPEED, CURSOR_MODE_OVERRIDE, CURSOR_RADIUS_OVERRIDE
    global SCREEN_W, SCREEN_H
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="generic", choices=sorted(PROFILES),
                    help="key mapping to use (default: generic)")
    ap.add_argument("--device", action="append",
                    help="specific pad, e.g. /dev/input/event5 (repeatable; default: all pads)")
    ap.add_argument("--cursor-speed", type=float, default=CURSOR_MAX_SPEED,
                    metavar="N",
                    help=f"pixels per tick at full stick deflection (default {CURSOR_MAX_SPEED}; "
                         f"higher is faster)")
    ap.add_argument("--cursor-mode", choices=["pointer", "direction"], default="",
                    help="override the profile's cursor behaviour (see CURSOR_MODES)")
    ap.add_argument("--cursor-radius", type=int, default=0, metavar="PX",
                    help="direction mode: how far from screen centre the cursor sits")
    ap.add_argument("--screen", default="", metavar="WxH",
                    help="direction mode: the instance's resolution (default 1920x1080)")
    ap.add_argument("--no-grab", action="store_true",
                    help="do not take an exclusive grab on the pad")
    ap.add_argument("--index", type=int, default=0,
                    help="player number to use in the device name (default: positional)")
    ap.add_argument("--print-node", action="store_true",
                    help="print ONLY the created /dev/input/eventN on the first stdout line, "
                         "then keep running. PartyDeck reads that line to learn which device "
                         "to hand the instance.")
    ap.add_argument("--list", action="store_true", help="list pads and profiles, then exit")
    ap.add_argument("--selftest", type=int, metavar="SECONDS", default=0,
                    help="create the virtual devices for N seconds with no pad attached, so "
                         "you can check they enumerate and that PartyDeck lists them")
    args = ap.parse_args()

    if args.selftest:
        kb, mouse = make_virtual(1, args.profile)
        print(f"[pad-keymap] selftest: created 'PD Keymap 1 Keyboard' and "
              f"'PD Keymap 1 Mouse' for {args.selftest}s", flush=True)
        time.sleep(args.selftest)
        kb.close(); mouse.close()
        print("[pad-keymap] selftest: devices removed", flush=True)
        return 0

    if args.list:
        print("profiles:", ", ".join(sorted(PROFILES)))
        pads = find_pads()
        if not pads:
            print("pads: none found (are the controllers switched on?)")
        for i, p in enumerate(pads, 1):
            print(f"  {i}. {p.path}  {p.name}")
        return 0

    PRINT_NODE = args.print_node
    CURSOR_MAX_SPEED = args.cursor_speed
    CURSOR_MODE_OVERRIDE = args.cursor_mode
    CURSOR_RADIUS_OVERRIDE = args.cursor_radius
    if args.screen and "x" in args.screen.lower():
        w, h = args.screen.lower().split("x", 1)
        try:
            SCREEN_W, SCREEN_H = int(w), int(h)
        except ValueError:
            pass

    if args.device:
        pads = []
        for path in args.device:
            try:
                pads.append(evdev.InputDevice(path))
            except OSError as ex:
                print(f"[pad-keymap] cannot open {path}: {ex}", file=sys.stderr)
                return 1
    else:
        pads = find_pads()

    if not pads:
        print("[pad-keymap] no gamepads found - switch the controllers on first",
              file=sys.stderr)
        return 1

    # Single pad: run in THIS process, no fork.
    #
    # PartyDeck starts one mapper per player with --device, then kills that process when the
    # session ends. If we forked, killing the parent would orphan the child - which still owns
    # the uinput device and still holds the pad's exclusive grab, so the next session cannot
    # assign that controller at all. Not forking makes the process it kills the process that
    # owns everything.
    if len(pads) == 1:
        try:
            run(pads[0], args.index or 1, args.profile, not args.no_grab)
        except KeyboardInterrupt:
            pass
        return 0

    # Several pads: one child each, so one pad going away cannot take the others down. The
    # parent must then forward its own termination to the children, for the same reason.
    children = []
    for i, pad in enumerate(pads, 1):
        idx = args.index if args.index else i
        pid = os.fork()
        if pid == 0:
            try:
                run(pad, idx, args.profile, not args.no_grab)
            except KeyboardInterrupt:
                pass
            os._exit(0)
        children.append(pid)

    def _reap(signum, _frame):
        for pid in children:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for pid in children:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _reap)
    signal.signal(signal.SIGINT, _reap)

    try:
        for pid in children:
            os.waitpid(pid, 0)
    except KeyboardInterrupt:
        _reap(signal.SIGINT, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
