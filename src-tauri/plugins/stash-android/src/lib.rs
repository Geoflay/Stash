//! Registers the Kotlin plugin (youtubedl-android, MediaStore, sharing) and exposes it to Rust.

use tauri::Runtime;
use tauri::plugin::{Builder, TauriPlugin};

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("stash-android")
        .setup(|app, api| {
            #[cfg(target_os = "android")]
            {
                use tauri::Manager;
                let handle = api.register_android_plugin("dev.stash.light.plugin", "StashPlugin")?;
                app.manage(Stash(handle));
            }
            #[cfg(not(target_os = "android"))]
            let _ = (app, api);
            Ok(())
        })
        .build()
}

#[cfg(target_os = "android")]
pub use android::*;

#[cfg(target_os = "android")]
mod android {
    use serde::{Deserialize, Serialize};
    use serde_json::{Value, json};
    use tauri::ipc::Channel;
    use tauri::plugin::{PluginHandle, mobile::PluginInvokeError};
    use tauri::{Manager, Runtime};

    pub struct Stash<R: Runtime>(pub(crate) PluginHandle<R>);

    #[derive(Deserialize)]
    pub struct RunOutput {
        pub code: i32,
        pub out: String,
        pub err: String,
    }

    #[derive(Serialize)]
    struct RunArgs {
        args: Vec<String>,
        id: Option<String>,
        lines: Channel<Value>,
    }

    /// A picked file copied into the app's folder.
    #[derive(Deserialize)]
    pub struct Imported {
        pub path: String,
        pub name: String,
    }

    #[derive(Deserialize)]
    struct Text {
        value: String,
    }

    #[derive(Deserialize)]
    struct MaybeText {
        value: Option<String>,
    }

    type Result<T> = std::result::Result<T, PluginInvokeError>;

    impl<R: Runtime> Stash<R> {
        /// Runs yt-dlp; each stdout line is sent to `lines` as `{ "line": ... }`.
        pub fn run(&self, args: Vec<String>, id: Option<String>, lines: Channel<Value>) -> Result<RunOutput> {
            self.0.run_mobile_plugin("run", RunArgs { args, id, lines })
        }

        pub fn cancel(&self, id: String) -> Result<()> {
            self.0.run_mobile_plugin("cancel", json!({ "id": id }))
        }

        pub fn version(&self) -> Result<String> {
            Ok(self.0.run_mobile_plugin::<Text>("version", ())?.value)
        }

        /// Updates yt-dlp to the latest stable release.
        pub fn update(&self) -> Result<()> {
            self.0.run_mobile_plugin::<Text>("update", ()).map(|_| ())
        }

        /// Reads a text file picked with the dialog (a content URI).
        pub fn read_text(&self, uri: &str) -> Result<String> {
            Ok(self.0.run_mobile_plugin::<Text>("readText", json!({ "uri": uri }))?.value)
        }

        /// Copies a picked file (a content URI) into `dir` for editing.
        pub fn import_file(&self, uri: &str, dir: &str) -> Result<Imported> {
            self.0.run_mobile_plugin("importFile", json!({ "uri": uri, "dir": dir }))
        }

        /// Copies a finished file into Download/Stash (or a `folder` inside it) and returns its content URI.
        pub fn save(&self, path: &str, name: &str, mime: &str, folder: Option<&str>) -> Result<String> {
            Ok(self.0.run_mobile_plugin::<Text>("save", json!({ "path": path, "name": name, "mime": mime, "folder": folder }))?.value)
        }

        /// Renames a saved file (its content URI) to `name` plus its extension, `name (n)`
        /// when taken in its folder; returns its URI.
        pub fn rename(&self, uri: &str, name: &str) -> Result<String> {
            Ok(self.0.run_mobile_plugin::<Text>("rename", json!({ "uri": uri, "name": name }))?.value)
        }

        /// Shows or updates a job's notification; `step` is the job event's step name.
        pub fn notify(&self, id: u32, title: &str, step: &str, fraction: Option<f64>) -> Result<()> {
            self.0.run_mobile_plugin("notify", json!({ "id": id, "title": title, "step": step, "fraction": fraction }))
        }

        pub fn clear_notification(&self, id: u32) -> Result<()> {
            self.0.run_mobile_plugin("clearNotification", json!({ "id": id }))
        }

        /// `cancels` receives `{ "id": n }` when a notification's Cancel action is tapped.
        pub fn on_cancel(&self, cancels: Channel<Value>) -> Result<()> {
            self.0.run_mobile_plugin("onCancel", json!({ "cancels": cancels }))
        }

        /// `shares` receives `{}` when another app shares a text to this one.
        pub fn on_share(&self, shares: Channel<Value>) -> Result<()> {
            self.0.run_mobile_plugin("onShare", json!({ "shares": shares }))
        }

        /// The text last shared to the app, once.
        pub fn take_shared(&self) -> Result<Option<String>> {
            Ok(self.0.run_mobile_plugin::<MaybeText>("takeShared", ())?.value)
        }

        /// Light or dark system bar icons, for the app's theme.
        pub fn system_bars(&self, dark: bool) -> Result<()> {
            self.0.run_mobile_plugin("systemBars", json!({ "dark": dark }))
        }

        pub fn open(&self, uri: &str) -> Result<()> {
            self.0.run_mobile_plugin("open", json!({ "uri": uri }))
        }

        pub fn share(&self, uri: &str) -> Result<()> {
            self.0.run_mobile_plugin("share", json!({ "uri": uri }))
        }
    }

    pub trait StashExt<R: Runtime> {
        fn stash(&self) -> &Stash<R>;
    }

    impl<R: Runtime, T: Manager<R>> StashExt<R> for T {
        fn stash(&self) -> &Stash<R> {
            self.state::<Stash<R>>().inner()
        }
    }
}
