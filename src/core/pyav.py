"""Merging, cutting, converting and compressing with PyAV, used when ffmpeg is not installed (Android)."""

import functools
from fractions import Fraction
from pathlib import Path

from core.backend import AUDIO_FORMATS, AV_AUDIO_CODECS, VIDEO_ENCODER, av
from core.jobs import entering_conversion


AUDIO_RATE = 44100


def probe_duration(path):
    try:
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1_000_000
            streams = list(c.streams.audio) or list(c.streams.video)
            if streams and streams[0].duration and streams[0].time_base:
                return float(streams[0].duration * streams[0].time_base)
    except Exception:
        pass
    return None


def has_audio_stream(path):
    with av.open(str(path)) as c:
        return len(c.streams.audio) > 0


def _add_copy_stream(out, template):
    if hasattr(out, "add_stream_from_template"):
        try:
            return out.add_stream_from_template(template)
        except av.codec.codec.UnknownCodecError:
            # No encoder of that name (AV1 is decoded by "libdav1d"): copy the track as-is.
            return out.add_stream_from_template(template, opaque=True)
    return out.add_stream(template=template)


def _timed_packets(container, stream):
    for p in container.demux(stream):
        if p.dts is not None:
            yield p


def av_merge(video_path, audio_path, out_path, notify):
    out_path = Path(out_path)
    options = {"movflags": "+faststart"} if out_path.suffix == ".mp4" else None
    with av.open(str(video_path)) as vin, av.open(str(audio_path)) as ain, \
            av.open(str(out_path), "w", options=options) as out:
        vs, as_ = vin.streams.video[0], ain.streams.audio[0]
        vo, ao = _add_copy_stream(out, vs), _add_copy_stream(out, as_)
        total = (vin.duration or 0) / 1_000_000
        vi, ai = _timed_packets(vin, vs), _timed_packets(ain, as_)
        nv, na = next(vi, None), next(ai, None)
        while nv is not None or na is not None:
            take_video = na is None or (nv is not None and nv.dts * nv.time_base <= na.dts * na.time_base)
            if take_video:
                pkt, target = nv, vo
                nv = next(vi, None)
                if total:
                    notify(min(float(pkt.dts * pkt.time_base) / total, 1.0), "Merging video + audio…")
            else:
                pkt, target = na, ao
                na = next(ai, None)
            pkt.stream = target
            out.mux(pkt)


def _frame_seconds(frame):
    if frame.pts is None or frame.time_base is None:
        return None
    return float(frame.pts * frame.time_base)


def _audio_frames(path, target_samples=None, start=0.0, end=None, rate=AUDIO_RATE, sample_format="fltp"):
    resampler = av.AudioResampler(format=sample_format, layout="stereo", rate=rate)
    done = 0
    while True:
        produced = False
        with av.open(str(path)) as c:
            if not c.streams.audio:
                raise RuntimeError("No audio track in the file.")
            if start > 0:
                c.seek(int(start * 1_000_000), backward=True)
            for frame in c.decode(c.streams.audio[0]):
                seconds = _frame_seconds(frame)
                if seconds is not None:
                    if seconds < start:
                        continue
                    if end is not None and seconds >= end:
                        return
                for rf in resampler.resample(frame):
                    rf.pts = done
                    rf.time_base = Fraction(1, rate)
                    done += rf.samples
                    produced = True
                    yield rf
                    if target_samples is not None and done >= target_samples:
                        return
        if not produced:
            raise RuntimeError("The audio track is empty.")
        if target_samples is None:
            return


def _new_audio_stream(out, codec, bit_rate, rate=AUDIO_RATE):
    stream = out.add_stream(codec, rate=rate)
    try:
        stream.layout = "stereo"
    except Exception:
        pass
    if bit_rate:
        stream.bit_rate = bit_rate
    return stream


def _clip_length(clip, duration):
    start, end = clip if clip else (0.0, None)
    stop = min(end, duration) if (end is not None and duration) else (end or duration)
    return (stop - start) if stop else None


