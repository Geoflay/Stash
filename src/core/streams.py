"""The streams a video offers: which ones to download, the quality choices and size estimates."""

from core.backend import AUDIO_BYTES_PER_SECOND, AUDIO_FORMATS, BACKEND, VIDEO_FORMATS
from core.units import format_bytes


QUALITY_FALLBACK = [("best", "Best"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]

# Video codec preferred (Settings > Advanced): "auto" keeps the choices below; "compatible" wants H.264 + AAC
# (old TVs and phones, converted when the site has none); the others are taken when the site offers them:
# HEVC (H.265: smaller than H.264, Apple devices and recent TVs), AV1 (the smallest), VP9 (smaller than H.264,
# older devices than AV1).
CODEC = {"preference": "auto"}
CODEC_CHOICES = {"auto": "Automatic (recommended)", "compatible": "Maximum compatibility (H.264 + AAC)",
                 "hevc": "HEVC (H.265)", "av1": "AV1", "vp9": "VP9"}
# The codec names sites give each one (TikTok calls HEVC "bytevc1"), and the containers that cannot hold it.
CODEC_FILTERS = {"hevc": ("[vcodec~='^(hvc1|hev1|h265|hevc|bytevc1)']", ("webm",)),
                 "av1": ("[vcodec^=av01]", ("mov",)), "vp9": ("[vcodec~='^vp0?9']", ("mov",))}


def codec_filter(container):
    """The yt-dlp filter of the codec preferred in Settings, when `container` can hold it; "" otherwise."""
    preference = CODEC["preference"]
    if preference == "compatible":
        return "" if container == "webm" else "[vcodec^=avc1]"
    found, refused = CODEC_FILTERS.get(preference, ("", ()))
    return "" if container in refused else found


def first_language(lang):
    """The first of several sound track languages (a list), or the one given."""
    return lang[0] if isinstance(lang, (list, tuple)) and lang else (lang or None)


def with_audio_language(selector, lang):
    """The same choice with the sound track in `lang` first (YouTube's dubbed tracks), else as before. `lang` may
    be a list: a video then gets every one of these tracks (the first is the default; see core.download), a sound
    file the first."""
    if isinstance(lang, (list, tuple)) and len(lang) > 1 and "+ba" in selector:
        picked = []
        for option in selector.split("/"):
            if "+ba" in option:
                video, sound = option.split("+ba", 1)  # sound: its filters ("[ext=m4a]"), for each track
                picked.append(video + "".join("+ba[language=%s]%s" % (one, sound) for one in lang))
        return "/".join(picked) + "/" + with_audio_language(selector, lang[0])
    lang = first_language(lang)
    if not lang:
        return selector
    tag = "[language=%s]" % lang
    picked = []
    for option in selector.split("/"):
        if "+ba" in option:
            picked.append(option.replace("+ba", "+ba" + tag))
        elif option.startswith("bestaudio"):
            picked.append("bestaudio" + tag + option[len("bestaudio"):])
        elif option.startswith("ba"):
            picked.append("ba" + tag + option[2:])
    return "/".join(picked + [selector])


def audio_tracks(data):
    """The sound tracks of a video in several languages (YouTube dubs): [{"lang", "name", "default"}], the
    original first; [] when there is only one."""
    tracks = {}
    for f in data.get("formats") or []:
        lang = f.get("language")
        if not lang or f.get("acodec") in (None, "none"):
            continue
        note = str(f.get("format_note") or "")
        track = tracks.setdefault(lang, {"lang": lang, "name": note.split(",")[0].replace("(default)", "").strip()
                                         or lang, "default": False})
        track["default"] = track["default"] or "(default)" in note or (f.get("language_preference") or 0) > 0
    if len(tracks) < 2:
        return []
    return sorted(tracks.values(), key=lambda t: (not t["default"], t["name"].casefold()))


def video_selector(quality, container="mp4", audio_lang=None, sound=True):
    """`sound` False: the picture alone (smaller download), when a stream without sound exists."""
    h = "" if quality == "best" else "[height<=%s]" % quality
    if BACKEND == "ffmpeg" and not sound:
        ext = "webm" if container == "webm" else "mp4"
        preferred = "bv*%s[ext=%s]/" % (h, ext)
        if codec_filter(container):
            preferred = "bv*%s%s/" % (h, codec_filter(container)) + preferred
        return preferred + "bv*%s/b%s/b" % (h, h)
    if BACKEND == "ffmpeg":
        # Streams that fit the container as-is (no re-encoding): WebM wants VP9/AV1 + Opus, MOV wants
        # H.264 (it cannot hold AV1), every other container takes the MP4 ones.
        if container == "webm":
            preferred = "bv*%s[ext=webm]+ba[ext=webm]/" % h
        elif container == "mov":
            preferred = "bv*%s[vcodec^=avc1]+ba[ext=m4a]/bv*%s[ext=mp4]+ba[ext=m4a]/" % (h, h)
        else:
            preferred = "bv*%s[ext=mp4]+ba[ext=m4a]/" % h
        wanted = codec_filter(container)
        if wanted and CODEC["preference"] == "compatible":
            preferred = "bv*%s%s+ba[acodec^=mp4a]/" % (h, wanted) + preferred
        elif wanted:
            preferred = "bv*%s%s+ba/" % (h, wanted) + preferred
        return with_audio_language(preferred + "bv*%s+ba/b%s/b" % (h, h), audio_lang)
    return "b%s[ext=mp4]/b%s/b" % (h, h)


AV_AUDIO_SELECTOR = "ba[ext=m4a]/ba/b"
# Picking a source already in the target codec lets ffmpeg copy it instead of re-encoding.
AUDIO_SELECTORS = {"m4a": "ba[ext=m4a]/ba/b", "opus": "ba[acodec^=opus]/ba/b"}


def av_video_selector(quality, complete_first=False):
    """Selector used without ffmpeg (PyAV merges the tracks itself)."""
    h = "" if quality == "best" else "[height<=%s]" % quality
    selector = "bv*%s[ext=mp4]/bv*%s/b%s[ext=mp4]/b%s/b" % (h, h, h, h)
    if complete_first:
        selector = "b%s[ext=mp4]/" % h + selector
    return selector


def _has_video(f):
    return f.get("vcodec") not in (None, "none")


def _has_audio(f):
    return f.get("acodec") not in (None, "none")


def frame_rate(formats):
    """The video's frames per second (the highest stream's), 30 when not known."""
    rates = [f.get("fps") for f in formats or [] if isinstance(f.get("fps"), (int, float)) and f.get("fps") > 0]
    return max(rates) if rates else 30.0


def compact_formats(data):
    return [
        {
            "height": f.get("height"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "size": f.get("filesize") or f.get("filesize_approx"),
            "tbr": f.get("tbr"),
            "abr": f.get("abr"),
            "ext": f.get("ext"),
            "fps": f.get("fps"),
        }
        for f in (data.get("formats") or [])
    ]


def _stream_bytes(f, duration):
    """Size of one raw yt-dlp format dict: exact size, else bitrate x duration."""
    size = f.get("filesize") or f.get("filesize_approx")
    if size:
        return int(size)
    rate = f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0)) or None
    if rate and duration:
        return int(rate * 125 * duration)
    return None


