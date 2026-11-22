//! yt-dlp: analysis (`-J`), stream choice, raw downloads and readable errors.
//! On Windows it runs the bundled sidecar (an updatable copy in the data folder);
//! on Android it goes through youtubedl-android in the stash-android plugin.

use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::AppHandle;

use crate::Failure;
use crate::tiktok::{self, Photo};

/// What a yt-dlp run printed.
pub struct Output {
    pub ok: bool,
    pub stdout: String,
    pub stderr: String,
}

/// Runs yt-dlp with `args`. A run with a `job` id can be stopped with [`cancel`];
/// `on_line` receives each stdout line as it comes.
pub fn run(app: &AppHandle, mut args: Vec<String>, job: Option<u32>, on_line: impl FnMut(&str) + Send + 'static) -> Result<Output, Failure> {
    let cookies = crate::cookies_path(app)?;
    if cookies.exists() {
        args.splice(0..0, ["--cookies".to_owned(), cookies.display().to_string()]);
    }
    args.insert(0, "--ignore-config".to_owned());
    platform::run(app, args, job, on_line)
}

/// `cancel` stops a job's run, `version` gives the installed yt-dlp version and `update`
/// installs the latest stable release.
pub use platform::{cancel, update, version};

#[cfg(windows)]
mod platform {
    use std::io::{self, BufRead, BufReader, Read};
    use std::os::windows::process::CommandExt;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::sync::Mutex;
    use std::{fs, thread};

    use tauri::AppHandle;

    use super::Output;
    use crate::Failure;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    /// Running cancelable processes: (job id, process id).
    static RUNNING: Mutex<Vec<(u32, u32)>> = Mutex::new(Vec::new());

    /// The updatable copy in the data folder, created from the bundled sidecar if missing.
    fn ytdlp_path(data_dir: &Path) -> io::Result<PathBuf> {
        let copy = data_dir.join("yt-dlp.exe");
        if !copy.exists() {
            let bundled = std::env::current_exe()?.with_file_name("yt-dlp.exe");
            fs::create_dir_all(data_dir)?;
            fs::copy(bundled, &copy)?;
        }
        Ok(copy)
    }

    fn command(app: &AppHandle) -> Result<Command, Failure> {
        let mut cmd = Command::new(ytdlp_path(&crate::data_dir(app)?).map_err(Failure::io)?);
        cmd.creation_flags(CREATE_NO_WINDOW);
        Ok(cmd)
    }

    pub fn run(app: &AppHandle, args: Vec<String>, job: Option<u32>, mut on_line: impl FnMut(&str)) -> Result<Output, Failure> {
        let qjs = std::env::current_exe().map_err(Failure::io)?.with_file_name("qjs.exe");
        let mut child = command(app)?
            // Otherwise it writes in the console's code page ("you’re" is not UTF-8).
            .args(["--encoding", "utf-8"])
            .args(["--no-js-runtimes", "--js-runtimes"])
            .arg(format!("quickjs:{}", qjs.display()))
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(Failure::io)?;
        if let Some(job) = job {
            RUNNING.lock().unwrap().push((job, child.id()));
        }
        let mut stderr = child.stderr.take().expect("piped");
        // Read as bytes: one character that is not UTF-8 must not lose the whole message.
        let errors = thread::spawn(move || {
            let mut bytes = Vec::new();
            let _ = stderr.read_to_end(&mut bytes);
            String::from_utf8_lossy(&bytes).into_owned()
        });
        let mut stdout = String::new();
        for line in BufReader::new(child.stdout.take().expect("piped")).lines() {
            let line = line.map_err(Failure::io)?;
            on_line(&line);
            stdout.push_str(&line);
            stdout.push('\n');
        }
        let status = child.wait().map_err(Failure::io)?;
        RUNNING.lock().unwrap().retain(|&(j, _)| Some(j) != job);
        Ok(Output { ok: status.success(), stdout, stderr: errors.join().unwrap_or_default() })
    }

    /// Kills the job's process tree: yt-dlp.exe is a launcher with a child process.
    pub fn cancel(_app: &AppHandle, job: u32) {
        let pid = RUNNING.lock().unwrap().iter().find(|&&(j, _)| j == job).map(|&(_, pid)| pid);
        if let Some(pid) = pid {
            let _ = Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"]).creation_flags(CREATE_NO_WINDOW).output();
        }
    }

