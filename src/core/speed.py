"""Playing speed: a video or audio file made faster or slower (the voices keep their pitch)."""

import math
from pathlib import Path

from core.ffmpeg import REENCODE_ARGS, ffmpeg_with_progress, media_codecs, media_duration
from core.jobs import entering_conversion


MIN_SPEED, MAX_SPEED = 0.1, 10.0
# Audio encoder for each kind of audio file (the speed change re-encodes the sound).
AUDIO_ARGS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".opus": ["-c:a", "libopus", "-b:a", "160k"],
    ".webm": ["-c:a", "libopus", "-b:a", "160k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    ".flac": ["-c:a", "flac"],
    ".wav": ["-c:a", "pcm_s16le"],
}


def parse_speed(text):
    """The speed typed ("1.25", "1,5x"), rounded to 2 decimals; ValueError if not between 0.1 and 10."""
    text = (text or "").strip().lower().rstrip("x×").strip().replace(",", ".")
    try:
        value = round(float(text), 2)
    except ValueError:
        value = math.nan
    if not MIN_SPEED <= value <= MAX_SPEED:
        raise ValueError("The speed must be a number between %g and %g, for example 1.25." % (MIN_SPEED, MAX_SPEED))
    return value


def atempo_chain(speed):
    """atempo filters for any speed: each one only goes from 0.5x to 2x on older ffmpeg versions."""
    factors = []
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    while speed > 2.0:
        factors.append(2.0)
        speed /= 2.0
    factors.append(speed)
    return ",".join("atempo=%.6g" % f for f in factors)


def change_speed(path, speed, workdir, notify, video=True):
    """A copy of `path` played `speed` times faster (0.5 = twice slower); atempo keeps the pitch."""
    entering_conversion()
    path = Path(path)
    out = Path(workdir, "speed" + path.suffix)
    duration = media_duration(path)
    suffix = path.suffix.lower()
    if video:
        streams = ["-map", "0:v:0", "-map", "0:a?", "-vf", "setpts=PTS/%.6g" % speed]
        codecs = (REENCODE_ARGS["webm" if suffix == ".webm" else "mp4"]
                  + (["-movflags", "+faststart"] if suffix in (".mp4", ".mov") else []))
    else:
        streams, codecs = ["-map", "0:a:0"], AUDIO_ARGS.get(suffix, [])
    # A video without sound has no sound to slow down.
    sound = ["-af", atempo_chain(speed)] if not video or media_codecs(path)[1] else []
    # Chapters would point to the wrong moments once the speed changes.
    ffmpeg_with_progress(["-i", str(path)] + streams + sound + ["-map_chapters", "-1"]
                         + codecs + [str(out)],
                         workdir, notify, "Changing the speed (%g×)…" % speed,
                         duration / speed if duration else None)
    return out
