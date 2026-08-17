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

This is the big one, and it is most of the reason the project exists.

Nucleus has somewhere around 600 handlers that people have already worked out and tested. They
are `.nc` files, which are just zip archives holding a `handler.js` plus any emulator DLLs the
game needs. RePartyDeck uses its own `handler.json` format.

The plan:

1. Read `.nc` files directly and pull out the fields that map cleanly. `Game.ExecutableName`,
   `Game.MaxPlayers`, `Game.PauseBetweenStarts` and whether an emulator is bundled all
   translate more or less directly.
2. Report honestly on the parts that do not translate. Nucleus handlers are JavaScript and can
   do arbitrary work, so a chunk of them will never convert automatically. Better to import
   what works and say clearly what needs a human than to produce something that silently fails
   halfway through a game night.
3. Keep the imported handler editable afterwards, since the last 10 percent is usually one
   wrong executable path.

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
