# RePartyDeck

RePartyDeck is a fork of [PartyDeck](https://github.com/wunnr/partydeck), a splitscreen and
local co-op launcher for Linux. It runs several copies of the same game at once, each one in
its own sandbox with its own save data and its own controller, and puts them side by side on
your screens.

This fork exists because I kept running into things that did not work on a real four player
setup, fixing them locally, and ending up a long way from where I started. Rather than sit on
all of it I renamed it and put it out here.

## What this is trying to be

PartyDeck is early and it says so. It is aimed mostly at the Steam Deck and at getting the
basic idea working.

RePartyDeck is aiming at something different. On Windows there is
[Nucleus Co-op](https://hub.splitscreen.me), which has been around for years, has close to 600
community made handlers, and mostly just works when you pick a game off the list. Nothing on
Linux is close to that yet. The goal here is to get to that level: a big library of games that
work without you having to know anything about sandboxes or gamescope, controllers that behave,
and a UI you can drive from the couch.

That is a much bigger target than upstream is going for, which is the main reason this is a
separate project instead of a pile of pull requests.

## What is different from PartyDeck

All of this came out of actually playing on a three monitor, four controller setup, so the list
is weighted towards things that break in that situation.

### Multi monitor and window placement

* Instances are placed by monitor **name**, not by index. Indexes get reordered by the
  compositor, so picking "HDMI-A-1" used to land your window on some other screen.
* Phantom outputs are filtered out. On systems where `simpledrm` sticks around, the kernel
  reports a display called `Unknown-1` that does not physically exist, and it can even end up
  as the primary. Windows sent there just vanish. It is now ignored, which also stops it
  shifting the numbering of the real screens.
* Per instance render resolution, so you can render a game at 1600x900 and still have
  gamescope upscale it to fill a 1080p screen.
* Per instance audio sink, so each player can be sent to a different output.
* Layout memory. Which profile and which monitor each player slot used last time is
  remembered per handler, so a four player setup is one click, not sixteen.

### Controllers

This is where most of the work went.

* **Controllers are detected automatically.** There is no refresh button any more. Turn a pad
  on, plug one in, let a battery die, it all just updates. Your existing player assignments
  survive the change because they are tracked by device path instead of by position in a list.
* **A pad that powers off mid game no longer kills that player.** This one took a while to
  find. Wireless pads switch themselves off when they are idle, and the Linux kernel does not
  just pause them, it destroys the device and builds a new one when you wake it up. Since each
  instance is sandboxed at launch, that new device does not exist as far as the running game is
  concerned, and that player is stuck for the rest of the session. Every pad now goes through a
  small mirror process that owns the real controller and hands the game a stable device that
  never disappears. Pads can sleep and wake as much as they like.
* **One controller cannot drive several instances at once.** `/dev/input` inside each sandbox is
  now a whitelist rather than a blacklist. Before, any device that showed up after launch was in
  nobody's block list, so a reconnecting pad would quietly start controlling everybody's game.
* Pads are matched by which port on the receiver they use, not by name or ID. Four controllers
  on one Xbox wireless receiver report the exact same name, the exact same vendor and product
  ID, the same phys string, and a blank serial. There is genuinely nothing else to tell them
  apart, and getting it wrong means someone is playing on another person's stick.
* **Built in gamepad to keyboard and mouse translation** for games with no controller support at
  all, like Torchlight II and Neverwinter Nights. Both sticks work as a pointer, face buttons
  click, and Select plus Start brings up the on screen keyboard.
* The physical keyboard still works while a pad is mapped. That sounds obvious but it is easy to
  break, and you need it constantly for naming a LAN game, a character, or a save.

### Logging and diagnostics

Debugging four sandboxed games at once with no output is miserable, so:

* Every launch writes a full report to `logs/last-launch.log`: every input device the app can
  see, exactly which ones each instance was allowed and denied, and what was handed to bwrap.
* Input hotplugs are logged for the whole session with timestamps and device identities, so you
  can tell after the fact whether somebody's controller actually dropped.
* Each controller helper writes its own log, so you can see when a pad was lost and when it
  came back.
* A `--dry-run-launch` mode that builds the real launch commands and prints them instead of
  starting anything, which makes the whole pipeline testable without opening a game.
* Test harnesses that create virtual controllers with uinput, so controller behaviour can be
  checked without four people and four pads.

### Other

* Launch timeout removed. Big games on slow drives were getting killed before they finished
  loading.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md). Short version: importing Nucleus handlers from splitscreen.me,
a search tool for finding a game's handler, better artwork, and full controller navigation of
the UI itself.

## Versioning

Version numbers carry on from where PartyDeck is, rather than restarting. Upstream was at
v0.8.3 when this fork split off, so this is v0.9.0. The bump to 0.9 rather than 0.8.4 is
because controller handling changed in ways that are not backwards compatible: every pad now
goes through a mirror device, and the sandbox switched from blocking devices to allowing them.

Config and handlers are compatible in both directions though, so you can go back to upstream
without losing anything.

## Building

```sh
cargo build --release
```

Same dependencies as upstream PartyDeck. You need `bwrap`, `gamescope` and
`fuse-overlayfs` available at runtime.

Config and handlers live in `~/.local/share/partydeck`, deliberately the same place upstream
uses, so you can switch between the two without moving anything.

## Credit

All of the original work is by [wunner](https://github.com/wunnr) and the PartyDeck
contributors. This is their project with changes on top. If you like this, go star the
[original](https://github.com/wunnr/partydeck).

Handler research leans heavily on the [Nucleus Co-op handler hub](https://hub.splitscreen.me),
which is the best documentation that exists for how to make any given game run more than once.

## License

MIT, same as upstream. See [LICENSE](LICENSE). Bundled components have their own licenses,
listed in [COPYING.md](COPYING.md).
