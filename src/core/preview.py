"""Still frames of a video at a given time, for choosing where a clip starts and ends."""

import subprocess
from pathlib import Path

from core.backend import BACKEND, FFMPEG_BIN, HAS_PIL, av
from core.ffmpeg import last_line
from core.network import NETWORK
from core.system import NO_WINDOW


PREVIEW_PROTOCOLS = ("https", "http", "m3u8", "m3u8_native")
PREVIEW_WIDTH = 320
PREVIEW_TIMEOUT = 45


def pick_preview_source(data):
    candidates = []
    for f in data.get("formats") or []:
        url = f.get("url")
        if not url or not str(url).startswith(("http://", "https://")):
            continue
        if f.get("protocol") not in PREVIEW_PROTOCOLS or f.get("vcodec") == "none":
            continue
        height = f.get("height") or 0
        codec = str(f.get("vcodec") or "")
        rank = 0 if codec.startswith("avc") else (1 if codec.startswith(("vp", "h26")) else 2)
        candidates.append((rank, abs((height or 360) - 360), f))
    if not candidates:
        return None
    chosen = min(candidates, key=lambda c: (c[0], c[1]))[2]
    headers = dict(chosen.get("http_headers") or {})
    if chosen.get("cookies"):
        headers["Cookie"] = chosen["cookies"]
    return {"url": chosen["url"], "headers": {str(k): str(v) for k, v in headers.items()}}


def _headers(f):
    headers = dict(f.get("http_headers") or {})
    if f.get("cookies"):
        headers["Cookie"] = f["cookies"]
    return {str(k): str(v) for k, v in headers.items()}


def pick_player_source(data):
    """The streams the app's player can play, to check a video before downloading it, or None:
    {"videos": [{"url", "height", "sound", "headers", "rank"}], "audio_url", "audio_headers", "audio_only",
    "silent"}. The one to play depends on the quality chosen (see player_choice); a picture stream without
    sound (YouTube has no other kind) is played together with the best sound stream."""
    def playable(f):
        return str(f.get("url") or "").startswith(("http://", "https://")) and f.get("protocol") in PREVIEW_PROTOCOLS

    def has(f, kind):
        return f.get(kind) not in (None, "none")

    formats = [f for f in (data.get("formats") or [data]) if playable(f)]
    if not formats:
        return None
    videos = [f for f in formats if has(f, "vcodec")]
    sound = [f for f in formats if has(f, "acodec") and not has(f, "vcodec")]
    if not videos and not sound:
        videos = formats[:1]  # codecs unknown (a direct link): try it, as if it had both
    def sound_rank(f):
        return f.get("protocol") == "https", f.get("abr") or f.get("tbr") or 0

    best_sound = max(sound, key=sound_rank, default=None)
    by_language = {}  # YouTube's dubbed tracks: the best sound of each language
    for f in sorted(sound, key=sound_rank):
        if f.get("language"):
            by_language[f["language"]] = f["url"]
    return {
        "videos": [{"url": f["url"], "height": f.get("height"), "headers": _headers(f),
                    "sound": has(f, "acodec") or not has(f, "vcodec"),
                    # H.264 over https first: every player reads it, and it starts quickly
                    "rank": [int(f.get("protocol") != "https"), int(not str(f.get("vcodec") or "avc").startswith("avc"))]}
                   for f in videos],
        "audio_url": best_sound["url"] if best_sound else "",
        "audio_by_language": by_language,
        "audio_headers": _headers(best_sound) if best_sound else {},
        "audio_only": not videos,
        "silent": bool(videos) and not sound and not any(has(f, "acodec") or not has(f, "vcodec") for f in videos),
    }


