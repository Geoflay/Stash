//! FFmpeg, linked as libraries: what a file holds, and remuxing streams (MP4, fragmented
//! MP4, MPEG-TS, Matroska…) into the kind of file chosen, without re-encoding.
//! Re-encoding lives in `encode`.

mod encode;

use std::path::{Path, PathBuf};
use std::sync::Once;

use anyhow::{Context as _, Result, bail};
use ffmpeg_next as ff;
use ff::codec::Id;
use ff::format::{self, stream::Disposition};
use ff::{Dictionary, Packet, Rational, ffi, media};
use serde::{Deserialize, Serialize};

pub use encode::{Ffmpeg, Settings, Slides, Transcoder};

/// FFmpeg's time unit for container-wide times.
const AV_TIME_BASE: f64 = 1_000_000.0;

fn init() {
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        let _ = ff::init();
        ff::log::set_level(ff::log::Level::Error);
    });
}

/// The kinds of file a result is saved as.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Container {
    #[default]
    Mp4,
    Mkv,
    Mov,
    M4a,
    Mp3,
    Flac,
    Wav,
}

impl Container {
    pub fn ext(self) -> &'static str {
        match self {
            Container::Mp4 => "mp4",
            Container::Mkv => "mkv",
            Container::Mov => "mov",
            Container::M4a => "m4a",
            Container::Mp3 => "mp3",
            Container::Flac => "flac",
            Container::Wav => "wav",
        }
    }

    #[cfg_attr(not(target_os = "android"), allow(dead_code))]
    pub fn mime(self) -> &'static str {
        match self {
            Container::Mp4 => "video/mp4",
            Container::Mkv => "video/x-matroska",
            Container::Mov => "video/quicktime",
            Container::M4a => "audio/mp4",
            Container::Mp3 => "audio/mpeg",
            Container::Flac => "audio/flac",
            Container::Wav => "audio/wav",
        }
    }

    fn muxer(self) -> &'static str {
        match self {
            Container::Mp4 | Container::M4a => "mp4",
            Container::Mkv => "matroska",
            Container::Mov => "mov",
            Container::Mp3 => "mp3",
            Container::Flac => "flac",
            Container::Wav => "wav",
        }
    }

    /// The codec of a sound-only file; `None` for a video file.
    pub fn sound(self) -> Option<AudioCodec> {
        match self {
            Container::M4a => Some(AudioCodec::Aac),
            Container::Mp3 => Some(AudioCodec::Mp3),
            Container::Flac => Some(AudioCodec::Flac),
            Container::Wav => Some(AudioCodec::Pcm),
            _ => None,
        }
    }

    /// The sound-only file for a codec.
    pub fn for_sound(codec: AudioCodec) -> Self {
        match codec {
            AudioCodec::Aac => Container::M4a,
            AudioCodec::Mp3 => Container::Mp3,
            AudioCodec::Flac => Container::Flac,
            AudioCodec::Pcm => Container::Wav,
        }
    }

    /// The picture codecs a video file of this kind holds (none for a sound file): a source
    /// in one of them can be copied as it is. H.264 and AV1 are encoded.
    pub fn video_codecs(self) -> &'static [VideoCodec] {
        use VideoCodec::*;
        match self {
            Container::Mp4 | Container::Mkv => &[H264, H265, Av1],
            Container::Mov => &[H264, H265],
            _ => &[],
        }
    }

    /// The sound codecs a video file of this kind holds, the usual one first; mirrors
    /// `AUDIO_CODECS` in the UI.
    pub fn audio_codecs(self) -> &'static [AudioCodec] {
        use AudioCodec::*;
        match self {
            Container::Mp4 => &[Aac, Mp3, Flac],
            Container::Mkv => &[Aac, Mp3, Flac, Pcm],
            Container::Mov => &[Aac, Mp3, Pcm],
            _ => &[],
        }
    }

    /// The MP4 family: the index goes first, so players can start before the end is read.
    fn faststart(self) -> bool {
        matches!(self, Container::Mp4 | Container::M4a | Container::Mov)
    }
}

