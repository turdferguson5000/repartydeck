mod app;
mod handler;
mod input;
mod instance;
mod launch;
mod layout;
mod monitor;
mod paths;
mod profiles;
mod util;

use crate::app::*;
use crate::handler::Handler;
use crate::monitor::{get_monitors_errorless, get_x11_dpi_scale};
use crate::paths::PATH_PARTY;
use crate::profiles::remove_guest_profiles;
use crate::util::*;

fn main() -> eframe::Result {
    if std::env::args().any(|arg| arg == "--help") {
        println!("{}", USAGE_TEXT);
        std::process::exit(0);
    }
    
    let monitors = get_monitors_errorless();

    println!("[partydeck] Monitors detected:");
    for monitor in &monitors {
        println!(
            "[partydeck] {} ({}x{})",
            monitor.name(),
            monitor.width(),
            monitor.height()
        );
    }

    let args: Vec<String> = std::env::args().collect();
    // --dry-run-launch <handler-dir> <pad1[,pad2...]>
    //
    // A testable seam for the whole launch pipeline. It builds the REAL commands - handler
    // parsing, pad-keymap spawning, gamescope arguments, bwrap device masking - and prints
    // them as JSON instead of starting any games. Everything downstream of this point is
    // rendering; everything upstream is logic that used to be verifiable only by launching a
    // game and watching it fail.
    if let Some(pos) = std::env::args().position(|a| a == "--dry-run-launch") {
        let argv: Vec<String> = std::env::args().collect();
        let handler_dir = argv.get(pos + 1).cloned().unwrap_or_default();
        let pad_list = argv.get(pos + 2).cloned().unwrap_or_default();

        let hpath = paths::PATH_PARTY.join("handlers").join(&handler_dir).join("handler.json");
        let mut h = match handler::Handler::from_json(&hpath) {
            Ok(h) => h,
            Err(e) => { eprintln!("dry-run: cannot load {}: {e}", hpath.display()); std::process::exit(2); }
        };
        h.path_handler = paths::PATH_PARTY.join("handlers").join(&handler_dir);

        let scanned = input::scan_input_devices(&app::PadFilterType::All);
        let devices: Vec<input::DeviceInfo> = scanned.iter().map(|d| d.info()).collect();
        let wanted: Vec<&str> = pad_list.split(',').filter(|s| !s.is_empty()).collect();
        let mut instances: Vec<instance::Instance> = Vec::new();
        for (i, want) in wanted.iter().enumerate() {
            let Some(idx) = devices.iter().position(|d| d.path == *want) else {
                eprintln!("dry-run: no such device {want}");
                std::process::exit(2);
            };
            instances.push(instance::Instance {
                devices: vec![idx],
                profname: format!("Player{}", i + 1),
                profselection: 0,
                monitor: 0,
                audio_sink: String::new(),
                width: 1920,
                height: 1080,
                res_override: None,
            });
        }

        let cfg = app::load_cfg();

        // The real flow creates each profile's gamesave dir before mounting - that dir is the
        // overlay's upperdir, and fuse-overlayfs fails outright without it.
        if let Err(e) = launch::setup_profiles(&h, &instances) {
            eprintln!("dry-run: setup_profiles failed: {e}");
            launch::stop_pad_keymaps();
            std::process::exit(3);
        }

        // The real flow mounts the per-instance overlays before building commands, and
        // launch_cmds validates the executable inside that merged view. Mount them here too,
        // or the dry run tests a path the real launch never takes.
        if let Err(e) = launch::fuse_overlayfs_mount_gamedirs(&h, &instances) {
            eprintln!("dry-run: overlay mount failed: {e}");
            launch::stop_pad_keymaps();
            std::process::exit(3);
        }

        match launch::launch_cmds(&h, &devices, &instances, &cfg) {
            Ok(cmds) => {
                println!("[");
                for (n, c) in cmds.iter().enumerate() {
                    let mut parts: Vec<String> = vec![c.get_program().to_string_lossy().to_string()];
                    for a in c.get_args() {
                        parts.push(a.to_string_lossy().to_string());
                    }
                    let esc: Vec<String> = parts
                        .iter()
                        .map(|p| format!("\"{}\"", p.replace('\\', "\\\\").replace('"', "\\\"")))
                        .collect();
                    println!("  [{}]{}", esc.join(", "), if n + 1 < cmds.len() { "," } else { "" });
                }
                println!("]");
            }
            Err(e) => {
                eprintln!("dry-run: launch_cmds failed: {e}");
                // Release the pads on THIS path too. Missing that leaves mappers running,
                // and because they inherit stdout the caller's pipe never sees EOF - a test
                // harness reading our output just hangs until its timeout.
                launch::stop_pad_keymaps();
                let _ = util::fuse_overlayfs_unmount_gamedirs();
                std::process::exit(1);
            }
        }
        // Release the pads: the mappers were really started, and a dry run must not leave
        // them holding controllers.
        launch::stop_pad_keymaps();
        let _ = util::fuse_overlayfs_unmount_gamedirs();
        std::process::exit(0);
    }


    if std::env::args().any(|arg| arg == "--kwin") {
        let args: Vec<String> = std::env::args().filter(|arg| arg != "--kwin").collect();

        let (w, h) = (monitors[0].width(), monitors[0].height());
        let mut cmd = std::process::Command::new("kwin_wayland");

        cmd.arg("--xwayland");
        cmd.arg("--width");
        cmd.arg(w.to_string());
        cmd.arg("--height");
        cmd.arg(h.to_string());
        cmd.arg("--exit-with-session");
        cmd.env("PARTYDECK_SCREEN_WIDTH", w.to_string());
        cmd.env("PARTYDECK_SCREEN_HEIGHT", h.to_string());
        let args_string = args
            .iter()
            .map(|arg| format!("\"{}\"", arg))
            .collect::<Vec<String>>()
            .join(" ");
        cmd.arg(args_string);

        println!("[partydeck] Launching kwin session: {:?}", cmd);

        match cmd.spawn() {
            Ok(_) => std::process::exit(0),
            Err(e) => {
                eprintln!("[partydeck] Failed to start kwin_wayland: {}", e);
                std::process::exit(1);
            }
        }
    }

    let mut exec = String::new();
    let mut execargs = String::new();
    if let Some(exec_index) = args.iter().position(|arg| arg == "--exec") {
        if let Some(next_arg) = args.get(exec_index + 1) {
            exec = next_arg.clone();
        } else {
            eprintln!("{}", USAGE_TEXT);
            std::process::exit(1);
        }
    }
    if let Some(execargs_index) = args.iter().position(|arg| arg == "--args") {
        if let Some(next_arg) = args.get(execargs_index + 1) {
            execargs = next_arg.clone();
        } else {
            eprintln!("{}", USAGE_TEXT);
            std::process::exit(1);
        }
    }

    let handler_lite = if !exec.is_empty() {
        Some(Handler::from_cli(&exec, &execargs))
    } else {
        None
    };

    let fullscreen = std::env::args().any(|arg| arg == "--fullscreen");

    std::fs::create_dir_all(PATH_PARTY.join("handlers"))
        .expect("Failed to create handlers directory");
    std::fs::create_dir_all(PATH_PARTY.join("profiles"))
        .expect("Failed to create profiles directory");
    if !PATH_PARTY.join("goldberg_data").exists() {
        std::fs::create_dir_all(PATH_PARTY.join("goldberg_data/steam_settings"))
            .expect("Failed to create goldberg data!");
        std::fs::write(PATH_PARTY.join("goldberg_data/steam_settings/auto_accept_invite.txt"), "").expect("failed to create auto_accept_invite.txt");
        std::fs::write(PATH_PARTY.join("goldberg_data/steam_settings/auto_send_invite.txt"), "").expect("failed to create auto_send_invite.txt");
    }

    remove_guest_profiles().unwrap();
    clear_tmp().unwrap();

    let scrheight = monitors[0].height();

    let scale = match fullscreen {
        true => scrheight as f32 / 560.0 / get_x11_dpi_scale(),
        false => 1.3,
    };

    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_inner_size([1080.0, 540.0])
            .with_min_inner_size([640.0, 360.0])
            .with_fullscreen(fullscreen)
            .with_icon(
                eframe::icon_data::from_png_bytes(&include_bytes!("../res/icon.png")[..])
                    .expect("Failed to load icon"),
            ),
        ..Default::default()
    };

    println!("[partydeck] Starting eframe app...");

    // Local build carrying fixes that are not in any upstream release (name-based monitor
    // assignment, phantom-output filtering, remembered per-handler layouts, no launch
    // timeout). The title says so, and the version is deliberately absurd, because an
    // upstream "update" would silently replace all of it.
    eframe::run_native(
        "PartyDeck [tim custom dont update]",
        options,
        Box::new(|cc| {
            // This gives us image support:
            egui_extras::install_image_loaders(&cc.egui_ctx);
            cc.egui_ctx.set_zoom_factor(scale);
            Ok(Box::<PartyApp>::new(PartyApp::new(
                monitors.clone(),
                handler_lite,
            )))
        }),
    )
}

static USAGE_TEXT: &str = r#"
Usage: partydeck [OPTIONS]

Options:
    --exec <executable>   Execute the specified executable in splitscreen. If this isn't specified, PartyDeck will launch in the regular GUI mode.
    --args [args]         Specify arguments for the executable to be launched with. Must be quoted if containing spaces.
    --fullscreen          Start the GUI in fullscreen mode
    --kwin                Launch PartyDeck inside of a KWin session
"#;
