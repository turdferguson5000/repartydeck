# PartyDeck — Tim's custom build

Local fork. The app titlebar reads **`PartyDeck [tim custom dont update]`** and the version is
**9.9.9** so this build is obvious at a glance and upstream's update check never flags it.

Base: upstream `main` @ `3fd0cb0` — which is **PR #185** (per-instance resolution, KWin
multi-monitor placement, audio sinks), *unmerged upstream*. Multi-monitor placement has never
worked in any tagged PartyDeck release; that PR is the only implementation of it.

Built on a Dell Precision T5810 running Bazzite (KDE Plasma 6 / Wayland), 3 monitors
(HDMI-A-1, DP-2, DP-3) plus a phantom `Unknown-1`.

## Changes on top of that base

### 1. Monitor assignments travel as NAMES, not indices
`src/launch.rs`, `res/splitscreen_kwin*.js`

Three components enumerate monitors and none agree on order:

| | order |
|---|---|
| PartyDeck (`monitor.rs`) | X11/RandR, **primary sorted first** (deliberately mimics SDL) |
| KWin `workspace.screens` | its own order, and it counts outputs PartyDeck never lists |
| gamescope `--display-index` | SDL's order (and ignored entirely on the Wayland backend) |

Passing a bare index across those boundaries silently placed windows on the wrong screen —
picking HDMI put the game on DP-3, with no error anywhere. The generated script now receives
`["HDMI-A-1", "HDMI-A-1", "DP-2", "DP-3"]` and matches on `screen.name`, so ordering stops
mattering.

### 2. Phantom output detection
`src/monitor.rs`, `res/splitscreen_kwin*.js`

The kernel's `simpledrm` driver registers the EFI boot framebuffer as a DRM connector named
`Unknown-N` (or `None-N`), permanently reported CONNECTED because there is no hardware behind
it. Normally the GPU driver evicts simpledrm during boot; here `nvidia_drm.fbdev=0` — required
to stop a boot hang (CLAUDE.md §10.26) — keeps it alive. KDE then periodically enables it and
can make it primary, so windows land on a screen that does not exist.

`is_phantom()` filters those connectors from the monitor list (only when a real monitor
survives, so a machine with nothing else still works), and the KWin script refuses to *fall
back* onto one when a named screen is missing.

### 3. Remembered per-handler layout
`src/layout.rs` (new), `src/app/app.rs`, `src/app/app_pages.rs`

Profile + monitor per player slot, saved to `~/.local/share/partydeck/layouts/<handler>.json`
and replayed next launch.

**Deliberately controller-agnostic** — no evdev path, device name, or vendor/product id is
recorded. Wireless pads are renumbered whenever the dongle re-enumerates, so keying off the
device would miss constantly and could hand player 1's save profile to whoever powered on
first. Slot *order* is the identity: first pad to join is player 1.

Hand-rolled JSON because `serde_json` is only a dependency behind the `download_deps` feature.

### 4. No launch timeout
`src/app/app.rs`

A hardcoded 60 s timer replaced the loading message with "Operation timed out". It never
cancelled anything — the launch continued in the background while the UI claimed failure. With
four instances and `pause_between_starts: 30`, the deliberate staggering alone is 90 s, so it
fired every time. Now polls to completion, showing elapsed time after 20 s.

## Building

```sh
distrobox enter pdbuild -- bash -lc 'cd ~/pd-build && cargo build --release'
cp -f target/release/partydeck            /var/home/user/partydeck/partydeck
cp -f res/splitscreen_kwin*.js            /var/home/user/partydeck/res/
```

Both the binary **and** `res/` must be copied — the KWin scripts carry half the fix.

Stock binary preserved at `/var/home/user/partydeck/partydeck.stock`.

## Gotcha when testing

Orphaned `gamescope-kbm` processes survive a failed launch and keep running the game on the
wrong screens. Check before relaunching, or two sets fight over the same outputs:

```sh
pgrep -af '[g]amescope-kbm'
```

KWin also **caches scripts by name** — `loadScript` returns the cached copy, so edits to a
script under the same name appear to do nothing. Verify with:

```sh
busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting isScriptLoaded s splitscreen
```