def _select_with_ytdlp(ydl, formats, selector):
    """The formats yt-dlp itself would download for `selector` (no network access)."""
    previous = ydl.params.get("check_formats")
    ydl.params["check_formats"] = False
    try:
        built = ydl.build_format_selector(selector)
        if hasattr(ydl, "_select_formats"):
            chosen = ydl._select_formats(formats, built)
        else:
            chosen = list(built({
                "formats": formats,
                "has_merged_format": any("none" not in (f.get("acodec"), f.get("vcodec")) for f in formats),
                "incomplete_formats": (all(f.get("vcodec") == "none" for f in formats)
                                       or all(f.get("acodec") == "none" for f in formats)),
            }))
    finally:
        ydl.params["check_formats"] = previous
    if not chosen:
        return []
    return list(chosen[0].get("requested_formats") or [chosen[0]])


def _download_plan(ydl, formats, quality, twitter, duration):
    """What a download at this quality will really fetch: (height, bytes or None, ids)."""
    selector = video_selector(quality) if BACKEND != "av" else av_video_selector(quality, twitter)
    parts = _select_with_ytdlp(ydl, formats, selector)
    if not parts:
        return None
    if BACKEND == "av" and not any(f.get("acodec") not in (None, "none") for f in parts):
        parts += _select_with_ytdlp(ydl, formats, AV_AUDIO_SELECTOR)[:1]
    sizes = [_stream_bytes(f, duration) for f in parts]
    video = [f for f in parts if f.get("vcodec") not in (None, "none")]
    height = max((f.get("height") or 0 for f in video), default=0) or None
    return {
        "height": height,
        "size": sum(sizes) if sizes and all(sizes) else None,
        "ids": tuple(str(f.get("format_id")) for f in parts),
    }


