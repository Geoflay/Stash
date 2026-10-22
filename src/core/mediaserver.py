"""Videos laid out for Plex and Jellyfin (Settings > Storage): each channel is a show, each year a season and
each video an episode numbered by its date, with a description file (.nfo, read by Jellyfin, Kodi and Emby) and
its thumbnail next to it:

    Channel/Season 2024/Channel - S2024E053101 - Title.mp4    (+ .nfo and -thumb.jpg)
    Channel/tvshow.nfo
"""

import re
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from core.ffmpeg import run_ffmpeg
from core.filenames import safe_filename
from core.web import http_get


def episode_place(base, fields, description="", ext=".mp4"):
    """(the season folder, the file name, the episode's .nfo text) of a video in the library at `base`.
    `fields`: filename_fields' values (title, uploader, date "YYYY-MM-DD", platform, id). Two videos of the
    same day get the next number (E053101, E053102…)."""
    show = fields.get("uploader") or fields.get("platform") or "Videos"
    day = fields.get("date") or date.today().isoformat()
    year, month_day = day[:4], day[5:7] + day[8:10]
    season = Path(base, safe_filename(show, ""), "Season %s" % year)
    taken = {m.group(1) for p in (season.iterdir() if season.is_dir() else [])
             for m in [re.search(r"S\d{4}E(\d{6})", p.name)] if m}
    number = next(n for n in range(1, 100) if "%s%02d" % (month_day, n) not in taken or n == 99)
    code = "S%sE%s%02d" % (year, month_day, number)
    name = safe_filename("%s - %s - %s" % (show, code, fields.get("title") or "video"), ext)
    nfo = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<episodedetails>\n"
           "  <title>%s</title>\n  <showtitle>%s</showtitle>\n  <season>%s</season>\n  <episode>%s%02d</episode>\n"
           "  <aired>%s</aired>\n  <plot>%s</plot>\n%s</episodedetails>\n" % (
               escape(fields.get("title") or ""), escape(show), year, month_day, number, day,
               escape(description or ""),
               "  <uniqueid type=\"%s\" default=\"true\">%s</uniqueid>\n" % (
                   escape((fields.get("platform") or "video").lower()), escape(fields["id"])) if fields.get("id") else ""))
    return season, name, nfo


def write_extras(target, nfo, thumbnail=None):
    """Next to the saved video: its .nfo, its thumbnail (bytes, or None) and the show's tvshow.nfo."""
    target = Path(target)
    target.with_suffix(".nfo").write_text(nfo, encoding="utf-8")
    if thumbnail:
        target.with_name(target.stem + "-thumb.jpg").write_bytes(thumbnail)
    show_dir = target.parent.parent
    show_nfo = show_dir / "tvshow.nfo"
    if not show_nfo.exists():
        show_nfo.write_text("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<tvshow>\n"
                            "  <title>%s</title>\n</tvshow>\n" % escape(show_dir.name), encoding="utf-8")


def fetch_thumbnail(url, workdir):
    """The video's thumbnail as JPEG bytes (WebP ones are converted with ffmpeg); None if it cannot be had."""
    try:
        data = http_get(url, timeout=30)
        if data[:3] == b"\xff\xd8\xff":
            return data
        source, out = Path(workdir, "thumb.source"), Path(workdir, "thumb.jpg")
        source.write_bytes(data)
        run_ffmpeg(["-i", str(source), "-frames:v", "1", "-q:v", "2", str(out)], workdir)
        return out.read_bytes()
    except Exception:
        return None
