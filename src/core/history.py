"""The download history shown in the Library tab, with its cached thumbnails: what each entry is (video,
music…), whether its file is still there and how much room it takes."""

import hashlib
import os
import re
import shutil
import threading
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core.storage import data_dir, read_json, write_json
from core.web import urlopen


HISTORY_LIMIT = 1000


def load_history():
    data = read_json("history.json", [])
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


# Search and filters of the Library tab.
HISTORY_PERIODS = {"": "Any date", "today": "Today", "week": "Last 7 days", "month": "Last 30 days",
                   "year": "Last 12 months"}
_PERIOD_DAYS = {"week": 7, "month": 30, "year": 365}


def searchable(text):
    """Lowercase without accents, so "video" finds "Vidéo"."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(c for c in text if not unicodedata.combining(c))


def _entry_time(entry):
    try:
        return datetime.strptime(entry.get("time") or "", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


# The types of the Library's filter, from the format saved.
LIBRARY_TYPES = ("Videos", "Music", "Images", "Subtitles", "Playlists")
_TYPE_FORMATS = {"Music": ("MP3", "M4A", "OPUS", "FLAC", "WAV", "OGG", "AAC", "WEBA"),
                 "Images": ("JPG", "JPEG", "PNG", "GIF", "WEBP"), "Subtitles": ("SRT", "VTT", "ASS", "TXT", "HTML"),
                 "Playlists": ("PLAYLIST",)}
FILE_STATES = {"": "Any file", "present": "Still on this device", "missing": "Deleted since",
               "duplicates": "Saved more than once"}
# How long the Library keeps its entries (Settings > Storage), in days; 0: always.
HISTORY_KEEP = {0: "Always", 30: "1 month", 90: "3 months", 180: "6 months", 365: "1 year"}


def entry_type(entry):
    fmt = str(entry.get("format") or "").upper()
    return next((kind for kind, formats in _TYPE_FORMATS.items() if fmt in formats), "Videos")


def file_state(entry):
    """"present", "missing" (deleted or moved since), or "" when where it was saved is not known."""
    path = entry.get("path")
    if not path:
        return ""
    return "present" if os.path.exists(path) else "missing"


def entry_size(entry):
    """The bytes the saved file (or folder of files) takes now; 0 when it is gone or not known."""
    path = entry.get("path")
    try:
        if not path or not os.path.exists(path):
            return 0
        if os.path.isdir(path):
            return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
        return os.path.getsize(path)
    except OSError:
        return 0


def save_history(items):
    """Write the Library as `items` (an undo, entries forgotten…)."""
    try:
        write_json("history.json", items[:HISTORY_LIMIT])
    except OSError:
        pass


def prune_history(days, now=None):
    """Forget the entries older than `days` days (0: none); returns how many."""
    if not days:
        return 0
    since = (now or datetime.now()) - timedelta(days=days)
    items = load_history()
    kept = [e for e in items if (_entry_time(e) or since) >= since]
    if len(kept) < len(items):
        save_history(kept)
    return len(items) - len(kept)


def _video_key(entry):
    """The same video, whatever the link: its site and ID when known."""
    if entry.get("video_id") and entry.get("platform"):
        return (entry["platform"], entry["video_id"])
    return entry.get("url") or id(entry)


def duplicates(items):
    """The entries whose video is saved more than once on this device (other files of the same video)."""
    present = [e for e in items if file_state(e) == "present"]
    counts = {}
    for entry in present:
        counts[_video_key(entry)] = counts.get(_video_key(entry), 0) + 1
    return [e for e in present if counts[_video_key(e)] > 1]


def delete_file(entry):
    """Delete the file (or folder of files) of `entry`; returns the bytes freed. OSError if it cannot be."""
    path, size = entry.get("path"), entry_size(entry)
    if not path or not os.path.exists(path):
        return 0
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    return size


def forget_missing():
    """Remove the entries whose file was deleted since; returns how many."""
    items = load_history()
    kept = [e for e in items if file_state(e) != "missing"]
    try:
        write_json("history.json", kept)
    except OSError:
        return 0
    return len(items) - len(kept)


def filter_history(items, text="", platform="", fmt="", period="", now=None, kind="", state=""):
    """The entries matching every filter: words of `text` in the title or file name, platform, format,
    period (a HISTORY_PERIODS key), type (LIBRARY_TYPES) and file state (FILE_STATES); an empty filter matches
    everything."""
    words = searchable(text).split()
    now = now or datetime.now()
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period in _PERIOD_DAYS:
        since = now - timedelta(days=_PERIOD_DAYS[period])
    else:
        since = None
    doubles = {id(e) for e in duplicates(items)} if state == "duplicates" else set()
    found = []
    for entry in items:
        if platform and entry.get("platform") != platform:
            continue
        if fmt and entry.get("format") != fmt:
            continue
        if kind and entry_type(entry) != kind:
            continue
        if state == "duplicates":
            if id(entry) not in doubles:
                continue
        elif state and file_state(entry) != state:
            continue
        if since is not None:
            when = _entry_time(entry)
            if when is None or when < since:
                continue
        if words:
            haystack = searchable("%s %s" % (entry.get("title") or "", entry.get("name") or ""))
            if not all(word in haystack for word in words):
                continue
        found.append(entry)
    return found


def find_downloaded(items, url, platform="", video_id="", choice=None):
    """The latest history entry of the same file: the same video (same link, or same site and video ID)
    saved the same way (`choice`: format, quality, clip… as chosen in the Download tab). None if there is
    none: the same video in another format, or cut differently, is another file."""
    for entry in items:
        same_video = (url and entry.get("url") == url) or (
            video_id and entry.get("video_id") == video_id and entry.get("platform") == platform)
        if not same_video:
            continue
        if "choice" in entry:
            if entry["choice"] == choice:
                return entry
        elif choice and entry.get("format") == str(choice.get("fmt") or "").upper():
            return entry  # saved before choices were recorded: the format is all that is known
    return None


def history_values(items, key):
    """The different platforms (or formats…) found in the history, for the filter menus."""
    return sorted({str(e.get(key)) for e in items if e.get(key)}, key=str.casefold)


THUMB_DIR = "thumbs"
THUMB_MAX_BYTES = 3_000_000
THUMB_KEEP = 200  # older entries show the thumbnail from the site instead of a saved copy


def thumbs_dir():
    folder = data_dir() / THUMB_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cache_thumbnail(url):
    if not url or not str(url).startswith(("http://", "https://")):
        return ""
    suffix = Path(urlparse(url).path).suffix.lower()
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + (suffix if suffix in (".jpg", ".jpeg", ".png", ".webp") else ".jpg")
    target = thumbs_dir() / name
    if target.exists() and target.stat().st_size > 0:
        return name
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            kind = resp.headers.get("Content-Type", "")
            body = resp.read(THUMB_MAX_BYTES + 1)
        if (kind and not kind.lower().startswith("image/")) or not body or len(body) > THUMB_MAX_BYTES:
            return ""
        tmp = target.with_name("%s.%d.tmp" % (name, threading.get_ident()))  # parallel downloads
        tmp.write_bytes(body)
        os.replace(tmp, target)
        return name
    except Exception:
        return ""


def thumbnail_source(entry, allow_local=True):
    name = entry.get("thumb")
    if allow_local and name and re.fullmatch(r"[0-9a-f]{20}\.(?:jpg|jpeg|png|webp)", str(name)):
        path = data_dir() / THUMB_DIR / name
        if path.exists():
            return str(path)
    return entry.get("thumbnail") or ""


THUMB_GRACE = 600  # seconds: a parallel download may have cached its thumbnail but not added its entry yet


def clear_thumbnails():
    """Settings > Clean up: delete the saved thumbnails (the history then shows the site's); returns
    (files, bytes)."""
    count = size = 0
    for path in thumbs_dir().iterdir():
        if path.is_file():
            size += path.stat().st_size
            path.unlink(missing_ok=True)
            count += 1
    return count, size


def prune_thumbnails():
    try:
        keep = {e.get("thumb") for e in load_history()[:THUMB_KEEP]}
        recent = time.time() - THUMB_GRACE
        for path in thumbs_dir().iterdir():
            if path.name not in keep and path.stat().st_mtime < recent:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def add_history(entry):
    entry = dict(entry, time=datetime.now().strftime("%Y-%m-%d %H:%M"))
    items = [e for e in load_history()
             if not (e.get("url") == entry.get("url") and e.get("format") == entry.get("format")
                     and e.get("choice") == entry.get("choice"))]
    items.insert(0, entry)
    try:
        write_json("history.json", items[:HISTORY_LIMIT])
    except OSError:
        pass
    prune_thumbnails()


def clear_history():
    try:
        write_json("history.json", [])
    except OSError:
        pass
    prune_thumbnails()


# The order of the Library (its menu): newest first unless chosen otherwise.
def history_csv(items, size=lambda entry: 0):
    """The entries as a CSV table (title, site, format, date, size, link, file), to open in a spreadsheet."""
    import csv
    import io
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Title", "Platform", "Format", "Date", "Size (bytes)", "Link", "File"])
    for entry in items:
        writer.writerow([entry.get("title") or "", entry.get("platform") or "", entry.get("format") or "",
                         entry.get("time") or "", size(entry) or "", entry.get("url") or "", entry.get("path") or ""])
    return out.getvalue()