def measure_downloads(ydl, info, twitter=False):
    """Exact download sizes, using the same format selection as the real download.

    Returns {"video": [{"key", "height", "size"}...], "audio": bytes|None}; empty on failure so the
    caller can fall back to the approximate estimate.
    """
    try:
        formats = list(info.get("formats") or [])
        duration = info.get("duration")
        can_merge = BACKEND is not None
        heights = sorted({int(f["height"]) for f in formats
                          if f.get("height") and f.get("vcodec") not in (None, "none")
                          and (can_merge or f.get("acodec") not in (None, "none"))}, reverse=True)
        video, seen_ids, seen_keys = [], set(), set()
        for index, requested in enumerate([None] + heights):
            plan = _download_plan(ydl, formats, "best" if requested is None else str(requested),
                                  twitter, duration)
            if plan is None or plan["ids"] in seen_ids:
                continue
            seen_ids.add(plan["ids"])
            key = "best" if index == 0 else str(plan["height"] or requested)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            video.append({"key": key, "height": plan["height"], "size": plan["size"]})
        audio_parts = _select_with_ytdlp(ydl, formats, "bestaudio/best")
        audio_sizes = [_stream_bytes(f, duration) for f in audio_parts]
        return {"video": video,
                "audio": sum(audio_sizes) if audio_sizes and all(audio_sizes) else None}
    except Exception:
        return {}


def _stream_size(f, duration):
    if f.get("size"):
        return f["size"]
    rate = f.get("tbr") or f.get("abr")
    if rate and duration:
        return int(rate * 125 * duration)
    return None


def _size_text(size, scale):
    return " · ~" + format_bytes(size * scale) if size else ""