/// How hard x264 works: a faster preset makes a larger or rougher file.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum H264Preset {
    Ultrafast,
    Superfast,
    Veryfast,
    #[default]
    Faster,
    Fast,
    Medium,
    Slow,
    Slower,
    Veryslow,
    Placebo,
}

impl H264Preset {
    /// The preset of x264.
    fn name(self) -> &'static str {
        match self {
            H264Preset::Ultrafast => "ultrafast",
            H264Preset::Superfast => "superfast",
            H264Preset::Veryfast => "veryfast",
            H264Preset::Faster => "faster",
            H264Preset::Fast => "fast",
            H264Preset::Medium => "medium",
            H264Preset::Slow => "slow",
            H264Preset::Slower => "slower",
            H264Preset::Veryslow => "veryslow",
            H264Preset::Placebo => "placebo",
        }
    }
}

/// SVT-AV1's presets: 0 is the slowest and best, 13 the fastest.
pub const AV1_PRESETS: std::ops::RangeInclusive<u8> = 0..=13;

/// The picture encoder of a re-encode, at its preset.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Encoder {
    /// x264.
    H264(H264Preset),
    /// SVT-AV1, at a preset of [`AV1_PRESETS`].
    Av1(u8),
    /// A graphics card's encoder for the codec, by FFmpeg's name (see [`gpu_encoder`]), at a
    /// preset of [`GPU_PRESETS`].
    Gpu(VideoCodec, &'static str, u8),
}

/// The graphics card encoders' presets: NVIDIA's p1 (fastest) to p7 (slowest, best).
pub const GPU_PRESETS: std::ops::RangeInclusive<u8> = 1..=7;

impl Default for Encoder {
    fn default() -> Self {
        Encoder::H264(H264Preset::default())
    }
}

impl Encoder {
    pub fn codec(self) -> VideoCodec {
        match self {
            Encoder::H264(_) => VideoCodec::H264,
            Encoder::Av1(_) => VideoCodec::Av1,
            Encoder::Gpu(codec, ..) => codec,
        }
    }
}

/// The graphics card encoders FFmpeg has (on Windows), NVIDIA's before AMD's.
const GPU_ENCODERS: [(VideoCodec, &str); 4] =
    [(VideoCodec::H264, "h264_nvenc"), (VideoCodec::H264, "h264_amf"), (VideoCodec::Av1, "av1_nvenc"), (VideoCodec::Av1, "av1_amf")];

