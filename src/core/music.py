"""Songs: their real tags from MusicBrainz (title, artist, album, date, and the cover art of the Cover Art
Archive) instead of what the video's title says ("Artist - Title (Official Video)"), and their lyrics from
LRCLIB (synced ones when there are: LRC text that players scroll). Both are free, without an account. The song
files of a folder are looked up the same way (find_song_tags, "Tag music" on Home).

Nothing is changed unless the match is sure: a high MusicBrainz score and the same length as the video."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.web import urlopen

USER_AGENT = "VideoDownloader/0.1 (personal downloads app)"  # MusicBrainz asks every app to name itself
MIN_SCORE = 90
LENGTH_SLACK = 5  # seconds between the video and the recording found
LYRICS_SLACK = 3
MIN_LYRIC_LINES = 4
MB_INTERVAL = 1.1  # MusicBrainz allows one request per second
_mb_lock = threading.Lock()
_mb_last = [0.0]

# "(Official Video)", "[Lyrics]", "(4K Remaster)"… around a song's title.
NOISE = re.compile(r"\s*[(\[][^)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|remaster(ed)?|hd|hq|4k|mv|clip|"
                    r"explicit|color coded|full album|album)\b[^)\]]*[)\]]", re.I)
# "01. ", "1 - ", "(3) " before a track's name in an album's chapters.
_TRACK_NUMBER = re.compile(r"^\s*[(\[]?\d{1,3}[)\].:-]?\s+(-\s+)?")
_FEATURING = re.compile(r"\s*[(\[]?\s*\b(ft|feat|featuring)\b\.?\s.*$", re.I)


def _get_json(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def guess_song(info):
    """(artist, title) of a song from yt-dlp's info: its own track fields (YouTube Music, SoundCloud…), else
    "Artist - Title" in the video's title, else the channel and the title."""
    artists = info.get("artists")
    artist = info.get("artist") or (artists[0] if artists else "")
    if info.get("track") and artist:
        return artist, info["track"]
    title = NOISE.sub("", info.get("title") or "").strip()
    found = re.match(r"(.+?)\s+[-–—|]\s+(.+)", title)
    if found:
        artist, title = found.groups()
    else:
        artist = re.sub(r"\s*(- Topic|VEVO)$", "", info.get("uploader") or info.get("channel") or "")
    return artist.strip(), _FEATURING.sub("", title).strip(" \"'")


def track_tags(chapter_title):
    """(artist or "", title) of an album's track from its chapter's title ("03. Artist - Title")."""
    title = _TRACK_NUMBER.sub("", chapter_title).strip() or chapter_title
    found = re.match(r"(.+?)\s+[-–—]\s+(.+)", title)
    return (found.group(1).strip(), found.group(2).strip()) if found else ("", title)


def _quote(text):
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


def _same(a, b):
    return re.sub(r"\W+", "", a.casefold()) == re.sub(r"\W+", "", b.casefold())


def _release_rank(release):
    """Sorting key: an official album first, then a single or an EP; no compilation or live; the earliest."""
    group = release.get("release-group") or {}
    return (release.get("status") != "Official", bool(group.get("secondary-types")),
            {"Album": 0, "Single": 1, "EP": 2}.get(group.get("primary-type"), 3), release.get("date") or "9999")


def _search_recordings(query):
    url = "https://musicbrainz.org/ws/2/recording?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 25})
    with _mb_lock:
        for attempt in range(3):
            time.sleep(max(0.0, _mb_last[0] + MB_INTERVAL * (1 + 2 * attempt) - time.monotonic()))
            try:
                return _get_json(url).get("recordings") or []
            except urllib.error.HTTPError as exc:
                if exc.code != 503 or attempt == 2:  # 503: too many requests, for a moment
                    raise
            finally:
                _mb_last[0] = time.monotonic()


def find_recording(artist, title, duration=None):
    """MusicBrainz's sure match: {"title", "artist", "album", "date", "release", "release_group"}, or None.
    The album is the earliest official album, single or EP holding it; none for a song only found on
    compilations or live records (their name is not the song's album)."""
    if not artist or not title:
        return None
    query = 'recording:"%s" AND artist:"%s"' % (_quote(title), _quote(artist))
    for extra in (" AND status:official AND NOT secondarytype:compilation AND NOT secondarytype:live"
                  " AND NOT secondarytype:remix AND NOT secondarytype:dj-mix", ""):
        # The same title (not its "(instrumental)" or "(live)" version) and the same length.
        recordings = [r for r in _search_recordings(query + extra) if int(r.get("score") or 0) >= MIN_SCORE
                      and _same(r.get("title") or "", title) and (
                          not duration or (r.get("length") and abs(r["length"] / 1000 - duration) <= LENGTH_SLACK))]
        if recordings:
            break
    else:
        return None
    found = sorted(((release, recording) for recording in recordings for release in recording.get("releases") or []),
                   key=lambda pair: _release_rank(pair[0]))
    release, recording = found[0] if found else ({}, recordings[0])
    group = release.get("release-group") or {}
    proper = release.get("status") == "Official" and not group.get("secondary-types")
    return {
        "title": recording.get("title") or title,
        "artist": "".join(c.get("name", "") + c.get("joinphrase", "") for c in recording.get("artist-credit") or [])
        or artist,
        "album": release.get("title", "") if proper else "",
        "date": release.get("date", "") if proper else "",
        "release": release.get("id", "") if proper else "",
        "release_group": group.get("id", "") if proper else "",
    }


