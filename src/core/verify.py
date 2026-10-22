"""Checking a finished file (Settings): it opens, lasts as long as expected, has its sound and picture, and its
end can be read. A file cut short by a dropped connection or a full disk is reported instead of saved silently."""

import subprocess

from core.backend import BACKEND, FFMPEG_BIN, av
from core.system import NO_WINDOW
from core.units import format_duration


SHORT_BY = 0.03  # a file may be a little shorter than announced (rounding, the last frames)
SHORT_MARGIN = 2.0  # seconds


def _streams(path):
    """(duration in seconds or None, has sound, has picture) of the file."""
    if BACKEND == "ffmpeg":
        from core.ffmpeg import media_codecs, media_duration
        video, audio = media_codecs(path)
        return media_duration(path), bool(audio), bool(video)
    with av.open(str(path)) as c:
        duration = c.duration / 1_000_000 if c.duration else None
        pictures = [s for s in c.streams.video if not s.disposition.attached_pic]
        return duration, bool(c.streams.audio), bool(pictures)


def _end_readable(path):
    """ffmpeg decodes the last seconds: False when there is nothing there or it cannot be decoded (a truncated
    file whose header still announces its whole length)."""
    r = subprocess.run([FFMPEG_BIN, "-v", "error", "-sseof", "-3", "-i", str(path), "-f", "framecrc", "-"],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120, **NO_WINDOW)
    frames = [line for line in r.stdout.splitlines() if line.strip() and not line.startswith("#")]
    return r.returncode == 0 and bool(frames) and "error" not in r.stderr.lower()


def check_media(path, expected=None, sound=True, picture=False):
    """The problems found in the file `path`: [] when it looks complete. `expected`: its length in seconds,
    when known; `sound`, `picture`: what it should hold."""
    if BACKEND is None:
        return []
    try:
        duration, has_sound, has_picture = _streams(path)
    except Exception:
        duration, has_sound, has_picture = None, False, False
    if not duration:
        return ["The file cannot be read: it may be damaged."]
    problems = []
    if expected and duration < expected * (1 - SHORT_BY) - SHORT_MARGIN:
        problems.append("It lasts %s instead of %s: it may be cut short." % (format_duration(duration) or "0:00",
                                                                           format_duration(expected)))
    if sound and not has_sound:
        problems.append("It has no sound.")
    if picture and not has_picture:
        problems.append("It has no picture.")
    if BACKEND == "ffmpeg" and not _end_readable(path):
        problems.append("Its end cannot be read: it may be cut short.")
    return problems