    pub fn version(app: &AppHandle) -> Result<String, Failure> {
        let out = command(app)?.arg("--version").output().map_err(Failure::io)?;
        if !out.status.success() {
            return Err(Failure::io(String::from_utf8_lossy(&out.stderr)));
        }
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_owned())
    }

    /// `yt-dlp -U` replaces the copy in the data folder.
    pub fn update(app: &AppHandle) -> Result<(), Failure> {
        let out = command(app)?.arg("-U").output().map_err(Failure::io)?;
        if out.status.success() {
            return Ok(());
        }
        let text = String::from_utf8_lossy(&out.stderr);
        let detail = text.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or_default().trim();
        Err(Failure::new("errors.updateFailed", serde_json::json!({ "detail": detail })))
    }
}

#[cfg(target_os = "android")]
mod platform {
    use std::sync::Mutex;

    use tauri::AppHandle;
    use tauri::ipc::{Channel, InvokeResponseBody};
    use tauri_plugin_stash_android::StashExt;

    use super::Output;
    use crate::Failure;

    pub fn run(app: &AppHandle, args: Vec<String>, job: Option<u32>, on_line: impl FnMut(&str) + Send + 'static) -> Result<Output, Failure> {
        let on_line = Mutex::new(on_line);
        let lines = Channel::<serde_json::Value>::new(move |body| {
            if let InvokeResponseBody::Json(text) = body
                && let Ok(value) = serde_json::from_str::<serde_json::Value>(&text)
                && let Some(line) = value["line"].as_str()
            {
                (on_line.lock().unwrap())(line);
            }
            Ok(())
        });
        let out = app.stash().run(args, job.map(|j| j.to_string()), lines).map_err(Failure::io)?;
        Ok(Output { ok: out.code == 0, stdout: out.out, stderr: out.err })
    }

    pub fn cancel(app: &AppHandle, job: u32) {
        let _ = app.stash().cancel(job.to_string());
    }

    pub fn version(app: &AppHandle) -> Result<String, Failure> {
        app.stash().version().map_err(Failure::io)
    }

    pub fn update(app: &AppHandle) -> Result<(), Failure> {
        app.stash().update().map_err(|e| Failure::new("errors.updateFailed", serde_json::json!({ "detail": e.to_string() })))
    }
}

/// Folder for the info files and the downloads in progress, emptied at startup.
pub fn work_dir(app: &AppHandle) -> Result<PathBuf, Failure> {
    use tauri::Manager;
    let dir = app.path().app_cache_dir().map_err(Failure::io)?.join("work");
    fs::create_dir_all(&dir).map_err(Failure::io)?;
    Ok(dir)
}

/// One raw stream to download, and the tracks to take from it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Part {
    pub format_id: String,
    pub video: bool,
    pub audio: bool,
    pub size: Option<u64>,
}

/// A download option: a video height (or audio only) and the streams it needs.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Choice {
    pub height: Option<u32>,
    pub size: Option<u64>,
    /// Frames per second, when the site says.
    pub fps: Option<f64>,
    pub parts: Vec<Part>,
}

#[derive(Debug, Serialize)]
pub struct Media {
    pub url: String,
    pub title: String,
    pub author: Option<String>,
    pub duration: Option<f64>,
    pub thumbnail: Option<String>,
    /// Saved analysis, handed back to yt-dlp for the download.
    pub info: PathBuf,
    /// Best first.
    pub qualities: Vec<Choice>,
    pub audio: Option<Choice>,
    /// A TikTok photo post's photos; its sound is `audio`.
    pub photos: Vec<Photo>,
    /// A small MP4 a player can stream, to show the video before downloading it.
    pub preview: Option<String>,
    /// The parts SponsorBlock lists to skip (YouTube), in order.
    pub segments: Vec<Segment>,
}

/// A part SponsorBlock lists to skip, in seconds of the video.
#[derive(Debug, PartialEq, Serialize)]
pub struct Segment {
    pub start: f64,
    pub end: f64,
    pub category: String,
}

static NEXT_INFO: AtomicU32 = AtomicU32::new(0);