def fetch_cover(match):
    """The front cover of the release found (1200 pixels), as (bytes, mime); None when it has none."""
    for kind, mbid in (("release", match.get("release")), ("release-group", match.get("release_group"))):
        if not mbid:
            continue
        request = urllib.request.Request("https://coverartarchive.org/%s/%s/front-1200" % (kind, mbid),
                                         headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, 30) as response:
                data = response.read()
        except (urllib.error.URLError, OSError):
            continue
        if data[:3] == b"\xff\xd8\xff":
            return data, "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data, "image/png"
    return None


def find_lyrics(artist, title, album="", duration=None):
    """The song's lyrics from LRCLIB: synced (LRC, "[01:02.30] words") when there are, else plain; "" if none."""
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = round(duration)
    try:
        found = [_get_json("https://lrclib.net/api/get?" + urllib.parse.urlencode(params))]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        found = []
    found += _get_json("https://lrclib.net/api/search?" + urllib.parse.urlencode(
        {"artist_name": artist, "track_name": title}))
    for item in found:
        if item.get("instrumental"):
            continue
        if duration and item.get("duration") and abs(item["duration"] - duration) > LYRICS_SLACK:
            continue
        lyrics = (item.get("syncedLyrics") or item.get("plainLyrics") or "").strip()
        words = [line for line in (re.sub(r"\[[\d:.]+\]", "", line).strip() for line in lyrics.splitlines()) if line]
        if len(words) >= MIN_LYRIC_LINES:  # some entries are tests ("probe") or a single line
            return lyrics
    return ""


def song_tags(info, tags=True, lyrics=True):
    """What to write into a song's file, from yt-dlp's info: the MusicBrainz tags and cover art (tags) and the
    lyrics (lyrics). Only what was found; nothing when the site cannot be reached."""
    artist, title = guess_song(info)
    duration = info.get("duration")
    found = {}
    if tags:
        try:
            match = find_recording(artist, title, duration)
        except Exception:
            match = None
        if match:
            found = {key: match[key] for key in ("title", "artist", "album", "date") if match[key]}
            artist, title = match["artist"], match["title"]
            try:
                cover = fetch_cover(match)
            except Exception:
                cover = None
            if cover:
                found["cover"] = cover
    if lyrics:
        try:
            text = find_lyrics(artist, title, found.get("album", ""), duration)
        except Exception:
            text = ""
        if text:
            found["lyrics"] = text
    return found


# Tagging the songs of a folder ("Tag music" on Home).
SONG_EXTENSIONS = ("mp3", "m4a", "flac", "opus", "ogg", "wav")  # those tag_file writes into


def read_song(path):
    """What a song file says of itself: {"title", "artist", "album", "cover", "lyrics", "duration", "tags"}: its
    own tags (also in "tags"), else "Artist - Title" in its name (a track number before it is left out)."""
    import mutagen
    easy, full = mutagen.File(path, easy=True), mutagen.File(path)
    if easy is None or full is None:
        raise RuntimeError("not a sound file the app can read")

    def first(key):
        try:
            values = easy.get(key) if easy.tags is not None else None
        except Exception:
            values = None
        return str(values[0]).strip() if values else ""
    tags = full.tags
    keys = [str(k) for k in (tags.keys() if tags is not None else [])]
    cover = bool(getattr(full, "pictures", None)) or any(
        k.startswith("APIC") or k in ("covr", "metadata_block_picture") for k in keys)
    lyrics = any(k.startswith("USLT") or k in ("\xa9lyr", "lyrics") for k in keys)
    song = {"title": first("title"), "artist": first("artist"), "album": first("album"), "cover": cover,
            "lyrics": lyrics, "duration": getattr(full.info, "length", None)}
    song["tags"] = {key: song[key] for key in ("title", "artist", "album") if song[key]}
    if not (song["title"] and song["artist"]):
        name = _TRACK_NUMBER.sub("", Path(path).stem).strip() or Path(path).stem
        artist, title = guess_song({"title": name, "uploader": song["artist"]})
        song["artist"], song["title"] = song["artist"] or artist, song["title"] or title
    return song


def is_tagged(song):
    return bool(song["title"] and song["artist"] and song["album"] and song["cover"])


def find_song_tags(path, tags=True, lyrics=True):
    """(what the file says, what to write into it: the MusicBrainz tags and cover art, the lyrics; only what is
    new)."""
    song = read_song(path)
    found = song_tags({"artist": song["artist"], "track": song["title"], "title": song["title"],
                       "duration": song["duration"]}, tags, lyrics and not song["lyrics"])
    # A song's own cover art is kept; the tags already right are not written again.
    new = {key: value for key, value in found.items()
           if (key == "cover" and not song["cover"]) or key == "lyrics"
           or (key not in ("cover", "lyrics") and value != song["tags"].get(key))}
    return song, new
