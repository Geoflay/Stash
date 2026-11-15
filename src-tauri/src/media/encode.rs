//! Re-encoding with FFmpeg: x264 for the picture (two passes for a size
//! limit, as the old Stash did), AAC, MP3, FLAC or PCM for the sound. Trimming, speed
//! (atempo keeps the pitch), frame rate, turning and resizing are FFmpeg filters.

use std::ffi::CStr;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{RecvTimeoutError, SyncSender, sync_channel};
use std::thread;
use std::time::Duration;

use anyhow::{Context as _, Result, bail};
use ff::codec::{self, threading};
use ff::{Dictionary, Packet, Rational, decoder, encoder, ffi, filter, format, frame};
use ffmpeg_next as ff;

use super::{AudioCodec, Container, Kind, Preset, create, find, init, open, origin, quarter_turns, set_tag, write_header};
use crate::Failure;

/// One encode. Times are in seconds from the start of the source.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub start: f64,
    /// `None` to the end of the source.
    pub end: Option<f64>,
    /// Parts removed between `start` and `end`, in order and apart.
    pub cuts: Vec<(f64, f64)>,
    /// 0.1 to 5, pitch kept.
    pub speed: f64,
    pub video: bool,
    pub audio: bool,
    /// Lower short side (720 for 720p, portrait or not); `None` keeps the source size.
    pub height: Option<u32>,
    /// Source frame rate, kept when speeding up.
    pub fps: f64,
    /// A lower frame rate chosen for the result.
    pub rate: Option<f64>,
    pub container: Container,
    pub audio_codec: AudioCodec,
    pub video_kbps: u32,
    pub audio_kbps: u32,
    /// A size limit: two passes, so the bitrate is met. Otherwise constant quality,
    /// never over `video_kbps`.
    pub exact: bool,
    /// Where x264 keeps its first-pass notes (in the job folder).
    pub stats: PathBuf,
    pub preset: Preset,
    /// A quick, rougher H.264 encode (previews).
    pub draft: bool,
}

/// A video from photos: each shown `seconds`, letterboxed into `width` × `height` at 25 fps.
#[derive(Clone, Debug, PartialEq)]
pub struct Slides {
    pub width: u32,
    pub height: u32,
    pub seconds: f64,
    pub video_kbps: u32,
    pub audio_kbps: u32,
    pub preset: Preset,
}

pub trait Transcoder {
    /// Encodes `input` into `output`; `progress` gets the fraction done.
    fn transcode(&self, input: &Path, output: &Path, settings: &Settings, progress: &mut dyn FnMut(f64)) -> Result<(), Failure>;
}

/// Encodes with FFmpeg; `stop` says when the job was cancelled.
pub struct Ffmpeg<'a> {
    pub stop: &'a dyn Fn() -> bool,
}

impl Transcoder for Ffmpeg<'_> {
    fn transcode(&self, input: &Path, output: &Path, s: &Settings, progress: &mut dyn FnMut(f64)) -> Result<(), Failure> {
        let result = if s.video && s.exact {
            let result = run(input, None, s, Pass::First, self.stop, &mut |f| progress(f / 2.0))
                .and_then(|()| run(input, Some(output), s, Pass::Second, self.stop, &mut |f| progress(0.5 + f / 2.0)));
            let stats = s.stats.display().to_string();
            for notes in [stats.clone(), format!("{stats}.mbtree"), format!("{stats}.temp")] {
                let _ = std::fs::remove_file(notes);
            }
            result
        } else {
            run(input, Some(output), s, Pass::Only, self.stop, progress)
        };
        result.map_err(|err| failure(err, self.stop))
    }
}

impl Ffmpeg<'_> {
    /// Makes an H.264 + AAC video from `photos`, with `sound` looped under them when given.
    pub fn slideshow(&self, photos: &[PathBuf], sound: Option<&Path>, output: &Path, s: &Slides, progress: &mut dyn FnMut(f64)) -> Result<(), Failure> {
        slideshow(photos, sound, output, s, self.stop, progress).map_err(|err| failure(err, self.stop))
    }
}

