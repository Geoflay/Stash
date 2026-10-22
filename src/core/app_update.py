"""Whether a newer version of the app itself is published (its GitHub releases), like the yt-dlp check: the app
is not updated by itself, the download page of the new version is opened."""

import json
import re
import urllib.error

from core.web import http_get

APP_VERSION = "0.1.0"  # the version in pyproject.toml
APP_RELEASES_API = "https://api.github.com/repos/Geoflay/video_downloader/releases/latest"
APP_RELEASES_PAGE = "https://github.com/Geoflay/video_downloader/releases/latest"
APP_CHECK_INTERVAL = 24 * 3600


def version_tuple(version):
    return tuple(int(x) for x in re.findall(r"\d+", version or ""))


def latest_app_release():
    """{"version", "page", "notes"} of the latest release, or None when none is published yet."""
    try:
        data = json.loads(http_get(APP_RELEASES_API, timeout=15))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # no release yet
            return None
        raise
    version = str(data.get("tag_name") or "").lstrip("vV")
    if not version_tuple(version):
        return None
    return {"version": version, "page": data.get("html_url") or APP_RELEASES_PAGE,
            "notes": str(data.get("body") or "")[:600]}


def is_newer(version, current=APP_VERSION):
    return bool(version) and version_tuple(version) > version_tuple(current)
