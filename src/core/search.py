"""Searching from the link field, when what is typed is not a link (the title of a song…): on YouTube (with
its filters: short or long videos, playlists, channels) or SoundCloud."""

from urllib.parse import quote_plus

from core.network import run_ytdlp


SEARCH_RESULTS = 10
SEARCH_SOURCES = {"youtube": "YouTube", "soundcloud": "SoundCloud"}
# YouTube's own filters (the "sp" of its search page): what kind of results.
SEARCH_FILTERS = {"": "Videos", "short": "Short videos (under 4 min)", "long": "Long videos (over 20 min)",
                  "playlist": "Playlists", "channel": "Channels"}
_YOUTUBE_FILTERS = {"short": "EgIYAQ%3D%3D", "long": "EgIYAg%3D%3D", "playlist": "EgIQAw%3D%3D",
                    "channel": "EgIQAg%3D%3D"}


def search_query(query, source="youtube", kind="", count=SEARCH_RESULTS):
    """What yt-dlp reads for this search."""
    if source == "soundcloud":
        return "scsearch%d:%s" % (count, query)
    if kind in _YOUTUBE_FILTERS:
        return "https://www.youtube.com/results?search_query=%s&sp=%s" % (quote_plus(query), _YOUTUBE_FILTERS[kind])
    return "ytsearch%d:%s" % (count, query)


def search_youtube(query, count=SEARCH_RESULTS, cookies_browser="", cookie_file=None, source="youtube", kind="",
                   start=1):
    """The results `start` to `start + count - 1`: [{"url", "title", "uploader", "duration", "thumbnail",
    "kind" ("video", "playlist" or "channel")}]."""
    opts = {"extract_flat": "in_playlist", "skip_download": True, "noplaylist": False,
            "playlist_items": "%d-%d" % (start, start + count - 1)}
    data = run_ytdlp(lambda ydl: ydl.extract_info(search_query(query, source, kind, start + count - 1),
                                                  download=False), opts, cookies_browser, cookie_file)
    results = []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        thumbs = [t.get("url") for t in entry.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
        url = str(entry.get("url") or "")
        results.append({
            "url": url if url.startswith("http") else "https://www.youtube.com/watch?v=%s" % entry["id"],
            "title": entry.get("title") or "video",
            "uploader": entry.get("uploader") or entry.get("channel") or "",
            "duration": entry.get("duration"),
            "thumbnail": thumbs[-1] if thumbs else "",
            "kind": "playlist" if "/playlist?" in url else "channel" if "/channel/" in url or "/@" in url
            else "video",
        })
    return results
