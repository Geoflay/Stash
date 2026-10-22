"""Tags written into the files: title, artist, album, album artist, genre, track number, composer, date,
description, link, lyrics and cover art (its size and shape as chosen in Settings, see TAG_OPTIONS)."""

import base64
import io
import re
import struct
from pathlib import Path

from yt_dlp.networking import Request

from core.backend import HAS_PIL
from core.ffmpeg import run_ffmpeg
from core.jobs import entering_conversion


# Settings > Downloads > Metadata: the extra tags written, and the cover art's size (0: as the site gives it) and
# shape (square: cut to a square around its middle).
TAG_OPTIONS = {"genre": True, "album_artist": True, "track": True, "composer": True, "comment": False,
               "cover_size": 0, "square_cover": False}
COVER_SIZES = (0, 1200, 800, 500, 300)
LABELS = {"album_artist": "Album artist", "track": "Track number"}  # the others: the key, capitalized


def try_tag(path, meta):
    """Best effort: a file that cannot be tagged is still a good download."""
    try:
        tag_file(path, meta)
    except Exception:
        pass


def tag_file(path, meta):
    """Write title, artist, album, date, source link, cover art and lyrics (synced ones as LRC text, which the
    players that show lyrics scroll) into the file, with mutagen.

    Covers MP3, WAV, M4A/MP4/MOV, FLAC and Ogg/Opus; other containers (WebM, MKV) are left as they are.
    Without mutagen, only MP3 is tagged (built-in ID3 writer).
    """
    try:
        import mutagen
    except ImportError:
        if Path(path).suffix.lower() == ".mp3":
            tag_mp3(path, meta)
        return
    ext = Path(path).suffix.lower()
    title, artist, album = meta.get("title"), meta.get("artist"), meta.get("album")
    date, source, cover = meta.get("date") or meta.get("year"), meta.get("source"), meta.get("cover")
    lyrics = meta.get("lyrics")
    album_artist, genre, track = meta.get("album_artist"), meta.get("genre"), meta.get("track")
    composer, comment = meta.get("composer"), meta.get("comment")

    if ext in (".mp3", ".wav"):
        from mutagen import id3
        if ext == ".wav":
            from mutagen.wave import WAVE
            audio = WAVE(path)
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
        else:
            try:
                tags = id3.ID3(path)
            except id3.ID3NoHeaderError:
                tags = id3.ID3()
        for frame, value in ((id3.TIT2, title), (id3.TPE1, artist), (id3.TALB, album), (id3.TDRC, date),
                             (id3.TPE2, album_artist), (id3.TCON, genre), (id3.TRCK, track), (id3.TCOM, composer)):
            if value:
                tags.setall(frame.__name__, [frame(encoding=3, text=str(value))])
        if source:
            tags.setall("WOAS", [id3.WOAS(url=source)])
        if lyrics:
            tags.setall("USLT", [id3.USLT(encoding=3, lang="und", desc="", text=lyrics)])
        if comment:
            tags.setall("COMM", [id3.COMM(encoding=3, lang="und", desc="", text=comment)])
        if cover:
            tags.setall("APIC", [id3.APIC(encoding=3, mime=cover[1], type=3, desc="Cover", data=cover[0])])
        if ext == ".wav":
            audio.save()
        else:
            tags.save(path, v2_version=3)
    elif ext in (".m4a", ".mp4", ".m4v", ".mov"):
        from mutagen.mp4 import MP4, MP4Cover
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for key, value in (("\xa9nam", title), ("\xa9ART", artist), ("\xa9alb", album), ("\xa9day", date),
                           ("\xa9cmt", source), ("\xa9lyr", lyrics), ("aART", album_artist), ("\xa9gen", genre),
                           ("\xa9wrt", composer), ("desc", comment)):
            if value:
                audio.tags[key] = [str(value)]
        number = re.match(r"\d+", str(track or ""))
        if number:
            audio.tags["trkn"] = [(int(number.group()), 0)]
        if cover:
            kind = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
            audio.tags["covr"] = [MP4Cover(cover[0], imageformat=kind)]
        audio.save()
    elif ext in (".flac", ".opus", ".ogg"):
        from mutagen.flac import Picture
        audio = mutagen.File(path)
        if audio is None:
            return
        if audio.tags is None:
            audio.add_tags()
        for key, value in (("title", title), ("artist", artist), ("album", album), ("date", date),
                           ("comment", source), ("lyrics", lyrics), ("albumartist", album_artist), ("genre", genre),
                           ("tracknumber", track), ("composer", composer), ("description", comment)):
            if value:
                audio[key] = [str(value)]
        if cover:
            picture = Picture()
            picture.type, picture.mime, picture.desc, picture.data = 3, cover[1], "Cover", cover[0]
            if ext == ".flac":
                audio.clear_pictures()
                audio.add_picture(picture)
            else:  # Ogg has no picture block: the FLAC picture goes base64-encoded in a comment
                audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
        audio.save()


def _synchsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _id3_text(frame_id, text):
    body = b"\x01" + text.encode("utf-16")
    return frame_id.encode("ascii") + struct.pack(">I", len(body)) + b"\x00\x00" + body


def _id3_picture(data, mime):
    body = b"\x00" + mime.encode("ascii") + b"\x00\x03\x00" + data
    return b"APIC" + struct.pack(">I", len(body)) + b"\x00\x00" + body


