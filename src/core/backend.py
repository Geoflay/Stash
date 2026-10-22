"""The media tool used for merging and converting (ffmpeg, else PyAV), and the output
formats it can make."""

import functools
import importlib.util

from core.ffmpeg_setup import find_ffmpeg


try:
    import av
    av.logging.set_level(av.logging.ERROR)
except Exception:
    av = None


HAS_PIL = importlib.util.find_spec("PIL") is not None


@functools.lru_cache(maxsize=None)
def _encoder_available(name):
    if av is None:
        return False
    try:
        av.Codec(name, "w")
        return True
    except Exception:
        return False


def _pick_video_encoder():
    for name in ("libx264", "libopenh264", "mpeg4"):
        if _encoder_available(name):
            return name
    return None


# The folder chosen in Settings, the app's own copy, or the PATH (see core/ffmpeg_setup.py).
FFMPEG_BIN, FFMPEG_SOURCE = find_ffmpeg()
BACKEND = "ffmpeg" if FFMPEG_BIN else ("av" if av is not None else None)
VIDEO_ENCODER = _pick_video_encoder() if BACKEND == "av" else None

CAN_CLIP = BACKEND is not None
CAN_SLIDESHOW = BACKEND == "ffmpeg" or (
    BACKEND == "av" and HAS_PIL and VIDEO_ENCODER is not None and _encoder_available("aac")
)

# Output formats. "audio" keeps the original audio stream as-is.
VIDEO_FORMATS = {"mp4": "MP4", "mkv": "MKV", "webm": "WebM", "mov": "MOV"}
AUDIO_FORMATS = {"mp3": "MP3", "m4a": "M4A (AAC)", "opus": "Opus", "flac": "FLAC (lossless)",
                 "wav": "WAV (uncompressed)", "audio": "Original"}
# Made from a passage of the video (GIF) or one moment of it (JPG, PNG); ffmpeg only.
IMAGE_FORMATS = {"gif": "GIF (animated)", "jpg": "JPG image", "png": "PNG image"}
# PyAV encoding of each converted audio format: (encoder, muxer, bit rate, sample rate).
AV_AUDIO_CODECS = {
    "mp3": ("libmp3lame", "mp3", 256_000, 44100),
    "m4a": ("aac", "ipod", 192_000, 44100),
    "opus": ("libopus", "ogg", 160_000, 48000),  # Opus only supports 48 kHz (among common rates)
    "flac": ("flac", "flac", None, 44100),
    "wav": ("pcm_s16le", "wav", None, 44100),
}
# yt-dlp ExtractAudio quality ("0" = best VBR for MP3, larger numbers are kbit/s); lossless formats have none.
FFMPEG_AUDIO_QUALITY = {"mp3": "0", "m4a": "192", "opus": "160"}
# Rough output size per second, for the estimate shown before downloading.
AUDIO_BYTES_PER_SECOND = {"mp3": 32_000, "m4a": 24_000, "opus": 20_000, "flac": 90_000, "wav": 176_400}
# Typical sizes per second for the playlist estimate (each video's own sizes are not known before it is read):
# the site's own audio, and a video with its sound by height.
ORIGINAL_AUDIO_BYTES_PER_SECOND = 17_000
VIDEO_BYTES_PER_SECOND = {360: 90_000, 480: 140_000, 720: 300_000, 1080: 560_000}


def format_available(fmt):
    """Whether this machine can produce `fmt` (PyAV only remuxes to MP4/MKV and needs each audio encoder)."""
    if fmt in ("mp4", "audio"):
        return True
    if BACKEND == "ffmpeg":
        return True
    if BACKEND == "av":
        return fmt == "mkv" or (fmt in AV_AUDIO_CODECS and _encoder_available(AV_AUDIO_CODECS[fmt][0]))
    return False


def format_kind(fmt):
    return "audio" if fmt in AUDIO_FORMATS else "video"


def backend_label():
    if BACKEND == "ffmpeg":
        return "ffmpeg (PATH)"
    if BACKEND == "av":
        audio = [AUDIO_FORMATS[f].split(" ")[0] for f in AV_AUDIO_CODECS if format_available(f)]
        return "PyAV (audio: %s, video: %s)" % (", ".join(audio) or "original only", VIDEO_ENCODER or "no")
    return "none"