pub fn analyze(app: &AppHandle, url: &str) -> Result<Media, Failure> {
    let url = tiktok::resolve(url.trim())?;
    let (url, photo_link) = tiktok::normalize(&url);
    let mut sponsor_block = crate::saved_settings(app)?.sponsor_block;
    let mut attempt = 0;
    let out = loop {
        let mut args: Vec<String> = ["-J", "--no-playlist", "--flat-playlist"].map(String::from).to_vec();
        // YouTube's visionos client needs no JS challenge: about 5 s quicker than yt-dlp's own
        // clients with QuickJS. The next tries use those.
        if attempt == 0 {
            args.extend(["--extractor-args".into(), "youtube:player_client=visionos".into()]);
        }
        // Lists the parts to skip (`sponsorblock_chapters`); nothing is cut here.
        if sponsor_block {
            args.extend(["--sponsorblock-mark".into(), "all".into()]);
        }
        args.push(url.clone());
        let out = run(app, args, None, |_| {})?;
        if out.ok {
            break out;
        }
        // SponsorBlock's server failing: the video analyzes without it.
        if sponsor_block && out.stderr.contains("SponsorBlock") {
            sponsor_block = false;
            continue;
        }
        let failure = failure(&out.stderr);
        if attempt > 0 && !retry(attempt, &failure) {
            return Err(failure);
        }
        attempt += 1;
    };
    let info = first_entry(serde_json::from_str(&out.stdout).map_err(Failure::io)?);
    let path = work_dir(app)?.join(format!("info-{}.json", NEXT_INFO.fetch_add(1, Ordering::Relaxed)));
    fs::write(&path, info.to_string()).map_err(Failure::io)?;
    let photo_post = tiktok::is_tiktok(&url) && (photo_link || sound_only(&info));
    let mut media = parse(&info, path, photo_post)?;
    if photo_post {
        media.photos = tiktok::photos(&url, tiktok::saved_cookies(app).as_deref())?;
    }
    Ok(media)
}

/// The link a saved analysis was made from.
pub fn page_url(info: &std::path::Path) -> Option<String> {
    let info: Value = serde_json::from_str(&fs::read_to_string(info).ok()?).ok()?;
    info["webpage_url"].as_str().or(info["original_url"].as_str()).map(str::to_owned)
}

/// A post with several items (an Instagram carousel, a tweet with videos) comes as a
/// playlist of full entries: takes its first video, else its first item with sound.
/// A real playlist only lists links (`--flat-playlist`) and stays a playlist.
fn first_entry(info: Value) -> Value {
    if info["_type"] != "playlist" {
        return info;
    }
    let entries = info["entries"].as_array().map(Vec::as_slice).unwrap_or_default();
    let usable = |e: &&Value| choose(&formats(e), e["duration"].as_f64());
    let entry = entries.iter().find(|e| !usable(e).0.is_empty()).or_else(|| entries.iter().find(|e| usable(e).1.is_some()));
    entry.cloned().unwrap_or(info)
}

fn formats(info: &Value) -> Vec<Format> {
    info["formats"].as_array().into_iter().flatten().filter_map(|f| Format::deserialize(f).ok()).collect()
}

/// Every format is sound only: how a TikTok photo post shows in yt-dlp.
fn sound_only(info: &Value) -> bool {
    let formats = info["formats"].as_array().map(Vec::as_slice).unwrap_or_default();
    !formats.is_empty() && formats.iter().all(|f| f["vcodec"].as_str().is_none_or(|v| v == "none"))
}

#[derive(Deserialize)]
struct Format {
    format_id: String,
    url: Option<String>,
    ext: Option<String>,
    vcodec: Option<String>,
    acodec: Option<String>,
    width: Option<u32>,
    height: Option<u32>,
    fps: Option<f64>,
    filesize: Option<f64>,
    filesize_approx: Option<f64>,
    tbr: Option<f64>,
    protocol: Option<String>,
    language_preference: Option<i64>,
}

impl Format {
    /// A single file over HTTP.
    fn direct(&self) -> bool {
        matches!(self.protocol.as_deref(), Some("https" | "http"))
    }
    /// What yt-dlp can save without ffmpeg into something the muxer reads:
    /// one file, or HLS and DASH segments joined into one MP4 or MPEG-TS stream.
    fn readable(&self) -> bool {
        self.direct() || matches!(self.protocol.as_deref(), Some("m3u8_native" | "m3u8" | "http_dash_segments"))
    }
    /// H.264 in MP4. Sites that leave the codec out (Twitter, Instagram) serve H.264.
    fn h264(&self) -> bool {
        self.ext.as_deref() == Some("mp4") && self.vcodec.as_deref().is_none_or(|v| v.starts_with("avc") || v == "h264" || v == "h.264")
    }
    /// AAC, or a sound whose codec is left out.
    fn aac(&self) -> bool {
        self.acodec.as_deref().is_none_or(|a| a.starts_with("mp4a") || a == "aac")
    }
    fn video_only(&self) -> bool {
        self.readable() && self.h264() && self.acodec.as_deref() == Some("none")
    }
    fn progressive(&self) -> bool {
        self.readable() && self.h264() && self.aac()
    }
    /// Sound in any codec, alone or with a picture: FFmpeg converts it.
    fn any_sound(&self) -> bool {
        self.readable() && self.acodec.as_deref() != Some("none") && !self.format_id.ends_with("-drc")
    }
    fn audio_only(&self) -> bool {
        self.readable()
            && self.aac()
            && self.vcodec.as_deref() == Some("none")
            && matches!(self.ext.as_deref(), Some("m4a" | "mp4"))
            && !self.format_id.ends_with("-drc")
    }
    /// The side a "720p" names, whatever the orientation.
    fn quality(&self) -> Option<u32> {
        self.height.map(|h| self.width.map_or(h, |w| w.min(h)))
    }
    fn size(&self, duration: Option<f64>) -> Option<u64> {
        let rate = self.tbr.zip(duration).map(|(kbps, secs)| kbps * 125.0 * secs);
        self.filesize.or(self.filesize_approx).or(rate).map(|b| b as u64)
    }
    fn part(&self, duration: Option<f64>, video: bool, audio: bool) -> Part {
        Part { format_id: self.format_id.clone(), video, audio, size: self.size(duration) }
    }
}