def tag_mp3(path, meta):
    frames = b""
    for frame_id, key in (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album"), ("TYER", "year")):
        if meta.get(key):
            frames += _id3_text(frame_id, str(meta[key]))
    if meta.get("cover"):
        frames += _id3_picture(*meta["cover"])
    if not frames:
        return
    path = Path(path)
    raw = path.read_bytes()
    if raw[:3] == b"ID3" and len(raw) >= 10:
        size = (raw[6] << 21) | (raw[7] << 14) | (raw[8] << 7) | raw[9]
        raw = raw[10 + size:]
    tag = b"ID3\x03\x00\x00" + _synchsafe(len(frames)) + frames
    tmp = path.with_name(path.name + ".tag")
    tmp.write_bytes(tag + raw)
    tmp.replace(path)


def album_art(data):
    """A YouTube Music track's thumbnail is its square album art between two plain bars: that square, as JPEG.
    None when the sides are not plain (a music video, a photo) or Pillow is missing."""
    if not HAS_PIL:
        return None
    from PIL import Image, ImageStat
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        side = (w - h) // 2
        if side < h // 8:
            return None
        bars = (im.crop((0, 0, side - 2, h)), im.crop((w - side + 2, 0, w, h)))
        if any(max(ImageStat.Stat(bar).stddev) > ALBUM_BAR_NOISE for bar in bars):
            return None
        out = io.BytesIO()
        im.crop((side, 0, side + h, h)).save(out, "JPEG", quality=92)
    return out.getvalue()


ALBUM_BAR_NOISE = 6  # colour variation left by JPEG compression in a plain bar


def fit_cover(data, size=0, square=False):
    """The cover art at `size` pixels at most on its longer side (0: unchanged), cut to a square around its
    middle if `square`, as JPEG; `data` as it is without Pillow, or when nothing is to change."""
    if not HAS_PIL or not (size or square):
        return data
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if square:
            side = min(im.size)
            left, top = (im.width - side) // 2, (im.height - side) // 2
            im = im.crop((left, top, left + side, top + side))
        if size and max(im.size) > size:
            im.thumbnail((size, size))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=92)
    return out.getvalue()


def _cover_bytes(ydl, url, square=False):
    data = ydl.urlopen(Request(url)).read()
    if square:
        try:
            art = album_art(data)
        except Exception:
            art = None
        if art:
            data = art
    try:
        data = fit_cover(data, TAG_OPTIONS["cover_size"], TAG_OPTIONS["square_cover"])
    except Exception:
        pass
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    if HAS_PIL:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            out = io.BytesIO()
            im.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue(), "image/jpeg"
    return None


EDITABLE_TAGS = ("title", "artist", "album", "album_artist", "genre", "track")


def _joined(info, key):
    """yt-dlp's single value ("genre"), else its list ("genres") joined."""
    values = info.get(key + "s")
    return str(info.get(key) or (", ".join(map(str, values)) if values else "") or "")


def main_tags(info):
    """The tags of EDITABLE_TAGS as they will be written, from yt-dlp's info (shown for editing before
    downloading)."""
    return {
        "title": info.get("track") or info.get("title") or "",
        "artist": (_joined(info, "artist") or info.get("creator") or info.get("uploader") or info.get("channel")
                   or ""),
        "album": info.get("album") or "",
        "album_artist": _joined(info, "album_artist"),
        "genre": _joined(info, "genre"),
        "track": str(info.get("track_number") or ""),
    }


def media_metadata(ydl, info):
    """Tags for tag_file, from yt-dlp's info (the cover is downloaded, as JPEG or PNG)."""
    date = str(info.get("release_date") or info.get("upload_date") or info.get("release_year") or "")
    meta = {
        **main_tags(info),
        "year": date[:4] if date[:4].isdigit() else "",
        "date": "%s-%s-%s" % (date[:4], date[4:6], date[6:8]) if re.fullmatch(r"\d{8}", date) else "",
        "source": info.get("webpage_url") or "",
        "composer": _joined(info, "composer"),
        "comment": (info.get("description") or "").strip(),
    }
    for key in ("genre", "album_artist", "track", "composer", "comment"):  # as chosen in Settings
        if not TAG_OPTIONS[key]:
            meta.pop(key, None)
    if info.get("thumbnail"):
        try:
            # YouTube Music tracks: the square album art instead of the 16:9 thumbnail.
            meta["cover"] = _cover_bytes(ydl, info["thumbnail"], square=info.get("extractor_key") == "Youtube")
        except Exception:
            pass
    return meta


def attach_cover(path, cover, workdir):
    """MKV keeps its cover art as an attachment (shown by VLC, Plex, Kodi…); returns the new file."""
    entering_conversion()
    data, mime = cover
    image = Path(workdir, "cover.png" if mime == "image/png" else "cover.jpg")
    image.write_bytes(data)
    out = Path(workdir, "with_cover.mkv")
    try:
        run_ffmpeg(["-i", str(path), "-map", "0", "-c", "copy", "-attach", str(image),
                     "-metadata:s:t", "mimetype=" + mime, "-metadata:s:t", "filename=" + image.name, str(out)],
                    workdir)
    except RuntimeError:
        out.unlink(missing_ok=True)
        return path
    return out
