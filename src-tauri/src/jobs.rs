//! Download jobs: at most two run at once, each can be canceled, and every change
//! is sent to the UI as a `job` event.

use std::collections::{HashMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

use crate::extractor::{self, Part};
use crate::media::{self, Container, Ffmpeg, Kind, Probe, Slides};
use crate::priority;
use crate::progress::{self, Live};
use crate::settings::Settings;
use crate::tiktok::{self, Photo};
use crate::{Failure, edit};

#[derive(Default)]
pub struct Jobs {
    next: AtomicU32,
    /// Jobs that run at once (the settings' choice).
    limit: AtomicUsize,
    running: Mutex<usize>,
    changed: Condvar,
    cancelled: Mutex<HashSet<u32>>,
    /// Queued and running jobs, for the taskbar and the notifications.
    live: Mutex<HashMap<u32, Live>>,
}

#[derive(Deserialize)]
pub struct Request {
    info: PathBuf,
    title: String,
    parts: Vec<Part>,
    #[serde(default)]
    options: edit::Options,
    /// A TikTok photo post: a video made from these photos, or the photos themselves.
    #[serde(default)]
    photos: Option<Photos>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Photos {
    /// In order; a single one for "one photo + sound".
    list: Vec<Photo>,
    /// Save the photos as files instead of making a video.
    #[serde(default)]
    files: bool,
    /// Seconds each photo shows in the video.
    #[serde(default)]
    seconds: f64,
}

/// A local file to save a changed copy of, as `title`; the original is only read.
#[derive(Deserialize)]
pub struct EditRequest {
    path: PathBuf,
    title: String,
    #[serde(default)]
    options: edit::Options,
}

impl Request {
    /// The parts to download; without the sound when it is removed from a video.
    fn parts(&self) -> Vec<Part> {
        let photo_video = match &self.photos {
            Some(photos) if photos.files => return Vec::new(),
            Some(_) => true,
            None => false,
        };
        let mute = self.options.mute && (photo_video || self.parts.iter().any(|p| p.video));
        self.parts.iter().filter(|p| p.video || !mute).map(|p| Part { audio: p.audio && !mute, ..p.clone() }).collect()
    }

    /// For a video made from photos: the photos and the seconds each one shows.
    fn slides(&self) -> Option<(&[Photo], f64)> {
        let photos = self.photos.as_ref().filter(|p| !p.files && !p.list.is_empty())?;
        Some((&photos.list, photos.seconds))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Step {
    Queued,
    Download,
    Mux,
    Encode,
    Done,
    Cancelled,
    Failed,
}

#[derive(Clone, Serialize)]
struct Event {
    id: u32,
    step: Step,
    fraction: Option<f64>,
    eta: Option<u64>,
    /// The finished file: a path on Windows, a content URI on Android.
    path: Option<String>,
    /// Why a job failed; on a finished one, a note (the size limit was missed).
    error: Option<Failure>,
    /// On a finished job, the seconds spent encoding, when it was encoded.
    encoded: Option<f64>,
}

/// A saved file, with a note when it came out over the size limit.
struct Saved {
    path: String,
    note: Option<Failure>,
    /// Seconds spent encoding (the slideshow and the re-encode), when there was any.
    encoded: Option<f64>,
}

impl Event {
    fn new(id: u32, step: Step, fraction: Option<f64>) -> Self {
        Event { id, step, fraction, eta: None, path: None, error: None, encoded: None }
    }
}

fn emit(app: &AppHandle, id: u32, step: Step, fraction: Option<f64>) {
    send(app, Event::new(id, step, fraction));
}

fn send(app: &AppHandle, event: Event) {
    app.state::<Jobs>().show(app, &event);
    let _ = app.emit("job", event);
}

impl Jobs {
    pub fn start(&self, app: &AppHandle, request: Request) -> u32 {
        self.spawn(app, request.title.clone(), move |app, id| download(app, id, &request))
    }

    pub fn start_edit(&self, app: &AppHandle, request: EditRequest) -> u32 {
        self.spawn(app, request.title.clone(), move |app, id| edit_file(app, id, &request))
    }

    /// Queues `work`, which returns the saved file.
    fn spawn(&self, app: &AppHandle, title: String, work: impl FnOnce(&AppHandle, u32) -> Result<Saved, Failure> + Send + 'static) -> u32 {
        let id = self.next.fetch_add(1, Ordering::Relaxed);
        self.live.lock().unwrap().insert(id, Live { title, step: Step::Queued, fraction: None });
        emit(app, id, Step::Queued, None);
        let app = app.clone();
        thread::spawn(move || {
            let jobs = app.state::<Jobs>();
            let result = if jobs.acquire(id) {
                let result = work(&app, id);
                jobs.release();
                result
            } else {
                Err(Failure::io("cancelled"))
            };
            let event = Event::new(id, Step::Done, None);
            send(
                &app,
                match result {
                    _ if jobs.cancelled.lock().unwrap().contains(&id) => Event { step: Step::Cancelled, ..event },
                    Ok(saved) => Event { path: Some(saved.path), error: saved.note, encoded: saved.encoded, ..event },
                    Err(error) => Event { step: Step::Failed, error: Some(error), ..event },
                },
            );
        });
        id
    }

    /// Shows a job's progress outside the window.
    fn show(&self, app: &AppHandle, event: &Event) {
        let mut live = self.live.lock().unwrap();
        let job = match event.step {
            Step::Done | Step::Cancelled | Step::Failed => {
                live.remove(&event.id);
                None
            }
            step => live.get_mut(&event.id).map(|job| {
                job.step = step;
                job.fraction = event.fraction;
                job.clone()
            }),
        };
        progress::show(app, event.id, job, &live);
    }

    pub fn cancel(&self, app: &AppHandle, id: u32) {
        self.cancelled.lock().unwrap().insert(id);
        self.changed.notify_all();
        extractor::cancel(app, id);
    }

    /// Sets how many jobs run at once; queued ones start when it grows.
    pub fn set_limit(&self, limit: u32) {
        let running = self.running.lock().unwrap();
        self.limit.store(limit.max(1) as usize, Ordering::Relaxed);
        drop(running);
        self.changed.notify_all();
    }

    /// Waits for a free slot; false if the job was cancelled while queued.
    fn acquire(&self, id: u32) -> bool {
        let mut running = self.running.lock().unwrap();
        loop {
            if self.cancelled.lock().unwrap().contains(&id) {
                return false;
            }
            if *running < self.limit.load(Ordering::Relaxed).max(1) {
                *running += 1;
                return true;
            }
            running = self.changed.wait(running).unwrap();
        }
    }

    fn release(&self) {
        *self.running.lock().unwrap() -= 1;
        self.changed.notify_all();
    }
}

/// Runs `work` in a job folder of its own, removed afterwards.
fn in_job_dir<T>(app: &AppHandle, id: u32, work: impl FnOnce(&Path) -> Result<T, Failure>) -> Result<T, Failure> {
    let dir = extractor::work_dir(app)?.join(format!("job-{id}"));
    fs::create_dir_all(&dir).map_err(Failure::io)?;
    let result = work(&dir);
    let _ = fs::remove_dir_all(&dir);
    result
}

/// Downloads the raw streams into a job folder, muxes them and saves the result.
fn download(app: &AppHandle, id: u32, request: &Request) -> Result<Saved, Failure> {
    in_job_dir(app, id, |dir| run_job(app, id, request, dir))
}

/// Saves the copy in the kind of file chosen: re-encoded when an option needs it,
/// otherwise the kept tracks remuxed. The original is never written to.
fn edit_file(app: &AppHandle, id: u32, request: &EditRequest) -> Result<Saved, Failure> {
    let probe = media::probe_supported(&request.path)?;
    let options = &request.options;
    in_job_dir(app, id, |dir| {
        let source = if options.needs_encode(&probe) {
            // Read in place; `finish` re-encodes it into a new file.
            Source::new(request.path.clone(), probe)
        } else {
            emit(app, id, Step::Mux, Some(1.0));
            let container = options.output(&probe).container;
            let file = dir.join(format!("muxed.{}", container.ext()));
            match remux_kept(&request.path, &file, container, options, &probe, &[]) {
                Ok(probe) => Source::new(file, probe),
                // Tracks the file cannot hold as they are: re-encoded instead.
                Err(_) => Source { convert: true, ..Source::new(request.path.clone(), probe) },
            }
        };
        finish(app, id, &request.title, options, dir, source)
    })
}

fn run_job(app: &AppHandle, id: u32, request: &Request, dir: &Path) -> Result<Saved, Failure> {
    if let Some(photos) = request.photos.as_ref().filter(|p| p.files) {
        return save_photos(app, id, request, &photos.list, dir).map(|path| Saved { path, note: None, encoded: None });
    }
    let inputs = if request.parts().is_empty() { Vec::new() } else { fetch(app, id, request, dir)? };
    let source = match request.slides() {
        Some((photos, seconds)) => slideshow(app, id, photos, seconds, dir, &inputs)?,
        None => assemble(app, id, dir, &inputs)?,
    };
    finish(app, id, &request.title, &request.options, dir, source)
}

fn is_cancelled(app: &AppHandle, id: u32) -> Result<(), Failure> {
    match app.state::<Jobs>().cancelled.lock().unwrap().contains(&id) {
        true => Err(Failure::io("cancelled")),
        false => Ok(()),
    }
}

/// Progress of a `step` (the fraction done and the seconds left), starting at 0 and then
/// sent at most four times a second.
fn reporter(app: &AppHandle, id: u32, step: Step) -> impl FnMut(f64, Option<u64>) + Send + 'static {
    emit(app, id, step, Some(0.0));
    let app = app.clone();
    let mut last = Instant::now();
    move |fraction, eta| {
        if last.elapsed() >= Duration::from_millis(250) {
            last = Instant::now();
            send(&app, Event { eta, ..Event::new(id, step, Some(fraction)) });
        }
    }
}

/// Re-encode progress.
fn encode_progress(app: &AppHandle, id: u32) -> impl FnMut(f64) {
    let mut report = reporter(app, id, Step::Encode);
    move |fraction| report(fraction, None)
}

/// Downloads the photos into `folder` as 01.jpg, 02.jpg…
fn download_photos(app: &AppHandle, id: u32, photos: &[Photo], folder: &Path) -> Result<Vec<PathBuf>, Failure> {
    let cookies = tiktok::saved_cookies(app);
    let mut files = Vec::new();
    for (i, photo) in photos.iter().enumerate() {
        is_cancelled(app, id)?;
        emit(app, id, Step::Download, Some(i as f64 / photos.len() as f64));
        files.push(tiktok::download(&photo.url, &folder.join(format!("{:02}", i + 1)), cookies.as_deref())?);
    }
    Ok(files)
}

/// Saves a photo post's photos in a folder named after it.
fn save_photos(app: &AppHandle, id: u32, request: &Request, photos: &[Photo], dir: &Path) -> Result<String, Failure> {
    let name = safe_name(&request.title);
    #[cfg(target_os = "android")]
    {
        use tauri_plugin_stash_android::StashExt;
        let mut first = None;
        for file in download_photos(app, id, photos, dir)? {
            let file_name = file.file_name().unwrap_or_default().to_string_lossy().into_owned();
            let mime = match file.extension().and_then(|e| e.to_str()) {
                Some("png") => "image/png",
                Some("webp") => "image/webp",
                _ => "image/jpeg",
            };
            let uri = app.stash().save(&file.display().to_string(), &file_name, mime, Some(&name)).map_err(Failure::io)?;
            first.get_or_insert(uri);
        }
        first.ok_or_else(|| Failure::plain("errors.noPhotos"))
    }
    #[cfg(not(target_os = "android"))]
    {
        let folder = reserve_dir(&download_dir(app, &crate::saved_settings(app)?, dir)?, &name).map_err(Failure::io)?;
        match download_photos(app, id, photos, &folder) {
            Ok(_) => Ok(folder.display().to_string()),
            Err(err) => {
                let _ = fs::remove_dir_all(&folder);
                Err(err)
            }
        }
    }
}

/// A file to save, before the options are applied.
struct Source {
    file: PathBuf,
    probe: Probe,
    /// Its tracks could not be remuxed: it must be re-encoded.
    convert: bool,
    /// Seconds spent encoding it (a slideshow).
    encoded: f64,
}

impl Source {
    fn new(file: PathBuf, probe: Probe) -> Self {
        Source { file, probe, convert: false, encoded: 0.0 }
    }
}

/// Remuxes the tracks of `file` the options keep into `out`, without the `cuts`, and probes it.
fn remux_kept(file: &Path, out: &Path, container: Container, options: &edit::Options, probe: &Probe, cuts: &[(f64, f64)]) -> anyhow::Result<Probe> {
    let (video, audio) = options.keeps(probe);
    media::remux(out, container, &media::tracks(file, video, audio), cuts)?;
    media::probe(out)
}

/// Makes the video from the photos and the downloaded sound, if any.
fn slideshow(app: &AppHandle, id: u32, photos: &[Photo], seconds: f64, dir: &Path, inputs: &[(PathBuf, Kind)]) -> Result<Source, Failure> {
    let files = download_photos(app, id, photos, dir)?;
    is_cancelled(app, id)?;
    let (width, height) = edit::slide_size(&photos[0]);
    let settings = crate::saved_settings(app)?;
    let slides = Slides { width, height, seconds, video_kbps: edit::SLIDE_KBPS, audio_kbps: edit::AUDIO_KBPS, preset: settings.h264_preset };
    let file = dir.join("slides.mp4");
    let sound = inputs.first().map(|(file, _)| file.as_path());
    let stop = || is_cancelled(app, id).is_err();
    let _priority = priority::enter(settings.encoding_priority);
    let started = Instant::now();
    Ffmpeg { stop: &stop }.slideshow(&files, sound, &file, &slides, &mut encode_progress(app, id))?;
    let encoded = started.elapsed().as_secs_f64();
    let probe = media::probe(&file).map_err(Failure::io)?;
    Ok(Source { encoded, ..Source::new(file, probe) })
}

/// Muxes the downloaded streams into one MP4 (or M4A); a lone sound that is not MP4
/// already (TikTok's MP3) is kept as it is, for `finish` to copy or re-encode.
fn assemble(app: &AppHandle, id: u32, dir: &Path, inputs: &[(PathBuf, Kind)]) -> Result<Source, Failure> {
    emit(app, id, Step::Mux, Some(1.0));
    let mp4 = |file: &PathBuf| file.extension().and_then(|e| e.to_str()).is_some_and(|e| matches!(e, "mp4" | "m4a" | "m4v" | "mov"));
    if let [(file, Kind::Audio)] = inputs
        && !mp4(file)
    {
        let probe = media::probe(file).map_err(Failure::io)?;
        return Ok(Source::new(file.clone(), probe));
    }
    let video = inputs.iter().any(|(_, k)| *k == Kind::Video);
    let (file, container) = if video { (dir.join("muxed.mp4"), Container::Mp4) } else { (dir.join("muxed.m4a"), Container::M4a) };
    media::remux(&file, container, inputs, &[]).map_err(Failure::io)?;
    let probe = media::probe(&file).map_err(Failure::io)?;
    Ok(Source::new(file, probe))
}

/// The download folder on Windows; Android saves from the job folder into Download/Stash.
fn download_dir(app: &AppHandle, settings: &Settings, dir: &Path) -> Result<PathBuf, Failure> {
    Ok(match &settings.download_dir {
        _ if !cfg!(windows) => dir.to_path_buf(),
        Some(folder) => folder.clone(),
        None => app.path().download_dir().map_err(Failure::io)?,
    })
}

const PROGRESS: &str = "download:stash %(info.format_id)s %(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.total_bytes_estimate)s %(progress.speed)s";

/// Runs yt-dlp for every part, one format at a time, and returns the tracks to mux.
fn fetch(app: &AppHandle, id: u32, request: &Request, dir: &Path) -> Result<Vec<(PathBuf, Kind)>, Failure> {
    let parts = request.parts();
    let formats: Vec<&str> = parts.iter().map(|p| p.format_id.as_str()).collect();
    let args = [
        "-f",
        &formats.join(","),
        "-o",
        &dir.join("%(format_id)s.%(ext)s").display().to_string(),
        "--fixup",
        "never",
        // yt-dlp hands plain HLS to ffmpeg otherwise.
        "--downloader",
        "m3u8:native",
        // Streams cut into fragments (HLS, DASH: X, TikTok, Twitch…) a few at a time, not one.
        "--concurrent-fragments",
        "4",
        "--no-part",
        "--newline",
        "--progress-template",
        PROGRESS,
    ]
    .map(String::from)
    .to_vec();

    // A retry reads the link again with yt-dlp's own clients: the saved stream addresses
    // may have stopped working (YouTube answers 403). The stream ids stay the same.
    let saved = vec!["--load-info-json".to_owned(), request.info.display().to_string()];
    let link = extractor::page_url(&request.info);
    for attempt in 0.. {
        let source = match &link {
            Some(url) if attempt > 0 => vec!["--no-playlist".to_owned(), url.clone()],
            _ => saved.clone(),
        };
        let mut progress = Progress::new(&parts);
        let mut report = reporter(app, id, Step::Download);
        let out = extractor::run(app, [source, args.clone()].concat(), Some(id), move |line| {
            if let Some((fraction, eta)) = progress.update(line) {
                report(fraction, eta);
            }
        })?;
        if out.ok {
            break;
        }
        is_cancelled(app, id)?;
        let failure = extractor::failure(&out.stderr);
        if !extractor::retry(attempt, &failure) {
            return Err(failure);
        }
        // The next try starts over.
        for entry in fs::read_dir(dir).map_err(Failure::io)?.flatten() {
            let _ = fs::remove_file(entry.path());
        }
    }

    let files: Vec<PathBuf> = fs::read_dir(dir).map_err(Failure::io)?.filter_map(|e| e.ok().map(|e| e.path())).collect();
    let mut inputs = Vec::new();
    for part in &parts {
        let file = files
            .iter()
            .find(|f| f.file_stem().is_some_and(|s| s == part.format_id.as_str()))
            .ok_or_else(|| Failure::io(format!("missing stream {}", part.format_id)))?;
        if part.video {
            inputs.push((file.clone(), Kind::Video));
        }
        // A file whose codecs the site left out may have no sound (Imgur's GIFs as MP4).
        let silent = part.audio && !media::probe(file).map_err(Failure::io)?.audio;
        if silent && !part.video {
            return Err(Failure::plain("errors.noSound"));
        }
        if part.audio && !silent {
            inputs.push((file.clone(), Kind::Audio));
        }
    }
    Ok(inputs)
}

/// Re-encodes only when an option requires it, and saves the result:
/// in the download folder on Windows, in Download/Stash on Android.
fn finish(app: &AppHandle, id: u32, title: &str, options: &edit::Options, dir: &Path, source: Source) -> Result<Saved, Failure> {
    let Source { mut file, mut probe, mut convert, mut encoded } = source;
    // A re-encode writes the picture codec of the settings, at its preset, or with the
    // graphics card when asked and it has an encoder for that codec.
    let settings = crate::saved_settings(app)?;
    let encoder = settings.encoder();
    let encoder = settings.gpu.then(|| media::gpu_encoder(encoder.codec(), settings.gpu_preset)).flatten().unwrap_or(encoder);
    let mut options = edit::Options { encoder, card: settings.gpu, ..options.clone() };
    let container = options.output(&probe).container;
    // SponsorBlock's cuts alone: copied out at the nearest keyframes rather than re-encoded.
    let cuts = options.cuts(options.range(probe.duration));
    if !cuts.is_empty() && !convert && !options.needs_encode(&probe) {
        emit(app, id, Step::Mux, Some(1.0));
        let cut = dir.join(format!("cut.{}", container.ext()));
        match remux_kept(&file, &cut, container, &options, &probe, &cuts) {
            Ok(cut_probe) => {
                (file, probe) = (cut, cut_probe);
                options.cuts.clear();
            }
            // Tracks the file cannot hold as they are: re-encoded instead.
            Err(_) => convert = true,
        }
    }
    let options = &options;
    let name = format!("{}.{}", safe_name(title), container.ext());
    let too_big = options.target_bytes(&probe).is_some_and(|max| fs::metadata(&file).map_or(true, |m| m.len() > max));

    let target = download_dir(app, &settings, dir)?;
    is_cancelled(app, id)?;
    let path = reserve(&target, &name).map_err(Failure::io)?;
    // Only a file made in the job folder is ever moved: an edited original always re-encodes here.
    let result = if convert || options.needs_encode(&probe) || too_big {
        // A re-encode writes the codecs of the settings, even for an edit.
        let options = &edit::Options { keep_codecs: false, ..options.clone() };
        let stop = || is_cancelled(app, id).is_err();
        let _priority = priority::enter(settings.encoding_priority);
        let started = Instant::now();
        let result = edit::encode(&Ffmpeg { stop: &stop }, &file, &path, options, &probe, dir, &mut encode_progress(app, id));
        encoded += started.elapsed().as_secs_f64();
        result
    } else if file.extension().is_some_and(|ext| ext == container.ext()) {
        crate::move_file(&file, &path).map(|_| None).map_err(Failure::io)
    } else {
        // The same tracks in another kind of file.
        remux_kept(&file, &path, container, options, &probe, &[]).map(|_| None).map_err(Failure::io)
    };
    let note = result.inspect_err(|_| {
        let _ = fs::remove_file(&path);
    })?;

    #[cfg(target_os = "android")]
    let path = {
        use tauri_plugin_stash_android::StashExt;
        app.stash().save(&path.display().to_string(), &name, container.mime(), None).map_err(Failure::io)?
    };
    #[cfg(not(target_os = "android"))]
    let path = path.display().to_string();
    Ok(Saved { path, note, encoded: (encoded > 0.0).then_some(encoded) })
}

/// Overall progress over all parts, from yt-dlp's progress lines.
struct Progress {
    ids: Vec<String>,
    sizes: Vec<f64>,
    done: Vec<f64>,
}

impl Progress {
    fn new(parts: &[Part]) -> Self {
        Progress {
            ids: parts.iter().map(|p| p.format_id.clone()).collect(),
            sizes: parts.iter().map(|p| p.size.unwrap_or(0) as f64).collect(),
            done: vec![0.0; parts.len()],
        }
    }

    /// Reads one stdout line; returns the fraction done and the seconds left when known.
    fn update(&mut self, line: &str) -> Option<(f64, Option<u64>)> {
        let mut fields = line.strip_prefix("stash ")?.split(' ');
        let id = fields.next()?;
        let i = self.ids.iter().position(|x| x == id)?;
        let mut number = || fields.next().and_then(|s| s.parse::<f64>().ok());
        let (got, total, estimate, speed) = (number(), number(), number(), number());
        for j in 0..i {
            self.done[j] = self.sizes[j];
        }
        if let Some(size) = total.or(estimate) {
            self.sizes[i] = size;
        }
        self.done[i] = got.unwrap_or(0.0);
        let (all, done): (f64, f64) = (self.sizes.iter().sum(), self.done.iter().sum());
        if all <= 0.0 {
            return None;
        }
        let eta = speed.filter(|&s| s > 0.0).map(|s| ((all - done).max(0.0) / s).round() as u64);
        Some(((done / all).min(1.0), eta))
    }
}

/// A file name from a title: no character Windows forbids, at most 120 characters.
fn safe_name(title: &str) -> String {
    let cleaned: String = title.chars().map(|c| if c.is_control() || r#"\/:*?"<>|"#.contains(c) { ' ' } else { c }).collect();
    let words: Vec<&str> = cleaned.split_whitespace().collect();
    let name: String = words.join(" ").chars().take(120).collect();
    let name = name.trim_end_matches(['.', ' ']);
    if name.is_empty() { "video".into() } else { name.into() }
}

/// `name`, then `name (1)`, `name (2)`… for the next tries.
fn numbered(name: &str, n: u32) -> String {
    if n == 0 { name.to_owned() } else { format!("{name} ({n})") }
}

/// Creates the first free `dir/name_for(n)` (n = 0, 1, 2…) with `make` and returns it.
/// Never overwrites.
fn first_free(dir: &Path, name_for: impl Fn(u32) -> String, make: impl Fn(&Path) -> io::Result<()>) -> io::Result<PathBuf> {
    fs::create_dir_all(dir)?;
    for n in 0.. {
        let candidate = dir.join(name_for(n));
        match make(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e),
        }
    }
    unreachable!("some name is free")
}

/// Creates `dir/name`, or `dir/stem (n).ext` when taken, and returns its path.
fn reserve(dir: &Path, name: &str) -> io::Result<PathBuf> {
    let (stem, ext) = name.rsplit_once('.').unwrap_or((name, ""));
    let name_for = |n| if n == 0 { name.to_owned() } else { format!("{}.{ext}", numbered(stem, n)) };
    first_free(dir, name_for, |path| OpenOptions::new().write(true).create_new(true).open(path).map(|_| ()))
}

/// Creates `dir/name`, or `dir/name (n)` when taken, as a folder.
#[cfg(not(target_os = "android"))]
fn reserve_dir(dir: &Path, name: &str) -> io::Result<PathBuf> {
    first_free(dir, |n| numbered(name, n), |path| fs::create_dir(path))
}

/// Renames a saved file (a path on Windows, a content URI on Android) after `title`,
/// keeping its extension, and returns where it now is.
pub fn rename_saved(app: &AppHandle, path: &str, title: &str) -> Result<String, Failure> {
    #[cfg(target_os = "android")]
    {
        use tauri_plugin_stash_android::StashExt;
        app.stash().rename(path, &safe_name(title)).map_err(Failure::io)
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        rename(Path::new(path), title).map(|path| path.display().to_string()).map_err(Failure::io)
    }
}

/// Renames a file or a photo folder after `title`, keeping its extension: `title (n)`
/// when another file has that name. Never overwrites.
#[cfg(not(target_os = "android"))]
fn rename(path: &Path, title: &str) -> io::Result<PathBuf> {
    let stem = safe_name(title);
    let ext = if path.is_dir() { None } else { path.extension().map(|e| e.to_string_lossy().into_owned()) };
    let name_for = |n| {
        let name = numbered(&stem, n);
        ext.as_ref().map_or(name.clone(), |ext| format!("{name}.{ext}"))
    };
    // Windows names ignore case: the file itself, in other case, is not another one.
    let itself = |candidate: &Path| candidate == path || (cfg!(windows) && candidate.as_os_str().eq_ignore_ascii_case(path.as_os_str()));
    let dir = path.parent().ok_or_else(|| io::Error::other("no folder"))?;
    first_free(dir, name_for, |candidate| match itself(candidate) || !candidate.exists() {
        true => fs::rename(path, candidate),
        false => Err(io::ErrorKind::AlreadyExists.into()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn photo_posts() {
        let sound = Part { format_id: "audio".into(), video: false, audio: true, size: None };
        let photo = |width, height| Photo { url: "https://x/1.jpeg".into(), width, height };
        let request = |files, mute| Request {
            info: PathBuf::new(),
            title: String::new(),
            parts: vec![sound.clone()],
            options: edit::Options { mute, ..edit::Options::default() },
            photos: Some(Photos { list: vec![photo(1168, 1548), photo(1168, 1548)], files, seconds: 4.0 }),
        };
        // Photos only: nothing for yt-dlp to download.
        assert!(request(true, false).parts().is_empty());
        assert!(request(true, false).slides().is_none());
        // A video, with its sound unless it is removed.
        assert_eq!(request(false, false).parts(), vec![sound.clone()]);
        assert_eq!(request(false, false).slides().unwrap().1, 4.0);
        assert!(request(false, true).parts().is_empty());
    }

    #[test]
    #[cfg(not(target_os = "android"))]
    fn folders_are_never_reused() {
        let dir = std::env::temp_dir().join(format!("stash-folders-{}", std::process::id()));
        let first = reserve_dir(&dir, "Post").unwrap();
        let second = reserve_dir(&dir, "Post").unwrap();
        assert_eq!((first.file_name().unwrap(), second.file_name().unwrap()), ("Post".as_ref(), "Post (1)".as_ref()));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn names_are_safe() {
        assert_eq!(safe_name(r#"AC/DC: "Live" <1991> | best?*"#), "AC DC Live 1991 best");
        assert_eq!(safe_name("end with dots... "), "end with dots");
        assert_eq!(safe_name("tab\there\nnewline"), "tab here newline");
        assert_eq!(safe_name(" ?? "), "video");
        assert_eq!(safe_name(&"é".repeat(200)).chars().count(), 120);
    }

    #[test]
    fn reserve_never_overwrites() {
        let dir = std::env::temp_dir().join(format!("stash-reserve-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let first = reserve(&dir, "clip.mp4").unwrap();
        fs::write(&first, b"keep").unwrap();
        let second = reserve(&dir, "clip.mp4").unwrap();
        let third = reserve(&dir, "clip.mp4").unwrap();
        assert_eq!(first.file_name().unwrap(), "clip.mp4");
        assert_eq!(second.file_name().unwrap(), "clip (1).mp4");
        assert_eq!(third.file_name().unwrap(), "clip (2).mp4");
        assert_eq!(fs::read(&first).unwrap(), b"keep");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    #[cfg(not(target_os = "android"))]
    fn renames_keep_the_extension_and_never_overwrite() {
        let dir = std::env::temp_dir().join(format!("stash-rename-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let clip = dir.join("clip.mp4");
        fs::write(&clip, b"clip").unwrap();
        fs::write(dir.join("taken.mp4"), b"keep").unwrap();

        let renamed = rename(&clip, "My: clip").unwrap();
        assert_eq!(renamed.file_name().unwrap(), "My clip.mp4");
        assert_eq!(fs::read(&renamed).unwrap(), b"clip");
        // Another file's name: numbered, the other file kept.
        let renamed = rename(&renamed, "taken").unwrap();
        assert_eq!(renamed.file_name().unwrap(), "taken (1).mp4");
        assert_eq!(fs::read(dir.join("taken.mp4")).unwrap(), b"keep");
        // Its own name: unchanged.
        assert_eq!(rename(&renamed, "taken (1)").unwrap(), renamed);
        // A photo folder: no extension.
        let folder = dir.join("Post");
        fs::create_dir(&folder).unwrap();
        assert_eq!(rename(&folder, "Photos.v2").unwrap().file_name().unwrap(), "Photos.v2");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    #[cfg(windows)]
    fn a_rename_can_change_only_the_case() {
        let dir = std::env::temp_dir().join(format!("stash-case-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let clip = dir.join("clip.mp4");
        fs::write(&clip, b"clip").unwrap();
        let renamed = rename(&clip, "Clip").unwrap();
        assert_eq!(renamed.file_name().unwrap(), "Clip.mp4");
        let names: Vec<_> = fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(names, ["Clip.mp4"]);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn progress_over_parts() {
        let part = |id: &str, size| Part { format_id: id.into(), video: true, audio: false, size: Some(size) };
        let mut p = Progress::new(&[part("137", 300), part("140", 100)]);
        assert_eq!(p.update("[download] something else"), None);
        assert_eq!(p.update("stash 137 150 300 NA 50.0"), Some((0.375, Some(5))));
        // The real size replaces the estimate; the first part counts as done once the second starts.
        assert_eq!(p.update("stash 140 0 NA 100 NA"), Some((0.75, None)));
        assert_eq!(p.update("stash 140 100 100 NA 10"), Some((1.0, Some(0))));
    }
}
