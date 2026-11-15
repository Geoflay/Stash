mod edit;
mod extractor;
mod jobs;
mod media;
mod progress;
mod settings;
mod tiktok;

use std::path::{Path, PathBuf};

use jobs::Jobs;
use serde::Serialize;
use settings::Settings;
use tauri::{AppHandle, Manager, State};

/// An error the UI translates: a message key from the locale files and its parameters.
#[derive(Clone, Debug, Serialize)]
pub struct Failure {
    key: &'static str,
    params: serde_json::Value,
}

impl Failure {
    fn new(key: &'static str, params: serde_json::Value) -> Self {
        Failure { key, params }
    }

    /// A message with no parameters.
    fn plain(key: &'static str) -> Self {
        Failure::new(key, serde_json::json!({}))
    }

    fn io(err: impl std::fmt::Display) -> Self {
        Failure::new("errors.io", serde_json::json!({ "detail": err.to_string() }))
    }
}

fn data_dir(app: &AppHandle) -> Result<PathBuf, Failure> {
    app.path().app_data_dir().map_err(Failure::io)
}

/// Moves a file; copies it when it is on another drive.
fn move_file(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::rename(from, to).or_else(|_| std::fs::copy(from, to).map(|_| ()))
}

/// Runs blocking work off the main thread (and off Android's UI thread, which the plugin needs).
async fn blocking<T: Send + 'static>(work: impl FnOnce() -> Result<T, Failure> + Send + 'static) -> Result<T, Failure> {
    tauri::async_runtime::spawn_blocking(work).await.map_err(Failure::io)?
}

#[tauri::command]
fn get_settings(app: AppHandle) -> Result<Settings, Failure> {
    let mut settings = settings::load(&data_dir(&app)?);
    if cfg!(windows) && settings.download_dir.is_none() {
        settings.download_dir = app.path().download_dir().ok();
    }
    Ok(settings)
}

#[tauri::command]
fn set_settings(app: AppHandle, jobs: State<Jobs>, settings: Settings) -> Result<(), Failure> {
    settings::save(&data_dir(&app)?, &settings).map_err(Failure::io)?;
    jobs.set_limit(settings.max_jobs);
    Ok(())
}

#[tauri::command]
async fn ytdlp_version(app: AppHandle) -> Result<String, Failure> {
    blocking(move || extractor::version(&app)).await
}

/// Updates yt-dlp; returns the installed version and whether it changed.
#[tauri::command]
async fn update_ytdlp(app: AppHandle) -> Result<(String, bool), Failure> {
    blocking(move || {
        let before = extractor::version(&app).ok();
        extractor::update(&app)?;
        let after = extractor::version(&app)?;
        let updated = before.as_deref() != Some(after.as_str());
        Ok((after, updated))
    })
    .await
}

/// The cookies.txt imported for yt-dlp.
fn cookies_path(app: &AppHandle) -> Result<PathBuf, Failure> {
    Ok(data_dir(app)?.join("cookies.txt"))
}

#[tauri::command]
fn cookies_imported(app: AppHandle) -> Result<bool, Failure> {
    Ok(cookies_path(&app)?.exists())
}

/// Copies a cookies.txt file (a path, or a content URI on Android) for yt-dlp to use.
#[tauri::command]
async fn import_cookies(app: AppHandle, path: String) -> Result<(), Failure> {
    blocking(move || {
        #[cfg(target_os = "android")]
        let text = {
            use tauri_plugin_stash_android::StashExt;
            app.stash().read_text(&path).map_err(Failure::io)?
        };
        #[cfg(not(target_os = "android"))]
        let text = std::fs::read_to_string(&path).map_err(Failure::io)?;
        if !extractor::is_cookie_file(&text) {
            return Err(Failure::plain("errors.cookies"));
        }
        std::fs::create_dir_all(data_dir(&app)?).map_err(Failure::io)?;
        std::fs::write(cookies_path(&app)?, text).map_err(Failure::io)
    })
    .await
}

#[tauri::command]
fn remove_cookies(app: AppHandle) -> Result<(), Failure> {
    let path = cookies_path(&app)?;
    if path.exists() {
        std::fs::remove_file(path).map_err(Failure::io)?;
    }
    Ok(())
}

#[tauri::command]
fn parse_time(text: String) -> Option<f64> {
    edit::parse_time(&text)
}

