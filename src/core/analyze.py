"""Reading a link before downloading it: title, formats, photos, subtitles."""

import re
from urllib.parse import unquote

from yt_dlp.utils import DownloadError

from core.chapters import chapter_list
from core.errors import clean_error
from core.links import is_instagram_url, is_twitter_url, normalize_url
from core.network import first_entry, run_ytdlp
from core.playlist import lists_several_items
from core.preview import pick_player_source, pick_preview_source
from core.streams import audio_tracks, compact_formats, measure_downloads
from core.subtitles import subtitle_choices
from core.tags import main_tags


def _is_audio_only(info):
    formats = info.get("formats") or []
    if formats:
        return all(f.get("vcodec") in (None, "none") for f in formats)
    return info.get("vcodec") == "none"


def parse_images(item):
    out = []
    for im in ((item or {}).get("imagePost") or {}).get("images") or []:
        urls = (im.get("imageURL") or {}).get("urlList") or []
        if not urls:
            continue
        url = next((u for u in urls if re.search(r"\.(jpe?g|png)(\?|$)", u)), urls[0])
        out.append({"url": url, "width": im.get("imageWidth"), "height": im.get("imageHeight")})
    return out


def tiktok_images(ydl, url):
    m = re.search(r"/video/(\d+)", url)
    if not m:
        raise RuntimeError("TikTok link without a post ID.")
    ie = ydl.get_info_extractor("TikTok")
    item, _status = ie._extract_web_data_and_status(url, m.group(1), fatal=False)
    images = parse_images(item)
    if not images:
        raise RuntimeError("No photos found on the TikTok page (yt-dlp may need an update).")
    return images


_AUDIO_URL_KEYS = ("progressive_download_url", "fast_start_progressive_download_url")


