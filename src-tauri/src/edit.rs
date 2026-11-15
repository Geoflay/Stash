//! Edit options: whether a job can stay a plain mux or needs a re-encode,
//! the bitrates for a size limit, and the encode-check-retry loop.

use std::fs;
use std::path::Path;

use serde::Deserialize;
use serde_json::json;

use crate::Failure;
use crate::media::{AudioCodec, Container, Preset, Probe, Settings, Transcoder, VideoCodec};
use crate::tiktok::Photo;

/// The share of a size limit aimed at: container overhead and encoder drift take the rest.
const MARGIN: f64 = 0.99;
/// A result under this share of its limit gets one larger try.
const FULL: f64 = 0.97;
/// With a size limit, the picture never gets more than this times the source's bitrate.
const MAX_GROWTH: f64 = 2.0;
/// The lowest video bitrate for a size limit: a limit that needs less is missed, not refused.
const MIN_VIDEO_KBPS: f64 = 30.0;
/// The sound bitrate without a size limit.
pub const AUDIO_KBPS: u32 = 128;
/// The lowest sound bitrate.
const MIN_AUDIO_KBPS: f64 = 32.0;
/// Short side worth keeping for a video bitrate: fewer pixels look better than blocky ones
/// (the old Stash's steps, never under 240p).
const SIDE_FOR_KBPS: [(f64, u32); 4] = [(2500.0, 1080), (1200.0, 720), (700.0, 480), (350.0, 360)];
const MIN_SIDE: u32 = 240;
/// Encodes after the first one when the result is still too big.
const MAX_RETRIES: usize = 2;

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct Options {
    /// Seconds; `None` from the beginning.
    pub start: Option<f64>,
    /// Seconds; `None` to the end.
    pub end: Option<f64>,
    pub mute: bool,
    /// 0.1 to 5, pitch kept.
    pub speed: f64,
    /// Size limit in megabytes (10^6 bytes, so the file also fits a limit counted in MiB).
    pub max_mb: Option<f64>,
    /// Lower short side, as in 720p (local files).
    pub height: Option<u32>,
    /// A video file, or a sound-only one (M4A, MP3…).
    pub format: Container,
    /// The sound of a video file; a sound-only file's kind sets its codec.
    pub audio_codec: AudioCodec,
    /// A lower frame rate.
    pub fps: Option<f64>,
    /// An edit: a copy that needs no re-encode keeps the file's own codecs.
    pub keep_codecs: bool,
    /// Parts removed (SponsorBlock), in source seconds, in order and apart.
    pub cuts: Vec<(f64, f64)>,
    /// The encoders' preset: from the settings, not the request.
    #[serde(skip)]
    pub preset: Preset,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            start: None,
            end: None,
            mute: false,
            speed: 1.0,
            max_mb: None,
            height: None,
            format: Container::Mp4,
            audio_codec: AudioCodec::Aac,
            fps: None,
            keep_codecs: false,
            cuts: Vec::new(),
            preset: Preset::default(),
        }
    }
}

/// The file saved and its codecs.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Output {
    pub container: Container,
    pub video: VideoCodec,
    pub audio: AudioCodec,
}

impl Options {
    /// Whether the result keeps the picture and the sound of `probe`.
    pub fn keeps(&self, probe: &Probe) -> (bool, bool) {
        let video = probe.video.is_some() && self.format.sound().is_none();
        (video, probe.audio && !(self.mute && video))
    }

    /// The file saved for `probe`: the chosen one, or with `keep_codecs` the source's codecs
    /// when nothing else needs a re-encode and the file can hold them.
    pub fn output(&self, probe: &Probe) -> Output {
        let chosen = self.chosen(probe);
        if self.keep_codecs && !self.reshapes(probe) {
            return self.source_codecs(chosen, probe).unwrap_or(chosen);
        }
        chosen
    }