#[tauri::command]
async fn analyze(app: AppHandle, url: String) -> Result<extractor::Media, Failure> {
    blocking(move || extractor::analyze(&app, &url)).await
}

/// A local file opened in the Edit tab.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LocalMedia {
    /// The file the core reads and the preview plays (on Android, a copy in the app's folder).
    path: String,
    name: String,
    size: u64,
    duration: f64,
    width: Option<u32>,
    height: Option<u32>,
    fps: Option<f64>,
    audio: bool,
    /// The codecs, when the app writes them too: an edit keeps them by default.
    video_codec: Option<media::VideoCodec>,
    audio_codec: Option<media::AudioCodec>,
}

/// Opens a file to edit: a path, or a content URI on Android (copied first).
#[tauri::command]
async fn open_media(app: AppHandle, path: String) -> Result<LocalMedia, Failure> {
    blocking(move || {
        #[cfg(target_os = "android")]
        let (file, name) = {
            use std::sync::atomic::{AtomicU32, Ordering};
            use tauri_plugin_stash_android::StashExt;
            static NEXT: AtomicU32 = AtomicU32::new(0);
            // Each file gets its own folder: an edit of the previous one may still be running.
            let dir = extractor::work_dir(&app)?.join(format!("open-{}", NEXT.fetch_add(1, Ordering::Relaxed)));
            let copy = app.stash().import_file(&path, &dir.display().to_string()).map_err(Failure::io)?;
            (PathBuf::from(copy.path), copy.name)
        };
        #[cfg(not(target_os = "android"))]
        let (file, name) = {
            let file = PathBuf::from(&path);
            let name = file.file_name().unwrap_or_default().to_string_lossy().into_owned();
            (file, name)
        };
        let size = std::fs::metadata(&file).map_err(Failure::io)?.len();
        let probe = media::probe_supported(&file)?;
        app.asset_protocol_scope().allow_file(&file).map_err(Failure::io)?;
        Ok(LocalMedia {
            path: file.display().to_string(),
            name,
            size,
            duration: probe.duration,
            width: probe.video.map(|v| v.width),
            height: probe.video.map(|v| v.height),
            fps: probe.video.map(|v| v.fps),
            audio: probe.audio,
            video_codec: probe.video.and_then(|v| v.codec),
            audio_codec: probe.audio_codec,
        })
    })
    .await
}

/// A copy of an opened file the preview can play, when the webview can't play it (MKV,
/// HEVC, AVI…), its progress sent on `on_progress`. Asking for another one stops the one
/// being made.
#[tauri::command]
async fn preview_media(app: AppHandle, path: String, on_progress: tauri::ipc::Channel<f64>) -> Result<String, Failure> {
    use std::sync::atomic::{AtomicU32, Ordering};
    static LATEST: AtomicU32 = AtomicU32::new(0);
    let id = LATEST.fetch_add(1, Ordering::Relaxed) + 1;
    blocking(move || {
        let file = PathBuf::from(&path);
        let probe = media::probe_supported(&file)?;
        let dir = extractor::work_dir(&app)?;
        // Earlier previews are no longer shown (one still playing stays until next time).
        for old in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
            if old.file_name().to_string_lossy().starts_with("preview-") {
                let _ = std::fs::remove_file(old.path());
            }
        }
        let out = dir.join(format!("preview-{id}.{}", media::preview_container(&probe).ext()));
        let stop = || LATEST.load(Ordering::Relaxed) != id;
        // Every percent at most.
        let mut sent = 0.0;
        let mut progress = |fraction: f64| {
            if fraction - sent >= 0.01 {
                sent = fraction;
                let _ = on_progress.send(fraction);
            }
        };
        media::preview(&file, &out, &probe, &stop, &mut progress).inspect_err(|_| {
            let _ = std::fs::remove_file(&out);
        })?;
        app.asset_protocol_scope().allow_file(&out).map_err(Failure::io)?;
        Ok(out.display().to_string())
    })
    .await
}

#[tauri::command]
fn start_download(app: AppHandle, jobs: State<Jobs>, request: jobs::Request) -> u32 {
    jobs.start(&app, request)
}

#[tauri::command]
fn start_edit(app: AppHandle, jobs: State<Jobs>, request: jobs::EditRequest) -> u32 {
    jobs.start_edit(&app, request)
}

#[tauri::command]
async fn cancel_download(app: AppHandle, id: u32) -> Result<(), Failure> {
    blocking(move || {
        app.state::<Jobs>().cancel(&app, id);
        Ok(())
    })
    .await
}

