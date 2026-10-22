"""Playlists, channels and profiles: listing their videos."""

import importlib.util
import os
import threading
import time
from pathlib import Path

from core.links import is_tiktok_url
from core.network import run_ytdlp


# How many videos of a playlist/channel/profile to list, per site (0 = no limit), set in Settings.
PLAYLIST_SITES = {"youtube": "YouTube playlists and channels", "tiktok": "TikTok profiles",
                  "podcast": "Podcasts (the newest episodes)", "other": "Other sites (albums, sets, showcases…)"}
DEFAULT_PLAYLIST_LIMITS = {"youtube": 100, "tiktok": 25, "podcast": 100, "other": 100}  # TikTok profiles are slow
PLAYLIST_LIMIT_CHOICES = (10, 25, 50, 100, 200, 500, 1000, 0)
# Pause between two videos of a list (Settings), so that a long one does not look like a bot to YouTube.
PLAYLIST_PAUSES = (0, 5, 15, 30, 60)
HAS_IMPERSONATION = importlib.util.find_spec("curl_cffi") is not None


def _entry_link(e):
    link = e.get("webpage_url") or e.get("url") or ""
    if link.startswith("http"):
        return link
    # YouTube lists may give only the video ID; on other sites an ID alone does not make a link.
    if e.get("ie_key") == "Youtube" and e.get("id"):
        return "https://www.youtube.com/watch?v=%s" % e["id"]
    return ""


def _playlist_entries(entries, out):
    """Flatten the videos of a playlist into out; returns True if it held nested playlists (channel tabs)."""
    nested = False
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        if e.get("entries"):
            nested = True
            _playlist_entries(e["entries"], out)
            continue
        title = e.get("title") or ""
        if title in ("[Private video]", "[Deleted video]"):
            continue
        link = _entry_link(e)
        if not link:
            continue
        thumbs = [t.get("url") for t in (e.get("thumbnails") or []) if isinstance(t, dict) and t.get("url")]
        date = e.get("upload_date") or (time.strftime("%Y%m%d", time.gmtime(e["timestamp"]))
                                        if isinstance(e.get("timestamp"), (int, float)) else "")
        out.append({
            "url": link,
            "title": title or "video",
            "duration": e.get("duration"),
            "thumbnail": e.get("thumbnail") or (thumbs[-1] if thumbs else ""),
            "date": date,
        })
    return nested


def lists_several_items(info):
    """True when yt-dlp read a link as a list of at least two items with links of their own (an album, a
    set…), not as one post holding several videos (they all share the post's link)."""
    if info.get("_type") != "playlist":
        return False
    items = []
    _playlist_entries(info.get("entries") or [], items)
    return len({item["url"] for item in items}) > 1


def fetch_playlist(url, cookies_browser="", cookie_file=None, limit=0):
    """The videos of a playlist, channel or profile: the first `limit` ones (all of them if 0)."""
    if is_tiktok_url(url) and not HAS_IMPERSONATION:
        raise RuntimeError("TikTok profiles can only be read with the curl-cffi package, which is not "
                           "included in this build. Download the videos one by one instead.")
    opts = {"noplaylist": False, "extract_flat": "in_playlist", "skip_download": True}
    if limit:
        opts["playlist_items"] = "1-%d" % limit
    data = run_ytdlp(lambda ydl: ydl.extract_info(url, download=False), opts, cookies_browser, cookie_file)
    entries = []
    nested = _playlist_entries(data.get("entries") or [], entries)
    if not entries:
        raise RuntimeError("No videos found in this playlist.")
    return {
        "title": data.get("title") or "playlist",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "entries": entries[:limit] if limit else entries,
        # A channel's count is its number of tabs (Videos, Shorts…), not of videos: unknown then.
        "total": None if nested else (data.get("playlist_count") or len(entries)),
        "limit": limit,
    }


# The order of a list's items (Download tab): "" keeps the list's own.
ITEM_ORDERS = {"": "List order", "newest": "Newest first", "oldest": "Oldest first", "longest": "Longest first",
               "shortest": "Shortest first"}


def sort_entries(entries, order):
    """The items in `order` (ITEM_ORDERS); those without a date or length stay at the end, in the list's order."""
    if order in ("newest", "oldest"):
        key = "date"
    elif order in ("longest", "shortest"):
        key = "duration"
    else:
        return list(entries)
    known = [e for e in entries if e.get(key)]
    known.sort(key=lambda e: e[key], reverse=order in ("newest", "longest"))
    return known + [e for e in entries if not e.get(key)]


_m3u_lock = threading.Lock()  # the items of a list are saved in parallel: one at a time in its playlist file


def add_to_m3u(playlist, file, position, title="", duration=None):
    """Put `file` at its `position` in the .m3u8 playlist file `playlist` (made if needed), so that a music
    player plays a list's items in its order. The positions are kept in the file itself ("#EXTVD:3")."""
    with _m3u_lock:
        return _add_to_m3u(Path(playlist), file, position, title, duration)


def _add_to_m3u(playlist, file, position, title, duration):
    entries = []  # (position, lines)
    try:
        lines = playlist.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    block, place = [], None
    for line in lines:
        if line.startswith("#EXTVD:"):
            place = int(line[7:]) if line[7:].isdigit() else None
        elif line.startswith("#EXTINF:"):
            block = [line]
        elif line and not line.startswith("#"):
            entries.append((place if place is not None else len(entries), block + [line]))
            block, place = [], None
    name = os.path.relpath(file, playlist.parent).replace(os.sep, "/")
    entries = [e for e in entries if e[1][-1] != name]
    entries.append((position, ["#EXTINF:%d,%s" % (round(duration or -1), title or Path(file).stem), name]))
    entries.sort(key=lambda e: e[0])
    text = ["#EXTM3U"]
    for place, block in entries:
        text += ["#EXTVD:%d" % place] + block
    playlist.write_text("".join(line + "\n" for line in text), encoding="utf-8")
    return playlist