    /// The source's codecs in the `out` file, if it is a video file that holds them.
    fn source_codecs(&self, out: Output, probe: &Probe) -> Option<Output> {
        if out.container.sound().is_some() {
            return None;
        }
        let (video, audio) = self.keeps(probe);
        let held = |codecs: &[VideoCodec], codec: Option<VideoCodec>| codec.filter(|c| codecs.contains(c));
        let video = if video { held(out.container.video_codecs(), probe.video?.codec)? } else { out.video };
        let audio = if audio { probe.audio_codec.filter(|c| out.container.audio_codecs().contains(c))? } else { out.audio };
        Some(Output { video, audio, ..out })
    }

    /// The file chosen for `probe`, its picture in H.264: a source without a picture gives a
    /// sound-only file, and a sound codec the file cannot hold falls back to its usual one.
    fn chosen(&self, probe: &Probe) -> Output {
        let (video, audio) = (VideoCodec::H264, self.audio_codec);
        match self.format.sound() {
            Some(sound) => Output { container: self.format, video, audio: sound },
            None if probe.video.is_none() => Output { container: Container::for_sound(audio), video, audio },
            None => Output { container: self.format, video, audio: held(self.format.audio_codecs(), audio) },
        }
    }

    /// The lower frame rate asked for, when under the source's.
    fn rate(&self, probe: &Probe) -> Option<f64> {
        self.fps.filter(|&fps| fps > 0.0 && probe.video.is_some_and(|v| fps < v.fps - 0.01))
    }
    /// The kept range in source seconds.
    pub fn range(&self, duration: f64) -> (f64, f64) {
        let start = self.start.unwrap_or(0.0).clamp(0.0, duration);
        let end = self.end.unwrap_or(duration).clamp(start, duration);
        (start, end)
    }

    /// The parts removed inside the kept `range`.
    pub fn cuts(&self, (start, end): (f64, f64)) -> Vec<(f64, f64)> {
        self.cuts.iter().map(|&(a, b)| (a.max(start), b.min(end))).filter(|(a, b)| b - a > 0.01).collect()
    }

    pub fn speed(&self) -> f64 {
        self.speed.clamp(0.1, 5.0)
    }

    /// No limit with FLAC or WAV sound: their size can't be chosen. (A file too big is
    /// re-encoded in the chosen codecs, so they decide.)
    pub fn target_bytes(&self, probe: &Probe) -> Option<u64> {
        let lossless = self.chosen(probe).audio.lossless();
        self.max_mb.filter(|&mb| mb > 0.0 && !lossless).map(|mb| (mb * 1e6) as u64)
    }

    /// Trimming, a speed or frame rate change, a lower height or another codec can't be
    /// done by remuxing. (Removing the sound alone is a remux without the audio track,
    /// SponsorBlock's cuts alone one cut at keyframes; a size limit re-encodes only when the
    /// remuxed file is too big.)
    pub fn needs_encode(&self, probe: &Probe) -> bool {
        let (video, audio) = self.keeps(probe);
        let out = self.output(probe);
        let picture = video && probe.video.is_some_and(|v| v.codec != Some(out.video));
        let sound = audio && probe.audio_codec != Some(out.audio);
        self.reshapes(probe) || picture || sound
    }

    /// Trimming to a start or an end, a speed or frame rate change or a lower height: a
    /// re-encode whatever the codecs, exact to the frame.
    fn reshapes(&self, probe: &Probe) -> bool {
        let (start, end) = self.range(probe.duration);
        let trims = start > 0.001 || end < probe.duration - 0.001;
        let (video, _) = self.keeps(probe);
        let lower = self.height.is_some_and(|h| probe.video.is_some_and(|v| video && h < v.short_side()));
        let slower = video && self.rate(probe).is_some();
        trims || (self.speed() - 1.0).abs() > 0.001 || lower || slower
    }
}

/// `codec` when it is in `codecs`, else the first one.
fn held<T: Copy + PartialEq>(codecs: &[T], codec: T) -> T {
    if codecs.contains(&codec) { codec } else { codecs[0] }
}

