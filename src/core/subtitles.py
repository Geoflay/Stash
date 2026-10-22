"""Subtitles: the languages offered, and saving them as .srt, inside the video, or as a transcript
(plain text or a readable page)."""

import html
import os
import re
from pathlib import Path

from core.backend import BACKEND
from core.ffmpeg import run_ffmpeg
from core.jobs import entering_conversion, ignore_progress
from core.network import run_ytdlp


_CUE_TIME = re.compile(
    r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
_CUE_TAGS = re.compile(r"<[^>]*>")
SKIP_BLOCKS = ("WEBVTT", "NOTE", "STYLE", "REGION")
EMBED_CODECS = {".mp4": "mov_text", ".m4v": "mov_text", ".mov": "mov_text", ".mkv": "srt", ".webm": "webvtt"}
CAN_EMBED_SUBS = BACKEND == "ffmpeg"


def _stamp_seconds(hours, minutes, seconds, fraction):
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction.ljust(3, "0")[:3]) / 1000


def parse_cues(text, dedupe=False):
    cues = []
    previous = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")):
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines or lines[0].startswith(SKIP_BLOCKS):
            continue
        for i, line in enumerate(lines):
            found = _CUE_TIME.search(line)
            if found:
                break
        else:
            continue
        g = found.groups()
        start = _stamp_seconds(g[0], g[1], g[2], g[3])
        end = _stamp_seconds(g[4], g[5], g[6], g[7])
        body = []
        for raw in lines[i + 1:]:
            clean = html.unescape(_CUE_TAGS.sub("", raw)).replace("\u200e", "").replace("\u200f", "").strip()
            if clean:
                body.append(clean)
        if dedupe:
            current = list(body)
            while body and body[0] in previous:
                body.pop(0)
            previous = current
        if body and end > start:
            cues.append((start, end, body))
    return cues


def _srt_stamp(seconds):
    total = int(round(max(seconds, 0.0) * 1000))
    return "%02d:%02d:%02d,%03d" % (total // 3600000, total // 60000 % 60, total // 1000 % 60, total % 1000)


def cues_to_srt(cues, start=0.0, end=None):
    out = []
    for cue_start, cue_end, body in cues:
        if cue_end <= start or (end is not None and cue_start >= end):
            continue
        new_start = max(cue_start - start, 0.0)
        new_end = cue_end - start
        if end is not None:
            new_end = min(new_end, end - start)
        if new_end > new_start:
            out.append("%d\n%s --> %s\n%s\n" % (len(out) + 1, _srt_stamp(new_start), _srt_stamp(new_end), "\n".join(body)))
    return "\n".join(out)


def subtitle_choices(data):
    manual = data.get("subtitles") or {}
    automatic = data.get("automatic_captions") or {}

    def label(entries, lang):
        for entry in entries or []:
            if entry.get("name"):
                return str(entry["name"])
        return lang

    def text_url(entries):
        """Link to a text version (for the player's preview), when the site offers one."""
        by_ext = {entry.get("ext"): entry.get("url") for entry in entries or [] if entry.get("url")}
        return by_ext.get("vtt") or by_ext.get("srt")

    def choice(lang, name, auto, entries, translated=False):
        item = {"lang": lang, "name": name, "auto": auto}
        if translated:
            item["translated"] = True
        if text_url(entries):
            item["url"] = text_url(entries)
        return item

    choices = []
    for lang, entries in manual.items():
        if lang and lang != "live_chat":
            choices.append(choice(lang, label(entries, lang), False, entries))
    originals = [k for k in automatic if k.endswith("-orig")]
    if not originals:
        base = (data.get("language") or "").split("-")[0].lower()
        originals = [k for k in automatic if base and k.lower() == base]
    for lang in originals:
        name = re.sub(r"\s*\(original\)\s*$", "", label(automatic[lang], lang), flags=re.I)
        choices.append(choice(lang, name, True, automatic[lang]))
    # The other automatic languages are YouTube's translations of the auto-generated ones; not the video's own
    # language again, nor one the site has real subtitles in.
    spoken = {re.sub(r"-orig$", "", lang).lower() for lang in originals}
    translated = [choice(lang, label(entries, lang), True, entries, translated=True)
                  for lang, entries in automatic.items()
                  if lang not in originals and not lang.endswith("-orig") and lang.lower() not in spoken
                  and lang not in manual]
    return choices[:60] + sorted(translated, key=lambda c: c["name"].lower())[:200]


def subtitle_key(choice):
    return "%s:%s" % ("a" if choice["auto"] else "m", choice["lang"])


def subtitle_label(choice):
    if choice.get("translated"):
        return "%s (auto-translated)" % choice["name"]
    if choice["auto"]:
        return "%s (auto-generated)" % choice["name"]
    return choice["name"] if choice["name"] == choice["lang"] else "%s (%s)" % (choice["name"], choice["lang"])


def split_subtitle_key(key):
    kind, _, lang = (key or "").partition(":")
    return lang, kind == "a"


def subtitle_tag(lang):
    """Language part of a subtitle file name, e.g. ".en" for "en-orig"."""
    return "." + re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"-orig$", "", lang or ""))