fn failure(err: anyhow::Error, stop: &dyn Fn() -> bool) -> Failure {
    match stop() {
        true => Failure::io("cancelled"),
        false => Failure::new("errors.encode", serde_json::json!({ "detail": format!("{err:#}") })),
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Pass {
    Only,
    /// The encoder notes how hard each part is; nothing is written.
    First,
    /// The encoder shares the bits out from those notes.
    Second,
}

/// The sound at `speed`, pitch kept: atempo takes 0.5 at the slowest, so slower is several steps.
fn tempo(speed: f64) -> String {
    let mut steps = Vec::new();
    let mut left = speed;
    while left < 0.5 {
        steps.push("atempo=0.5".to_string());
        left /= 0.5;
    }
    steps.push(format!("atempo={left}"));
    steps.join(",")
}

/// The sample format each sound encoder takes.
fn sample_format(codec: AudioCodec) -> format::Sample {
    use format::sample::Type;
    if codec.lossless() { format::Sample::I16(Type::Packed) } else { format::Sample::F32(Type::Planar) }
}

/// The sound every encode ends on, in the sample format the encoder takes.
fn sound_format(codec: AudioCodec) -> String {
    let sample = sample_format(codec).name();
    format!("aformat=sample_fmts={sample}:sample_rates=48000|44100|32000|24000|22050|16000:channel_layouts=mono|stereo")
}
/// The seconds cut before a frame's time `T` ("0" without cuts): for setpts.
fn cut_before(cuts: &[(f64, f64)]) -> String {
    let before: Vec<String> = cuts.iter().map(|(a, b)| format!("clip(T-{a},0,{})", b - a)).collect();
    if before.is_empty() { "0".into() } else { before.join("+") }
}

const SLIDE_FPS: i32 = 25;
/// Encoders pick their own thread count (FFmpeg's default is one).
const AUTO_THREADS: threading::Config = threading::Config { kind: threading::Type::Frame, count: 0 };
/// Frames decoded ahead of the encoders.
const FRAMES_AHEAD: usize = 8;

/// What the decoding thread sends the encoding one: a frame for the encoder of chain `n`,
/// or `None` at the chain's end.
type Message = (usize, Option<frame::Frame>);

/// A stream from the decoder through a filter graph, to frames timed for its encoder.
struct Chain {
    /// The input stream read.
    index: usize,
    decoder: decoder::Opened,
    graph: filter::Graph,
    /// The encoder's time base, which frames are given in.
    encoder_base: Rational,
    /// Source seconds past which frames are not needed.
    end: Option<f64>,
    /// Source seconds whose frames are left out before the filters (aselect let every frame
    /// through in these graphs with FFmpeg 8.0.1, even `aselect=0`).
    cuts: Vec<(f64, f64)>,
    input_base: Rational,
    /// The time base the graph takes frames in: the stream's, or one sample for sound (an
    /// AVI steps its MP3 sound a whole MP3 frame at a time, too coarse for AAC's frames).
    graph_base: Rational,
    /// Past the end, or the filters want no more.
    done: bool,
}

/// Takes a chain's frames; fails once the encoding stopped.
type Out<'a> = &'a mut dyn FnMut(frame::Frame) -> Result<()>;

impl Chain {
    fn packet(&mut self, packet: &Packet, send: Out) -> Result<()> {
        // A damaged packet is skipped, as players do.
        if self.decoder.send_packet(packet).is_ok() {
            self.decoded(send)?;
        }
        Ok(())
    }

    fn decoded(&mut self, send: Out) -> Result<()> {
        // A frame of either kind: the decoder and the filters say which.
        let mut frame = unsafe { frame::Frame::empty() };
        while self.decoder.receive_frame(&mut frame).is_ok() {
            if self.done {
                continue;
            }
            let pts = unsafe { (*frame.as_ptr()).best_effort_timestamp };
            frame.set_pts((pts != i64::MIN).then_some(pts));
            if let (Some(end), Some(pts)) = (self.end, frame.pts())
                && pts as f64 * f64::from(self.input_base) > end
            {
                self.done = true;
            }
            if let Some(pts) = frame.pts() {
                let time = pts as f64 * f64::from(self.input_base);
                if self.cuts.iter().any(|&(a, b)| (a..b).contains(&time)) {
                    continue;
                }
            }
            if let Some(pts) = frame.pts() {
                frame.set_pts(Some(pts.rescale(self.input_base, self.graph_base)));
            }
            match self.graph.get("in").unwrap().source().add(&frame) {
                Err(ff::Error::Eof) => self.done = true,
                result => result.context("filter")?,
            }
            self.filtered(send)?;
        }
        Ok(())
    }

    fn filtered(&mut self, send: Out) -> Result<()> {
        // A frame of either kind: the decoder and the filters say which.
        let mut frame = unsafe { frame::Frame::empty() };
        let base = sink_time_base(&mut self.graph);
        while self.graph.get("out").unwrap().sink().frame(&mut frame).is_ok() {
            if let Some(pts) = frame.pts() {
                frame.set_pts(Some(pts.rescale(base, self.encoder_base)));
            }
            free_kind(&mut frame);
            send(std::mem::replace(&mut frame, unsafe { frame::Frame::empty() }))?;
        }
        Ok(())
    }

    fn finish(&mut self, send: Out) -> Result<()> {
        let _ = self.decoder.send_eof();
        self.decoded(send)?;
        let _ = self.graph.get("in").unwrap().source().flush();
        self.filtered(send)
    }
}

/// A chain's encoder, and the stream it writes unless nothing is written (a first pass).
struct Encoding {
    encoder: encoder::Encoder,
    base: Rational,
    out: Option<(usize, Rational)>,
    /// Output seconds encoded so far.
    reached: f64,
}

impl Encoding {
    /// Encodes `frame`, or finishes at `None`, and writes what the encoder has ready.
    fn encode(&mut self, frame: Option<&frame::Frame>, output: &mut Option<format::context::Output>) -> Result<()> {
        match frame {
            Some(frame) => {
                if let Some(pts) = frame.pts() {
                    self.reached = self.reached.max(pts as f64 * f64::from(self.base));
                }
                self.encoder.send_frame(frame).context("encode")?;
            }
            None => self.encoder.send_eof().context("encode")?,
        }
        if let (Some(output), Some((index, base))) = (output.as_mut(), self.out) {
            return drain(&mut self.encoder, self.base, output, index, base);
        }
        let mut packet = Packet::empty();
        while self.encoder.receive_packet(&mut packet).is_ok() {}
        Ok(())
    }
}

/// Reads `source` through the chains, sending each frame on, then each chain's end.
fn read(source: &mut format::context::Input, chains: &mut [Chain], frames: &SyncSender<Message>) -> Result<()> {
    let send = |n: usize, frame: Option<frame::Frame>| frames.send((n, frame)).ok().context("the encoding stopped");
    let mut packet = Packet::empty();
    while chains.iter().any(|c| !c.done) && packet.read(source).is_ok() {
        if let Some(n) = chains.iter().position(|c| c.index == packet.stream() && !c.done) {
            chains[n].packet(&packet, &mut |frame| send(n, Some(frame)))?;
        }
    }
    for (n, chain) in chains.iter_mut().enumerate() {
        chain.finish(&mut |frame| send(n, Some(frame)))?;
        send(n, None)?;
    }
    Ok(())
}

trait Rescale {
    fn rescale(self, from: Rational, to: Rational) -> i64;
}

impl Rescale for i64 {
    fn rescale(self, from: Rational, to: Rational) -> i64 {
        unsafe { ffi::av_rescale_q(self, from.into(), to.into()) }
    }
}

/// Lets the encoder pick the frame's kind: a decoded frame keeps the source's (I, P, B),
/// which the encoders obey, so a new codec would copy the old file's structure.
fn free_kind(frame: &mut frame::Frame) {
    unsafe { (*frame.as_mut_ptr()).pict_type = ffi::AVPictureType::AV_PICTURE_TYPE_NONE };
}

/// Writes the packets `encoder` has ready (timed in `from`) into `stream` (timed in `to`).
fn drain(encoder: &mut encoder::Encoder, from: Rational, output: &mut format::context::Output, stream: usize, to: Rational) -> Result<()> {
    let mut packet = Packet::empty();
    while encoder.receive_packet(&mut packet).is_ok() {
        packet.rescale_ts(from, to);
        packet.set_stream(stream);
        packet.write_interleaved(output).context("write")?;
    }
    Ok(())
}

/// A buffer source's arguments for pictures of `width` × `height` in `format`.
fn picture_source(width: u32, height: u32, format: format::Pixel, base: Rational, aspect: Rational) -> String {
    format!(
        "video_size={width}x{height}:pix_fmt={}:time_base={}/{}:pixel_aspect={}/{}",
        ffi::AVPixelFormat::from(format) as i32,
        base.numerator(),
        base.denominator(),
        aspect.numerator().max(0),
        aspect.denominator().max(1),
    )
}

/// An abuffer source's arguments for what `decoder` gives out.
fn sound_source(decoder: &decoder::Audio, base: Rational) -> String {
    format!(
        "time_base={}/{}:sample_rate={}:sample_fmt={}:channel_layout={}",
        base.numerator(),
        base.denominator(),
        decoder.rate(),
        decoder.format().name(),
        layout_name(unsafe { decoder.as_ptr() }),
    )
}

fn sink_time_base(graph: &mut filter::Graph) -> Rational {
    unsafe { ffi::av_buffersink_get_time_base(graph.get("out").unwrap().as_ptr()).into() }
}

/// A graph from a buffer source with `args` through `spec` to a sink.
fn graph(source: &str, args: &str, spec: &str) -> Result<filter::Graph> {
    let mut graph = filter::Graph::new();
    let sink = if source == "buffer" { "buffersink" } else { "abuffersink" };
    graph.add(&filter::find(source).unwrap(), "in", args).context("filter source")?;
    graph.add(&filter::find(sink).unwrap(), "out", "").context("filter sink")?;
    graph.output("in", 0)?.input("out", 0)?.parse(spec).with_context(|| format!("filters {spec}"))?;
    graph.validate().context("filters")?;
    Ok(graph)
}

fn decoder_for(stream: &format::stream::Stream) -> Result<codec::context::Context> {
    let mut context = codec::context::Context::from_parameters(stream.parameters())?;
    context.set_threading(AUTO_THREADS);
    Ok(context)
}

/// An output stream for `encoder`, in a container that wants codec headers up front.
fn add_stream(output: &mut format::context::Output, encoder: &encoder::Encoder, container: Container) -> Result<usize> {
    let mut stream = output.add_stream(encoder.codec())?;
    stream.set_parameters(encoder);
    set_tag(&mut stream, container);
    stream.set_time_base(encoder.time_base());
    Ok(stream.index())
}

/// The picture encoder, x264 at the preset chosen: two passes for a size limit, otherwise
/// constant quality never over `video_kbps`.
fn video_encoder(width: u32, height: u32, base: Rational, fps: f64, aspect: Rational, s: &Settings, pass: Pass) -> Result<encoder::Encoder> {
    let codec = encoder::find_by_name("libx264").context("no libx264")?;
    let mut context = codec::context::Context::new_with_codec(codec);
    context.set_threading(AUTO_THREADS);
    let mut video = context.encoder().video()?;
    video.set_width(width);
    video.set_height(height);
    video.set_format(format::Pixel::YUV420P);
    video.set_time_base(base);
    video.set_frame_rate(Some(Rational::from(fps)));
    video.set_aspect_ratio(aspect);
    let kbps = s.video_kbps.max(1);
    let two_pass = pass != Pass::Only;
    let mut flags = codec::Flags::GLOBAL_HEADER;
    if two_pass {
        video.set_bit_rate(kbps as usize * 1000);
        flags |= if pass == Pass::First { codec::Flags::PASS1 } else { codec::Flags::PASS2 };
    }
    let mut options = Dictionary::new();
    options.set("preset", if s.draft { "veryfast" } else { s.preset.name() });
    if two_pass {
        options.set("stats", &s.stats.display().to_string());
    } else {
        options.set("crf", if s.draft { "28" } else { "20" });
        options.set("x264-params", &format!("vbv-maxrate={kbps}:vbv-bufsize={}", kbps * 2));
    }
    video.set_flags(flags);
    Ok(video.open_with(options).context("start libx264")?.0.0)
}

/// The channel layout of an audio decoder, as filters name it.
fn layout_name(context: *const ffi::AVCodecContext) -> String {
    unsafe {
        let mut layout = std::mem::zeroed::<ffi::AVChannelLayout>();
        let channels = (*context).ch_layout.nb_channels.max(1);
        if (*context).ch_layout.order == ffi::AVChannelOrder::AV_CHANNEL_ORDER_UNSPEC || ffi::av_channel_layout_check(&(*context).ch_layout) == 0 {
            ffi::av_channel_layout_default(&mut layout, channels);
        } else {
            ffi::av_channel_layout_copy(&mut layout, &(*context).ch_layout);
        }
        let mut name = [0 as std::ffi::c_char; 64];
        ffi::av_channel_layout_describe(&layout, name.as_mut_ptr(), name.len());
        ffi::av_channel_layout_uninit(&mut layout);
        CStr::from_ptr(name.as_ptr()).to_string_lossy().into_owned()
    }
}

/// The sound encoder for what the graph's sink gives out.
fn sound_encoder(graph: &mut filter::Graph, codec: AudioCodec, kbps: u32) -> Result<encoder::Encoder> {
    let found = match codec {
        AudioCodec::Aac => encoder::find(codec::Id::AAC),
        AudioCodec::Mp3 => encoder::find_by_name("libmp3lame"),
        AudioCodec::Flac => encoder::find(codec::Id::FLAC),
        AudioCodec::Pcm => encoder::find(codec::Id::PCM_S16LE),
    };
    let mut audio = codec::context::Context::new_with_codec(found.with_context(|| format!("no {codec:?} encoder"))?).encoder().audio()?;
    let sink = unsafe { graph.get("out").unwrap().as_ptr() };
    let rate = unsafe { ffi::av_buffersink_get_sample_rate(sink) };
    audio.set_rate(rate);
    unsafe { ffi::av_buffersink_get_ch_layout(sink, &mut (*audio.as_mut_ptr()).ch_layout) };
    audio.set_format(sample_format(codec));
    if !codec.lossless() {
        audio.set_bit_rate(kbps as usize * 1000);
    }
    audio.set_time_base((1, rate));
    audio.set_flags(codec::Flags::GLOBAL_HEADER);
    let encoder = audio.open_with(Dictionary::new()).with_context(|| format!("start {codec:?}"))?.0.0;
    // Encoders with a fixed frame size get frames of exactly that size.
    let frame_size = unsafe { (*encoder.as_ptr()).frame_size };
    if frame_size > 0 {
        graph.get("out").unwrap().sink().set_frame_size(frame_size as u32);
    }
    Ok(encoder)
}

fn video_chain(input: &format::context::Input, s: &Settings, pass: Pass, from: f64, to: Option<f64>, cuts: &[(f64, f64)]) -> Result<(Chain, Encoding)> {
    let index = find(input, Kind::Video).context("no video")?;
    let stream = input.stream(index).unwrap();
    let decoder = decoder_for(&stream)?.decoder().video()?;
    let base = stream.time_base();
    let args = picture_source(decoder.width(), decoder.height(), decoder.format(), base, decoder.aspect_ratio());
    let mut spec = Vec::new();
    if s.start > 0.0 || to.is_some() {
        spec.push(format!("trim=start={from}{}", to.map(|t| format!(":end={t}")).unwrap_or_default()));
    }
    // The cut parts closed up.
    spec.push(format!("setpts='(PTS-({from}+{})/TB)/{}'", cut_before(cuts), s.speed));
    // The chosen frame rate; speeding up keeps the source's rather than packing in more frames.
    let fps = s.rate.or((s.speed > 1.0).then_some(s.fps));
    if let Some(fps) = fps {
        spec.push(format!("fps={fps}"));
    }
    let turns = quarter_turns(&stream);
    match turns {
        1 => spec.push("transpose=clock".into()),
        2 => spec.push("hflip,vflip".into()),
        3 => spec.push("transpose=cclock".into()),
        _ => {}
    }
    let landscape = (decoder.width() >= decoder.height()) == turns.is_multiple_of(2);
    spec.push(match s.height {
        Some(h) if landscape => format!("scale=-2:{h}"),
        Some(h) => format!("scale={h}:-2"),
        // 4:2:0 needs even sides.
        None => "scale=trunc(iw/2)*2:trunc(ih/2)*2".into(),
    });
    spec.push("format=yuv420p".into());
    let mut graph = graph("buffer", &args, &spec.join(","))?;

    let sink = unsafe { graph.get("out").unwrap().as_ptr() };
    let (width, height, sar) = unsafe { (ffi::av_buffersink_get_w(sink), ffi::av_buffersink_get_h(sink), ffi::av_buffersink_get_sample_aspect_ratio(sink)) };
    let fps = fps.unwrap_or(s.fps * s.speed);
    let encoder_base = sink_time_base(&mut graph);
    let mut encoder = video_encoder(width as u32, height as u32, encoder_base, fps, sar.into(), s, pass)?;
    // The source's colours, so players show them the same.
    unsafe {
        let (from, to) = (decoder.as_ptr(), encoder.as_mut_ptr());
        (*to).color_primaries = (*from).color_primaries;
        (*to).color_trc = (*from).color_trc;
        (*to).colorspace = (*from).colorspace;
    }
    let chain = Chain { index, decoder: decoder.0, graph, encoder_base, end: to, cuts: cuts.to_vec(), input_base: base, graph_base: base, done: false };
    Ok((chain, Encoding { encoder, base: encoder_base, out: None, reached: 0.0 }))
}

fn audio_chain(input: &format::context::Input, s: &Settings, from: f64, to: Option<f64>, cuts: &[(f64, f64)]) -> Result<(Chain, Encoding)> {
    let index = find(input, Kind::Audio).context("no sound")?;
    let stream = input.stream(index).unwrap();
    let decoder = decoder_for(&stream)?.decoder().audio()?;
    let base = stream.time_base();
    let graph_base = Rational::new(1, decoder.rate() as i32);
    let args = sound_source(&decoder, graph_base);
    let mut spec = Vec::new();
    if s.start > 0.0 || to.is_some() {
        spec.push(format!("atrim=start={from}{}", to.map(|t| format!(":end={t}")).unwrap_or_default()));
    }
    if cuts.is_empty() {
        spec.push(format!("asetpts=PTS-({from})/TB"));
    } else {
        // Whole sound frames were left out; the samples kept run on without a gap.
        spec.push("asetpts=N/SR/TB".into());
    }
    if s.speed != 1.0 {
        spec.push(tempo(s.speed));
    }
    spec.push(sound_format(s.audio_codec));
    let mut graph = graph("abuffer", &args, &spec.join(","))?;
    let encoder = sound_encoder(&mut graph, s.audio_codec, s.audio_kbps)?;
    let encoder_base = encoder.time_base();
    let chain = Chain { index, decoder: decoder.0, graph, encoder_base, end: to, cuts: cuts.to_vec(), input_base: base, graph_base, done: false };
    Ok((chain, Encoding { encoder, base: encoder_base, out: None, reached: 0.0 }))
}

/// One read of the source: decode, filter, encode, and write unless it is a first pass.
fn run(input: &Path, output: Option<&Path>, s: &Settings, pass: Pass, stop: &dyn Fn() -> bool, progress: &mut dyn FnMut(f64)) -> Result<()> {
    let mut source = open(input)?;
    let origin = origin(&source);
    let (from, to) = (origin + s.start, s.end.map(|end| origin + end));
    let cuts: Vec<(f64, f64)> = s.cuts.iter().map(|&(a, b)| (origin + a, origin + b)).collect();
    let removed: f64 = s.cuts.iter().map(|(a, b)| b - a).sum();
    let length = (s.end.unwrap_or(source.duration() as f64 / super::AV_TIME_BASE) - s.start - removed).max(0.001) / s.speed;

    let (mut chains, mut encodings): (Vec<Chain>, Vec<Encoding>) = (Vec::new(), Vec::new());
    if s.video {
        let (chain, encoding) = video_chain(&source, s, pass, from, to, &cuts)?;
        chains.push(chain);
        encodings.push(encoding);
    }
    // The first pass only looks at the picture.
    if s.audio && pass != Pass::First {
        let (chain, encoding) = audio_chain(&source, s, from, to, &cuts)?;
        chains.push(chain);
        encodings.push(encoding);
    }
    let mut output = match output {
        Some(path) => {
            let mut file = create(path, s.container)?;
            let streams: Vec<usize> = encodings.iter().map(|e| add_stream(&mut file, &e.encoder, s.container)).collect::<Result<_>>()?;
            write_header(&mut file, s.container)?;
            for (encoding, index) in encodings.iter_mut().zip(streams) {
                encoding.out = Some((index, file.stream(index).unwrap().time_base()));
            }
            Some(file)
        }
        None => None,
    };

    if s.start > 0.0 {
        // To the keyframe before the start; the trim filters drop what comes before it.
        let ts = (from * super::AV_TIME_BASE) as i64;
        let _ = source.seek(ts, ..ts);
    }
    // Decoding runs on its own thread: done in turn with the encoding, each waited for the other.
    thread::scope(|scope| {
        // Made here, so that leaving early drops `frames` and the decoding thread stops.
        let (sender, frames) = sync_channel::<Message>(FRAMES_AHEAD);
        let reading = scope.spawn(move || read(&mut source, &mut chains, &sender));
        loop {
            if stop() {
                bail!("cancelled");
            }
            let (n, frame) = match frames.recv_timeout(Duration::from_millis(100)) {
                Ok(message) => message,
                Err(RecvTimeoutError::Timeout) => continue,
                Err(RecvTimeoutError::Disconnected) => break,
            };
            encodings[n].encode(frame.as_ref(), &mut output)?;
            progress((encodings[n].reached / length).min(1.0));
        }
        reading.join().expect("the decoding thread")
    })?;
    if let Some(mut file) = output {
        file.write_trailer().context("finish")?;
    }
    progress(1.0);
    Ok(())
}

/// A photo scaled to fit `w` × `h` without distortion, centred on black, as BT.709 YUV.
fn picture(path: &Path, w: u32, h: u32) -> Result<frame::Video> {
    let mut input = open(path)?;
    let stream = input.streams().best(ff::media::Type::Video).context("not a picture")?;
    let index = stream.index();
    let mut decoder = decoder_for(&stream)?.decoder().video()?;
    let mut decoded = frame::Video::empty();
    let mut packet = Packet::empty();
    loop {
        match packet.read(&mut input) {
            Ok(()) if packet.stream() == index => {
                let _ = decoder.send_packet(&packet);
            }
            Ok(()) => continue,
            Err(_) => {
                let _ = decoder.send_eof();
            }
        }
        match decoder.receive_frame(&mut decoded) {
            Ok(()) => break,
            Err(ff::Error::Eof) => bail!("{}: no picture", path.display()),
            Err(_) => {}
        }
    }
    let args = picture_source(decoded.width(), decoded.height(), decoded.format(), Rational::new(1, SLIDE_FPS), decoded.aspect_ratio());
    let spec = format!(
        "scale=w={w}:h={h}:force_original_aspect_ratio=decrease:out_color_matrix=bt709:out_range=tv,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    );
    let mut graph = graph("buffer", &args, &spec)?;
    decoded.set_pts(Some(0));
    graph.get("in").unwrap().source().add(&decoded).context("filter")?;
    graph.get("in").unwrap().source().flush().context("filter")?;
    let mut fitted = frame::Video::empty();
    graph.get("out").unwrap().sink().frame(&mut fitted).context("filter")?;
    free_kind(&mut fitted);
    Ok(fitted)
}

/// Sound read from the start again whenever it runs out.
struct Loop {
    input: format::context::Input,
    index: usize,
    decoder: decoder::Audio,
    graph: filter::Graph,
    encoder: encoder::Encoder,
    rate: i64,
    /// Samples handed to the filters, and given out by them.
    fed: i64,
    made: i64,
    /// Set at the end of the sound, cleared by the next frame: a sound with no frames stops.
    rewound: bool,
}

impl Loop {
    fn new(path: &Path, kbps: u32) -> Result<Self> {
        let input = open(path)?;
        let index = find(&input, Kind::Audio).context("no sound")?;
        let stream = input.stream(index).unwrap();
        let decoder = decoder_for(&stream)?.decoder().audio()?;
        let args = sound_source(&decoder, Rational::new(1, decoder.rate() as i32));
        let mut graph = graph("abuffer", &args, &sound_format(AudioCodec::Aac))?;
        let encoder = sound_encoder(&mut graph, AudioCodec::Aac, kbps)?;
        let rate = encoder.time_base().denominator() as i64;
        Ok(Loop { input, index, decoder, graph, encoder, rate, fed: 0, made: 0, rewound: false })
    }

    /// Encodes sound up to `until` seconds.
    fn until(&mut self, until: f64, output: &mut format::context::Output, stream: usize, base: Rational) -> Result<()> {
        let target = (until * self.rate as f64) as i64;
        let mut frame = frame::Audio::empty();
        let mut packet = Packet::empty();
        while self.made < target {
            // Filtered sound first, then more from the file.
            if self.graph.get("out").unwrap().sink().frame(&mut frame).is_ok() {
                frame.set_pts(Some(self.made));
                self.made += frame.samples() as i64;
                self.encoder.send_frame(&frame).context("encode")?;
                self.write(output, stream, base)?;
                continue;
            }
            if self.decoder.receive_frame(&mut frame).is_ok() {
                self.rewound = false;
                frame.set_pts(Some(self.fed));
                self.fed += frame.samples() as i64;
                self.graph.get("in").unwrap().source().add(&frame).context("filter")?;
                continue;
            }
            match packet.read(&mut self.input) {
                Ok(()) if packet.stream() == self.index => {
                    let _ = self.decoder.send_packet(&packet);
                }
                Ok(()) => {}
                Err(_) if self.rewound => break,
                Err(_) => {
                    self.rewound = true;
                    self.input.seek(0, ..).context("loop the sound")?;
                    self.decoder.flush();
                }
            }
        }
        Ok(())
    }

    fn finish(&mut self, output: &mut format::context::Output, stream: usize, base: Rational) -> Result<()> {
        self.encoder.send_eof().context("encode")?;
        self.write(output, stream, base)
    }

    fn write(&mut self, output: &mut format::context::Output, stream: usize, base: Rational) -> Result<()> {
        let from = self.encoder.time_base();
        drain(&mut self.encoder, from, output, stream, base)
    }
}

fn slideshow(photos: &[PathBuf], sound: Option<&Path>, output: &Path, s: &Slides, stop: &dyn Fn() -> bool, progress: &mut dyn FnMut(f64)) -> Result<()> {
    init();
    if photos.is_empty() {
        bail!("no photos");
    }
    let (w, h) = (s.width & !1, s.height & !1);
    let codec = encoder::find_by_name("libx264").context("no x264")?;
    let mut context = codec::context::Context::new_with_codec(codec);
    context.set_threading(AUTO_THREADS);
    let mut video = context.encoder().video()?;
    video.set_width(w);
    video.set_height(h);
    video.set_format(format::Pixel::YUV420P);
    video.set_time_base((1, SLIDE_FPS));
    video.set_frame_rate(Some((SLIDE_FPS, 1)));
    video.set_flags(codec::Flags::GLOBAL_HEADER);
    unsafe {
        let v = video.as_mut_ptr();
        (*v).color_primaries = ffi::AVColorPrimaries::AVCOL_PRI_BT709;
        (*v).color_trc = ffi::AVColorTransferCharacteristic::AVCOL_TRC_BT709;
        (*v).colorspace = ffi::AVColorSpace::AVCOL_SPC_BT709;
    }
    let mut options = Dictionary::new();
    options.set("preset", s.preset.name());
    options.set("tune", "stillimage");
    options.set("crf", "20");
    options.set("x264-params", &format!("vbv-maxrate={k}:vbv-bufsize={}", s.video_kbps * 2, k = s.video_kbps));
    let mut encoder = video.open_with(options).context("start x264")?.0.0;

    let mut file = create(output, Container::Mp4)?;
    let video_stream = add_stream(&mut file, &encoder, Container::Mp4)?;
    let mut sound = sound.map(|path| Loop::new(path, s.audio_kbps)).transpose()?;
    let audio_stream = sound.as_ref().map(|l| add_stream(&mut file, &l.encoder, Container::Mp4)).transpose()?;
    write_header(&mut file, Container::Mp4)?;
    let video_base = file.stream(video_stream).unwrap().time_base();
    let audio_base = audio_stream.map(|i| file.stream(i).unwrap().time_base());

    let total = (photos.len() as f64 * s.seconds * SLIDE_FPS as f64).round().max(1.0) as i64;
    let mut shown = (usize::MAX, frame::Video::empty());
    let encoder_base = Rational::new(1, SLIDE_FPS);
    for i in 0..total {
        if stop() {
            bail!("cancelled");
        }
        let photo = ((i as f64 / SLIDE_FPS as f64 / s.seconds) as usize).min(photos.len() - 1);
        if photo != shown.0 {
            shown = (photo, picture(&photos[photo], w, h)?);
        }
        shown.1.set_pts(Some(i));
        encoder.send_frame(&shown.1).context("encode")?;
        drain(&mut encoder, encoder_base, &mut file, video_stream, video_base)?;
        if let (Some(sound), Some(stream), Some(base)) = (sound.as_mut(), audio_stream, audio_base) {
            sound.until((i + 1) as f64 / SLIDE_FPS as f64, &mut file, stream, base)?;
        }
        progress(i as f64 / total as f64);
    }
    encoder.send_eof().context("encode")?;
    drain(&mut encoder, encoder_base, &mut file, video_stream, video_base)?;
    if let (Some(sound), Some(stream), Some(base)) = (sound.as_mut(), audio_stream, audio_base) {
        sound.finish(&mut file, stream, base)?;
    }
    file.write_trailer().context("finish")?;
    progress(1.0);
    Ok(())
}

#[cfg(test)]
pub(super) mod tests {
    use super::super::tests::{Temp, fixture};
    use super::*;
    use crate::media::{VideoCodec, probe};

    pub fn settings(stats: &Path) -> Settings {
        Settings {
            start: 0.0,
            end: None,
            cuts: Vec::new(),
            speed: 1.0,
            video: true,
            audio: true,
            height: None,
            fps: 25.0,
            rate: None,
            container: Container::Mp4,
            audio_codec: AudioCodec::Aac,
            video_kbps: 200,
            audio_kbps: 64,
            exact: false,
            stats: stats.to_path_buf(),
            preset: Preset::Faster,
            draft: false,
        }
    }

    fn encode(s: &Settings) -> (Probe, u64) {
        let out = Temp::new(&format!("out.{}", s.container.ext()));
        let mut last = 0.0;
        Ffmpeg { stop: &|| false }.transcode(&fixture("clip.ts"), &out.0, s, &mut |f| last = f).unwrap();
        assert_eq!(last, 1.0);
        (probe(&out.0).unwrap(), std::fs::metadata(&out.0).unwrap().len())
    }

    use crate::media::Probe;

    #[test]
    fn trims_speeds_and_resizes() {
        let stats = Temp::new("x264.log");
        let (p, _) = encode(&Settings { start: 0.2, end: Some(0.8), speed: 2.0, height: Some(60), ..settings(&stats.0) });
        // 0.6 s at double speed.
        assert!((p.duration - 0.3).abs() < 0.08, "{p:?}");
        let video = p.video.unwrap();
        assert_eq!((video.width, video.height), (106, 60));
        assert!(p.audio);
        // Slowed down, without the sound.
        let (p, _) = encode(&Settings { speed: 0.5, audio: false, ..settings(&stats.0) });
        assert!((p.duration - 2.0).abs() < 0.1, "{p:?}");
        assert!(!p.audio);
        // Much slower or faster, the sound too.
        let (p, _) = encode(&Settings { speed: 0.25, ..settings(&stats.0) });
        assert!((p.duration - 4.0).abs() < 0.2 && p.audio, "{p:?}");
        let (p, _) = encode(&Settings { speed: 5.0, ..settings(&stats.0) });
        assert!((p.duration - 0.2).abs() < 0.1 && p.audio, "{p:?}");
        assert_eq!(tempo(0.1), "atempo=0.5,atempo=0.5,atempo=0.5,atempo=0.8");
        // A lower frame rate (the average counts the last frame's length short).
        let (p, _) = encode(&Settings { rate: Some(10.0), ..settings(&stats.0) });
        assert!((p.video.unwrap().fps - 10.0).abs() < 1.5, "{p:?}");
    }

    #[test]
    fn cuts_parts_out() {
        let stats = Temp::new("x264.log");
        // 0.6 s kept of the 1 s clip, picture and sound.
        let (p, _) = encode(&Settings { cuts: vec![(0.2, 0.4), (0.6, 0.8)], ..settings(&stats.0) });
        assert!((p.duration - 0.6).abs() < 0.08 && p.audio, "{p:?}");
        // Within an extract, at double speed: 0.5 s left of 0.1–0.9, 0.25 s.
        let (p, _) = encode(&Settings { start: 0.1, end: Some(0.9), cuts: vec![(0.3, 0.6)], speed: 2.0, ..settings(&stats.0) });
        assert!((p.duration - 0.25).abs() < 0.08 && p.audio, "{p:?}");
        // The sound alone.
        let (p, _) = encode(&Settings { video: false, cuts: vec![(0.0, 0.5)], ..settings(&stats.0) });
        assert!((p.duration - 0.5).abs() < 0.08, "{p:?}");
    }

    #[test]
    fn every_codec_and_container() {
        let stats = Temp::new("enc.log");
        use AudioCodec::*;
        for (container, audio) in [(Container::Mp4, Mp3), (Container::Mp4, Flac), (Container::Mov, Pcm), (Container::Mkv, Pcm), (Container::Mkv, Flac)] {
            for exact in [false, true] {
                let s = Settings { container, audio_codec: audio, exact, ..settings(&stats.0) };
                let (p, _) = encode(&s);
                assert_eq!((p.video.unwrap().codec, p.audio_codec), (Some(VideoCodec::H264), Some(audio)), "{s:?}");
            }
        }
        for container in [Container::M4a, Container::Mp3, Container::Flac, Container::Wav] {
            let audio = container.sound().unwrap();
            let (p, _) = encode(&Settings { container, video: false, audio_codec: audio, ..settings(&stats.0) });
            assert_eq!((p.video, p.audio_codec), (None, Some(audio)));
        }
    }

    #[test]
    fn every_preset() {
        let stats = Temp::new("enc.log");
        for preset in [Preset::Veryfast, Preset::Faster, Preset::Medium, Preset::Slow] {
            let s = Settings { preset, audio: false, ..settings(&stats.0) };
            assert_eq!(encode(&s).0.video.unwrap().codec, Some(VideoCodec::H264), "{s:?}");
        }
        // x264 notes its settings in the file: one reference picture at veryfast, five at slow.
        for (preset, refs) in [(Preset::Veryfast, " ref=1 "), (Preset::Slow, " ref=5 ")] {
            let out = Temp::new("preset.mp4");
            let s = Settings { preset, audio: false, ..settings(&stats.0) };
            Ffmpeg { stop: &|| false }.transcode(&fixture("clip.ts"), &out.0, &s, &mut |_| {}).unwrap();
            let bytes = std::fs::read(&out.0).unwrap();
            assert!(bytes.windows(refs.len()).any(|w| w == refs.as_bytes()), "{preset:?}");
        }
    }

    #[test]
    fn the_encoder_places_its_own_keyframes() {
        // The clip has 3 I frames, one a keyframe; copying them would add 2 more.
        let stats = Temp::new("enc.log");
        let out = Temp::new("keys.mp4");
        let s = Settings { audio: false, ..settings(&stats.0) };
        Ffmpeg { stop: &|| false }.transcode(&fixture("clip.ts"), &out.0, &s, &mut |_| {}).unwrap();
        let mut file = format::input(&out.0).unwrap();
        assert_eq!(file.packets().filter(|(_, packet)| packet.is_key()).count(), 1);
    }

    #[test]
    fn reads_h265_and_av1() {
        // FFmpeg decodes H.265 and dav1d AV1; the copy is H.264.
        let stats = Temp::new("enc.log");
        for (name, codec) in [("clip-h265.mkv", VideoCodec::H265), ("clip-av1.mkv", VideoCodec::Av1)] {
            assert_eq!(probe(&fixture(name)).unwrap().video.unwrap().codec, Some(codec));
            let out = Temp::new("from.mp4");
            Ffmpeg { stop: &|| false }.transcode(&fixture(name), &out.0, &settings(&stats.0), &mut |_| {}).unwrap();
            let p = probe(&out.0).unwrap();
            assert_eq!((p.video.unwrap().codec, p.audio), (Some(VideoCodec::H264), true), "{name}");
            assert!((p.duration - 1.0).abs() < 0.1, "{name}: {p:?}");
        }
    }

    #[test]
    fn sound_timed_in_whole_frames() {
        // An AVI times its MP3 sound in steps of 1152 samples; AAC takes frames of 1024.
        let stats = Temp::new("enc.log");
        let out = Temp::new("from.mp4");
        Ffmpeg { stop: &|| false }.transcode(&fixture("clip-mp3.avi"), &out.0, &settings(&stats.0), &mut |_| {}).unwrap();
        let p = probe(&out.0).unwrap();
        assert_eq!((p.audio_codec, (p.duration - 1.0).abs() < 0.1), (Some(AudioCodec::Aac), true), "{p:?}");
    }

    #[test]
    fn two_passes_meet_the_bitrate() {
        let stats = Temp::new("x264.log");
        let (p, size) = encode(&Settings { exact: true, video_kbps: 40, audio_kbps: 32, ..settings(&stats.0) });
        assert!(p.video.is_some() && p.audio);
        // About (40 + 32) kbps over one second, plus the container.
        assert!(size < 15_000, "{size}");
        // The sound alone.
        let (p, _) = encode(&Settings { video: false, ..settings(&stats.0) });
        assert!(p.video.is_none() && p.audio);
    }

    #[test]
    fn cancelling_stops() {
        let out = Temp::new("cancelled.mp4");
        let stats = Temp::new("x264.log");
        let err = Ffmpeg { stop: &|| true }.transcode(&fixture("clip.ts"), &out.0, &settings(&stats.0), &mut |_| {}).unwrap_err();
        assert_eq!(err.params["detail"], "cancelled");
    }

    #[test]
    fn slideshow_from_photos() {
        // A portrait photo letterboxed into a landscape frame, over a 1 s sound.
        let photo = fixture("photo.jpg");
        let fitted = picture(&photo, 320, 240).unwrap();
        assert_eq!((fitted.width(), fitted.height(), fitted.format()), (320, 240, format::Pixel::YUV420P));

        let out = Temp::new("slides.mp4");
        let slides = Slides { width: 320, height: 240, seconds: 0.6, video_kbps: 500, audio_kbps: 64, preset: Preset::Faster };
        Ffmpeg { stop: &|| false }.slideshow(&[photo.clone(), photo], Some(&fixture("clip.ts")), &out.0, &slides, &mut |_| {}).unwrap();
        let p = probe(&out.0).unwrap();
        assert_eq!(p.video.map(|v| (v.width, v.height)), Some((320, 240)));
        // 1.2 s of photos: the sound loops to the end.
        assert!((p.duration - 1.2).abs() < 0.1, "{p:?}");
        assert!(p.audio);
    }
}
