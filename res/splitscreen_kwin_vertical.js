// Loaded by PartyDeck. Two tokens are replaced at launch:
//   PARTYDECK_ASSIGNMENTS      -> array mapping instance index -> screen NAME,
//                                 e.g. ["HDMI-A-1", "DP-3"].
//   PARTYDECK_USE_ASSIGNMENTS  -> true  : place each instance on its assigned
//                                         screen (multi-monitor);
//                                 false : classic splitscreen — ignore
//                                         assignments and split whatever screen
//                                         KWin opened each window on.
// Instances sharing a screen are split (vertical: side by side).
const assignments = PARTYDECK_ASSIGNMENTS;
const useAssignments = PARTYDECK_USE_ASSIGNMENTS;

const x = [[], [0], [0, 0.5], [0, 0, 0.5], [0, 0.5, 0, 0.5]];
const y = [[], [0], [0, 0], [0, 0.5, 0.5], [0, 0, 0.5, 0.5]];
const width = [[], [1], [0.5, 0.5], [1, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5]];
const height = [[], [1], [1, 1], [0.5, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5]];

function gamescopeClients() {
  const all = workspace.windowList();
  const out = [];
  for (let i = 0; i < all.length; i++) {
    const rc = all[i].resourceClass;
    if (rc == "gamescope" || rc == "gamescope-kbm") out.push(all[i]);
  }
  return out;
}

// A phantom output (simpledrm's "Unknown-N"/"None-N" EFI framebuffer connector) is a
// screen KWin believes in but no display is attached to. PartyDeck filters it from its own
// monitor list, but KWin still reports it here, so never fall back onto one.
function isPhantom(name) {
  const n = (name || "").toLowerCase();
  return n.indexOf("unknown") === 0 || n.indexOf("none-") === 0;
}

// The screen a given client should live on, resolved by NAME. Indices are unusable as an
// interchange: PartyDeck, KWin and SDL each enumerate monitors in a different order, and
// KWin's list additionally includes phantom outputs. Multi-monitor mode matches the baked-in
// per-instance monitor name; classic mode uses whatever screen KWin already chose.
function screenIndexFor(clients, i, screens) {
  if (useAssignments) {
    const want = i < assignments.length ? assignments[i] : "";
    for (let s = 0; s < screens.length; s++) {
      if (screens[s] && screens[s].name === want) return s;
    }
    // Named screen is gone (unplugged, or renamed since launch): use the first real one
    // rather than index 0, which could be the phantom.
    for (let s = 0; s < screens.length; s++) {
      if (screens[s] && !isPhantom(screens[s].name)) return s;
    }
    return 0;
  }
  const out = clients[i].output;
  for (let s = 0; s < screens.length; s++) {
    if (screens[s] === out) return s;
  }
  return 0;
}

// Re-place every gamescope window on each event (robust to windows whose class
// isn't set the instant they're added). Window i (in list order) == instance i.
function layout() {
  const clients = gamescopeClients();
  const screens = workspace.screens;

  const total = {};
  for (let i = 0; i < clients.length; i++) {
    const s = screenIndexFor(clients, i, screens);
    total[s] = (total[s] || 0) + 1;
  }

  const slot = {};
  for (let i = 0; i < clients.length; i++) {
    const s = screenIndexFor(clients, i, screens);
    if (slot[s] === undefined) slot[s] = 0;
    const idx = slot[s];
    slot[s] += 1;
    const c = total[s];
    const screen = s >= 0 && s < screens.length ? screens[s] : screens[0];
    if (!screen) continue;
    const g = screen.geometry;
    const w = clients[i];
    w.noBorder = true;
    w.keepAbove = true;
    w.frameGeometry = {
      x: g.x + x[c][idx] * g.width,
      y: g.y + y[c][idx] * g.height,
      width: g.width * width[c][idx],
      height: g.height * height[c][idx],
    };
  }
}

workspace.windowAdded.connect(layout);
workspace.windowRemoved.connect(layout);
