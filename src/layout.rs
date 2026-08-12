//! Remembered per-handler instance layout.
//!
//! Players re-run the same game with the same seating: player 1 on the left screen with
//! their profile, player 2 next to them, and so on. Re-picking a profile and a monitor for
//! every slot on every launch is pure repetition, so the choices are saved per handler and
//! replayed the next time that game is set up.
//!
//! DELIBERATELY CONTROLLER-AGNOSTIC. Nothing about the physical pad is recorded - no evdev
//! path, no device name, no vendor/product id. Wireless pads get a different event device
//! almost every session (they are renumbered whenever the dongle re-enumerates), so keying
//! off the device would make the memory miss constantly and, worse, could hand player 1's
//! save profile to whoever happened to power on first. The slot *order* is the identity:
//! the first pad to join is player 1 and gets slot 0's remembered settings, the second gets
//! slot 1, and so on.

use std::fs;
use std::path::PathBuf;

use crate::paths::PATH_PARTY;

/// What is remembered for one player slot.
#[derive(Clone, Debug, Default)]
pub struct SlotLayout {
    /// Profile name, e.g. "Player1". Empty means "no preference".
    pub profname: String,
    /// Monitor index as listed on the Instances page.
    pub monitor: usize,
}

fn layouts_dir() -> PathBuf {
    PATH_PARTY.join("layouts")
}

/// Handler names are free text and end up as a filename, so anything that could escape the
/// directory or upset the filesystem is folded to '_'.
fn sanitize(name: &str) -> String {
    let s: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect();
    if s.is_empty() { "unnamed".to_string() } else { s }
}

fn layout_path(handler_name: &str) -> PathBuf {
    layouts_dir().join(format!("{}.json", sanitize(handler_name)))
}

/// Minimal hand-rolled JSON writer/reader.
///
/// serde_json is already a dependency, but only behind the `download_deps` feature - it is
/// not compiled into a default build. Adding it unconditionally would pull it into every
/// build for two fields, so this format is written and parsed directly. It stays readable
/// and hand-editable, which matters because a wrong monitor index is easiest to fix in a
/// text editor.
fn escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

pub fn save(handler_name: &str, slots: &[SlotLayout]) {
    let dir = layouts_dir();
    if fs::create_dir_all(&dir).is_err() {
        return; // remembering is a convenience; never let it break a launch
    }
    let mut out = String::from("[\n");
    for (i, s) in slots.iter().enumerate() {
        out.push_str(&format!(
            "  {{ \"profile\": \"{}\", \"monitor\": {} }}{}\n",
            escape(&s.profname),
            s.monitor,
            if i + 1 < slots.len() { "," } else { "" }
        ));
    }
    out.push_str("]\n");
    let _ = fs::write(layout_path(handler_name), out);
}

pub fn load(handler_name: &str) -> Vec<SlotLayout> {
    let Ok(text) = fs::read_to_string(layout_path(handler_name)) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    // One record per line, in the shape written above.
    for line in text.lines() {
        let line = line.trim();
        if !line.starts_with('{') {
            continue;
        }
        let profile = between(line, "\"profile\":", ',')
            .map(|v| v.trim().trim_matches('"').replace("\\\"", "\"").replace("\\\\", "\\"))
            .unwrap_or_default();
        let monitor = between(line, "\"monitor\":", '}')
            .and_then(|v| v.trim().parse::<usize>().ok())
            .unwrap_or(0);
        out.push(SlotLayout { profname: profile, monitor });
    }
    out
}

fn between<'a>(line: &'a str, key: &str, end: char) -> Option<&'a str> {
    let start = line.find(key)? + key.len();
    let rest = &line[start..];
    let stop = rest.find(end).unwrap_or(rest.len());
    Some(&rest[..stop])
}
