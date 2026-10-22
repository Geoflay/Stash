"""Errors: cancellation, readable messages, and hints on how to fix the usual problems."""

import platform
import re
import sys

from yt_dlp.utils import DownloadCancelled

from core.links import is_tiktok_url, is_youtube_url


class Cancelled(DownloadCancelled):
    pass


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def clean_error(exc, limit=300):
    text = _ANSI.sub("", str(exc)).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    text = re.sub(r";?\s*please report this issue.*", "", text, flags=re.I)
    return text.strip()[:limit] or "Unknown yt-dlp error"


def error_report(error, url="", hint=None):
    """Everything about an error to paste into a message (the "Copy the details" button): the whole message,
    the link, and the versions of the app's parts."""
    from yt_dlp.version import __version__ as ytdlp_version
    from core.backend import backend_label
    lines = ["Stash: error report",
             "Error: %s" % clean_error(error, limit=20000)]
    if url:
        lines.append("Link: %s" % url)
    if hint:
        lines.append("Hint shown: %s" % hint)
    lines.append("yt-dlp %s · media: %s · %s · Python %s" % (ytdlp_version, backend_label(), platform.platform(),
                                                            sys.version.split()[0]))
    return "\n".join(lines)


# What YouTube answers when it takes the app for a bot (too many downloads in a short time, VPN…).
_YOUTUBE_BLOCK_MARKERS = ("not a bot", "rate-limited by youtube", "http error 429", "too many requests")


def is_youtube_block(url, message):
    """YouTube refuses every download for a while: retrying other videos right away would fail too."""
    low = str(message or "").lower().replace("’", "'")
    if "video subtitles" in low:  # only the subtitles were refused (see TRANSLATED_SUBTITLES_HINT)
        return False
    return is_youtube_url(url) and any(marker in low for marker in _YOUTUBE_BLOCK_MARKERS)


YOUTUBE_BLOCK_HINT = ("YouTube takes the app for a bot, usually after many downloads in a short time or "
                      "behind a VPN. Wait a while (often up to an hour) before resuming. Importing the "
                      "cookies of a browser logged in to YouTube (Settings) usually helps.")


TRANSLATED_SUBTITLES_HINT = ("YouTube often refuses its auto-translated subtitles (too many requests), "
                             "even after a single one. Importing the cookies of a browser logged in to YouTube "
                             "(Settings) can help; the subtitles in the video's own language usually work.")


def make_hint(message, url, desktop):
    low = (message or "").lower()
    if "video subtitles" in low and ("429" in low or "too many requests" in low):
        return TRANSLATED_SUBTITLES_HINT
    if is_youtube_block(url, message):
        return YOUTUBE_BLOCK_HINT
    if "cookie database" in low or "decrypt" in low:
        return ("The browser's cookies could not be read: Chrome and Edge lock them while running, "
                "and recent versions encrypt them. Close the browser completely and try again, pick "
                "Firefox instead, or (most reliable) export a cookies.txt file with a browser extension "
                "and import it in Settings, then set Browser cookies to None.")
    if "login" in low or "cookies" in low or "rate-limit" in low or "empty media response" in low:
        text = "This platform requires a login. Import your cookies.txt file (Cookies card)"
        if desktop:
            text += " or pick the browser you are logged in to"
        return text + ", then try again."
    if "unsupported url" in low:
        return ("yt-dlp does not know this site, or found no video on this page. Check that the link opens "
                "the video itself; for a new site, updating yt-dlp (yt-dlp card) may add it.")
    if is_tiktok_url(url):
        return ("TikTok regularly blocks downloaders. Try importing your cookies.txt file "
                "(Cookies card), or update yt-dlp (yt-dlp card) and restart the app.")
    if "unable to extract" in low or "unexpected response" in low:
        return ("yt-dlp can no longer read this page, which happens when the platform changes. "
                "Update yt-dlp (yt-dlp card), then restart the app.")
    return None