const MAX_HEIGHT: u32 = 1080;

/// A single H.264 file a player streams: with its sound when there is one, about 360p.
fn preview(formats: &[Format]) -> Option<String> {
    let near_360 = |f: &&Format| {
        let q = f.quality().unwrap_or(0);
        if q >= 360 { (0, q) } else { (1, u32::MAX - q) }
    };
    let single = |f: &&Format| f.direct() && f.url.is_some();
    let with_sound = formats.iter().filter(|f| f.progressive()).filter(single).min_by_key(near_360);
    let silent = formats.iter().filter(|f| f.video_only()).filter(single).min_by_key(near_360);
    with_sound.or(silent).and_then(|f| f.url.clone())
}

fn choice(height: Option<u32>, fps: Option<f64>, parts: Vec<Part>) -> Choice {
    let size = parts.iter().map(|p| p.size).sum();
    Choice { height, size, fps, parts }
}

/// Single files first (one request, a known size), then the highest bitrate.
fn by_rank(a: &&Format, b: &&Format) -> std::cmp::Ordering {
    a.direct().cmp(&b.direct()).then(a.tbr.unwrap_or(0.0).total_cmp(&b.tbr.unwrap_or(0.0)))
}

/// The video choices (best first) and the audio-only choice, H.264 + AAC up to 1080p.
/// A height uses a video-only stream plus the best AAC stream when it can,
/// else a stream that already has both; a single file beats segmented streams.
fn choose(formats: &[Format], duration: Option<f64>) -> (Vec<Choice>, Option<Choice>) {
    let audio = formats
        .iter()
        .filter(|f| f.audio_only())
        .max_by_key(|f| (f.direct(), f.language_preference.unwrap_or(0), f.tbr.unwrap_or(0.0) as u64));

    let mut heights: Vec<u32> = formats
        .iter()
        .filter(|f| f.video_only() || f.progressive())
        .filter_map(Format::quality)
        .filter(|&h| h <= MAX_HEIGHT)
        .collect();
    heights.sort_unstable_by(|a, b| b.cmp(a));
    heights.dedup();

    let mut qualities: Vec<Choice> = heights
        .into_iter()
        .filter_map(|h| {
            let at = |f: &&Format| f.quality() == Some(h);
            let video = formats.iter().filter(|f| f.video_only()).filter(at).max_by(by_rank);
            let both = formats.iter().filter(|f| f.progressive()).filter(at).max_by(by_rank);
            let separate_files = video.is_some_and(Format::direct) && audio.is_some_and(Format::direct);
            let (picture, parts) = match (video, audio, both) {
                (_, _, Some(b)) if b.direct() && !separate_files => (b, vec![b.part(duration, true, true)]),
                (Some(v), Some(a), _) => (v, vec![v.part(duration, true, false), a.part(duration, false, true)]),
                (_, _, Some(b)) => (b, vec![b.part(duration, true, true)]),
                (Some(v), None, None) => (v, vec![v.part(duration, true, false)]),
                _ => return None,
            };
            Some(choice(Some(h), picture.fps.filter(|&f| f > 0.0), parts))
        })
        .collect();
    // A link straight to a file: no height nor codecs, only an MP4.
    if qualities.is_empty()
        && let Some(file) = formats.iter().filter(|f| f.progressive() && f.quality().is_none()).max_by(by_rank)
    {
        qualities.push(choice(None, file.fps.filter(|&f| f > 0.0), vec![file.part(duration, true, true)]));
    }

    // The sound alone: AAC, else a sound-only stream in another codec, else the smallest
    // file with a picture, H.264 + AAC first.
    let sound_only = |f: &&Format| f.any_sound() && f.vcodec.as_deref() == Some("none");
    let with_picture = |f: &&Format| f.any_sound() && f.acodec.is_some() && f.vcodec.as_deref() != Some("none");
    let smallest = |a: &&Format, b: &&Format| (!a.direct(), a.size(duration).unwrap_or(u64::MAX)).cmp(&(!b.direct(), b.size(duration).unwrap_or(u64::MAX)));
    let audio = audio
        .or_else(|| formats.iter().filter(sound_only).max_by(by_rank))
        .or_else(|| formats.iter().filter(|f| f.progressive()).min_by(smallest))
        .or_else(|| formats.iter().filter(with_picture).min_by(smallest))
        .map(|f| choice(None, None, vec![f.part(duration, false, true)]));
    (qualities, audio)
}

