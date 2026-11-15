//! Progress outside the window: the taskbar button on Windows, one notification per
//! job on Android (with a Cancel action).

use std::collections::HashMap;

use tauri::AppHandle;

use crate::jobs::Step;

/// A queued or running job as the taskbar and the notifications show it.
#[derive(Clone, Debug)]
pub struct Live {
    /// Only the notifications show it.
    #[cfg_attr(not(target_os = "android"), allow(dead_code))]
    pub title: String,
    pub step: Step,
    pub fraction: Option<f64>,
}

/// Called on every job event; `job` is `None` once the job has ended.
pub fn show(app: &AppHandle, id: u32, job: Option<Live>, all: &HashMap<u32, Live>) {
    #[cfg(desktop)]
    {
        let _ = (id, job);
        taskbar(app, all);
    }
    #[cfg(target_os = "android")]
    {
        let _ = all;
        notifications::send(app, id, job);
    }
}

/// The mean progress of all jobs on the taskbar button; nothing when none is left.
#[cfg(desktop)]
fn taskbar(app: &AppHandle, all: &HashMap<u32, Live>) {
    use tauri::Manager;
    use tauri::window::{ProgressBarState, ProgressBarStatus};
    let Some(window) = app.get_webview_window("main") else { return };
    let state = if all.is_empty() {
        ProgressBarState { status: Some(ProgressBarStatus::None), progress: None }
    } else {
        let mean = all.values().map(|job| job.fraction.unwrap_or(0.0)).sum::<f64>() / all.len() as f64;
        ProgressBarState { status: Some(ProgressBarStatus::Normal), progress: Some((mean * 100.0).round() as u64) }
    };
    let _ = window.set_progress_bar(state);
}

#[cfg(target_os = "android")]
pub use notifications::listen_for_cancel;

#[cfg(target_os = "android")]
mod notifications {
    use std::collections::HashMap;
    use std::sync::mpsc::{self, Sender};
    use std::sync::{Mutex, OnceLock};
    use std::thread;
    use std::time::{Duration, Instant};

    use tauri::ipc::{Channel, InvokeResponseBody};
    use tauri::{AppHandle, Manager};
    use tauri_plugin_stash_android::StashExt;

    use super::Live;
    use crate::jobs::{Jobs, Step};

    type Update = (u32, Option<Live>);

    /// Plugin calls wait for Android's main thread, so they run on a thread of their own:
    /// job events can come from that main thread (the start commands).
    static UPDATES: OnceLock<Mutex<Sender<Update>>> = OnceLock::new();

    pub fn send(app: &AppHandle, id: u32, job: Option<Live>) {
        let updates = UPDATES.get_or_init(|| {
            let (tx, rx) = mpsc::channel();
            let app = app.clone();
            thread::spawn(move || post(&app, rx));
            Mutex::new(tx)
        });
        let _ = updates.lock().unwrap().send((id, job));
    }

    fn post(app: &AppHandle, updates: mpsc::Receiver<Update>) {
        let mut last: HashMap<u32, (Step, Instant)> = HashMap::new();
        for (id, job) in updates {
            let Some(job) = job else {
                last.remove(&id);
                let _ = app.stash().clear_notification(id);
                continue;
            };
            // Android drops updates posted too often: at most one a second, plus each new step.
            if let Some((step, at)) = last.get(&id)
                && *step == job.step
                && at.elapsed() < Duration::from_secs(1)
            {
                continue;
            }
            last.insert(id, (job.step, Instant::now()));
            let step = serde_json::to_value(job.step).ok().and_then(|v| v.as_str().map(String::from)).unwrap_or_default();
            let _ = app.stash().notify(id, &job.title, &step, job.fraction);
        }
    }

    /// Cancels the job whose notification's Cancel action was tapped.
    pub fn listen_for_cancel(app: &AppHandle) {
        let app = app.clone();
        // Setup runs on the main thread, which the plugin call needs.
        thread::spawn(move || {
            let handle = app.clone();
            let channel = Channel::new(move |body| {
                if let InvokeResponseBody::Json(text) = body
                    && let Ok(value) = serde_json::from_str::<serde_json::Value>(&text)
                    && let Some(id) = value["id"].as_u64()
                {
                    let app = handle.clone();
                    thread::spawn(move || app.state::<Jobs>().cancel(&app, id as u32));
                }
                Ok(())
            });
            if let Err(err) = app.stash().on_cancel(channel) {
                eprintln!("notification Cancel unavailable: {err}");
            }
        });
    }
}