def av_trim(src, dst, clip, notify, message="Cutting clip…", sound=True):
    """Copy (without re-encoding) the [start, end) range of src into dst; also used to remux whole files.
    `sound` False: the picture alone."""
    entering_conversion()
    start, end = clip
    src, dst = Path(src), Path(dst)
    options = {"movflags": "+faststart"} if dst.suffix == ".mp4" else None
    length = _clip_length(clip, probe_duration(src))
    try:
        with av.open(str(src)) as vin, av.open(str(dst), "w", options=options) as out:
            video = vin.streams.video[0] if vin.streams.video else None
            audio = vin.streams.audio[0] if vin.streams.audio and sound else None
            streams = [s for s in (video, audio) if s is not None]
            if not streams:
                raise RuntimeError("No audio or video track in the file.")
            outputs = {s.index: _add_copy_stream(out, s) for s in streams}
            anchor = video or audio
            if start > 0:
                vin.seek(int(start * 1_000_000), backward=True)
            origin = None
            finished = set()
            for pkt in vin.demux(*streams):
                if pkt.dts is None or pkt.stream.index in finished:
                    continue
                seconds = float(pkt.dts * pkt.time_base)
                if origin is None:
                    if pkt.stream.index != anchor.index:
                        continue
                    if anchor is video and not pkt.is_keyframe:
                        continue
                    if anchor is audio and seconds < start:
                        continue
                    origin = seconds
                elif seconds < origin - 0.001:
                    continue
                if end is not None and seconds >= end:
                    finished.add(pkt.stream.index)
                    if len(finished) == len(streams):
                        break
                    continue
                shift = int(origin / pkt.time_base)
                pkt.dts = max(pkt.dts - shift, 0)
                if pkt.pts is not None:
                    pkt.pts = max(pkt.pts - shift, pkt.dts)
                if length:
                    notify(min(max(seconds - origin, 0) / length, 1.0), message)
                pkt.stream = outputs[pkt.stream.index]
                out.mux(pkt)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    if origin is None:
        dst.unlink(missing_ok=True)
        raise RuntimeError("The selected range is empty.")
    return dst


def av_to_audio(src, dst, fmt, notify, clip=None, bit_rate=None, message=None):
    """`bit_rate`: another one than the format's own (a file made to fit a size)."""
    entering_conversion()
    codec, muxer, own_rate, rate = AV_AUDIO_CODECS[fmt]
    bit_rate = bit_rate or own_rate
    total = _clip_length(clip, probe_duration(src)) if clip else probe_duration(src)
    start, end = clip if clip else (0.0, None)
    message = message or "Converting to %s…" % AUDIO_FORMATS[fmt].split(" ")[0]
    try:
        with av.open(str(dst), "w", format=muxer) as out:
            stream = _new_audio_stream(out, codec, bit_rate, rate)
            for frame in _audio_frames(src, start=start, end=end, rate=rate, sample_format=stream.format.name):
                out.mux(stream.encode(frame))
                if total:
                    notify(min(frame.pts / rate / total, 1.0), message)
            out.mux(stream.encode(None))
    except Exception:
        Path(dst).unlink(missing_ok=True)
        raise