def subtitle_suffix(lang):
    return subtitle_tag(lang) + ".srt"


def fetch_subtitles(url, lang, auto, workdir, cookies_browser="", cookie_file=None, clip=None):
    opts = {
        "skip_download": True,
        "playlist_items": "1",
        "noprogress": True,
        "writesubtitles": not auto,
        "writeautomaticsub": auto,
        "subtitleslangs": [lang],
        "subtitlesformat": "srt/vtt/best",
        "outtmpl": os.path.join(workdir, "subs.%(ext)s"),
    }
    run_ytdlp(lambda ydl: ydl.download([url]), opts, cookies_browser, cookie_file)
    files = sorted(p for p in Path(workdir).glob("subs.*") if p.is_file())
    if not files:
        raise RuntimeError("No subtitles were found for this language.")
    raw = files[0]
    if raw.suffix.lower() not in (".srt", ".vtt"):
        return raw
    text = raw.read_text(encoding="utf-8", errors="replace")
    start, end = (clip or (0.0, None))
    srt = cues_to_srt(parse_cues(text, dedupe=auto), start or 0.0, end)
    if not srt.strip():
        raise RuntimeError("The subtitles are empty for the selected range.")
    out = Path(workdir, "subtitles.srt")
    out.write_text(srt, encoding="utf-8")
    return out


TRANSCRIPT_MODES = {"txt": ".txt", "html": ".html"}
PARAGRAPH_PAUSE = 2.0  # seconds of silence that start a new paragraph
PARAGRAPH_MAX = 60.0  # auto-generated captions have no pauses: a new paragraph at least every minute


def transcript_paragraphs(cues):
    """The cues' words joined into paragraphs: [(start in seconds, text)]."""
    paragraphs, words, start, last_end, last_line = [], [], 0.0, None, None
    for cue_start, cue_end, body in cues:
        text = " ".join(" ".join(body).split())
        if not text or text == last_line:
            continue
        if words and (cue_start - last_end >= PARAGRAPH_PAUSE or cue_start - start >= PARAGRAPH_MAX):
            paragraphs.append((start, " ".join(words)))
            words = []
        if not words:
            start = cue_start
        words.append(text)
        last_end, last_line = cue_end, text
    if words:
        paragraphs.append((start, " ".join(words)))
    return paragraphs


