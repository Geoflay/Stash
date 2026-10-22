"""Running ffmpeg, and changing a video's container."""

import re
import subprocess
import tempfile
from pathlib import Path

from core.backend import FFMPEG_BIN, VIDEO_FORMATS
from core.jobs import entering_conversion
from core.system import NO_WINDOW
from core.units import format_duration


def last_line(text, default):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else default


def run_ffmpeg(args, workdir):
    r = subprocess.run([FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error"] + args,
                       cwd=workdir, capture_output=True, text=True, timeout=900, **NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("Could not create the video: " + last_line(r.stderr, "ffmpeg error"))


def media_duration(path):
    """Length of a media file in seconds, read by ffmpeg (None if unknown)."""
    # The tags printed (title, description…) are UTF-8 whatever the system's code page.
    r = subprocess.run([FFMPEG_BIN, "-hide_banner", "-i", str(path)], capture_output=True, encoding="utf-8",
                       errors="replace", timeout=60, **NO_WINDOW)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def media_codecs(path):
    """(video codec, audio codec) of a file as ffmpeg names them ("" when there is none)."""
    r = subprocess.run([FFMPEG_BIN, "-hide_banner", "-i", str(path)], capture_output=True, encoding="utf-8",
                       errors="replace", timeout=60, **NO_WINDOW)
    video = re.search(r"Stream #.*?: Video: (\w+)(?!.*attached pic)", r.stderr)
    audio = re.search(r"Stream #.*?: Audio: (\w+)", r.stderr)
    return (video.group(1) if video else ""), (audio.group(1) if audio else "")


def strip_audio(path, workdir):
    """A copy of the video without its sound (the streams are copied, nothing is re-encoded)."""
    path = Path(path)
    out = Path(workdir, "silent" + path.suffix)
    run_ffmpeg(["-i", str(path), "-map", "0", "-map", "-0:a", "-c", "copy", str(out)], workdir)
    return out


def ensure_compatible(path, workdir, notify):
    """Maximum compatibility (Settings > Advanced): a video that is not H.264 + AAC is re-encoded to it."""
    video, audio = media_codecs(path)
    if video in ("h264", "") and audio in ("aac", ""):
        return path
    entering_conversion()
    path = Path(path)
    out = Path(workdir, "compatible" + path.suffix)
    streams = ["-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0"]
    codecs = (["-c:v", "copy"] if video == "h264" else REENCODE_ARGS["mp4"][:6]) + REENCODE_ARGS["mp4"][6:]
    faststart = ["-movflags", "+faststart"] if path.suffix.lower() in (".mp4", ".mov") else []
    ffmpeg_with_progress(streams + codecs + faststart + [str(out)], workdir, notify,
                         "Converting to H.264 for compatibility (slow)…", media_duration(path))
    return out


def ffmpeg_with_progress(args, workdir, notify, message, duration=None):
    """Run a possibly long ffmpeg job, reporting how much is encoded (as a fraction of `duration` when
    known); Cancel (raised by notify) stops it."""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errors:
        proc = subprocess.Popen([FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
                                 "-progress", "pipe:1"] + args,
                                cwd=workdir, stdout=subprocess.PIPE, stderr=errors, text=True, **NO_WINDOW)
        try:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit():
                    seconds = int(value) / 1_000_000
                    if duration:
                        notify(min(seconds / duration, 1.0), message)
                    else:
                        notify(None, "%s %s" % (message, format_duration(seconds) or "0:00"))
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if proc.returncode != 0:
            errors.seek(0)
            raise RuntimeError("Conversion failed: " + last_line(errors.read(), "ffmpeg error"))


# Used when the downloaded streams cannot be copied as-is into the requested container.
REENCODE_ARGS = {
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-deadline", "good", "-cpu-used", "4",
             "-row-mt", "1", "-c:a", "libopus", "-b:a", "160k"],
    "mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k"],
}


def convert_container(src, fmt, workdir, notify):
    """Put the downloaded streams in the `fmt` container: copied when they fit, re-encoded otherwise."""
    entering_conversion()
    dst = Path(workdir, "converted." + fmt)
    faststart = ["-movflags", "+faststart"] if fmt in ("mp4", "mov") else []
    streams = ["-i", str(src), "-map", "0:v:0?", "-map", "0:a?"]
    notify(None, "Converting to %s…" % VIDEO_FORMATS[fmt])
    try:
        run_ffmpeg(streams + ["-c", "copy"] + faststart + [str(dst)], workdir)
        return dst
    except RuntimeError:
        dst.unlink(missing_ok=True)
    try:
        ffmpeg_with_progress(streams + REENCODE_ARGS.get(fmt, REENCODE_ARGS["mp4"]) + faststart + [str(dst)],
                              workdir, notify, "Re-encoding to %s (slow)…" % VIDEO_FORMATS[fmt])
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    return dst