/// The first graphics card encoder for `codec` that starts on this computer; `None` without
/// one (no such card, or an older one without AV1). Tried once, when first asked.
pub fn gpu_encoder(codec: VideoCodec, preset: u8) -> Option<Encoder> {
    static WORKING: std::sync::OnceLock<Vec<(VideoCodec, &str)>> = std::sync::OnceLock::new();
    let working = WORKING.get_or_init(|| {
        init();
        GPU_ENCODERS.into_iter().filter(|(_, name)| encode::starts(name)).collect()
    });
    let preset = preset.clamp(*GPU_PRESETS.start(), *GPU_PRESETS.end());
    working.iter().find(|(c, _)| *c == codec).map(|&(c, name)| Encoder::Gpu(c, name, preset))
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum VideoCodec {
    #[default]
    H264,
    H265,
    Av1,
}

impl VideoCodec {
    fn from_id(id: Id) -> Option<Self> {
        match id {
            Id::H264 => Some(VideoCodec::H264),
            Id::HEVC => Some(VideoCodec::H265),
            Id::AV1 => Some(VideoCodec::Av1),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum AudioCodec {
    #[default]
    Aac,
    Mp3,
    Flac,
    /// 16-bit PCM, as in WAV.
    Pcm,
}

impl AudioCodec {
    fn from_id(id: Id) -> Option<Self> {
        match id {
            Id::AAC => Some(AudioCodec::Aac),
            Id::MP3 => Some(AudioCodec::Mp3),
            Id::FLAC => Some(AudioCodec::Flac),
            Id::PCM_S16LE => Some(AudioCodec::Pcm),
            _ => None,
        }
    }

    /// FLAC and WAV: no bitrate to choose.
    pub fn lossless(self) -> bool {
        matches!(self, AudioCodec::Flac | AudioCodec::Pcm)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Kind {
    Video,
    Audio,
}

impl Kind {
    fn medium(self) -> media::Type {
        match self {
            Kind::Video => media::Type::Video,
            Kind::Audio => media::Type::Audio,
        }
    }
}

/// What re-encoding needs to know about a file.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Probe {
    /// Seconds.
    pub duration: f64,
    pub video: Option<VideoProbe>,
    pub audio: bool,
    /// `None` without sound, or in a codec the app does not write.
    pub audio_codec: Option<AudioCodec>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VideoProbe {
    /// As shown: swapped when the picture is turned a quarter.
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub kbps: f64,
    /// `None` for a codec the app does not write (VP9…).
    pub codec: Option<VideoCodec>,
}

impl VideoProbe {
    /// The side a "720p" names, whatever the orientation.
    pub fn short_side(&self) -> u32 {
        self.width.min(self.height)
    }
}

fn open(path: &Path) -> Result<format::context::Input> {
    init();
    format::input(path).with_context(|| format!("open {}", path.display()))
}

/// The stream a kind is read from: the best one, never a cover picture.
fn find(input: &format::context::Input, kind: Kind) -> Option<usize> {
    let usable = |s: &format::stream::Stream| s.parameters().medium() == kind.medium() && !s.disposition().contains(Disposition::ATTACHED_PIC);
    match input.streams().best(kind.medium()) {
        Some(s) if usable(&s) => Some(s.index()),
        _ => input.streams().find(|s| usable(s)).map(|s| s.index()),
    }
}

/// Where the file's timeline starts, in seconds (MPEG-TS rarely starts at 0).
fn origin(input: &format::context::Input) -> f64 {
    let start = unsafe { (*input.as_ptr()).start_time };
    if start == i64::MIN { 0.0 } else { start as f64 / AV_TIME_BASE }
}

/// Quarter turns clockwise a player applies to the stream's pictures (phones store
/// portrait video as landscape frames plus a rotation).
fn quarter_turns(stream: &format::stream::Stream) -> u32 {
    unsafe {
        let par = (*stream.as_ptr()).codecpar;
        let data = ffi::av_packet_side_data_get((*par).coded_side_data, (*par).nb_coded_side_data, ffi::AVPacketSideDataType::AV_PKT_DATA_DISPLAYMATRIX);
        if data.is_null() || (*data).size < 36 {
            return 0;
        }
        let counterclockwise = ffi::av_display_rotation_get((*data).data as *const i32);
        if counterclockwise.is_nan() {
            return 0;
        }
        ((-counterclockwise).rem_euclid(360.0) / 90.0).round() as u32 % 4
    }
}

/// [`probe`] for a file the user opened: one FFmpeg can't read is "not supported".
pub fn probe_supported(path: &Path) -> Result<Probe, crate::Failure> {
    probe(path).map_err(|_| crate::Failure::plain("errors.unsupportedFile"))
}

pub fn probe(path: &Path) -> Result<Probe> {
    let input = open(path)?;
    let mut duration = input.duration() as f64 / AV_TIME_BASE;
    if duration <= 0.0 {
        duration = input.streams().map(|s| s.duration() as f64 * f64::from(s.time_base())).fold(0.0, f64::max);
    }
    if duration <= 0.0 {
        bail!("{}: empty", path.display());
    }
    let audio = find(&input, Kind::Audio).and_then(|i| input.stream(i));
    let video = find(&input, Kind::Video).and_then(|i| input.stream(i)).map(|s| {
        let par = unsafe { &*(*s.as_ptr()).codecpar };
        let (w, h) = (par.width as u32, par.height as u32);
        let (width, height) = if quarter_turns(&s) % 2 == 1 { (h, w) } else { (w, h) };
        let rate = s.avg_frame_rate();
        let fps = if rate.numerator() > 0 && rate.denominator() > 0 { f64::from(rate) } else { 30.0 };
        // WebM and Matroska leave the bitrate out: the whole file's, less the sound's.
        let kbps = if par.bit_rate > 0 {
            par.bit_rate as f64
        } else {
            let sound = audio.as_ref().map_or(0, |a| unsafe { (*(*a.as_ptr()).codecpar).bit_rate });
            (input.bit_rate() as f64 - sound as f64).max(0.0)
        } / 1000.0;
        VideoProbe { width, height, fps, kbps, codec: VideoCodec::from_id(par.codec_id.into()) }
    });
    let audio_codec = audio.as_ref().and_then(|a| AudioCodec::from_id(a.parameters().id()));
    Ok(Probe { duration, video, audio: audio.is_some(), audio_codec })
}

/// Mends the jumps in an MPEG-TS file's timestamps, as ffmpeg does: HLS joins segments
/// whose clock may restart (TED's intro, then the talk). A packet more than 0.1 s before,
/// or 10 s after, where its stream was due moves the rest of the file to follow on.
struct Clock {
    /// Only a format whose timestamps may jump is mended.
    mends: bool,
    /// Seconds added to every timestamp from here on.
    shift: f64,
    /// Per stream: when its next packet is due (seconds, shifted).
    due: Vec<Option<f64>>,
}

impl Clock {
    fn new(input: &format::context::Input) -> Self {
        let mends = unsafe { (*(*input.as_ptr()).iformat).flags & ffi::AVFMT_TS_DISCONT != 0 };
        Clock { mends, shift: 0.0, due: vec![None; input.nb_streams() as usize] }
    }

    fn mend(&mut self, packet: &mut Packet, time_base: Rational) {
        let Some(stamp) = packet.dts().or(packet.pts()).filter(|_| self.mends) else {
            return;
        };
        let base = f64::from(time_base);
        let index = packet.stream();
        let time = stamp as f64 * base + self.shift;
        if let Some(due) = self.due[index]
            && !(due - 0.1..=due + 10.0).contains(&time)
        {
            self.shift += due - time;
        }
        let ticks = (self.shift / base).round() as i64;
        packet.set_pts(packet.pts().map(|t| t + ticks));
        packet.set_dts(packet.dts().map(|t| t + ticks));
        self.due[index] = Some((stamp + ticks + packet.duration().max(0)) as f64 * base);
    }
}

/// One file read for a remux, and where its streams go.
struct Feed {
    path: PathBuf,
    input: format::context::Input,
    /// Per input stream: the output stream, the input time base and the kind.
    map: Vec<Option<(usize, Rational, Kind)>>,
    /// The file's start, taken off every timestamp (seconds).
    origin: f64,
    next: Option<Packet>,
    clock: Clock,
    done: bool,
    /// When the picture's last keyframe read starts (seconds from the file's start): the
    /// packets after it go with it, kept or cut.
    keyframe: f64,
}

impl Feed {
    /// Reads up to the next packet of a kept stream.
    fn fill(&mut self) {
        while self.next.is_none() && !self.done {
            let mut packet = Packet::empty();
            match packet.read(&mut self.input) {
                Ok(()) => {
                    if let Some(&Some((_, time_base, _))) = self.map.get(packet.stream()) {
                        self.clock.mend(&mut packet, time_base);
                        self.next = Some(packet);
                    }
                }
                Err(_) => self.done = true,
            }
        }
    }

    /// When the next packet is decoded, in seconds.
    fn next_time(&self) -> Option<f64> {
        let packet = self.next.as_ref()?;
        let (_, time_base, _) = self.map[packet.stream()]?;
        Some(packet.dts().or(packet.pts()).unwrap_or(0) as f64 * f64::from(time_base))
    }
}

/// Where a copy of `path`'s picture can start or stop: its keyframes and its end, in
/// seconds from the file's start.
fn cut_points(path: &Path) -> Result<Vec<f64>> {
    let mut input = open(path)?;
    let index = find(&input, Kind::Video).context("no video")?;
    let base = f64::from(input.stream(index).unwrap().time_base());
    let origin = origin(&input);
    let mut points = vec![input.duration() as f64 / AV_TIME_BASE];
    let mut clock = Clock::new(&input);
    for (stream, mut packet) in input.packets() {
        if stream.index() != index {
            continue;
        }
        clock.mend(&mut packet, stream.time_base());
        if packet.is_key() {
            points.extend(packet.pts().or(packet.dts()).map(|t| t as f64 * base - origin));
        }
    }
    Ok(points)
}

/// The `cuts` moved to the nearest of `points`; a cut from the first one on also takes
/// what comes before it (the sound's lead-in).
fn snap(cuts: &[(f64, f64)], points: &[f64]) -> Vec<(f64, f64)> {
    let nearest = |t: f64| points.iter().copied().min_by(|a, b| (a - t).abs().total_cmp(&(b - t).abs())).unwrap_or(t);
    let first = points.iter().copied().fold(f64::INFINITY, f64::min);
    cuts.iter()
        .map(|&(a, b)| (nearest(a), nearest(b)))
        .filter(|(a, b)| b > a)
        .map(|(a, b)| (if a <= first { f64::NEG_INFINITY } else { a }, b))
        .collect()
}

/// The seconds cut before `time`, or `None` when `time` is cut out.
fn cut_before(cuts: &[(f64, f64)], time: f64) -> Option<f64> {
    let mut before = 0.0;
    for &(a, b) in cuts {
        if (a..b).contains(&time) {
            return None;
        }
        if b <= time {
            before += b - a.max(0.0);
        }
    }
    Some(before)
}

/// The picture and the sound of `file`, for [`remux`], as far as they are kept.
pub fn tracks(file: &Path, video: bool, audio: bool) -> Vec<(PathBuf, Kind)> {
    [(video, Kind::Video), (audio, Kind::Audio)].into_iter().filter(|(keep, _)| *keep).map(|(_, kind)| (file.to_path_buf(), kind)).collect()
}

/// Copies the first track of each kind from its file into one file, interleaved, without
/// the `cuts` (seconds from the start, in order and apart). A copy can only start and stop
/// a picture at a keyframe: each cut moves to the nearest ones, and the sound follows.
pub fn remux(out: &Path, container: Container, tracks: &[(PathBuf, Kind)], cuts: &[(f64, f64)]) -> Result<()> {
    init();
    let cuts = match tracks.iter().find(|(_, kind)| *kind == Kind::Video) {
        Some((path, _)) if !cuts.is_empty() => snap(cuts, &cut_points(path)?),
        _ => cuts.to_vec(),
    };
    let mut output = create(out, container)?;
    let mut feeds: Vec<Feed> = Vec::new();
    for (path, kind) in tracks {
        let feed = match feeds.iter().position(|f| f.path == *path) {
            Some(i) => &mut feeds[i],
            None => {
                let input = open(path)?;
                let streams = input.nb_streams() as usize;
                let origin = origin(&input);
                let clock = Clock::new(&input);
                feeds.push(Feed { path: path.clone(), input, map: vec![None; streams], origin, next: None, clock, done: false, keyframe: 0.0 });
                feeds.last_mut().unwrap()
            }
        };
        let index = find(&feed.input, *kind).with_context(|| format!("no {kind:?} track in {}", path.display()))?;
        let source = feed.input.stream(index).unwrap();
        let mut stream = output.add_stream(ff::encoder::find(ff::codec::Id::None))?;
        stream.set_parameters(source.parameters());
        set_tag(&mut stream, container);
        stream.set_time_base(source.time_base());
        feed.map[index] = Some((stream.index(), source.time_base(), *kind));
    }
    write_header(&mut output, container, false)?;
    let out_bases: Vec<Rational> = output.streams().map(|s| s.time_base()).collect();
    let mut last_dts: Vec<Option<i64>> = vec![None; out_bases.len()];

    loop {
        feeds.iter_mut().for_each(Feed::fill);
        let Some(feed) = feeds.iter_mut().filter(|f| f.next.is_some()).min_by(|a, b| a.next_time().partial_cmp(&b.next_time()).unwrap()) else {
            break;
        };
        let mut packet = feed.next.take().unwrap();
        let (index, time_base, kind) = feed.map[packet.stream()].unwrap();
        // A picture's packets go with their keyframe; the sound's by their own time.
        let time = packet.pts().or(packet.dts()).unwrap_or(0) as f64 * f64::from(time_base) - feed.origin;
        if kind == Kind::Video && packet.is_key() {
            feed.keyframe = time;
        }
        let Some(cut) = cut_before(&cuts, if kind == Kind::Video { feed.keyframe } else { time }) else {
            continue;
        };
        let shift = ((feed.origin + cut) / f64::from(time_base)).round() as i64;
        packet.set_pts(packet.pts().map(|t| t - shift));
        packet.set_dts(packet.dts().map(|t| t - shift));
        packet.rescale_ts(time_base, out_bases[index]);
        // Where parts join (cuts, or segments whose clock was mended), a time may repeat
        // or overlap a little, which the file can't hold.
        if let (Some(dts), Some(last)) = (packet.dts(), last_dts[index])
            && dts <= last
        {
            packet.set_dts(Some(last + 1));
            packet.set_pts(packet.pts().map(|pts| pts.max(last + 1)));
        }
        last_dts[index] = packet.dts();
        packet.set_stream(index);
        packet.set_position(-1);
        packet.write_interleaved(&mut output).context("write")?;
    }
    output.write_trailer().context("finish")?;
    Ok(())
}

/// The kind of file a preview of `probe` is: MP4, or M4A for a sound.
pub fn preview_container(probe: &Probe) -> Container {
    if probe.video.is_some() { Container::Mp4 } else { Container::M4a }
}

/// A copy of `input` the webview can play, written to `out`: its tracks remuxed into MP4
/// (M4A for a sound) when they are H.264 and AAC or MP3, or a sound encoded to AAC, whose
/// `progress` goes from 0 to 1. `false` when it is a video to encode: it is played in parts
/// made as it plays (`preview_part`).
pub fn preview(input: &Path, out: &Path, probe: &Probe, stop: &dyn Fn() -> bool, progress: &mut dyn FnMut(f64)) -> Result<bool, crate::Failure> {
    let video = probe.video.is_some();
    let container = preview_container(probe);
    let plays = probe.video.is_none_or(|v| v.codec == Some(VideoCodec::H264))
        && (!probe.audio || matches!(probe.audio_codec, Some(AudioCodec::Aac | AudioCodec::Mp3)));
    if plays && remux(out, container, &tracks(input, video, probe.audio), &[]).is_ok() {
        return Ok(true);
    }
    if video {
        return Ok(false);
    }
    Ffmpeg { stop }.transcode(input, out, &preview_settings(probe, out, false), progress)?;
    Ok(true)
}

/// `seconds` of a video's preview from `from`, written to `out`: a quick H.264 + AAC encode,
/// 480p at most, in a fragmented MP4 the webview appends to what it plays. The source is
/// decoded on the graphics card with `card`.
pub fn preview_part(input: &Path, out: &Path, probe: &Probe, from: f64, seconds: f64, card: bool, stop: &dyn Fn() -> bool) -> Result<(), crate::Failure> {
    let end = from + seconds;
    let settings = Settings {
        start: from,
        end: (end < probe.duration).then_some(end),
        fragments: true,
        ..preview_settings(probe, out, card)
    };
    Ffmpeg { stop }.transcode(input, out, &settings, &mut |_| {})
}

fn preview_settings(probe: &Probe, out: &Path, card: bool) -> Settings {
    Settings {
        start: 0.0,
        end: None,
        cuts: Vec::new(),
        speed: 1.0,
        video: probe.video.is_some(),
        audio: probe.audio,
        height: probe.video.filter(|v| v.short_side() > 480).map(|_| 480),
        fps: probe.video.map_or(30.0, |v| v.fps),
        rate: None,
        container: preview_container(probe),
        audio_codec: AudioCodec::Aac,
        video_kbps: 2000,
        audio_kbps: 128,
        exact: false,
        stats: out.with_extension("log"),
        encoder: Encoder::H264(H264Preset::Veryfast),
        draft: true,
        card,
        fragments: false,
    }
}

fn create(path: &Path, container: Container) -> Result<format::context::Output> {
    format::output_as(path, container.muxer()).with_context(|| format!("create {}", path.display()))
}

/// The input's codec tag may mean nothing in the output, so the muxer picks one; HEVC in
/// MP4 and MOV is tagged hvc1, the one Apple players show.
fn set_tag(stream: &mut format::stream::StreamMut, container: Container) {
    unsafe {
        let par = (*stream.as_mut_ptr()).codecpar;
        let hevc = (*par).codec_id == ffi::AVCodecID::AV_CODEC_ID_HEVC;
        (*par).codec_tag = if hevc && container.faststart() { u32::from_le_bytes(*b"hvc1") } else { 0 };
    }
}

/// With `fragments`, an MP4 in fragments, each one playable as soon as it is read (by the
/// webview's Media Source).
fn write_header(output: &mut format::context::Output, container: Container, fragments: bool) -> Result<()> {
    let mut options = Dictionary::new();
    if fragments {
        options.set("movflags", "frag_keyframe+empty_moov+default_base_moof");
    } else if container.faststart() {
        options.set("movflags", "+faststart");
    }
    output.write_header_with(options).context("write header")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A file under the target folder, removed when dropped.
    pub struct Temp(pub PathBuf);

    impl Temp {
        pub fn new(name: &str) -> Self {
            static NEXT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
            let n = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            Temp(std::env::temp_dir().join(format!("stash-{}-{n}-{name}", std::process::id())))
        }
    }

    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    pub fn fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name)
    }

    #[test]
    fn probes_a_stream() {
        let probe = probe(&fixture("clip.ts")).unwrap();
        assert!((probe.duration - 1.0).abs() < 0.1, "{probe:?}");
        let video = probe.video.unwrap();
        assert_eq!((video.width, video.height, video.short_side()), (160, 90, 90));
        assert!((video.fps - 25.0).abs() < 0.01);
        assert!(probe.audio);
        assert!(super::probe(&fixture("youtube.json")).is_err());
    }

    #[test]
    fn remuxes_tracks() {
        // MPEG-TS in, MP4 out; then the picture and the sound taken from separate files.
        let both = Temp::new("both.mp4");
        remux(&both.0, Container::Mp4, &[(fixture("clip.ts"), Kind::Video), (fixture("clip.ts"), Kind::Audio)], &[]).unwrap();
        let probe = super::probe(&both.0).unwrap();
        assert!(probe.video.is_some() && probe.audio && (probe.duration - 1.0).abs() < 0.1, "{probe:?}");

        let sound = Temp::new("sound.m4a");
        remux(&sound.0, Container::M4a, &[(both.0.clone(), Kind::Audio)], &[]).unwrap();
        let probe = super::probe(&sound.0).unwrap();
        assert!(probe.video.is_none() && probe.audio);

        let joined = Temp::new("joined.mp4");
        remux(&joined.0, Container::Mp4, &[(both.0.clone(), Kind::Video), (sound.0.clone(), Kind::Audio)], &[]).unwrap();
        let probe = super::probe(&joined.0).unwrap();
        assert!(probe.video.is_some() && probe.audio);
        // The index comes first, so players can start before the end is read.
        let head = std::fs::read(&joined.0).unwrap();
        let moov = head.windows(4).position(|w| w == b"moov").unwrap();
        let mdat = head.windows(4).position(|w| w == b"mdat").unwrap();
        assert!(moov < mdat);

        // Into Matroska, codecs untouched.
        let mkv = Temp::new("joined.mkv");
        remux(&mkv.0, Container::Mkv, &[(joined.0.clone(), Kind::Video), (joined.0.clone(), Kind::Audio)], &[]).unwrap();
        let probe = super::probe(&mkv.0).unwrap();
        assert_eq!((probe.video.unwrap().codec, probe.audio_codec), (Some(VideoCodec::H264), Some(AudioCodec::Aac)));
    }

    #[test]
    fn mends_a_clock_that_restarts() {
        // Two HLS segments joined, the second's clock starting over: one 2 s file.
        let joined = Temp::new("joined.ts");
        let clip = std::fs::read(fixture("clip.ts")).unwrap();
        std::fs::write(&joined.0, [clip.as_slice(), &clip].concat()).unwrap();
        let out = Temp::new("mended.mp4");
        remux(&out.0, Container::Mp4, &[(joined.0.clone(), Kind::Video), (joined.0.clone(), Kind::Audio)], &[]).unwrap();
        let probe = super::probe(&out.0).unwrap();
        assert!((probe.duration - 2.0).abs() < 0.1, "{probe:?}");
        // The cut points follow on too.
        let points = cut_points(&joined.0).unwrap();
        assert!(points.iter().any(|&p| (p - 1.0).abs() < 0.1) && points.iter().all(|&p| p < 2.1), "{points:?}");
    }

    #[test]
    fn cuts_at_keyframes() {
        // A keyframe every second; B-frames, so the picture is stored out of order.
        let tracks = [(fixture("clip-gop.mp4"), Kind::Video), (fixture("clip-gop.mp4"), Kind::Audio)];
        let duration = |cuts: &[(f64, f64)]| {
            let out = Temp::new("cut.mp4");
            remux(&out.0, Container::Mp4, &tracks, cuts).unwrap();
            let probe = super::probe(&out.0).unwrap();
            assert!(probe.video.is_some() && probe.audio);
            // Plays to the end.
            let copy = Temp::new("copy.mp4");
            Ffmpeg { stop: &|| false }.transcode(&out.0, &copy.0, &encode::tests::settings(Path::new("unused")), &mut |_| {}).unwrap();
            (probe.duration * 10.0).round() / 10.0
        };
        // Each cut moves to the nearest keyframes: 0.9 s to 2.1 s takes out 1 s to 2 s.
        assert_eq!(duration(&[(0.9, 2.1)]), 3.0);
        assert_eq!(duration(&[(0.0, 0.6), (2.8, 4.0)]), 2.0);
        // Too short to reach a keyframe: nothing cut.
        assert_eq!(duration(&[(1.2, 1.4)]), 4.0);
    }

    #[test]
    fn previews() {
        // H.264 + AAC in Matroska: remuxed into MP4 as it is.
        let mkv = Temp::new("clip.mkv");
        remux(&mkv.0, Container::Mkv, &[(fixture("clip.ts"), Kind::Video), (fixture("clip.ts"), Kind::Audio)], &[]).unwrap();
        let out = Temp::new("preview.mp4");
        assert!(preview(&mkv.0, &out.0, &super::probe(&mkv.0).unwrap(), &|| false, &mut |_| {}).unwrap());
        let probe = super::probe(&out.0).unwrap();
        assert_eq!((probe.video.unwrap().codec, probe.audio_codec), (Some(VideoCodec::H264), Some(AudioCodec::Aac)));
        // FLAC sound: re-encoded to AAC.
        let flac = Temp::new("sound.flac");
        let s = Settings { video: false, container: Container::Flac, audio_codec: AudioCodec::Flac, ..encode::tests::settings(Path::new("unused")) };
        Ffmpeg { stop: &|| false }.transcode(&fixture("clip.ts"), &flac.0, &s, &mut |_| {}).unwrap();
        let out = Temp::new("preview.m4a");
        let mut shown = Vec::new();
        assert!(preview(&flac.0, &out.0, &super::probe(&flac.0).unwrap(), &|| false, &mut |f| shown.push(f)).unwrap());
        assert!(shown.last().is_some_and(|&f| f > 0.9), "{shown:?}");
        assert_eq!(super::probe(&out.0).unwrap().audio_codec, Some(AudioCodec::Aac));
    }

    #[test]
    fn preview_parts() {
        // An HEVC video: played in parts, each a fragmented MP4 of the seconds asked for.
        let probe = super::probe(&fixture("clip.ts")).unwrap();
        let hevc = Probe { video: probe.video.map(|v| VideoProbe { codec: Some(VideoCodec::H265), ..v }), ..probe };
        let out = Temp::new("preview.mp4");
        assert!(!preview(&fixture("clip.ts"), &out.0, &hevc, &|| false, &mut |_| {}).unwrap());
        let part = Temp::new("part.mp4");
        preview_part(&fixture("clip.ts"), &part.0, &probe, 0.2, 0.5, false, &|| false).unwrap();
        let bytes = std::fs::read(&part.0).unwrap();
        assert!(bytes.windows(4).any(|w| w == b"moof"), "not in fragments");
        let made = super::probe(&part.0).unwrap();
        assert!((made.duration - 0.5).abs() < 0.1, "{}", made.duration);
        assert!(made.audio);
    }
}
