"""Options shared by every yt-dlp call: automatic retries, speed limit (another one at night, if chosen), proxy
and cookies."""

import math
import time
import urllib.error
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

from core.backend import FFMPEG_BIN
from core.errors import Cancelled
from core.schedule import DEFAULT_NIGHT, is_night


# night_ratelimit: the limit during the night hours (None: no limit), when night_speed is on.
NETWORK = {"retries": 3, "ratelimit": None, "proxy": "", "night_speed": False, "night_ratelimit": None,
           "night": DEFAULT_NIGHT}
PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
RETRY_CHOICES = (0, 1, 2, 3, 5, 10)
RESTART_CHOICES = (0, 1, 2, 3, 5, 10)  # new tries of the queue after a connection loss (Settings)
DEFAULT_RESTARTS = 3
MAX_RETRY_WAIT = 30
MAX_SPEED_LIMIT = 10000.0

_TRANSIENT_MARKERS = (
    "timed out", "timeout", "time-out", "connection reset", "connection aborted", "connection refused",
    "remote end closed", "incomplete read", "temporary failure", "network is unreachable",
    "name or service not known", "getaddrinfo", "http error 5", "bad gateway", "service unavailable",
    "unable to connect", "eof occurred", "broken pipe", "ssl:", "read error", "transporterror",
)
_PERMANENT_MARKERS = (
    "http error 4", "login", "sign in", "private", "not available", "unavailable", "unsupported url",
    "requested format", "no video formats", "cookies", "copyright", "members-only",
)


def parse_speed_limit(text):
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError("Enter a number of MB/s (for example 2 or 0.5), or leave empty for no limit.")
    if not math.isfinite(value) or value <= 0 or value > MAX_SPEED_LIMIT:
        raise ValueError("The speed limit must be between 0.01 and %d MB/s." % MAX_SPEED_LIMIT)
    return value


def parse_proxy(text):
    """The proxy typed in Settings ("" for none); ValueError if it is not a proxy address."""
    text = (text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.scheme.lower() not in PROXY_SCHEMES or not parsed.hostname or not port:
        raise ValueError("Enter a proxy like http://host:8080 or socks5://host:1080 "
                         "(user:password@ before the host if needed).")
    return text


def speed_limit_bytes(megabytes):
    return max(1024, int(megabytes * 1024 * 1024)) if megabytes else None


def current_ratelimit(now=None):
    """The speed limit of a download starting now: the night one during the night hours, if set."""
    if NETWORK["night_speed"] and is_night(NETWORK["night"], now):
        return NETWORK["night_ratelimit"]
    return NETWORK["ratelimit"]


def is_transient(exc):
    inner = exc
    info = getattr(exc, "exc_info", None)
    if info and len(info) > 1 and info[1] is not None:
        inner = info[1]
    text = ("%s %s" % (exc, inner)).lower()
    if isinstance(inner, (TimeoutError, ConnectionError)) or any(m in text for m in _TRANSIENT_MARKERS):
        return True
    # YouTube sometimes refuses a media URL with 403 (more often with parallel downloads); a new try
    # fetches fresh URLs and usually works. A 403 on the page itself stays permanent (login, region…).
    if "unable to download video data" in text and "http error 403" in text:
        return True
    if any(m in text for m in _PERMANENT_MARKERS):
        return False
    if isinstance(inner, urllib.error.HTTPError):
        return inner.code >= 500
    return isinstance(inner, OSError) or any(c.__name__ == "TransportError" for c in type(inner).__mro__)


def call_with_retries(report, func, *args, **kwargs):
    attempt = 0
    while True:
        try:
            return func(*args, **kwargs)
        except Cancelled:
            raise
        except Exception as exc:
            total = NETWORK["retries"]
            if attempt >= total or not is_transient(exc):
                raise
            attempt += 1
            delay = min(2 ** attempt, MAX_RETRY_WAIT)
            for remaining in range(delay, 0, -1):
                report(None, "Connection problem. Retrying in %ds (attempt %d of %d)…" % (remaining, attempt, total))
                time.sleep(1)


def run_ytdlp(extract, extra_opts, cookies_browser="", cookie_file=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 30,
    }
    if current_ratelimit():
        opts["ratelimit"] = current_ratelimit()
    if NETWORK["proxy"]:
        opts["proxy"] = NETWORK["proxy"]
    if FFMPEG_BIN:  # also when it is not in the PATH (chosen or downloaded in Settings)
        opts["ffmpeg_location"] = FFMPEG_BIN
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    opts.update(extra_opts)
    with YoutubeDL(opts) as ydl:
        return extract(ydl)


def first_entry(info):
    """The single video of a result that yt-dlp may have wrapped in a playlist."""
    if info.get("_type") == "playlist":
        entries = list(info.get("entries") or [])
        return entries[0] if entries else {}
    return info
