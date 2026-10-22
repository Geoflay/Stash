"""Plain HTTPS requests made outside yt-dlp (update checks, thumbnails, TikTok short links)."""

import functools
import ssl
import urllib.request


@functools.lru_cache(maxsize=None)
def ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def urlopen(req, timeout):
    """urllib's urlopen through the proxy set in Settings (HTTP proxies only: urllib cannot use SOCKS)."""
    # Imported here: this module is loaded before yt-dlp may be updated, and core.network imports yt-dlp.
    from core.network import NETWORK
    handlers = [urllib.request.HTTPSHandler(context=ssl_context())]
    proxy = NETWORK["proxy"]
    if proxy.startswith(("http://", "https://")):
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "video-downloader"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()
