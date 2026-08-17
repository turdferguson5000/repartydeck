# Tests

These drive the real code paths with virtual controllers created through uinput, so
controller behaviour can be checked without four people and four pads in the room.

Run everything:

```sh
./test-all.sh          # add -v for detail
```

| File | What it covers |
|---|---|
| `test-pad-keymap.py` | Gamepad to keyboard and mouse translation: sticks, buttons, deadzone, cursor speed |
| `test-pad-keymap-edge.py` | The awkward cases. Device isolation, malformed input, d-pad, scroll wheel |
| `test-partydeck-flow.py` | The whole launch pipeline through `--dry-run-launch`, checking the arguments handed to gamescope and bwrap |
| `test-lastmile.py` | Proves input actually arrives in a real gamescope-hosted client, rather than just being emitted |
| `fake-pads.py` | Helper for creating virtual controllers by hand while poking at things |
| `pd-xprobe.py` | The stub client `test-lastmile.py` runs inside gamescope. Counts X events |

Before running any of them, close the app and any game. A leftover mapper keeps its
exclusive grab on the pads and a stale `fuse-overlayfs` mount on `tmp/game-*` blocks
launches, and both produce failures that look like real ones:

```sh
pgrep -af '/pad-keymap\.py'; mount | grep partydeck/tmp
```

One trap worth knowing. Do not put the mapper's path on your command line while running
these. The suites clear orphaned mappers with `pkill -f '/pad-keymap\.py'`, which will
match your own shell if its arguments mention that path, and kill it.