/// `photo_post`: the sound may be any direct audio stream (TikTok gives MP3), re-encoded later.
fn parse(info: &Value, path: PathBuf, photo_post: bool) -> Result<Media, Failure> {
    if info["_type"] == "playlist" {
        return Err(Failure::plain("errors.playlist"));
    }
    if info["is_live"] == true {
        return Err(Failure::plain("errors.live"));
    }
    let text = |key: &str| info[key].as_str().filter(|s| !s.is_empty()).map(str::to_owned);
    let duration = info["duration"].as_f64();
    let formats = formats(info);
    let (mut qualities, mut audio) = choose(&formats, duration);
    if photo_post {
        let sound = formats.iter().filter(|f| f.direct() && f.vcodec.as_deref().is_none_or(|v| v == "none")).max_by(by_rank);
        qualities.clear();
        audio = audio.or_else(|| sound.map(|f| choice(None, None, vec![f.part(duration, false, true)])));
    }
    if qualities.is_empty() && audio.is_none() {
        return Err(Failure::plain("errors.noStream"));
    }
    Ok(Media {
        url: text("webpage_url").or_else(|| text("original_url")).unwrap_or_default(),
        title: text("title").unwrap_or_else(|| "video".into()),
        author: text("uploader").or_else(|| text("channel")),
        duration,
        thumbnail: text("thumbnail"),
        info: path,
        preview: if photo_post { None } else { preview(&formats) },
        qualities,
        audio,
        photos: Vec::new(),
        segments: segments(info),
    })
}

/// The parts SponsorBlock says to skip; its highlight and chapter marks are left out.
fn segments(info: &Value) -> Vec<Segment> {
    let chapters = info["sponsorblock_chapters"].as_array().into_iter().flatten();
    chapters
        .filter(|c| c["type"] == "skip")
        .filter_map(|c| Some(Segment { start: c["start_time"].as_f64()?, end: c["end_time"].as_f64()?, category: c["category"].as_str()?.to_owned() }))
        .collect()
}

/// A Netscape cookies.txt: its header, or at least one line with the 7 tab-separated fields.
pub fn is_cookie_file(text: &str) -> bool {
    text.lines().any(|line| {
        line.starts_with("# Netscape HTTP Cookie File")
            || line.starts_with("# HTTP Cookie File")
            || (!line.starts_with('#') && line.split('\t').count() == 7)
    })
}

/// Turns yt-dlp's stderr into a message key, with a hint when the cause is known.
pub fn failure(stderr: &str) -> Failure {
    let text = stderr.to_lowercase();
    let has = |words: &[&str]| words.iter().any(|w| text.contains(w));
    let key = if has(&["private video", "video is private", "account is private"]) {
        "errors.private"
    } else if has(&["not a bot", "rate-limited", "http error 429", "too many requests", "ip address is blocked", "ip may be blocked"]) {
        "errors.blocked"
    } else if has(&["sign in", "log in", "login", "cookies", "empty media response"]) {
        "errors.login"
    } else if has(&["unsupported url", "is not a valid url"]) {
        "errors.unsupported"
    } else if has(&["unable to extract", "unexpected response", "nsig", "signature"]) {
        "errors.update"
    } else {
        let mut lines = stderr.lines().rev();
        let line = lines.clone().find(|l| l.contains("ERROR:")).or_else(|| lines.find(|l| !l.trim().is_empty())).unwrap_or_default();
        let detail = line.split_once("ERROR:").map_or(line, |(_, rest)| rest).split("; please report").next().unwrap_or_default();
        return Failure::new("errors.ytdlp", json!({ "detail": detail.trim() }));
    };
    Failure::plain(key)
}

/// Runs after the first one when yt-dlp fails.
const RETRIES: u32 = 2;

