"""Making a file fit under a size limit (for example 10 MB for Discord) by re-encoding it at a lower bit rate: with
ffmpeg (two passes), else with PyAV (Android: x264, the phone's own encoder, or MPEG-4; see pyav.av_compress)."""

import os
from pathlib import Path

from core.backend import BACKEND, VIDEO_FORMATS, format_available
from core.ffmpeg import ffmpeg_with_progress, media_codecs, media_duration
from core.jobs import entering_conversion
from core.pyav import av_compress, av_to_audio, compress_encoder, has_audio_stream, probe_duration
from core.units import format_bytes


MB = 1024 * 1024  # like the sizes the app shows (format_bytes)
# Lossy audio formats that can be made smaller; FLAC and WAV are lossless, their size cannot be chosen.
AUDIO_CODECS = {"mp3": "libmp3lame", "m4a": "aac", "opus": "libopus"}
SIZE_MARGIN = 0.94  # room for the container and for the encoder missing its target a little
MIN_VIDEO_BITRATE = 60_000
MIN_AUDIO_BITRATE = 32_000
# Picture height worth keeping for a video bit rate (bits/s): fewer pixels look better than blocky ones.
HEIGHT_FOR_BITRATE = ((2_500_000, 1080), (1_200_000, 720), (700_000, 480), (350_000, 360), (0, 240))


def fit_box(side):
    """ffmpeg scale filter: shrink the picture to fit a side x side box (landscape or portrait), never enlarge it."""
    return "scale=w='min(%d,iw)':h='min(%d,ih)':force_original_aspect_ratio=decrease" % (side, side)


# The formats PyAV writes a compressed video into (WebM needs a VP9 encoder it does not have).
AV_VIDEO_FORMATS = ("mp4", "mov", "mkv")


def can_compress(fmt):
    if BACKEND == "ffmpeg":
        return fmt in VIDEO_FORMATS or fmt in AUDIO_CODECS
    if BACKEND == "av":
        return (fmt in AV_VIDEO_FORMATS and compress_encoder() is not None) or (
            fmt in AUDIO_CODECS and format_available(fmt))
    return False


def too_long_error(max_bytes):
    return RuntimeError("This is too long to fit in %s: choose a shorter clip or a bigger size limit."
                        % format_bytes(max_bytes))


def fit_in_size(path, max_bytes, workdir, notify, reserve=0):
    """Return `path` if it is small enough, else a re-encoded copy under `max_bytes` (raises if impossible).
    `reserve`: bytes added to the file afterwards (cover art), kept free."""
    path = Path(path)
    limit = max_bytes - reserve
    if path.stat().st_size <= limit:
        return path
    entering_conversion()
    if BACKEND == "av":
        duration = probe_duration(path)
    else:
        duration = media_duration(path)
    if not duration:
        raise RuntimeError("The file's length is unknown, so it cannot be compressed to a given size.")
    is_audio = path.suffix.lstrip(".").lower() in AUDIO_CODECS
    bits = limit * 8 * SIZE_MARGIN
    for attempt in range(3):  # the encoder can still miss the target: aim lower each time
        out = Path(workdir, "compressed%d%s" % (attempt, path.suffix))
        if is_audio:
            _compress_audio(path, out, int(bits / duration), workdir, notify, duration, max_bytes)
        else:
            _compress_video(path, out, int(bits / duration), workdir, notify, duration, max_bytes)
        size = out.stat().st_size
        if size <= limit:
            return out
        out.unlink(missing_ok=True)
        bits *= limit / size * 0.95
    raise RuntimeError("The file could not be compressed under %s." % format_bytes(max_bytes))


def _compress_audio(src, out, bitrate, workdir, notify, duration, max_bytes):
    codec = AUDIO_CODECS[out.suffix.lstrip(".").lower()]
    bitrate = min(bitrate, 192_000)
    if bitrate < MIN_AUDIO_BITRATE:
        raise too_long_error(max_bytes)
    if BACKEND == "av":
        av_to_audio(src, out, out.suffix.lstrip(".").lower(), notify, bit_rate=bitrate,
                    message="Compressing to fit in %s…" % format_bytes(max_bytes))
        return
    ffmpeg_with_progress(["-i", str(src), "-map", "0:a:0", "-c:a", codec, "-b:a", str(bitrate), str(out)],
                         workdir, notify, "Compressing to fit in %s…" % format_bytes(max_bytes), duration)


def _compress_video(src, out, bitrate, workdir, notify, duration, max_bytes):
    """Two passes: the first one measures the video, so the second one spends the bits where they are needed."""
    # A video without sound gets every bit for its picture.
    if BACKEND == "av":
        sound = has_audio_stream(src)
    else:
        sound = bool(media_codecs(src)[1])
    audio = 0 if not sound else 64_000 if bitrate < 500_000 else 96_000 if bitrate < 1_500_000 else 128_000
    video = bitrate - audio
    if video < MIN_VIDEO_BITRATE:
        raise too_long_error(max_bytes)
    height = next(h for rate, h in HEIGHT_FOR_BITRATE if video >= rate)
    if BACKEND == "av":  # one pass: PyAV has no two-pass mode
        av_compress(src, out, video, audio, height * 16 // 9, notify, duration,
                    "Compressing to fit in %s…" % format_bytes(max_bytes))
        return
    webm = out.suffix.lower() == ".webm"
    codec = (["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1"] if webm
             else ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"])
    common = ["-i", str(src), "-map", "0:v:0"] + codec + [
        "-b:v", str(video), "-vf", fit_box(height * 16 // 9) + ":force_divisible_by=2",
        "-passlogfile", str(Path(workdir, "pass"))]
    message = "Compressing to fit in %s (step %d of 2)…"
    ffmpeg_with_progress(common + ["-pass", "1", "-an", "-f", "null", os.devnull],
                         workdir, notify, message % (format_bytes(max_bytes), 1), duration)
    sound = ["-c:a", "libopus" if webm else "aac", "-b:a", str(audio)]
    faststart = ["-movflags", "+faststart"] if out.suffix.lower() in (".mp4", ".mov") else []
    ffmpeg_with_progress(common + ["-pass", "2", "-map", "0:a:0?"] + sound + faststart + [str(out)],
                         workdir, notify, message % (format_bytes(max_bytes), 2), duration)