def estimate_choice(formats, duration, fmt, quality, can_merge, scale=1.0, measured=None, sound=True):
    """The size shown before downloading; `sound` False: a video without its sound (smaller)."""
    if not formats:
        return ""
    if measured:
        if fmt == "audio" and measured.get("audio"):
            return "Audio" + _size_text(measured["audio"], scale)
        # Measured with the MP4 stream choice, which every container but WebM downloads.
        if fmt in VIDEO_FORMATS and fmt != "webm":
            entry = next((v for v in measured.get("video", []) if v["key"] == quality), None)
            if entry:
                label = "%dp" % entry["height"] if entry.get("height") else "Video"
                size = entry["size"]
                if size and not sound and measured.get("audio"):
                    size = max(size - measured["audio"], 0)
                return label + _size_text(size, scale)
    audios = [f for f in formats if _has_audio(f) and not _has_video(f)]
    if fmt in AUDIO_FORMATS:
        if fmt in AUDIO_BYTES_PER_SECOND:
            size = duration * AUDIO_BYTES_PER_SECOND[fmt] if duration else None
            return AUDIO_FORMATS[fmt].split(" ")[0] + _size_text(size, scale)
        pool = audios or [f for f in formats if _has_audio(f)]
        if not pool:
            return ""
        best = max(pool, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        return "Audio" + _size_text(_stream_size(best, duration), scale)
    videos = [f for f in formats if _has_video(f)]
    if not can_merge:
        videos = [f for f in videos if _has_audio(f)]
    limit = None if quality == "best" else int(quality)
    pool = [f for f in videos if limit is None or (f.get("height") or 0) <= limit] or videos
    if not pool:
        return ""
    # The download prefers MP4 streams, or WebM ones for WebM (see video_selector), so the estimate must too.
    video_ext, audio_ext = ("webm", "webm") if fmt == "webm" else ("mp4", "m4a")
    pool = [f for f in pool if f.get("ext") == video_ext] or pool
    top = max(pool, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    size = _stream_size(top, duration)
    if size and not _has_audio(top) and audios and sound:
        m4a = [f for f in audios if f.get("ext") == audio_ext] or audios
        extra = _stream_size(max(m4a, key=lambda f: f.get("abr") or f.get("tbr") or 0), duration)
        size = size + extra if extra else None
    return ("%dp" % top["height"] if top.get("height") else "Video") + _size_text(size, scale)


def estimate_playlist(items):
    """Rough total size of [(duration, format, quality)] from typical sizes per second ("best" counted as
    1080p): (bytes, how many have no known duration)."""
    from core.backend import AUDIO_BYTES_PER_SECOND, AUDIO_FORMATS, ORIGINAL_AUDIO_BYTES_PER_SECOND, VIDEO_BYTES_PER_SECOND
    total, unknown = 0, 0
    for duration, fmt, quality in items:
        if not duration:
            unknown += 1
            continue
        if fmt in AUDIO_FORMATS:
            total += duration * AUDIO_BYTES_PER_SECOND.get(fmt, ORIGINAL_AUDIO_BYTES_PER_SECOND)
        else:
            height = int(quality) if str(quality).isdigit() else 1080
            known = [h for h in sorted(VIDEO_BYTES_PER_SECOND) if h <= height] or [min(VIDEO_BYTES_PER_SECOND)]
            total += duration * VIDEO_BYTES_PER_SECOND[known[-1]]
    return int(total), unknown


def valid_quality(quality):
    return quality == "best" or (isinstance(quality, str) and quality.isdigit() and int(quality) > 0)


def video_heights(formats, can_merge):
    heights = {
        f["height"] for f in formats
        if f.get("height") and f.get("vcodec") != "none" and (can_merge or f.get("acodec") != "none")
    }
    return sorted(heights, reverse=True)


def quality_choices(formats, duration, can_merge, measured=None):
    if measured and measured.get("video"):
        choices = []
        for entry in measured["video"]:
            height = "%dp" % entry["height"] if entry.get("height") else "Video"
            label = "Best (%s)" % height if entry["key"] == "best" else height
            if entry.get("size"):
                label += " · " + format_bytes(entry["size"])
            choices.append((entry["key"], label))
        return choices
    heights = video_heights(formats, can_merge)
    if not heights:
        return list(QUALITY_FALLBACK)
    choices = []
    for index, height in enumerate(heights):
        key = "best" if index == 0 else str(height)
        estimate = estimate_choice(formats, duration, "mp4", key, can_merge)
        size = estimate.split(" · ", 1)[1] if " · " in estimate else ""
        label = "Best (%dp)" % height if index == 0 else "%dp" % height
        choices.append((key, label + (" · " + size if size else "")))
    return choices


def pick_quality(remembered, choices, top_height=None):
    keys = [k for k, _ in choices]
    if remembered in keys:
        return remembered
    if not valid_quality(remembered) or remembered == "best":
        return keys[0]
    wanted = int(remembered)
    if top_height and wanted >= top_height:
        return keys[0]
    numeric = [int(k) for k in keys if k.isdigit()]
    below = [h for h in numeric if h <= wanted]
    if below:
        return str(max(below))
    if numeric and wanted >= min(numeric):
        return keys[0]
    return str(min(numeric)) if numeric else keys[0]


def best_audio_ext(formats):
    audios = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    if not audios:
        return None
    best = max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0)
    return best.get("ext")


def has_stream(formats, kind):
    if not formats:
        return True
    if kind == "video":
        return any(f.get("height") and f.get("vcodec") != "none" for f in formats)
    return any(f.get("acodec") not in (None, "none") for f in formats)
