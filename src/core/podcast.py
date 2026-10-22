"""Podcasts: the episodes of an RSS (or Atom) feed, listed like a playlist with their descriptions, and the tags
of their files (episode title, podcast name, author, date, cover art) read from the feed. An Apple Podcasts link
is turned into its podcast's feed."""

import html
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

from core.units import parse_time
from core.web import http_get


ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


class NotAFeed(ValueError):
    pass


def apple_podcast_id(url):
    """The podcast of an Apple Podcasts link (podcasts.apple.com/…/id123), or None (also for one episode's
    link, "?i=…": yt-dlp reads it)."""
    try:
        p = urlparse(url)
    except ValueError:
        return None
    found = re.search(r"/id(\d+)", p.path)
    if p.netloc.lower() not in ("podcasts.apple.com", "itunes.apple.com") or not found or "i" in parse_qs(p.query):
        return None
    return found.group(1)


def feed_address(url):
    """The feed of `url`: itself, or the one Apple Podcasts gives for its podcast."""
    podcast = apple_podcast_id(url)
    if podcast is None:
        return url
    results = json.loads(http_get("https://itunes.apple.com/lookup?id=%s&entity=podcast" % podcast)).get("results")
    feed = next((r.get("feedUrl") for r in results or [] if r.get("feedUrl")), None)
    if not feed:
        raise RuntimeError("Apple Podcasts gives no feed for this podcast: it may only be on Apple's app.")
    return feed


def maybe_feed(url):
    """Whether the link looks like a feed (feeds.…, ….rss, …/feed…) or an Apple Podcasts podcast: it is read as
    one before asking yt-dlp."""
    if apple_podcast_id(url):
        return True
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host, path = p.netloc.lower(), p.path.lower()
    return (host.startswith(("feeds.", "feed.", "rss.")) or path.endswith((".rss", ".xml", ".atom"))
            or re.search(r"(^|/)(rss|feeds?)(/|$)", path) is not None or "format=rss" in p.query.lower())


def _text(node, path):
    found = node.find(path) if node is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def _date(text):
    """"Tue, 03 Jun 2025 08:00:00 +0000" (RSS) or "2025-06-03T08:00:00Z" (Atom) -> "20250603", else ""."""
    if not text:
        return ""
    found = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if found:
        return "".join(found.groups())
    try:
        return parsedate_to_datetime(text).strftime("%Y%m%d")
    except (TypeError, ValueError):
        return ""


def _seconds(text):
    """itunes:duration: "1:02:03", "62:03" or "3723"."""
    try:
        return parse_time(text) or None
    except ValueError:
        return None


def _plain(text):
    """An episode's description as plain text (feeds often give HTML)."""
    text = re.sub(r"<\s*(br|/p|/li)\b[^>]*>", "\n", text or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _episode(title, url, kind, date, duration, image, number, author, show, page, description=""):
    date_tag = "%s-%s-%s" % (date[:4], date[4:6], date[6:]) if date else ""
    return {
        "url": url, "title": title or "episode", "duration": duration, "thumbnail": image, "date": date,
        "number": number, "video": kind.startswith("video/"), "page": page, "cover": image,
        "description": _plain(description),
        # Written into the file instead of what yt-dlp finds in it (a bare MP3 link has no title).
        "tags": {key: value for key, value in (("title", title), ("artist", author), ("album", show),
                                               ("date", date_tag)) if value},
    }


def parse_feed(data):
    """The podcast of an RSS or Atom feed: {"title", "uploader", "image", "entries": [episode…], "podcast"}.
    Only the items with a sound or video file are episodes. NotAFeed when it is not a feed."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise NotAFeed("This link is not a feed.")
    entries = []
    if root.tag == "rss":
        channel = root.find("channel")
        show = _text(channel, "title")
        author = _text(channel, ITUNES + "author") or _text(channel, "managingEditor")
        image_node = channel.find(ITUNES + "image") if channel is not None else None
        image = (image_node.get("href") if image_node is not None else "") or _text(channel, "image/url")
        for item in channel.findall("item") if channel is not None else []:
            enclosure = item.find("enclosure")
            if enclosure is None:
                enclosure = item.find(MEDIA + "content")
            url = (enclosure.get("url") or "").strip() if enclosure is not None else ""
            if not url.startswith("http"):
                continue
            item_image = item.find(ITUNES + "image")
            entries.append(_episode(
                _text(item, "title"), url, enclosure.get("type") or "", _date(_text(item, "pubDate")),
                _seconds(_text(item, ITUNES + "duration")),
                (item_image.get("href") if item_image is not None else "") or image,
                _text(item, ITUNES + "episode"), _text(item, ITUNES + "author") or author, show,
                _text(item, "link"), _text(item, ITUNES + "summary") or _text(item, "description")
                or _text(item, CONTENT + "encoded")))
    elif root.tag == ATOM + "feed":
        show = _text(root, ATOM + "title")
        author = _text(root, ATOM + "author/" + ATOM + "name")
        image = _text(root, ATOM + "logo") or _text(root, ATOM + "icon")
        for item in root.findall(ATOM + "entry"):
            links = item.findall(ATOM + "link")
            enclosure = next((link for link in links if link.get("rel") == "enclosure"), None)
            url = (enclosure.get("href") or "").strip() if enclosure is not None else ""
            if not url.startswith("http"):
                continue
            page = next((link.get("href") for link in links if link.get("rel") in (None, "alternate")), "")
            entries.append(_episode(
                _text(item, ATOM + "title"), url, enclosure.get("type") or "",
                _date(_text(item, ATOM + "published") or _text(item, ATOM + "updated")), None, image, "",
                _text(item, ATOM + "author/" + ATOM + "name") or author, show, page or "",
                _text(item, ATOM + "summary") or _text(item, ATOM + "content")))
    else:
        raise NotAFeed("This link is not a feed.")
    if not entries:
        raise RuntimeError("This feed has no episodes to download (no sound or video files in it).")
    return {"title": show or "podcast", "uploader": author, "image": image, "entries": entries, "podcast": True}


def fetch_feed(url, limit=0):
    """The podcast at `url` (a feed, or an Apple Podcasts link), with its first `limit` episodes (the newest,
    in most feeds; all if 0)."""
    feed = parse_feed(http_get(feed_address(url)))
    feed["total"] = len(feed["entries"])
    feed["limit"] = limit
    if limit:
        feed["entries"] = feed["entries"][:limit]
    return feed