/// After failed try `attempt` (from 0), waits a little and says whether to try again: not
/// after the last try, nor when the link itself is the cause (private, login, unsupported).
pub fn retry(attempt: u32, failure: &Failure) -> bool {
    let again = attempt < RETRIES && !matches!(failure.key, "errors.private" | "errors.login" | "errors.unsupported");
    if again {
        std::thread::sleep(std::time::Duration::from_secs(2));
    }
    again
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> Value {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name);
        serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
    }

    fn ids(choice: &Choice) -> Vec<&str> {
        choice.parts.iter().map(|p| p.format_id.as_str()).collect()
    }

    #[test]
    fn youtube_streams() {
        let media = parse(&fixture("youtube.json"), "info.json".into(), false).unwrap();
        assert_eq!(media.title, "Me at the zoo");
        assert_eq!(media.author.as_deref(), Some("jawed"));
        let heights: Vec<_> = media.qualities.iter().map(|q| q.height).collect();
        assert_eq!(heights, [Some(240), Some(144)]);
        // Highest bitrate H.264 over HTTPS, plain AAC (not -drc, not m3u8).
        assert_eq!(ids(&media.qualities[0]), ["133", "140"]);
        assert_eq!(ids(&media.qualities[1]), ["160", "140"]);
        assert_eq!(media.qualities[0].size, Some(433_081 + 309_288));
        assert_eq!(ids(media.audio.as_ref().unwrap()), ["140"]);
    }

    #[test]
    fn sponsor_block_segments() {
        let mut info = fixture("youtube.json");
        assert!(parse(&info, "info.json".into(), false).unwrap().segments.is_empty());
        info["sponsorblock_chapters"] = json!([
            { "start_time": 10.0, "end_time": 20.5, "category": "sponsor", "title": "Sponsor", "type": "skip" },
            { "start_time": 30.0, "end_time": 30.0, "category": "poi_highlight", "title": "Highlight", "type": "poi" },
        ]);
        let media = parse(&info, "info.json".into(), false).unwrap();
        assert_eq!(media.segments, [Segment { start: 10.0, end: 20.5, category: "sponsor".into() }]);
    }

    #[test]
    fn progressive_only_and_limits() {
        let f = |id: &str, h: u32, size: f64| {
            json!({ "format_id": id, "ext": "mp4", "vcodec": "h264", "acodec": "mp4a.40.2", "height": h, "filesize": size, "protocol": "https" })
        };
        let info = json!({ "title": "t", "duration": 10.0, "formats": [
            f("a", 2160, 9.0), f("b", 720, 5.0), f("c", 480, 3.0),
            { "format_id": "hevc", "ext": "mp4", "vcodec": "hvc1", "acodec": "mp4a.40.2", "height": 1080, "protocol": "https" },
            { "format_id": "hls", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 1080, "protocol": "m3u8_native" },
        ]});
        let media = parse(&info, "i".into(), false).unwrap();
        // HLS counts too, but not HEVC nor more than 1080p.
        let heights: Vec<_> = media.qualities.iter().map(|q| q.height).collect();
        assert_eq!(heights, [Some(1080), Some(720), Some(480)]);
        assert_eq!(ids(&media.qualities[0]), ["hls"]);
        assert_eq!(media.qualities[1].parts, [Part { format_id: "b".into(), video: true, audio: true, size: Some(5) }]);
        // Audio only comes from the smallest file that has it.
        assert_eq!(media.audio.unwrap().parts, [Part { format_id: "c".into(), video: false, audio: true, size: Some(3) }]);
    }

    #[test]
    fn previews() {
        let format = |id: &str, vcodec: &str, acodec: &str, height: u32, protocol: &str| {
            json!({ "format_id": id, "url": format!("https://cdn/{id}"), "ext": "mp4", "vcodec": vcodec, "acodec": acodec, "height": height, "protocol": protocol })
        };
        let preview_of = |formats: Vec<Value>| parse(&json!({ "formats": formats }), "i".into(), false).unwrap().preview;
        // A single file with sound, the one nearest above 360p; segmented streams don't play.
        assert_eq!(
            preview_of(vec![
                format("18", "avc1", "mp4a", 240, "https"),
                format("22", "avc1", "mp4a", 720, "https"),
                format("hls", "avc1", "mp4a", 360, "m3u8_native"),
                format("v", "avc1", "none", 360, "https"),
            ]),
            Some("https://cdn/22".into())
        );
        // Else a picture alone; else nothing.
        assert_eq!(preview_of(vec![format("v", "avc1", "none", 1080, "https"), format("w", "avc1", "none", 480, "https")]), Some("https://cdn/w".into()));
        assert_eq!(preview_of(vec![format("hls", "avc1", "mp4a", 360, "m3u8_native")]), None);
    }

    #[test]
    fn size_from_bitrate_and_nothing_usable() {
        let info = json!({ "title": "t", "duration": 8.0, "formats": [
            { "format_id": "v", "ext": "mp4", "vcodec": "avc1.64", "acodec": "none", "height": 360, "tbr": 1000.0, "protocol": "https" },
        ]});
        let media = parse(&info, "i".into(), false).unwrap();
        assert_eq!(media.qualities[0].size, Some(1_000_000));
        assert!(media.audio.is_none());

        // Only WebM: no video to copy, but its sound converts.
        let webm = json!({ "formats": [{ "format_id": "w", "ext": "webm", "vcodec": "vp9", "acodec": "opus", "protocol": "https" }] });
        let media = parse(&webm, "i".into(), false).unwrap();
        assert!(media.qualities.is_empty());
        assert_eq!(media.audio.unwrap().parts[0].format_id, "w");
        // SoundCloud: MP3 and Opus only; the single file wins.
        let songs = json!({ "formats": [
            { "format_id": "hls_opus", "ext": "opus", "vcodec": "none", "acodec": "opus", "tbr": 64.0, "protocol": "m3u8_native" },
            { "format_id": "http_mp3", "ext": "mp3", "vcodec": "none", "acodec": "mp3", "tbr": 128.0, "protocol": "https" },
        ]});
        assert_eq!(parse(&songs, "i".into(), false).unwrap().audio.unwrap().parts[0].format_id, "http_mp3");
        // A video in H.264 with its sound in Opus only: the Opus stream for "sound only".
        let split = json!({ "formats": [
            { "format_id": "v", "ext": "mp4", "vcodec": "avc1.64", "acodec": "none", "height": 720, "protocol": "https" },
            { "format_id": "a", "ext": "webm", "vcodec": "none", "acodec": "opus", "protocol": "https" },
        ]});
        assert_eq!(parse(&split, "i".into(), false).unwrap().audio.unwrap().parts[0].format_id, "a");
        let none = json!({ "formats": [{ "format_id": "w", "ext": "webm", "vcodec": "vp9", "acodec": "none", "protocol": "https" }] });
        assert_eq!(parse(&none, "i".into(), false).unwrap_err().key, "errors.noStream");
        assert_eq!(parse(&json!({ "_type": "playlist" }), "i".into(), false).unwrap_err().key, "errors.playlist");
    }

    #[test]
    fn codecs_left_out() {
        // Twitter: progressive files without codecs, and HLS streams that are separate.
        let tweet = json!({ "title": "t", "duration": 10.0, "formats": [
            { "format_id": "hls-audio", "ext": "mp4", "vcodec": "none", "protocol": "m3u8_native", "tbr": 64.0 },
            { "format_id": "hls-348", "ext": "mp4", "vcodec": "avc1.42C01E", "acodec": "none", "height": 360, "tbr": 348.0, "protocol": "m3u8_native" },
            { "format_id": "hls-2000", "ext": "mp4", "vcodec": "avc1.64", "acodec": "none", "height": 720, "tbr": 2000.0, "protocol": "m3u8_native" },
            { "format_id": "http-832", "ext": "mp4", "height": 360, "tbr": 832.0, "protocol": "https" },
        ]});
        let media = parse(&tweet, "i".into(), false).unwrap();
        assert_eq!(ids(&media.qualities[0]), ["hls-2000", "hls-audio"]);
        assert_eq!(media.qualities[1].parts, [Part { format_id: "http-832".into(), video: true, audio: true, size: Some(1_040_000) }]);
        assert_eq!(ids(media.audio.as_ref().unwrap()), ["hls-audio"]);

        // Instagram: a silent reel whose plain files say neither codec nor height.
        let reel = json!({ "title": "t", "formats": [
            { "format_id": "0", "ext": "mp4", "acodec": "none", "protocol": "https" },
            { "format_id": "dash-v", "ext": "mp4", "vcodec": "avc1.64001F", "acodec": "none", "height": 1280, "width": 720, "protocol": "https" },
            { "format_id": "dash-s", "ext": "mp4", "vcodec": "avc1.64001F", "acodec": "none", "height": 640, "width": 360, "protocol": "https" },
        ]});
        let heights: Vec<_> = parse(&reel, "i".into(), false).unwrap().qualities.iter().map(|q| q.height).collect();
        assert_eq!(heights, [Some(720), Some(360)]);
        assert_eq!(parse(&json!({ "is_live": true, "formats": [] }), "i".into(), false).unwrap_err().key, "errors.live");

        // TikTok names its codecs plainly; portrait 576 × 1024 is "576p".
        let tiktok = json!({ "title": "t", "formats": [
            { "format_id": "h264", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "width": 576, "height": 1024, "protocol": "https" },
            { "format_id": "hevc", "ext": "mp4", "vcodec": "h265", "acodec": "aac", "width": 576, "height": 1024, "protocol": "https" },
        ]});
        let media = parse(&tiktok, "i".into(), false).unwrap();
        assert_eq!((media.qualities[0].height, ids(&media.qualities[0])), (Some(576), vec!["h264"]));
    }

    #[test]
    fn a_link_to_a_file() {
        // What yt-dlp knows of https://example.com/clip.mp4: its kind, nothing else.
        let info = json!({ "title": "clip", "formats": [{ "format_id": "mp4", "ext": "mp4", "url": "https://example.com/clip.mp4", "protocol": "https" }] });
        let media = parse(&info, "i".into(), false).unwrap();
        let both = Part { format_id: "mp4".into(), video: true, audio: true, size: None };
        assert_eq!((media.qualities.len(), media.qualities[0].height, &media.qualities[0].parts), (1, None, &vec![both]));
        assert_eq!(media.audio.unwrap().parts[0].format_id, "mp4");
        assert_eq!(media.preview.as_deref(), Some("https://example.com/clip.mp4"));
    }

    #[test]
    fn posts_with_several_items() {
        let photo = json!({ "title": "photo", "formats": [{ "format_id": "jpg", "ext": "jpg", "protocol": "https" }] });
        let video = json!({ "title": "clip", "formats": [{ "format_id": "v", "ext": "mp4", "height": 640, "protocol": "https" }] });
        let carousel = json!({ "_type": "playlist", "entries": [photo, video] });
        assert_eq!(first_entry(carousel)["title"], "clip");
        // A real playlist: links only.
        let playlist = json!({ "_type": "playlist", "entries": [{ "_type": "url", "url": "https://youtu.be/x" }] });
        let kept = first_entry(playlist);
        assert_eq!(parse(&kept, "i".into(), false).unwrap_err().key, "errors.playlist");
    }

    #[test]
    fn tiktok_photo_post() {
        let info = fixture("tiktok-photo.json");
        assert!(sound_only(&info));
        assert!(!sound_only(&fixture("youtube.json")) && !sound_only(&json!({ "formats": [] })));
        // Its MP3 is the sound, as a plain link or as a photo post.
        assert_eq!(parse(&info, "i".into(), false).unwrap().audio.unwrap().parts[0].format_id, "audio");
        let media = parse(&info, "i".into(), true).unwrap();
        assert!(media.qualities.is_empty());
        assert_eq!(media.audio.unwrap().parts[0].format_id, "audio");
        assert_eq!(media.duration, Some(8.0));
    }

    #[test]
    fn cookie_files() {
        assert!(is_cookie_file("# Netscape HTTP Cookie File\n"));
        assert!(is_cookie_file(".youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=8"));
        assert!(!is_cookie_file("{\"cookies\": []}"));
        assert!(!is_cookie_file("# just a comment\twith\ttabs\ta\tb\tc\td"));
    }

    #[test]
    fn error_hints() {
        let key = |s: &str| failure(s).key;
        assert_eq!(key("ERROR: [youtube] x: Private video. Sign in if you've been granted access"), "errors.private");
        assert_eq!(key("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies"), "errors.blocked");
        assert_eq!(key("ERROR: [youtube] x: HTTP Error 429: Too Many Requests"), "errors.blocked");
        assert_eq!(key("ERROR: [TikTok] x: Your IP address is blocked from accessing this post"), "errors.blocked");
        let vimeo = "ERROR: [vimeo] x: Got HTTP Error 403 when using impersonate target. If you are using a data center IP or VPN/proxy, your IP may be blocked";
        assert_eq!(key(vimeo), "errors.blocked");
        assert_eq!(key("ERROR: [instagram] x: Requested content is not available, rate-limit reached or login required"), "errors.login");
        assert_eq!(key("ERROR: Unsupported URL: https://example.com/"), "errors.unsupported");
        assert_eq!(key("ERROR: 'abc' is not a valid URL."), "errors.unsupported");
        assert_eq!(key("ERROR: [tiktok] x: Unable to extract webpage video data; please report this issue"), "errors.update");
        let other = failure("WARNING: w\nERROR: [generic] x: Video unavailable; please report this issue on GitHub");
        assert_eq!(other.key, "errors.ytdlp");
        assert_eq!(other.params["detail"], "[generic] x: Video unavailable");
        let crash = failure("Traceback (most recent call last):\n  File \"x.py\"\nFileNotFoundError: no such file\n\n");
        assert_eq!(crash.params["detail"], "FileNotFoundError: no such file");
    }

    #[test]
    fn no_retry_when_the_link_is_the_cause() {
        // Only the cases that answer at once: a retry waits.
        assert!(!retry(0, &failure("ERROR: [youtube] x: Private video")));
        assert!(!retry(0, &failure("ERROR: [youtube] x: Sign in to confirm your age")));
        assert!(!retry(0, &failure("ERROR: Unsupported URL: https://example.com")));
        assert!(!retry(RETRIES, &failure("ERROR: unable to download video data: HTTP Error 403")));
    }
}