/// Opens a finished file with the default app.
#[tauri::command]
async fn open_file(app: AppHandle, path: String) -> Result<(), Failure> {
    blocking(move || {
        #[cfg(windows)]
        {
            let _ = app;
            tauri_plugin_opener::open_path(&path, None::<&str>).map_err(Failure::io)
        }
        #[cfg(target_os = "android")]
        {
            use tauri_plugin_stash_android::StashExt;
            app.stash().open(&path).map_err(Failure::io)
        }
    })
    .await
}

/// Renames a finished file (or photo folder) after `title`; returns its new path or URI.
#[tauri::command]
async fn rename_file(app: AppHandle, path: String, title: String) -> Result<String, Failure> {
    blocking(move || jobs::rename_saved(&app, &path, &title)).await
}

/// Android: the text last shared to the app from another one, once.
#[tauri::command]
async fn take_shared(app: AppHandle) -> Result<Option<String>, Failure> {
    #[cfg(target_os = "android")]
    return blocking(move || {
        use tauri_plugin_stash_android::StashExt;
        app.stash().take_shared().map_err(Failure::io)
    })
    .await;
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Ok(None)
    }
}

/// Android: a `shared` event tells the webview when another app shares a text to this one.
#[cfg(target_os = "android")]
fn listen_for_shares(app: &AppHandle) {
    use tauri::Emitter;
    use tauri::ipc::Channel;
    use tauri_plugin_stash_android::StashExt;
    let app = app.clone();
    // Setup runs on the main thread, which the plugin call needs.
    std::thread::spawn(move || {
        let handle = app.clone();
        let channel = Channel::new(move |_| {
            let _ = handle.emit("shared", ());
            Ok(())
        });
        if let Err(err) = app.stash().on_share(channel) {
            eprintln!("sharing to the app unavailable: {err}");
        }
    });
}

/// Matches Android's status and navigation bar icons to the app's theme.
#[tauri::command]
async fn system_bars(app: AppHandle, dark: bool) -> Result<(), Failure> {
    #[cfg(target_os = "android")]
    return blocking(move || {
        use tauri_plugin_stash_android::StashExt;
        app.stash().system_bars(dark).map_err(Failure::io)
    })
    .await;
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, dark);
        Ok(())
    }
}

/// Shows a finished file in Explorer (Windows).
#[tauri::command]
async fn reveal_file(path: String) -> Result<(), Failure> {
    blocking(move || {
        #[cfg(windows)]
        return tauri_plugin_opener::reveal_item_in_dir(path).map_err(Failure::io);
        #[cfg(not(windows))]
        {
            let _ = path;
            Err(Failure::io("revealing is Windows only"))
        }
    })
    .await
}

/// Opens the share sheet for a finished file (Android).
#[tauri::command]
async fn share_file(app: AppHandle, path: String) -> Result<(), Failure> {
    #[cfg(target_os = "android")]
    return blocking(move || {
        use tauri_plugin_stash_android::StashExt;
        app.stash().share(&path).map_err(Failure::io)
    })
    .await;
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, path);
        Err(Failure::io("sharing is Android only"))
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init());
    #[cfg(target_os = "android")]
    let builder = builder.plugin(tauri_plugin_stash_android::init());
    builder
        .manage(Jobs::default())
        .setup(|app| {
            // Leftovers from a previous session: info files and unfinished downloads.
            if let Ok(work) = extractor::work_dir(app.handle()) {
                let _ = std::fs::remove_dir_all(work);
            }
            #[cfg(target_os = "android")]
            {
                progress::listen_for_cancel(app.handle());
                listen_for_shares(app.handle());
            }
            let handle = app.handle().clone();
            let settings = data_dir(&handle).map(|dir| settings::load(&dir)).unwrap_or_default();
            app.state::<Jobs>().set_limit(settings.max_jobs);
            if settings.auto_update {
                std::thread::spawn(move || extractor::update(&handle));
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            set_settings,
            ytdlp_version,
            update_ytdlp,
            cookies_imported,
            import_cookies,
            remove_cookies,
            parse_time,
            analyze,
            open_media,
            preview_media,
            start_download,
            start_edit,
            cancel_download,
            open_file,
            rename_file,
            reveal_file,
            share_file,
            system_bars,
            take_shared
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