def player_choice(source, quality="best", sound_only=False, audio_lang=None):
    """What to play: (url, separate sound url or "", headers). The picture is the highest one within the
    quality chosen ("best", "720"…), with its sound in the same file when there is such a stream; with
    `audio_lang`, the sound track in that language."""
    dubbed = (source.get("audio_by_language") or {}).get(audio_lang) if audio_lang else None
    if source["audio_only"] or (sound_only and source["audio_url"]):
        return dubbed or source["audio_url"], "", source["audio_headers"]
    videos = source["videos"]
    limit = int(quality) if str(quality).isdigit() else None
    fitting = [v for v in videos if limit is None or (v["height"] or 0) <= limit]
    if not fitting:
        lowest = min(v["height"] or 0 for v in videos)
        fitting = [v for v in videos if (v["height"] or 0) == lowest]
    top = max(v["height"] or 0 for v in fitting)
    chosen = min((v for v in fitting if (v["height"] or 0) == top),
                 key=lambda v: (bool(dubbed) or not v["sound"], v["rank"]))
    if dubbed:
        return chosen["url"], dubbed, chosen["headers"]
    return chosen["url"], "" if chosen["sound"] else source["audio_url"], chosen["headers"]


def player_resource(url, audio_url="", clip=None):
    """What the player (mpv) opens: the link, or an edl:// link playing the picture and the sound together
    and/or only the part from `clip` (start, end or None), cut by mpv itself (nothing is converted)."""
    urls = [u for u in (url, audio_url) if u]
    if len(urls) == 1 and not clip:
        return url
    if clip:
        start, end = clip
        cut = ",%g" % start + (",%g" % (end - start) if end is not None else "")
        return "edl://" + ";!new_stream;".join("%%%d%%%s%s" % (len(u.encode()), u, cut) for u in urls)
    return "edl://" + ";!new_stream;".join("!no_clip;!no_chapters;%%%d%%%s" % (len(u.encode()), u) for u in urls)


def _header_blob(headers):
    return "".join("%s: %s\r\n" % (k, str(v).replace("\r", " ").replace("\n", " ")) for k, v in (headers or {}).items())


def _preview_ffmpeg(source, seconds, out, width):
    args = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,http,https,tcp,tls,crypto"]  # file: the Edit tab's own videos
    blob = _header_blob(source.get("headers"))
    if blob:
        args += ["-headers", blob]
    if NETWORK["proxy"].startswith(("http://", "https://")):  # ffmpeg only knows HTTP proxies
        args += ["-http_proxy", NETWORK["proxy"]]
    args += ["-ss", "%.3f" % max(seconds, 0.0), "-i", source["url"], "-frames:v", "1",
             "-vf", "scale=%d:-2" % width, "-q:v", "5", str(out)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=PREVIEW_TIMEOUT, **NO_WINDOW)
    if result.returncode != 0 or not Path(out).exists() or Path(out).stat().st_size == 0:
        raise RuntimeError(last_line(result.stderr, "ffmpeg could not read the video"))


def _preview_av(source, seconds, out, width):
    if not HAS_PIL:
        raise RuntimeError("Previews need Pillow.")
    options = {"timeout": str(PREVIEW_TIMEOUT * 1000000)}
    blob = _header_blob(source.get("headers"))
    if blob:
        options["headers"] = blob
    if NETWORK["proxy"].startswith(("http://", "https://")):
        options["http_proxy"] = NETWORK["proxy"]
    with av.open(source["url"], options=options, timeout=PREVIEW_TIMEOUT) as container:
        stream = container.streams.video[0]
        target = max(seconds, 0.0)
        if target > 0 and stream.time_base:
            container.seek(int(target / stream.time_base), stream=stream, backward=True)
        picked = None
        for index, frame in enumerate(container.decode(stream)):
            picked = frame
            if frame.time is None or frame.time >= target - 0.04 or index > 600:
                break
        if picked is None:
            raise RuntimeError("No frame could be read.")
        image = picked.to_image()
    image.thumbnail((width, width))
    image.convert("RGB").save(str(out), "JPEG", quality=80)


def make_preview(source, seconds, out, duration=None, width=PREVIEW_WIDTH):
    if BACKEND is None or not source:
        raise RuntimeError("Previews need ffmpeg or PyAV.")
    if duration:
        seconds = min(max(seconds, 0.0), max(duration - 0.5, 0.0))
    out = Path(out)
    out.unlink(missing_ok=True)
    try:
        (_preview_ffmpeg if BACKEND == "ffmpeg" else _preview_av)(source, seconds, out, width)
    except subprocess.TimeoutExpired:
        raise RuntimeError("The preview took too long.")
    return out
