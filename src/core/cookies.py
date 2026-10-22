"""Cookies for content that needs a login: the imported cookies.txt, the browser to read
them from, and checking that they still work."""

import os
import re
import time
from datetime import datetime

from yt_dlp.cookies import YoutubeDLCookieJar

from core.errors import clean_error
from core.network import run_ytdlp
from core.storage import data_dir


BROWSERS = ["firefox", "chrome", "edge", "brave"]
ENV_COOKIES_BROWSER = os.environ.get("YTDLP_COOKIES_BROWSER", "").strip()


def saved_cookies_path():
    path = data_dir() / "cookies.txt"
    return path if path.is_file() else None


def describe_cookies(path):
    jar = YoutubeDLCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    if len(jar) == 0:
        raise ValueError("No cookies found in this file.")
    domains = sorted({c.domain.lstrip(".") for c in jar})
    shown = ", ".join(domains[:4]) + (" +%d" % (len(domains) - 4) if len(domains) > 4 else "")
    return "%d cookie%s · %s" % (len(jar), "s" if len(jar) > 1 else "", shown)


def import_cookies(content):
    text = content.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("The file is empty.")
    if not re.search(r"#( Netscape)? HTTP Cookie File", text.split("\n", 1)[0]):
        text = "# Netscape HTTP Cookie File\n" + text

    folder = data_dir()
    tmp = folder / "cookies.import.tmp"
    tmp.write_text(text, encoding="utf-8")
    try:
        summary = describe_cookies(tmp)
    except ValueError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError("This is not a valid cookies.txt file (Netscape format expected): %s"
                         % clean_error(exc)) from exc
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, folder / "cookies.txt")
    return summary


# Cookie that only exists while logged in, per site (checked offline: present and not expired).
LOGIN_COOKIES = (("Instagram", ("instagram.com",), "sessionid"),
                 ("TikTok", ("tiktok.com",), "sessionid"),
                 ("Twitter", ("x.com", "twitter.com"), "auth_token"))


def _youtube_login(ydl):
    """YouTube is asked for real: the home page says whether the session is logged in, and YouTube
    deletes the login cookies in its answer if they were rotated (same checks as yt-dlp)."""
    ie = ydl.get_info_extractor("Youtube")
    ie.initialize()
    if not ie.is_authenticated:
        return "info", "YouTube: no login cookies."
    try:
        page = ie._download_webpage("https://www.youtube.com/", None, note=False, errnote=False)
    except Exception as exc:
        return "err", "YouTube: login cookies found, but YouTube could not be reached (%s)." % clean_error(exc)
    logged_in = re.search(r'"LOGGED_IN"\s*:\s*(true|false)', page or "")
    if not ie.is_authenticated or (logged_in and logged_in.group(1) == "false"):
        return "err", ("YouTube: these cookies are no longer accepted (the browser renewed them, or you "
                       "logged out). Export them again from a private window that you then close.")
    if logged_in:
        return "ok", "YouTube: logged in (checked with YouTube)."
    return "info", "YouTube: login cookies found, but YouTube's answer did not confirm the login."


def check_cookies(cookies_browser="", cookie_file=None):
    """Which sites the configured cookies (file and/or browser) are logged in to: [(kind, text)]."""
    if not cookies_browser and not cookie_file:
        return [("err", "No cookies to test: import a cookies.txt file (or, on desktop, pick a browser).")]

    def inspect(ydl):
        results = [_youtube_login(ydl)]
        now = time.time()
        cookies = list(ydl.cookiejar)
        if cookie_file:  # yt-dlp drops expired cookies when loading: read them too, to report them
            raw = YoutubeDLCookieJar()
            raw.load(str(cookie_file), ignore_discard=True, ignore_expires=True)
            cookies += list(raw)
        for site, domains, name in LOGIN_COOKIES:
            found = [c for c in cookies if c.name == name and c.value
                     and any(c.domain.lstrip(".") == d or c.domain.endswith("." + d) for d in domains)]
            if not found:
                results.append(("info", "%s: no login cookie." % site))
                continue
            expires = max((c.expires or float("inf")) for c in found)
            if expires <= now:
                results.append(("err", "%s: the login cookie expired on %s: export the cookies again." % (
                    site, datetime.fromtimestamp(expires).strftime("%Y-%m-%d"))))
            else:
                until = ("until %s" % datetime.fromtimestamp(expires).strftime("%Y-%m-%d")
                         if expires != float("inf") else "for this browser session")
                results.append(("ok", "%s: login cookie found, valid %s (not checked online)." % (site, until)))
        return results

    return run_ytdlp(inspect, {}, cookies_browser, cookie_file)


def remove_cookies():
    path = data_dir() / "cookies.txt"
    path.unlink(missing_ok=True)
