//! Import Nucleus Co-op handlers (`.nc`) and turn them into PartyDeck handlers.
//!
//! Nucleus Co-op has had a community writing handlers for years, and there are around 600 of
//! them on <https://hub.splitscreen.me>. That is the single biggest reason it feels finished on
//! Windows and Linux does not. Getting at that work is worth a lot, even partially.
//!
//! A `.nc` file is a zip holding `handler.js` plus whatever the game needs (emulator DLLs,
//! config, cover art). `handler.js` is real JavaScript, but the parts worth having are almost
//! all plain assignments at the top level:
//!
//! ```js
//! Game.ExecutableName = "stormgame-win64-shipping.exe";
//! Game.BinariesFolder = "Binaries\\Win64";
//! Game.MaxPlayers = 4;
//! Game.UseGoldberg = true;
//! ```
//!
//! WHAT THIS DOES NOT DO, and why that is stated up front rather than discovered later.
//!
//! Measured across the 594 handlers published on the hub, **96% also define at least one
//! JavaScript function**, usually `Game.Play`, and 67% ship a DLL. None of that is executed or
//! converted here. Nucleus runs handlers in a JS engine on Windows and hooks the running game;
//! PartyDeck does neither. So an import is a well-informed starting point, not a finished
//! handler, and anything requiring the scripted half will need a person.
//!
//! What is reliably recovered is the part that is tedious and easy to get wrong by hand: which
//! executable actually starts the game (often not the one you would guess), how many players it
//! supports, the Steam appid, the launch arguments, and how long to wait between instances.
//! Every import carries a report of exactly what was dropped, which goes into the handler's
//! info text so it is visible in the app rather than buried in a log.

use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;

use crate::handler::Handler;

/// A value read out of `handler.js`.
#[derive(Debug, Clone, PartialEq)]
pub enum JsValue {
    Str(String),
    Num(f64),
    Bool(bool),
    List(Vec<String>),
    /// A function body. Never converted, only counted, so the report can say so.
    Function,
}

impl JsValue {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            JsValue::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            JsValue::Num(n) => Some(*n),
            JsValue::Str(s) => s.trim().parse().ok(),
            _ => None,
        }
    }
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            JsValue::Bool(b) => Some(*b),
            _ => None,
        }
    }
}

/// What an import could not bring across.
#[derive(Debug, Default)]
pub struct ImportReport {
    pub functions: Vec<String>,
    pub bundled_dlls: Vec<String>,
    pub other_files: Vec<String>,
    pub warnings: Vec<String>,
}

/// Strip `//` and `/* */` comments without mangling string literals.
///
/// Handlers are full of Windows paths, so a naive strip on `//` eats the middle of
/// `"C:\\a//b"` and a URL in a comment ends a line early. Tracking quotes avoids both.
fn strip_comments(src: &str) -> String {
    let mut out = String::with_capacity(src.len());
    let b: Vec<char> = src.chars().collect();
    let (mut i, n) = (0usize, b.len());
    let (mut in_str, mut quote, mut escaped) = (false, '"', false);
    while i < n {
        let c = b[i];
        if in_str {
            out.push(c);
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == quote {
                in_str = false;
            }
            i += 1;
            continue;
        }
        if c == '"' || c == '\'' {
            in_str = true;
            quote = c;
            out.push(c);
            i += 1;
            continue;
        }
        if c == '/' && i + 1 < n && b[i + 1] == '/' {
            while i < n && b[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c == '/' && i + 1 < n && b[i + 1] == '*' {
            i += 2;
            while i + 1 < n && !(b[i] == '*' && b[i + 1] == '/') {
                i += 1;
            }
            i = (i + 2).min(n);
            continue;
        }
        out.push(c);
        i += 1;
    }
    out
}

fn unescape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('r') => out.push('\r'),
            Some(other) => out.push(other), // covers \\ and \" and Windows paths
            None => {}
        }
    }
    out
}

