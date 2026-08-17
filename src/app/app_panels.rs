use super::app::{MenuPage, PartyApp};
use crate::Handler;
use crate::handler::import_pd2;
use crate::handler::scan_handlers;
use crate::input::*;
use crate::monitor::get_monitors_errorless;
use crate::profiles::scan_profiles;
use crate::util::*;

use eframe::egui::Popup;
use eframe::egui::RichText;
use eframe::egui::{self, Ui};

macro_rules! cur_handler {
    ($self:expr) => {
        &$self.handlers[$self.selected_handler]
    };
}

impl PartyApp {
    pub fn display_panel_top(&mut self, ui: &mut Ui) {
        ui.horizontal(|ui| {
            let hometext = match self.is_lite() {
                true => "▶",
                false => "ℹ",
            };
            let homepage = match self.is_lite() {
                true => MenuPage::Instances,
                false => MenuPage::Home,
            };

            let homebtn = ui.add(
                egui::Button::image_and_text(
                    egui::include_image!("../../res/BTN_EAST.png"),
                    hometext,
                )
                .selected(self.cur_page == MenuPage::Home),
            );

            if homebtn.clicked() {
                self.cur_page = homepage;
            }

            let settingsbtn = ui.add(
                egui::Button::image_and_text(egui::include_image!("../../res/BTN_NORTH.png"), "⛭")
                    .selected(self.cur_page == MenuPage::Settings),
            );
            if settingsbtn.clicked() {
                self.cur_page = MenuPage::Settings;
            }

            let profilesbtn = ui.add(
                egui::Button::image_and_text(egui::include_image!("../../res/BTN_WEST.png"), "👥")
                    .selected(self.cur_page == MenuPage::Profiles),
            );
            if profilesbtn.clicked() {
                self.profiles = scan_profiles(false);
                self.cur_page = MenuPage::Profiles;
            }

            // No controller refresh button: the device list keeps itself current (see
            // PartyApp::poll_input_devices). This is a read-out, not a control. It stays
            // clickable only as a manual override for the case where a device is present but
            // misbehaving, and unlike the old button it does NOT clear the instance list -
            // losing a four-player setup because one battery died was the whole problem.
            let pads = self
                .input_devices
                .iter()
                .filter(|d| d.device_type() == DeviceType::Gamepad)
                .count();
            if ui
                .button(format!("🎮 {pads}"))
                .on_hover_text("Controllers detected. Added and removed automatically; click to force a rescan.")
                .clicked()
            {
                self.input_devices = scan_input_devices(&self.options.pad_filter_type);
                self.input_fingerprint = String::new(); // make the next poll re-map assignments
            }


            if ui.button("🖵 🔄").clicked() {
                self.instances.clear();
                self.monitors = get_monitors_errorless();
                self.audio_sinks = get_audio_sinks();
            }

            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("❌").clicked() {
                    ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
                }
                ui.add(egui::Separator::default().vertical());
                let version_label = match self.options.check_for_updates {
                    true => match self.needs_update.load(std::sync::atomic::Ordering::Relaxed) {
                        true => format!("v{} (🆕 available)", env!("CARGO_PKG_VERSION")),
                        false => format!("v{}", env!("CARGO_PKG_VERSION")),
                    },
                    false => format!("(Frozen) v{}", env!("CARGO_PKG_VERSION")),
                };
                ui.hyperlink_to(version_label, format!("{}/releases", crate::util::REPO_URL));
                ui.add(egui::Separator::default().vertical());
                ui.hyperlink_to("⮋", "https://drive.proton.me/urls/D9HBKM18YR#zG8XC8yVy9WL")
                    .on_hover_text("Download Game Handlers");
                ui.hyperlink_to("♥", "https://ko-fi.com/wunner")
                    .on_hover_text("Support PartyDeck Development");
                ui.hyperlink_to(
                    "🖹",
                    format!("{}/blob/main/LICENSE", crate::util::REPO_URL),
                )
                .on_hover_text("Third-Party Licenses");
                ui.hyperlink_to("", crate::util::REPO_URL)
                    .on_hover_text("GitHub");
            });
        });
    }

    pub fn display_panel_left(&mut self, ui: &mut Ui) {
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            ui.heading("Games");
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("➕").clicked() {
                    self.handler_edit = Some(Handler::default());
                    self.cur_page = MenuPage::EditHandler;
                }
                if ui.button("⬇").on_hover_text("Import a PartyDeck handler (.pd2)").clicked() {
                    if let Err(e) = import_pd2() {
                        msg("Error", &format!("Error importing PD2: {}", e));
                    } else {
                        self.handlers = scan_handlers();
                    }
                }
                if ui
                    .button("🅝")
                    .on_hover_text("Import a Nucleus Co-op handler (.nc) from hub.splitscreen.me")
                    .clicked()
                {
                    match crate::nucleus::import_nc_dialog() {
                        Ok(Some(summary)) => {
                            self.handlers = scan_handlers();
                            msg("Imported from Nucleus", &summary);
                        }
                        Ok(None) => {}
                        Err(e) => msg("Error", &format!("Could not import that handler: {e}")),
                    }
                }
                if ui.button("🔄").clicked() {
                    self.handlers = scan_handlers();
                }
            });
        });
        // Search. Worth having as soon as the library stops being a handful of games, which
        // importing from Nucleus does immediately: the hub publishes around 600 handlers, and
        // scrolling that to find one is hopeless.
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut self.handler_filter)
                    .desired_width(150.0)
                    .hint_text("🔍 Search games"),
            );
            if !self.handler_filter.is_empty() && ui.small_button("✖").clicked() {
                self.handler_filter.clear();
            }
        });
        ui.separator();
        egui::ScrollArea::vertical().show(ui, |ui| {
            self.panel_left_game_list(ui);
        });
    }

    pub fn display_panel_bottom(&mut self, ctx: &egui::Context) {
        egui::TopBottomPanel::bottom("info_panel")
            .exact_height(100.0)
            .show(ctx, |ui| {
                if self.task.is_some() {
                    ui.disable();
                }
                match self.cur_page {
                    MenuPage::Game => {
                        self.infotext = cur_handler!(self).info.to_owned();
                    }
                    MenuPage::Profiles => {
                        self.infotext = "Create profiles to persistently store game save data, settings, and stats.".to_string();
                    }
                    _ => {}
                }
                egui::ScrollArea::vertical().show(ui, |ui| {
                    if self.cur_page == MenuPage::EditHandler && let Some(handler) = &mut self.handler_edit {
                        ui.add(egui::TextEdit::multiline(&mut handler.info).hint_text("Put game info/instructions here"));
                    } else {
                        ui.label(&self.infotext);
                    }
                });
            });
    }

    pub fn display_panel_right(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        ui.add_space(6.0);

        ui.heading("Devices");
        ui.separator();

        for pad in self.input_devices.iter() {
            let mut dev_text = RichText::new(format!(
                "{} {} ({})",
                pad.emoji(),
                pad.fancyname(),
                pad.path().trim_start_matches("/dev/input/event")
            ))
            .small();

            if !pad.enabled() {
                dev_text = dev_text.weak();
            } else if pad.has_button_held() {
                dev_text = dev_text.strong();
            }

            ui.label(dev_text);
        }

        ui.with_layout(egui::Layout::bottom_up(egui::Align::Center), |ui| {
            ui.link("ℹ Incorrect/missing controller mappings in-game?").on_hover_ui(|ui| {
                ui.label("Some native Linux games run using an older version of SDL2 that doesn't support newer controllers; you can edit the handler and change the SDL2 Override setting to \"Steam Runtime\" for older 32-bit games, or \"System Installation\" for 64-bit games.\n\nWindows Unity-based games may not recognize input from PlayStation controllers; the current workaround for this is to use them through Steam Input, and change PartyDeck controller filter setting to \"Only Steam Input\".");
            });
            ui.link("ℹ Devices not being detected?").on_hover_ui(|ui| {
                ui.style_mut().interaction.selectable_labels = true;
                ui.label("Try adding your user to the `input` group.");
                ui.label("In a terminal, enter the following command:");
                ui.horizontal(|ui| {
                    ui.code("sudo usermod -aG input $USER");
                    if ui.button("📎").clicked() {
                        ctx.copy_text("sudo usermod -aG input $USER".to_string());
                    }
                });
            });
        });
    }

    pub fn panel_left_game_list(&mut self, ui: &mut Ui) {
        // Filtering is on the DISPLAY name, and the real index is carried through, because
        // self.selected_handler indexes self.handlers. Rebuilding a filtered vec and using its
        // positions would select the wrong game the moment a filter is active.
        let needle = self.handler_filter.to_ascii_lowercase();
        let needle = needle.trim();
        let mut shown = 0usize;

        for i in 0..self.handlers.len() {
            // Skip if index is out of bounds to catch for removing/rescanning handlers
            if i >= self.handlers.len() {
                return;
            }
            if !needle.is_empty()
                && !self.handlers[i].display().to_ascii_lowercase().contains(needle)
            {
                continue;
            }
            shown += 1;

            ui.horizontal(|ui| {
                ui.add(
                    egui::Image::new(self.handlers[i].icon())
                        .max_width(16.0)
                        .corner_radius(2),
                );

                let btn = ui.selectable_value(
                    &mut self.selected_handler,
                    i,
                    self.handlers[i].display_clamp(),
                );
                if btn.has_focus() {
                    btn.scroll_to_me(None);
                }
                if btn.clicked() {
                    self.cur_page = MenuPage::Game;
                };

                Popup::context_menu(&btn).show(|ui| self.handler_ctx_menu(ui, i));
            });
        }

        // Say so, rather than showing an empty panel that reads as "your handlers are gone".
        if shown == 0 && !needle.is_empty() {
            ui.add_space(8.0);
            ui.weak(format!("No game matches \"{}\"", self.handler_filter));
        }
    }

    pub fn handler_ctx_menu(&mut self, ui: &mut Ui, i: usize) {
        if ui.button("Edit").clicked() {
            self.handler_edit = Some(self.handlers[i].clone());
            self.cur_page = MenuPage::EditHandler;
        }

        if ui.button("Open Folder").clicked() {
            if let Err(_) = std::process::Command::new("xdg-open")
                .arg(self.handlers[i].path_handler.clone())
                .status()
            {
                msg("Error", "Couldn't open handler folder!");
            }
        }

        if ui.button("Remove").clicked() {
            if yesno(
                "Remove handler?",
                &format!(
                    "Are you sure you want to remove {}?",
                    self.handlers[i].display()
                ),
            ) {
                if let Err(err) = self.handlers[i].remove_handler() {
                    println!("[partydeck] Failed to remove handler: {}", err);
                    msg("Error", &format!("Failed to remove handler: {}", err));
                }

                self.handlers = scan_handlers();
                if self.handlers.is_empty() {
                    self.cur_page = MenuPage::Home;
                }
                if i >= self.handlers.len() {
                    self.selected_handler = 0;
                }
            }
        }

        if ui.button("Export").clicked() {
            if let Err(err) = self.handlers[i].export_pd2() {
                println!("[partydeck] Failed to export handler: {}", err);
                msg("Error", &format!("Failed to export handler: {}", err));
            }
        }
    }
}