def _find_audio_url(node, depth=0):
    if depth > 8:
        return None
    if isinstance(node, dict):
        for key in _AUDIO_URL_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        for value in node.values():
            found = _find_audio_url(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_audio_url(value, depth + 1)
            if found:
                return found
    return None


def instagram_photo_post(product):
    if not isinstance(product, dict):
        return None
    items = [i for i in (product.get("carousel_media") or []) if isinstance(i, dict)] or [product]
    images = []
    for item in items:
        if item.get("video_versions") or item.get("media_type") == 2:
            return None
        candidates = [c for c in ((item.get("image_versions2") or {}).get("candidates") or [])
                      if isinstance(c, dict) and c.get("url")]
        if not candidates:
            continue
        best = max(candidates, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
        images.append({"url": best["url"], "width": best.get("width"), "height": best.get("height")})
    if not images:
        return None
    caption = product.get("caption")
    text = (caption.get("text") if isinstance(caption, dict) else "") or ""
    text = " ".join(text.split())
    user = product.get("user")
    uploader = (user.get("username") if isinstance(user, dict) else "") or ""
    if len(text) > 80:
        text = text[:77].rstrip() + "…"
    title = text or ("Instagram photos by %s" % uploader if uploader else "Instagram photos")
    return {"images": images, "audio_url": _find_audio_url(product), "title": title, "uploader": uploader}


def twitter_photo_post(status):
    """The photos of a tweet that has no video (yt-dlp only downloads videos): like instagram_photo_post."""
    if not isinstance(status, dict):
        return None
    media = [m for m in ((status.get("extended_entities") or {}).get("media") or []) if isinstance(m, dict)]
    if not media or any(m.get("type") != "photo" for m in media):
        return None
    images = []
    for m in media:
        url = m.get("media_url_https") or ""
        if url:
            size = m.get("original_info") or {}
            images.append({"url": url + ("&" if "?" in url else "?") + "name=orig",
                           "width": size.get("width"), "height": size.get("height")})
    if not images:
        return None
    text = " ".join(re.sub(r"https?://t\.co/\S+", "", status.get("full_text") or status.get("text") or "").split())
    if len(text) > 80:
        text = text[:77].rstrip() + "…"
    user = status.get("user") or (((status.get("core") or {}).get("user_results") or {}).get("result") or {}).get(
        "legacy") or {}
    uploader = user.get("screen_name") or ""
    return {"images": images, "audio_url": None, "uploader": uploader,
            "title": text or ("Photos by %s" % uploader if uploader else "Twitter photos")}


def _capture_twitter_status(ydl, captured):
    try:
        ie = ydl.get_info_extractor("Twitter")
        original = ie._extract_status

        def wrapper(*args, **kwargs):
            status = original(*args, **kwargs)
            captured.setdefault("status", status)
            return status

        ie._extract_status = wrapper
    except Exception:
        pass


def _capture_instagram_product(ydl, captured):
    try:
        ie = ydl.get_info_extractor("Instagram")
        original = ie._extract_product

        def wrapper(product_info, *args, **kwargs):
            captured.setdefault("product", product_info)
            return original(product_info, *args, **kwargs)

        ie._extract_product = wrapper
    except Exception:
        pass


def fetch_info(url, cookies_browser="", cookie_file=None):
    photo_hint = "/photo/" in unquote(url)
    url = normalize_url(url)
    extra = {}

    def photo_post_data(post, site):
        extra.clear()
        extra.update(slideshow=True, images=post["images"], audio_url=post["audio_url"])
        return {"title": post["title"], "uploader": post["uploader"], "thumbnail": post["images"][0]["url"],
                "extractor_key": site, "formats": []}

    def extract(ydl):
        captured = {}
        instagram, twitter = is_instagram_url(url), is_twitter_url(url)
        if instagram:
            _capture_instagram_product(ydl, captured)
        if twitter:
            _capture_twitter_status(ydl, captured)
        try:
            data = ydl.extract_info(url, download=False)
        except DownloadError:
            post = (instagram_photo_post(captured.get("product")) if instagram
                    else twitter_photo_post(captured.get("status")) if twitter else None)
            if post is None:
                raise
            return photo_post_data(post, "Instagram" if instagram else "Twitter")
        post = instagram_photo_post(captured.get("product")) if instagram else None
        if post is not None:
            return photo_post_data(post, "Instagram")
        extra.clear()
        if lists_several_items(data):
            extra["playlist"] = True
            return data
        data = first_entry(data)
        if data.get("_type") in ("url", "url_transparent"):  # a list of one item, left unread by extract_flat
            data = first_entry(ydl.extract_info(data["url"], download=False, ie_key=data.get("ie_key")))
        extra["measured"] = measure_downloads(ydl, data, is_twitter_url(url))
        if data.get("extractor_key") == "TikTok" and (photo_hint or _is_audio_only(data)):
            extra["slideshow"] = True
            try:
                extra["images"] = tiktok_images(ydl, url)
            except Exception as exc:
                extra["images_error"] = clean_error(exc)
        return data

    # Only the first two items of a list are read: enough to tell an album or a set (shown as a playlist)
    # from a single video, without reading each item.
    opts = {"skip_download": True, "playlist_items": "1-2", "extract_flat": "in_playlist"}
    data = run_ytdlp(extract, opts, cookies_browser, cookie_file)
    if extra.get("playlist"):
        # Generic: a page yt-dlp has no extractor for, or a feed (a podcast's episodes, see core.podcast).
        return {"url": url, "playlist": True, "generic": data.get("extractor_key") == "Generic"}
    return {
        "url": url,
        "title": data.get("title") or "video",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "upload_date": data.get("upload_date") or "",
        "video_id": data.get("id") or "",
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "platform": data.get("extractor_key") or "",
        "slideshow": bool(extra.get("slideshow")),
        "images": extra.get("images") or [],
        "images_error": extra.get("images_error") or "",
        "audio_url": extra.get("audio_url") or "",
        "formats": compact_formats(data),
        "measured": extra.get("measured") or {},
        "subtitles": subtitle_choices(data),
        "preview": pick_preview_source(data),
        "player": pick_player_source(data),
        "tags": main_tags(data),
        "chapters": chapter_list(data),
        "audio_tracks": audio_tracks(data),
        "live": data.get("live_status") == "is_live" or bool(data.get("is_live")),
    }