def _frame_from_photo(path, fw, fh):
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.contain(im, (fw, fh), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (fw, fh), (0, 0, 0))
        canvas.paste(im, ((fw - im.width) // 2, (fh - im.height) // 2))
    return av.VideoFrame.from_image(canvas).reformat(format="yuv420p")


def av_make_video(photos, mode, audio, per, audio_dur, fw, fh, out_path, notify):
    fps = 10
    if audio is not None:
        audio_dur = probe_duration(audio) or audio_dur
    total = (audio_dur if audio is not None else per) if mode == "cover" else per * len(photos)
    if not total or total <= 0:
        raise RuntimeError("Could not read the audio duration.")
    total_frames = max(1, round(total * fps))
    frames = [_frame_from_photo(p, fw, fh) for p in photos]
    time_base = Fraction(1, fps)

    options = {"preset": "veryfast", "tune": "stillimage"} if VIDEO_ENCODER == "libx264" else None
    with av.open(str(out_path), "w", format="mp4", options={"movflags": "+faststart"}) as out:
        vs = out.add_stream(VIDEO_ENCODER, rate=fps, options=options)
        vs.width, vs.height, vs.pix_fmt = fw, fh, "yuv420p"
        vs.bit_rate = 2_500_000
        vs.gop_size = fps
        if VIDEO_ENCODER == "mpeg4":
            try:
                vs.max_b_frames = 0
            except Exception:
                pass
        aus = _new_audio_stream(out, "aac", 192_000) if audio is not None else None

        audio_iter = _audio_frames(audio, int(total * AUDIO_RATE)) if audio is not None else iter(())
        af = next(audio_iter, None)
        n = 0
        while n < total_frames or af is not None:
            v_time = n / fps if n < total_frames else None
            if af is None or (v_time is not None and v_time <= af.pts / AUDIO_RATE):
                idx = 0 if mode == "cover" else min(int(v_time // per), len(frames) - 1)
                frame = frames[idx]
                frame.pts, frame.time_base = n, time_base
                out.mux(vs.encode(frame))
                n += 1
                if n % fps == 0:
                    notify(n / total_frames, "Creating video…")
            else:
                out.mux(aus.encode(af))
                af = next(audio_iter, None)
        out.mux(vs.encode(None))
        if aus is not None:
            out.mux(aus.encode(None))
    return Path(out_path)


# Video encoders a file is compressed with, the best first: x264, the phone's own (Android's MediaCodec, which
# works without the Java side through the NDK), OpenH264, and MPEG-4 (built into every PyAV, so always there).
COMPRESS_ENCODERS = ("libx264", "h264_mediacodec", "libopenh264", "mpeg4")


@functools.lru_cache(maxsize=1)
def compress_encoder():
    """The first video encoder of COMPRESS_ENCODERS that opens here (None: none)."""
    if av is None:
        return None
    for name in COMPRESS_ENCODERS:
        try:
            context = av.CodecContext.create(name, "w")
            context.width, context.height, context.pix_fmt = 320, 240, "yuv420p"
            context.time_base = Fraction(1, 30)
            context.bit_rate = 500_000
            context.open()
            return name
        except Exception:
            continue
    return None


def av_compress(src, out, video_rate, audio_rate, side, notify, duration, message):
    """Re-encode `src` into `out` (MP4, MOV or MKV) at `video_rate` bits/s for the picture, shrunk to fit a
    side x side box, and `audio_rate` for the sound (0: no sound)."""
    entering_conversion()
    out = Path(out)
    encoder = compress_encoder()
    if encoder is None:
        raise RuntimeError("No video encoder is available to make the file smaller.")
    options = {"movflags": "+faststart"} if out.suffix.lower() in (".mp4", ".mov") else None
    try:
        with av.open(str(src)) as source, av.open(str(out), "w", options=options) as target:
            video_in = source.streams.video[0] if source.streams.video else None
            audio_in = source.streams.audio[0] if audio_rate and source.streams.audio else None
            if video_in is None:
                raise RuntimeError("No picture in the file.")
            width, height = video_in.codec_context.width, video_in.codec_context.height
            scale = min(1.0, side / max(width, height))
            width, height = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
            fps = video_in.average_rate or Fraction(30)
            video_out = target.add_stream(encoder, rate=fps,
                                          options={"preset": "medium"} if encoder == "libx264" else None)
            video_out.width, video_out.height, video_out.pix_fmt = width, height, "yuv420p"
            video_out.bit_rate = video_rate
            if encoder == "mpeg4":
                video_out.max_b_frames = 0  # its B-frames would need more care than they save
            audio_out = _new_audio_stream(target, "aac", audio_rate) if audio_in is not None else None
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=AUDIO_RATE) if audio_out else None
            last, samples = -1, 0
            streams = [s for s in (video_in, audio_in) if s is not None]
            for packet in source.demux(*streams):
                for frame in packet.decode():
                    if packet.stream is video_in:
                        seconds = _frame_seconds(frame) or 0.0
                        pts = round(seconds * fps)
                        if pts <= last:  # a frame of the same moment (variable frame rate): left out
                            continue
                        last = pts
                        frame = frame.reformat(width=width, height=height, format="yuv420p")
                        frame.pts, frame.time_base = pts, 1 / fps
                        target.mux(video_out.encode(frame))
                        if duration:
                            notify(min(seconds / duration, 1.0), message)
                    else:
                        for sound in resampler.resample(frame):
                            sound.pts, sound.time_base = samples, Fraction(1, AUDIO_RATE)
                            samples += sound.samples
                            target.mux(audio_out.encode(sound))
            target.mux(video_out.encode(None))
            if audio_out is not None:
                target.mux(audio_out.encode(None))
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out


def av_probe(path):
    """What the Edit tab needs about a file, read by PyAV (as editor.probe_media reads it with ffmpeg)."""
    info = {"duration": None, "width": None, "height": None, "video": "", "audio": "", "audio_tracks": [],
            "subtitles": [], "tags": {}, "cover": False, "fps": None}
    with av.open(str(path)) as container:
        if container.duration:
            info["duration"] = container.duration / 1_000_000
        tags = {str(k).lower(): str(v) for k, v in (container.metadata or {}).items()}
        info["tags"] = {key: tags[key].strip() for key in ("title", "artist", "album", "album_artist", "genre",
                                                           "track", "date") if tags.get(key)}
        for stream in container.streams:
            metadata = stream.metadata or {}
            track = {"codec": stream.codec_context.name if stream.codec_context else "",
                     "lang": "" if metadata.get("language") in (None, "und") else metadata["language"],
                     "title": metadata.get("title", "")}
            if stream.type == "video":
                if _is_cover(stream):
                    info["cover"] = True
                elif not info["video"]:
                    info["video"] = track["codec"]
                    info["width"], info["height"] = stream.codec_context.width, stream.codec_context.height
                    info["fps"] = float(stream.average_rate) if stream.average_rate else None
            elif stream.type == "audio":
                info["audio"] = info["audio"] or track["codec"]
                info["audio_tracks"].append(track)
            elif stream.type == "subtitle":
                info["subtitles"].append(track)
    if not info["video"] and not info["audio"]:
        raise RuntimeError("There is no video or sound in this file.")
    return info


def _is_cover(stream):
    """A video stream that is a still picture (a sound file's cover art)."""
    try:
        return bool(stream.disposition & av.stream.Disposition.attached_pic)
    except Exception:
        return stream.frames == 1 and stream.codec_context.name in ("mjpeg", "png")


def av_cover(path):
    """The cover art of a file (its attached picture), as (bytes, mime), or None."""
    with av.open(str(path)) as container:
        for stream in container.streams.video:
            if _is_cover(stream):
                for packet in container.demux(stream):
                    data = bytes(packet)
                    if data:
                        return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return None
