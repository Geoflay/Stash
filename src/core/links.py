"""Recognizing links: which platform, single video or playlist, and short links."""

import functools
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from core.web import ssl_context


TWITTER_DOMAINS = ("twitter.com", "x.com")
TIKTOK_DOMAINS = ("tiktok.com",)
INSTAGRAM_DOMAINS = ("instagram.com",)
YOUTUBE_DOMAINS = ("youtube.com",)
_LINK = re.compile(r"https?://\S+")


def _host_in(url, domains):
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def is_web_link(url):
    """Any http(s) link: yt-dlp decides whether it can read it (it knows about 1,800 sites, and its
    generic extractor finds videos embedded in other pages)."""
    try:
        parts = urlparse(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


@functools.lru_cache(maxsize=1)
def _site_extractors():
    from yt_dlp.extractor import gen_extractor_classes
    return [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]


def supported_site(url):
    """Name of the yt-dlp extractor made for this site ("Vimeo", "Soundcloud"…), or None when only the
    generic one could try. The first call loads every extractor (under a second): not from the event loop."""
    if not is_web_link(url):
        return None
    return next((ie.ie_key() for ie in _site_extractors() if ie.suitable(url)), None)


def is_twitter_url(url):
    return _host_in(url, TWITTER_DOMAINS)


def is_tiktok_url(url):
    return _host_in(url, TIKTOK_DOMAINS)


def is_instagram_url(url):
    return _host_in(url, INSTAGRAM_DOMAINS)


def is_youtube_url(url):
    return _host_in(url, YOUTUBE_DOMAINS + ("youtu.be",))


def first_link(text):
    m = _LINK.search(text or "")
    return m.group(0) if m else None


def all_links(text):
    """Every link of the text (several pasted at once, one per line), each once, in order."""
    return list(dict.fromkeys(_LINK.findall(text or "")))


def as_link(text):
    """A link typed without https:// ("youtube.com/watch?v=…"), with it; None for other text (a search)."""
    text = (text or "").strip()
    if re.fullmatch(r"(www\.)?[\w-]+(\.[\w-]+)*\.[a-z]{2,}/\S*", text, re.I):
        return "https://" + text
    return None


_TT_POST = re.compile(
    r"tiktok(?:v)?\.com/(?:@(?P<user>[\w.\-]*)/)?(?:share/)?(?:video|photo|v)/(?P<id>\d{8,})"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _next_location(url):
    handlers = [_NoRedirect()]
    ctx = ssl_context()
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": "facebookexternalhit/1.1"})
        try:
            with opener.open(req, timeout=15):
                return None
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location")
            exc.close()
            if exc.code in (301, 302, 303, 307, 308) and location:
                return urljoin(url, location)
        except Exception:
            pass
    return None


def resolve_tiktok(url):
    current = url
    for _ in range(6):
        m = _TT_POST.search(unquote(current))
        if m:
            return "https://www.tiktok.com/@%s/video/%s" % (m.group("user") or "_", m.group("id"))
        current = _next_location(current)
        if not current:
            break
    return url


def normalize_url(url):
    return resolve_tiktok(url) if is_tiktok_url(url) else url


_YT_COLLECTION_PATHS = ("/playlist", "/@", "/channel/", "/c/", "/user/")


def is_playlist_url(url):
    try:
        p = urlparse(url)
    except ValueError:
        return False
    path = p.path.lower()
    if _host_in(url, YOUTUBE_DOMAINS):
        if path.startswith("/playlist"):
            return "list" in parse_qs(p.query)
        return path.startswith(_YT_COLLECTION_PATHS)
    if is_tiktok_url(url):
        return re.fullmatch(r"/@[^/]+/?", path) is not None
    return False


def playlist_site(url):
    """Which playlist limit of the Settings applies (core.playlist.PLAYLIST_SITES)."""
    if is_tiktok_url(url):
        return "tiktok"
    return "youtube" if _host_in(url, YOUTUBE_DOMAINS) else "other"