/// Read the top-level `Game.*` / `Hub.*` assignments out of a handler script.
///
/// Only depth zero is accepted. Function bodies contain assignments of their own, and taking
/// those would mean a value set conditionally deep inside `Game.Play` could overwrite the real
/// declaration and silently produce a wrong handler.
pub fn parse_handler_js(src: &str) -> BTreeMap<String, JsValue> {
    let src = strip_comments(src);
    let mut out = BTreeMap::new();
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let (mut i, mut depth) = (0usize, 0i32);
    let (mut in_str, mut quote, mut escaped) = (false, '"', false);
    let mut stmt_start = 0usize;

    while i < n {
        let c = chars[i];
        if in_str {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == quote {
                in_str = false;
            }
            i += 1;
            continue;
        }
        match c {
            '"' | '\'' => {
                in_str = true;
                quote = c;
            }
            '{' | '(' | '[' if depth > 0 => depth += 1,
            '{' | '(' => depth += 1,
            '}' | ')' | ']' => depth -= 1,
            ';' | '\n' if depth <= 0 => {
                let stmt: String = chars[stmt_start..i].iter().collect();
                if let Some((k, v)) = parse_assignment(&stmt) {
                    out.insert(k, v);
                }
                stmt_start = i + 1;
            }
            _ => {}
        }
        // A top-level `[` opens an array literal we want to read whole, so it is not counted
        // as depth here; parse_assignment handles it from the statement text.
        if c == '[' && depth == 0 {
            // skip to the matching ']' so a ';' or newline inside the array does not split it
            let mut d = 1;
            let mut j = i + 1;
            let (mut s2, mut q2, mut e2) = (false, '"', false);
            while j < n && d > 0 {
                let cj = chars[j];
                if s2 {
                    if e2 {
                        e2 = false;
                    } else if cj == '\\' {
                        e2 = true;
                    } else if cj == q2 {
                        s2 = false;
                    }
                } else if cj == '"' || cj == '\'' {
                    s2 = true;
                    q2 = cj;
                } else if cj == '[' {
                    d += 1;
                } else if cj == ']' {
                    d -= 1;
                }
                j += 1;
            }
            i = j;
            continue;
        }
        i += 1;
    }
    let stmt: String = chars[stmt_start..].iter().collect();
    if let Some((k, v)) = parse_assignment(&stmt) {
        out.insert(k, v);
    }
    out
}

fn parse_assignment(stmt: &str) -> Option<(String, JsValue)> {
    let stmt = stmt.trim();
    if stmt.is_empty() {
        return None;
    }
    let eq = stmt.find('=')?;
    let key = stmt[..eq].trim();
    if !(key.starts_with("Game.") || key.starts_with("Hub.")) {
        return None;
    }
    if !key
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_')
    {
        return None;
    }
    let val = stmt[eq + 1..].trim().trim_end_matches(';').trim();
    if val.is_empty() {
        return None;
    }
    if val.starts_with("function") {
        return Some((key.to_string(), JsValue::Function));
    }
    if val == "true" || val == "false" {
        return Some((key.to_string(), JsValue::Bool(val == "true")));
    }
    if val.starts_with('[') {
        let inner = val.trim_start_matches('[').trim_end_matches(']');
        let items: Vec<String> = inner
            .split(',')
            .map(|s| unescape(s.trim().trim_matches(|c| c == '"' || c == '\'')))
            .filter(|s| !s.is_empty())
            .collect();
        return Some((key.to_string(), JsValue::List(items)));
    }
    if (val.starts_with('"') && val.ends_with('"') && val.len() >= 2)
        || (val.starts_with('\'') && val.ends_with('\'') && val.len() >= 2)
    {
        return Some((
            key.to_string(),
            JsValue::Str(unescape(&val[1..val.len() - 1])),
        ));
    }
    if let Ok(num) = val.parse::<f64>() {
        return Some((key.to_string(), JsValue::Num(num)));
    }
    None
}

/// Windows path separators, and a leading `.\` that some handlers use.
fn to_unix_path(s: &str) -> String {
    s.replace('\\', "/")
        .trim_start_matches("./")
        .trim_matches('/')
        .to_string()
}

/// Convert a parsed handler into a PartyDeck one.
pub fn to_handler(fields: &BTreeMap<String, JsValue>, report: &mut ImportReport) -> Handler {
    let get = |k: &str| fields.get(k);
    let s = |k: &str| get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();

    let mut h = Handler::default();

    h.name = match s("Game.GameName").is_empty() {
        false => s("Game.GameName"),
        true => s("Game.GUID"),
    };
    h.author = match get("Hub.Maintainer.Name").and_then(|v| v.as_str()) {
        Some(a) if !a.is_empty() => format!("{a} (via Nucleus)"),
        _ => "Nucleus Co-op".to_string(),
    };
    h.version = "1.0".to_string();
    h.spec_ver = 3;

    // The executable is the field most worth having and the easiest to get wrong by hand: it
    // frequently sits under a Binaries folder and is often not the launcher you see in the
    // game root.
    let exe = s("Game.ExecutableName");
    let bin = to_unix_path(&s("Game.BinariesFolder"));
    h.exec = match (bin.is_empty(), exe.is_empty()) {
        (_, true) => String::new(),
        (true, false) => to_unix_path(&exe),
        (false, false) => format!("{bin}/{}", to_unix_path(&exe)),
    };
    if h.exec.is_empty() {
        report
            .warnings
            .push("No Game.ExecutableName; you will have to set the executable yourself".into());
    }

    h.runtime = match h.exec.to_ascii_lowercase().ends_with(".exe") {
        true => "proton".to_string(),
        false => String::new(),
    };
    h.args = s("Game.StartArguments");
    h.pause_between_starts = get("Game.PauseBetweenStarts").and_then(|v| v.as_f64());

    // Nucleus expresses "this game needs the Steam API faked" two ways depending on the
    // handler's age. Either is enough.
    h.use_goldberg = get("Game.UseGoldberg").and_then(|v| v.as_bool()).unwrap_or(false)
        || get("Game.NeedsSteamEmulation")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

    h.steam_appid = get("Game.SteamID").and_then(|v| v.as_f64()).map(|f| f as u32);

    h
}

