# Roadmap

The target is simple to say and hard to do: get Linux local co-op to roughly where
[Nucleus Co-op](https://hub.splitscreen.me) is on Windows. Pick a game from a list, it works,
you do not need to understand anything underneath.

Nothing here has a date on it. This is a spare time project.

## v0.9.0 (now)

Everything in the "What is different" section of the README. Mostly controller reliability,
multi monitor placement, and enough logging to debug four sandboxed games at once.

## Next up

### Nucleus handler compatibility

Done in v0.9.1, at least the import half. There is a button in the game list that reads a
`.nc` file and turns it into a PartyDeck handler.

Measured against all 594 handlers published on hub.splitscreen.me:

| | |
|---|---|
| Imported without error | 594 of 594 |
| Got a working executable path | 593 (99%) |
| Got a Steam appid | 562 (94%) |
| Got a pause between starts | 586 (98%) |
| Scripted sections dropped | 635 |
| Bundled DLLs dropped | 396 |

The last two rows are the honest part. 96% of handlers also define JavaScript functions,
usually `Game.Play`, and Nucleus runs those in a JS engine on Windows while hooking the
running game. PartyDeck does neither, so none of that comes across. What you get is the
tedious, error prone part filled in correctly: which executable actually starts the game,
the appid, the launch arguments, and how long to wait between instances. Every import
writes what it dropped into the handler's info text, so it is visible in the app rather
than something you find out when the game misbehaves.

Multi-select import landed too, so a whole library is one dialog rather than 600, and
handlers with a Steam appid get the game's header image pulled from Steam for cover art.
Bundled art was checked first and turned out to be a dead end: only 11 of the 594 contain
images at all, and those are game assets rather than artwork.

Still to do here: pull the emulator DLLs across where there is a Linux equivalent, and
work out which of the scripted sections are simple enough to translate rather than drop.

### Handler search

A built in browser for the splitscreen.me hub. Search by game name, see how many players a
handler supports, whether it is verified, what the comments say, and install it without
leaving the app. Right now finding a handler means opening a browser and reading a forum
thread, and the hub is a single page app so it does not even respond to a normal HTTP fetch.

### Full controller navigation

You should be able to sit on a sofa and set up a four player game without touching a keyboard
or mouse. The pieces are partly there already, since there is gamepad input in the menus, but
it is not complete and some screens can only be driven with a pointer. Needs a proper focus
model, on screen keyboard wherever text is entered, and the ability to back out of anything.

### Artwork and general look

The UI is functional and pretty plain. Wants proper game art, a cleaner layout, readable text
at TV distance, and icons that make sense at a glance.

## Later

* **Rumble through the controller mirror.** The mirror forwards input but not force feedback,
  because that needs upload and erase events handled and passed back to the real pad. It is
  doable, just not done.
* **Automatic gamescope crash recovery.** There is an intermittent gamescope crash involving
  explicit sync that can take down one instance out of four. Working around it per game is what
  happens now. Detecting it and relaunching that instance would be better.
* **Better handling of games that need one Steam account each.** Some titles validate their
  auth server side, which no amount of local emulation fixes. The app should say so up front
  rather than letting you find out when the lobby refuses to connect.
* **Per player input remapping in the UI.** The gamepad to keyboard translation currently uses
  per game profiles defined in a script. It should be editable in the app.
* **Documentation for writing handlers**, so people other than me can add games.

## Not planned

* Windows support. Nucleus already exists and is good.
* Anything that tries to defeat anti cheat. Games that block virtualisation or multiple
  instances are simply out of scope.