def _clock(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60) if seconds >= 3600 \
        else "%d:%02d" % (seconds // 60, seconds % 60)


TRANSCRIPT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font: 18px/1.65 Georgia, serif; max-width: 42em; margin: 3em auto; padding: 0 1.2em; color: #222;
        background: #fdfcf9; }}
h1 {{ font: 600 1.6em/1.3 system-ui, sans-serif; margin-bottom: .2em; }}
.source {{ font: .85em system-ui, sans-serif; color: #777; margin-bottom: 2.5em; }}
p {{ margin: 0 0 1.1em; }}
.time {{ font: .7em system-ui, sans-serif; color: #999; margin-right: .8em; user-select: none; }}
a {{ color: inherit; }}
@media (prefers-color-scheme: dark) {{ body {{ color: #ddd; background: #1b1b1d; }} }}
</style></head>
<body><h1>{title}</h1>
<div class="source"><a href="{url}">{url}</a></div>
{paragraphs}
</body></html>
"""


def make_transcript(subs, mode, title, url, workdir):
    """The .srt `subs` turned into a transcript: plain text, or a readable page where each paragraph shows
    when it starts. Returns the new file."""
    paragraphs = transcript_paragraphs(parse_cues(Path(subs).read_text(encoding="utf-8", errors="replace")))
    out = Path(workdir, "transcript" + TRANSCRIPT_MODES[mode])
    if mode == "txt":
        out.write_text("%s\n%s\n\n%s\n" % (title, url, "\n\n".join(text for _, text in paragraphs)),
                       encoding="utf-8")
    else:
        body = "\n".join('<p><span class="time">%s</span>%s</p>' % (_clock(start), html.escape(text))
                         for start, text in paragraphs)
        out.write_text(TRANSCRIPT_PAGE.format(title=html.escape(title), url=html.escape(url, quote=True),
                                              paragraphs=body), encoding="utf-8")
    return out


def preview_subtitles(choice, clip=None):
    """The subtitles of `choice` as SRT text for the player's preview, with the times of the part played
    (`clip`); "" when the site gives no text version."""
    if not choice.get("url"):
        return ""
    from core.web import http_get
    text = http_get(choice["url"], timeout=20).decode("utf-8", "replace")
    start, end = clip or (0.0, None)
    return cues_to_srt(parse_cues(text, dedupe=choice.get("auto", False)), start, end)


def iso_language(lang):
    """"fr-FR" -> "fre": the language code that video files hold, or ""."""
    base = re.sub(r"-orig$", "", lang or "").split("-")[0].lower()
    try:
        from yt_dlp.utils import ISO639Utils
        return ISO639Utils.short2long(base) or ""
    except Exception:
        return ""


def embed_subtitles(video, tracks, workdir):
    """`tracks`: [(language, .srt file)], put into the video as subtitle tracks (the first one shown by default)."""
    video = Path(video)
    codec = EMBED_CODECS.get(video.suffix.lower())
    if codec is None or any(Path(subs).suffix.lower() != ".srt" for _, subs in tracks):
        raise RuntimeError("Subtitles cannot be embedded in this file type.")
    entering_conversion()
    out = Path(workdir, "embedded" + video.suffix)
    args = ["-i", str(video)]
    for _, subs in tracks:
        args += ["-i", str(subs)]
    args += ["-map", "0:v", "-map", "0:a?"]
    for index in range(len(tracks)):
        args += ["-map", "%d:0" % (index + 1)]
    args += ["-c:v", "copy", "-c:a", "copy", "-c:s", codec, "-disposition:s:0", "default"]
    for index, (lang, _) in enumerate(tracks):
        iso = iso_language(lang)
        if iso:
            args += ["-metadata:s:s:%d" % index, "language=" + iso]
    try:
        run_ffmpeg(args + [str(out)], workdir)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out


def fetch_several(url, keys, workdir, cookies_browser="", cookie_file=None, clip=None):
    """The subtitles of each language key, each fetched in its own folder: [(language, file)]."""
    found = []
    for index, key in enumerate(keys):
        lang, auto = split_subtitle_key(key)
        folder = Path(workdir, "subs%d" % index)
        folder.mkdir(exist_ok=True)
        found.append((lang, fetch_subtitles(url, lang, auto, folder, cookies_browser, cookie_file, clip)))
    return found


def apply_subtitles(url, keys, mode, video, workdir, cookies_browser="", cookie_file=None, clip=None, notify=None,
                    title=""):
    """The subtitles of `keys` (one language or several) for the downloaded `video`: embedded, else (or as a
    transcript) files to save next to it. Returns (the video, [(language, file to save)], a problem or "")."""
    notify = notify or ignore_progress
    keys = [keys] if isinstance(keys, str) else list(keys)
    notify(None, "Downloading subtitles…")
    found = fetch_several(url, keys, workdir, cookies_browser, cookie_file, clip)
    if mode in TRANSCRIPT_MODES:
        return Path(video), [(lang, make_transcript(subs, mode, title or Path(video).stem, url, subs.parent))
                             for lang, subs in found], ""
    problem = ""
    if mode == "embed" and CAN_EMBED_SUBS:
        notify(None, "Adding subtitles to the video…")
        try:
            return embed_subtitles(video, found, workdir), [], ""
        except RuntimeError as exc:
            problem = "Could not embed the subtitles (%s): saved as separate files." % exc
    elif mode == "embed":
        problem = "Embedding needs ffmpeg: the subtitles are saved as separate files."
    return Path(video), found, problem