/// Build the info text shown in the app for an imported handler.
pub fn info_text(fields: &BTreeMap<String, JsValue>, report: &ImportReport) -> String {
    let mut out = String::new();
    if let Some(d) = fields.get("Game.Description").and_then(|v| v.as_str()) {
        if !d.trim().is_empty() {
            out.push_str(d.trim());
            out.push_str("\n\n");
        }
    }
    out.push_str("IMPORTED FROM A NUCLEUS CO-OP HANDLER. Treat it as a starting point.\n");

    if let Some(mp) = fields.get("Game.MaxPlayers").and_then(|v| v.as_f64()) {
        out.push_str(&format!("Supports up to {} players.\n", mp as i64));
    }
    if let Some(m) = fields.get("Hub.Maintainer.Name").and_then(|v| v.as_str()) {
        out.push_str(&format!("Original handler by {m}, from hub.splitscreen.me.\n"));
    }

    if !report.functions.is_empty() {
        out.push_str(&format!(
            "\nNOT CONVERTED: {} scripted section(s) ({}). Nucleus runs these in a JS engine on \
             Windows and hooks the running game; PartyDeck does not, so whatever they did is not \
             happening here. If the game misbehaves in a way the settings cannot explain, this is \
             the first place to look.\n",
            report.functions.len(),
            report.functions.join(", ")
        ));
    }
    if !report.bundled_dlls.is_empty() {
        out.push_str(&format!(
            "\nNOT INSTALLED: {} bundled DLL(s) ({}). These are usually an emulator or an input \
             shim the game needs. Goldberg covers the Steam side; anything else has to be put in \
             place by hand.\n",
            report.bundled_dlls.len(),
            report
                .bundled_dlls
                .iter()
                .take(6)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    for w in &report.warnings {
        out.push_str(&format!("\nWARNING: {w}\n"));
    }
    out.push_str("\nYou still need to point path_gameroot at your copy of the game.");
    out
}

/// Pick a `.nc` file, convert it, and install it as a handler.
///
/// Returns a human-readable summary of what came across and what did not, which the caller
/// shows in a dialog. The dropped parts are the whole point of the summary: an import that
/// quietly looks complete is worse than one that tells you it is half a handler.
pub fn import_nc_dialog() -> Result<Option<String>, Box<dyn std::error::Error>> {
    let Some(file) = rfd::FileDialog::new()
        .set_title("Select a Nucleus Co-op handler")
        .set_directory(&*crate::paths::PATH_HOME)
        .add_filter("Nucleus Co-op Handler", &["nc"])
        .pick_file()
    else {
        return Ok(None);
    };

    let (mut h, report) = import_nc(&file)?;
    if h.name.trim().is_empty() {
        return Err("Handler has no game name".into());
    }

    // Directory name has to survive being a path: game names carry ':' and '/' freely.
    let safe: String = h
        .name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
        .collect();
    let dir = crate::paths::PATH_PARTY.join("handlers").join(safe.trim_matches('_'));
    if dir.exists() {
        return Err(format!("A handler already exists at {}", dir.display()).into());
    }
    std::fs::create_dir_all(&dir)?;
    h.path_handler = dir.clone();
    h.save_to_json()?;

    Ok(Some(format!(
        "Imported \"{}\".\n\nExecutable: {}\nSteam appid: {}\nGoldberg: {}\n\n\
         Not converted: {} scripted section(s), {} bundled DLL(s).\n\n\
         Set the game directory before launching, and read the handler's info text for what \
         was dropped.",
        h.name,
        match h.exec.is_empty() {
            true => "NOT FOUND - set this yourself".to_string(),
            false => h.exec.clone(),
        },
        h.steam_appid.map(|a| a.to_string()).unwrap_or("none".into()),
        h.use_goldberg,
        report.functions.len(),
        report.bundled_dlls.len(),
    )))
}

/// Read a `.nc` archive and produce a handler plus a report of what was lost.
pub fn import_nc(path: &Path) -> Result<(Handler, ImportReport), Box<dyn std::error::Error>> {
    let file = std::fs::File::open(path)?;
    let mut zip = zip::ZipArchive::new(file)?;

    let mut js_src = String::new();
    let mut report = ImportReport::default();

    for i in 0..zip.len() {
        let mut entry = zip.by_index(i)?;
        let name = entry.name().to_string();
        if name.ends_with('/') {
            continue;
        }
        let lower = name.to_ascii_lowercase();
        if lower.ends_with("handler.js") && js_src.is_empty() {
            entry.read_to_string(&mut js_src)?;
        } else if lower.ends_with(".dll") {
            report.bundled_dlls.push(name);
        } else {
            report.other_files.push(name);
        }
    }

    if js_src.is_empty() {
        return Err("No handler.js inside this .nc file".into());
    }

    let fields = parse_handler_js(&js_src);
    report.functions = fields
        .iter()
        .filter(|(_, v)| **v == JsValue::Function)
        .map(|(k, _)| k.clone())
        .collect();

    let mut h = to_handler(&fields, &mut report);
    h.info = info_text(&fields, &report);
    Ok((h, report))
}