/// Reads "m:ss", "h:mm:ss" or plain seconds, with optional decimals ("1:02.5" or "1:02,5").
pub fn parse_time(text: &str) -> Option<f64> {
    let fields: Vec<&str> = text.trim().split(':').collect();
    if fields.len() > 3 {
        return None;
    }
    let mut total = 0.0;
    for (i, field) in fields.iter().enumerate() {
        let value: f64 = if i + 1 == fields.len() {
            field.replace(',', ".").parse().ok()?
        } else {
            field.parse::<u32>().ok()?.into()
        };
        if !value.is_finite() || value < 0.0 || (i > 0 && value >= 60.0) {
            return None;
        }
        total = total * 60.0 + value;
    }
    Some(total)
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bitrates {
    pub video_kbps: u32,
    pub audio_kbps: u32,
}

impl Bitrates {
    fn total(self) -> u32 {
        self.video_kbps + self.audio_kbps
    }

    fn min(self, other: Bitrates) -> Bitrates {
        Bitrates { video_kbps: self.video_kbps.min(other.video_kbps), audio_kbps: self.audio_kbps.min(other.audio_kbps) }
    }
}

/// Bitrates for at most `target` bytes over `seconds`: size × 8 ÷ duration, with a margin.
pub fn fit(target: u64, seconds: f64, video: bool, audio: bool) -> Bitrates {
    split(target as f64 * 8.0 / seconds.max(0.1) / 1000.0 * MARGIN, video, audio)
}

/// Shares a total bitrate out: less sound when it is tight, the rest for the picture,
/// never under the lowest bitrates.
fn split(total: f64, video: bool, audio: bool) -> Bitrates {
    let sound = match total {
        _ if !video => total.clamp(MIN_AUDIO_KBPS, AUDIO_KBPS as f64),
        t if t >= 1000.0 => 128.0,
        t if t >= 400.0 => 96.0,
        t if t >= 150.0 => 64.0,
        _ => MIN_AUDIO_KBPS,
    };
    let audio_kbps = if audio { sound as u32 } else { 0 };
    let video_kbps = if video { (total - audio_kbps as f64).max(MIN_VIDEO_KBPS) } else { 0.0 };
    Bitrates { video_kbps: video_kbps as u32, audio_kbps }
}

/// The short side for an H.264 bitrate.
fn side_for(video_kbps: u32) -> u32 {
    SIDE_FOR_KBPS.iter().find(|(kbps, _)| video_kbps as f64 >= *kbps).map_or(MIN_SIDE, |&(_, side)| side)
}

/// Bitrate of a video made from photos: still pictures need little.
pub const SLIDE_KBPS: u32 = 2000;

/// 1080 × 1920 when the first photo is portrait, 1920 × 1080 otherwise.
pub fn slide_size(first: &Photo) -> (u32, u32) {
    if first.height > first.width { (1080, 1920) } else { (1920, 1080) }
}

/// Re-encodes `input` into `output` with the options; `work` is the job folder. With a
/// size limit, checks the result: one too big is tried again lower, at most twice, and
/// one well under the limit once higher, kept only if it fits. A file still too big is
/// kept, with a note saying so.
pub fn encode(
    transcoder: &impl Transcoder,
    input: &Path,
    output: &Path,
    options: &Options,
    probe: &Probe,
    work: &Path,
    progress: &mut dyn FnMut(f64),
) -> Result<Option<Failure>, Failure> {
    let (start, end) = options.range(probe.duration);
    let cuts = options.cuts((start, end));
    let kept = end - start - cuts.iter().map(|(a, b)| b - a).sum::<f64>();
    let speed = options.speed();
    let (keep_video, audio) = options.keeps(probe);
    let source = probe.video.filter(|_| keep_video);
    let out = options.output(probe);
    let chosen = options.height.filter(|&h| source.is_some_and(|v| h < v.short_side()));
    let target = options.target_bytes(probe);
    let sound_kbps = if audio { AUDIO_KBPS } else { 0 };

    // Without a limit: constant quality, capped a little over the source's bitrate.
    let scale = match (chosen, source) {
        (Some(h), Some(v)) => (h as f64 / v.short_side() as f64).powi(2),
        _ => 1.0,
    };
    let video_kbps = source.map_or(0.0, |v| (v.kbps * 1.2 * scale).max(300.0));
    let unlimited = Bitrates { video_kbps: video_kbps as u32, audio_kbps: sound_kbps };
    // A size limit fills the space it gives, up to twice the source's bitrate (unknown: no cap).
    let ceiling = Bitrates {
        video_kbps: source.filter(|v| v.kbps > 0.0).map_or(u32::MAX, |v| (v.kbps * MAX_GROWTH).max(300.0) as u32),
        audio_kbps: sound_kbps,
    };
    let mut rates = match target {
        Some(target) => fit(target, kept / speed, source.is_some(), audio).min(ceiling),
        None => unlimited,
    };
    // Set once a result fits: the larger try goes here, so the one that fits stays.
    let mut larger: Option<std::path::PathBuf> = None;
    for attempt in 0..=MAX_RETRIES {
        // A size limit also lowers the picture size to what the bitrate can carry.
        let fitted = target.map(|_| side_for(rates.video_kbps));
        let height = [chosen, fitted].into_iter().flatten().min().filter(|&h| source.is_some_and(|v| h < v.short_side()));
        let settings = Settings {
            start,
            end: (end < probe.duration - 0.001).then_some(end),
            cuts: cuts.clone(),
            speed,
            video: source.is_some(),
            audio,
            height,
            fps: source.map_or(30.0, |v| v.fps),
            rate: options.rate(probe).filter(|_| source.is_some()),
            container: out.container,
            audio_codec: out.audio,
            video_kbps: rates.video_kbps,
            audio_kbps: rates.audio_kbps,
            exact: target.is_some(),
            stats: work.join("pass.log"),
            preset: options.preset,
            draft: false,
        };
        let file = larger.as_deref().unwrap_or(output);
        transcoder.transcode(input, file, &settings, progress)?;
        let Some(target) = target else { return Ok(None) };
        let size = fs::metadata(file).map_err(Failure::io)?.len();
        if larger.is_some() {
            if size <= target {
                crate::move_file(file, output).map_err(Failure::io)?;
            }
            let _ = fs::remove_file(file);
            return Ok(None);
        }
        let keep = attempt < MAX_RETRIES;
        // The bitrates scaled by how far the result was from `share` of the limit.
        let scaled = |share: f64| split(rates.total() as f64 * target as f64 * share / size as f64, source.is_some(), audio).min(ceiling);
        if size <= target {
            // Too much of the limit left unused: one larger try, if the bitrates can grow.
            let next = scaled(MARGIN);
            if !keep || size as f64 >= target as f64 * FULL || next.total() as f64 <= rates.total() as f64 * 1.01 {
                return Ok(None);
            }
            rates = next;
            larger = Some(work.join(format!("larger.{}", out.container.ext())));
            continue;
        }
        // Too big: lower, unless they are as low as they go.
        let next = scaled(0.97);
        if !keep || next.total() >= rates.total() {
            return Ok(Some(Failure::new("jobs.overSize", json!({ "sizeMb": (size as f64 / 1e4).ceil() / 100.0 }))));
        }
        rates = next;
    }
    unreachable!("the last attempt returns")
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::path::PathBuf;

    use super::*;
    use crate::media::VideoProbe;

    const SIDE_1080: VideoProbe = VideoProbe { width: 1920, height: 1080, fps: 30.0, kbps: 4000.0, codec: Some(VideoCodec::H264) };
    const VIDEO: Probe = Probe { duration: 60.0, video: Some(SIDE_1080), audio: true, audio_codec: Some(AudioCodec::Aac) };

    #[test]
    fn mux_or_encode() {
        let o = Options::default();
        assert!(!o.needs_encode(&VIDEO));
        assert!(!Options { mute: true, ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { max_mb: Some(10.0), ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { start: Some(0.0), end: Some(60.0), ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { height: Some(1080), ..o.clone() }.needs_encode(&VIDEO));
        assert!(Options { start: Some(5.0), ..o.clone() }.needs_encode(&VIDEO));
        assert!(Options { end: Some(30.0), ..o.clone() }.needs_encode(&VIDEO));
        // SponsorBlock's cuts alone are copied out at keyframes; with a trim they re-encode.
        assert!(!Options { cuts: vec![(10.0, 20.0)], ..o.clone() }.needs_encode(&VIDEO));
        assert!(Options { cuts: vec![(10.0, 20.0)], start: Some(5.0), ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { cuts: vec![(60.0, 70.0)], ..o.clone() }.needs_encode(&VIDEO));
        assert_eq!(Options { start: Some(15.0), cuts: vec![(10.0, 20.0), (30.0, 40.0)], ..o.clone() }.cuts((15.0, 35.0)), [(15.0, 20.0), (30.0, 35.0)]);
        assert!(Options { speed: 1.5, ..o.clone() }.needs_encode(&VIDEO));
        assert!(Options { height: Some(720), ..o.clone() }.needs_encode(&VIDEO));
        // 720p of a portrait 1080 × 1920 video is 720 wide.
        let portrait = Probe { video: Some(VideoProbe { width: 1080, height: 1920, ..SIDE_1080 }), ..VIDEO };
        assert!(!Options { height: Some(1080), ..o.clone() }.needs_encode(&portrait));
        assert!(Options { height: Some(720), ..o.clone() }.needs_encode(&portrait));
        // Another kind of file holding the same codecs is a remux; another codec is not.
        assert!(!Options { format: Container::Mkv, ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { format: Container::M4a, ..o.clone() }.needs_encode(&VIDEO));
        assert!(Options { format: Container::Mp3, ..o.clone() }.needs_encode(&VIDEO));
        let h265 = Probe { video: Some(VideoProbe { codec: Some(VideoCodec::H265), ..SIDE_1080 }), ..VIDEO };
        assert!(o.needs_encode(&h265));
        assert!(Options { audio_codec: AudioCodec::Mp3, ..o.clone() }.needs_encode(&VIDEO));
        // …unless that track is left out.
        assert!(!Options { audio_codec: AudioCodec::Mp3, mute: true, ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { format: Container::M4a, ..o.clone() }.needs_encode(&h265));
        let vp9 = Probe { video: Some(VideoProbe { codec: None, ..SIDE_1080 }), ..VIDEO };
        assert!(Options { format: Container::Mkv, ..o.clone() }.needs_encode(&vp9));
        // A lower frame rate re-encodes; the source's or a higher one does not.
        assert!(Options { fps: Some(24.0), ..o.clone() }.needs_encode(&VIDEO));
        assert!(!Options { fps: Some(30.0), ..o.clone() }.needs_encode(&VIDEO));
    }

    #[test]
    fn edits_keep_the_codecs_of_a_copy() {
        let edit = Options { keep_codecs: true, audio_codec: AudioCodec::Mp3, ..Options::default() };
        // Only the codecs differ: a copy, in the file's own codecs, H.265 too.
        assert!(!edit.needs_encode(&VIDEO));
        assert_eq!(edit.output(&VIDEO).audio, AudioCodec::Aac);
        assert!(!Options { format: Container::Mkv, ..edit.clone() }.needs_encode(&VIDEO));
        let h265 = Probe { video: Some(VideoProbe { codec: Some(VideoCodec::H265), ..SIDE_1080 }), ..VIDEO };
        assert!(!edit.needs_encode(&h265));
        assert_eq!(edit.output(&h265).video, VideoCodec::H265);
        // A re-encode for another reason writes H.264 and the chosen sound.
        let trimmed = Options { start: Some(5.0), ..edit.clone() };
        assert!(trimmed.needs_encode(&h265));
        assert_eq!((trimmed.output(&h265).video, trimmed.output(&h265).audio), (VideoCodec::H264, AudioCodec::Mp3));
        // A codec the file can't hold as it is, or one the app doesn't write, is re-encoded.
        let vp9 = Probe { video: Some(VideoProbe { codec: None, ..SIDE_1080 }), ..VIDEO };
        assert!(edit.needs_encode(&vp9));
        let av1 = Probe { video: Some(VideoProbe { codec: Some(VideoCodec::Av1), ..SIDE_1080 }), ..VIDEO };
        assert!(!Options { format: Container::Mkv, ..edit.clone() }.needs_encode(&av1));
        assert!(Options { format: Container::Mov, ..edit }.needs_encode(&av1));
    }

    #[test]
    fn output_files() {
        let o = Options::default();
        let out = |o: Options, probe: &Probe| {
            let out = o.output(probe);
            (out.container, out.video, out.audio)
        };
        use AudioCodec::*;
        use VideoCodec::*;
        assert_eq!(out(Options { format: Container::Mkv, audio_codec: Pcm, ..o.clone() }, &VIDEO), (Container::Mkv, H264, Pcm));
        // A sound-only file sets its codec.
        assert_eq!(out(Options { format: Container::Flac, audio_codec: Mp3, ..o.clone() }, &VIDEO).2, Flac);
        // MOV can't hold AV1: an edit of an AV1 file is re-encoded in H.264, the sound chosen.
        let av1 = Probe { video: Some(VideoProbe { codec: Some(Av1), ..SIDE_1080 }), ..VIDEO };
        let edit = Options { keep_codecs: true, audio_codec: Mp3, ..o.clone() };
        assert_eq!(out(Options { format: Container::Mov, ..edit.clone() }, &av1), (Container::Mov, H264, Mp3));
        assert_eq!(out(Options { format: Container::Mkv, ..edit }, &av1), (Container::Mkv, Av1, Aac));
        assert_eq!(out(Options { format: Container::Mp4, audio_codec: Pcm, ..o.clone() }, &VIDEO), (Container::Mp4, H264, Aac));
        // A sound without a picture is saved as a sound file of its codec.
        let sound = Probe { video: None, ..VIDEO };
        assert_eq!(out(Options { audio_codec: Mp3, ..o.clone() }, &sound), (Container::Mp3, H264, Mp3));
        assert_eq!(out(o.clone(), &sound).0, Container::M4a);
        // FLAC and WAV take no size limit.
        let limit = Options { max_mb: Some(5.0), ..o };
        assert_eq!(Options { format: Container::Wav, ..limit.clone() }.target_bytes(&VIDEO), None);
        assert_eq!(Options { audio_codec: Flac, ..limit.clone() }.target_bytes(&VIDEO), None);
        assert_eq!(Options { format: Container::Mp3, audio_codec: Flac, ..limit.clone() }.target_bytes(&VIDEO), Some(5_000_000));
        // MOV can't hold FLAC: AAC, which takes the limit.
        assert_eq!(Options { format: Container::Mov, audio_codec: Flac, ..limit }.target_bytes(&VIDEO), Some(5_000_000));
    }

    #[test]
    fn times() {
        assert_eq!(parse_time("0:19"), Some(19.0));
        assert_eq!(parse_time(" 1:02.5 "), Some(62.5));
        assert_eq!(parse_time("1:02,5"), Some(62.5));
        assert_eq!(parse_time("1:00:01"), Some(3601.0));
        assert_eq!(parse_time("75"), Some(75.0));
        assert_eq!(parse_time("12:5"), Some(725.0));
        for bad in ["", "1:60", "1:2:3:4", "-3", "a:10", "1.5:00", "1:-1", "inf"] {
            assert_eq!(parse_time(bad), None, "{bad}");
        }
    }

    #[test]
    fn range_and_target() {
        let o = Options { start: Some(-3.0), end: Some(90.0), max_mb: Some(25.0), ..Options::default() };
        assert_eq!(o.range(60.0), (0.0, 60.0));
        assert_eq!(Options { start: Some(50.0), end: Some(10.0), ..o.clone() }.range(60.0), (50.0, 50.0));
        assert_eq!(o.target_bytes(&VIDEO), Some(25_000_000));
        assert_eq!(Options { speed: 9.0, ..o.clone() }.speed(), 5.0);
        assert_eq!(Options { speed: 0.01, ..o }.speed(), 0.1);
    }

    #[test]
    fn bitrate_for_a_size() {
        // 25 MB over 100 s: 2000 kbps budget, 1980 with the margin, minus 128 for audio.
        assert_eq!(fit(25_000_000, 100.0, true, true), Bitrates { video_kbps: 1852, audio_kbps: 128 });
        assert_eq!(fit(25_000_000, 100.0, true, false), Bitrates { video_kbps: 1980, audio_kbps: 0 });
        // Tight budgets keep less sound; the sound alone takes the whole budget, up to 128 kbps…
        assert_eq!(fit(5_000_000, 100.0, true, true), Bitrates { video_kbps: 332, audio_kbps: 64 });
        assert_eq!(fit(2_000_000, 60.0, false, true), Bitrates { video_kbps: 0, audio_kbps: 128 });
        assert_eq!(fit(1_000_000, 60.0, false, true), Bitrates { video_kbps: 0, audio_kbps: 128 });
        // …and any size gets bitrates: the lowest ones when the limit needs less.
        assert_eq!(fit(10_000_000, 1800.0, true, true), Bitrates { video_kbps: 30, audio_kbps: 32 });
        assert_eq!(fit(500_000, 60.0, false, true), Bitrates { video_kbps: 0, audio_kbps: 66 });
        assert_eq!(fit(100_000, 60.0, false, true), Bitrates { video_kbps: 0, audio_kbps: 32 });
    }

    #[test]
    fn picture_sizes() {
        let sides: Vec<u32> = [5000, 2500, 1000, 400, 200, 30].into_iter().map(side_for).collect();
        assert_eq!(sides, [1080, 1080, 480, 360, 240, 240]);
    }

    /// Writes files whose size follows the bitrate, `overshoot` times too big (or too small).
    struct Fake {
        overshoot: RefCell<Vec<f64>>,
        calls: RefCell<Vec<Settings>>,
    }

    impl Transcoder for Fake {
        fn transcode(&self, _: &Path, output: &Path, s: &Settings, progress: &mut dyn FnMut(f64)) -> Result<(), Failure> {
            let seconds = (s.end.unwrap_or(60.0) - s.start) / s.speed;
            let factor = self.overshoot.borrow_mut().remove(0);
            let bytes = (s.video_kbps + s.audio_kbps) as f64 * 125.0 * seconds * factor;
            fs::write(output, vec![0u8; bytes as usize]).unwrap();
            self.calls.borrow_mut().push(s.clone());
            progress(1.0);
            Ok(())
        }
    }

    fn run(overshoot: &[f64], options: &Options) -> (Result<Option<Failure>, Failure>, Vec<Settings>, u64) {
        let fake = Fake { overshoot: RefCell::new(overshoot.to_vec()), calls: RefCell::default() };
        static RUN: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
        let n = RUN.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let work = std::env::temp_dir().join(format!("stash-edit-{}-{n}", std::process::id()));
        fs::create_dir_all(&work).unwrap();
        let out = work.join("out.mp4");
        let result = encode(&fake, &PathBuf::from("in.mp4"), &out, options, &VIDEO, &work, &mut |_| {});
        let size = fs::metadata(&out).map(|m| m.len()).unwrap_or(0);
        // Nothing left behind but the result.
        assert_eq!(fs::read_dir(&work).unwrap().count(), usize::from(size > 0));
        let _ = fs::remove_dir_all(work);
        (result, fake.calls.into_inner(), size)
    }

    #[test]
    fn size_limit_retries_at_most_twice() {
        let limit = Options { max_mb: Some(5.0), ..Options::default() };
        let (result, calls, size) = run(&[1.0], &limit);
        assert!(result.unwrap().is_none() && size <= 5_000_000);
        assert!(calls[0].exact);
        assert_eq!(calls[0].end, None);
        // 5 MB over a minute leaves about 540 kbps for the picture: 360p.
        assert_eq!(calls[0].height, Some(360));

        let (result, calls, size) = run(&[1.3, 1.1, 1.0], &limit);
        assert!(result.unwrap().is_none() && size <= 5_000_000);
        assert!(calls[1].video_kbps < calls[0].video_kbps);

        // Still too big after three tries: the file is kept, with a note.
        let (result, calls, size) = run(&[1.2, 1.5, 2.0], &limit);
        assert_eq!(calls.len(), 3);
        let note = result.unwrap().unwrap();
        assert_eq!(note.key, "jobs.overSize");
        assert_eq!(note.params["sizeMb"], (size as f64 / 1e4).ceil() / 100.0);

        // A limit under the lowest bitrates: one try at them, no refusal.
        let (result, calls, _) = run(&[1.0], &Options { max_mb: Some(0.1), ..Options::default() });
        assert_eq!(calls.len(), 1);
        assert_eq!((calls[0].video_kbps, calls[0].height), (30, Some(240)));
        assert_eq!(result.unwrap().unwrap().key, "jobs.overSize");
    }

    #[test]
    fn size_limit_is_filled() {
        let limit = Options { max_mb: Some(5.0), ..Options::default() };
        // 98 % of the limit is full enough: one try.
        let (_, calls, size) = run(&[0.99], &limit);
        assert_eq!(calls.len(), 1);
        assert!(size > 4_900_000);
        // 80 %: a larger try, kept as it fits.
        let (result, calls, size) = run(&[0.8, 0.8], &limit);
        assert!(result.unwrap().is_none());
        assert_eq!(calls.len(), 2);
        assert!(calls[1].video_kbps > calls[0].video_kbps);
        assert!(size > 4_800_000 && size <= 5_000_000, "{size}");
        // A larger try over the limit is dropped: the first result stays.
        let (result, calls, size) = run(&[0.8, 1.3], &limit);
        assert!(result.unwrap().is_none());
        assert_eq!(calls.len(), 2);
        assert!(size < 4_100_000, "{size}");
        // Too big, then too small: the larger try is the third and last.
        let (_, calls, _) = run(&[1.2, 0.7, 1.0], &limit);
        assert_eq!(calls.len(), 3);
        // Never more than twice the source's bitrate, however large the limit.
        let (_, calls, _) = run(&[1.0], &Options { max_mb: Some(500.0), ..Options::default() });
        assert_eq!((calls.len(), calls[0].video_kbps), (1, 8000));
    }

    #[test]
    fn slides() {
        let photo = |width, height| Photo { url: String::new(), width, height };
        assert_eq!(slide_size(&photo(1168, 1548)), (1080, 1920));
        assert_eq!(slide_size(&photo(1920, 1080)), (1920, 1080));
        assert_eq!(slide_size(&photo(1000, 1000)), (1920, 1080));
    }

    #[test]
    fn encode_settings() {
        let o = Options { start: Some(10.0), end: Some(40.0), mute: true, speed: 2.0, ..Options::default() };
        let (result, calls, _) = run(&[1.0], &o);
        assert!(result.is_ok());
        let s = &calls[0];
        assert_eq!((s.start, s.end, s.speed, s.video, s.audio, s.exact), (10.0, Some(40.0), 2.0, true, false, false));
        // No limit: capped at the source bitrate with some headroom.
        assert_eq!((s.video_kbps, s.audio_kbps), (4800, 0));
        let (_, calls, _) = run(&[1.0], &Options { height: Some(540), ..Options::default() });
        assert_eq!((calls[0].height, calls[0].video_kbps), (Some(540), 1200));
        // A loose limit fills up to twice the source's bitrate, at the source's size.
        let (_, calls, _) = run(&[1.0], &Options { max_mb: Some(100.0), ..Options::default() });
        assert_eq!((calls[0].video_kbps, calls[0].audio_kbps, calls[0].exact, calls[0].height), (8000, 128, true, None));
        // The chosen size when the limit allows more.
        let (_, calls, _) = run(&[1.0], &Options { max_mb: Some(100.0), height: Some(720), ..Options::default() });
        assert_eq!(calls[0].height, Some(720));
        // The file, codecs and frame rate chosen.
        let o = Options { format: Container::Mkv, audio_codec: AudioCodec::Mp3, fps: Some(15.0), ..Options::default() };
        let (_, calls, _) = run(&[1.0], &o);
        let s = &calls[0];
        assert_eq!((s.container, s.audio_codec, s.rate, s.fps), (Container::Mkv, AudioCodec::Mp3, Some(15.0), 30.0));
    }
}
