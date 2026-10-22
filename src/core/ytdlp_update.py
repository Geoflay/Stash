"""Updating yt-dlp without rebuilding the app: the newer release is downloaded into the
app's data folder and loaded at the next start, before anything imports yt_dlp."""

import hashlib
import json
import os
import re
import sys
import urllib.request
import zipfile

from core.storage import data_dir, write_json
from core.web import http_get, urlopen


YTDLP_META = "yt-dlp.json"
YTDLP_ZIP_GLOB = "yt-dlp-*.zip"


def _version_tuple(version):
    return tuple(int(x) for x in re.findall(r"\d+", version or ""))


def _bundled_ytdlp_version():
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        return None


def _purge_ytdlp_modules():
    for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
        del sys.modules[name]


def _cleanup_ytdlp_files(keep=()):
    for p in data_dir().glob(YTDLP_ZIP_GLOB):
        if p.name not in keep:
            try:
                p.unlink()
            except OSError:
                pass


def remove_ytdlp_update():
    try:
        (data_dir() / YTDLP_META).unlink(missing_ok=True)
    except OSError:
        pass
    _cleanup_ytdlp_files()


def _activate_updated_ytdlp():
    folder = data_dir()
    try:
        meta = json.loads((folder / YTDLP_META).read_text(encoding="utf-8"))
        archive = folder / meta["file"]
        version = meta["version"]
    except Exception:
        _cleanup_ytdlp_files()
        return False
    bundled = _bundled_ytdlp_version()
    if not archive.is_file() or (bundled and _version_tuple(bundled) >= _version_tuple(version)):
        remove_ytdlp_update()
        return False
    try:
        sys.path.insert(0, str(archive))
        import yt_dlp
        if not str(yt_dlp.__file__).startswith(str(archive)):
            raise ImportError("yt-dlp was not loaded from the update")
        _cleanup_ytdlp_files(keep=(archive.name,))
        return True
    except Exception:
        if str(archive) in sys.path:
            sys.path.remove(str(archive))
        _purge_ytdlp_modules()
        remove_ytdlp_update()
        return False


YTDLP_UPDATE_ACTIVE = _activate_updated_ytdlp()
YTDLP_RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/%s"
YTDLP_LATEST_PAGE = "https://github.com/yt-dlp/yt-dlp/releases/latest"
YTDLP_LATEST_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
YTDLP_CHECK_INTERVAL = 12 * 3600


def latest_ytdlp_version():
    tag = None
    try:
        req = urllib.request.Request(YTDLP_LATEST_PAGE, method="HEAD", headers={"User-Agent": "video-downloader"})
        with urlopen(req, timeout=15) as resp:
            found = re.search(r"/releases/tag/([^/?#]+)$", resp.geturl())
        tag = found.group(1) if found else None
    except Exception:
        tag = None
    if not tag:
        tag = json.loads(http_get(YTDLP_LATEST_API, timeout=15)).get("tag_name")
    if not tag or not re.fullmatch(r"\d+(\.\d+)+", tag):
        raise RuntimeError("Could not read the latest yt-dlp version.")
    return tag


def ytdlp_is_newer(latest, current):
    return bool(latest) and _version_tuple(latest) > _version_tuple(current)


def _zip_version(path):
    with zipfile.ZipFile(path) as z:
        text = z.read("yt_dlp/version.py").decode("utf-8", "replace")
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return m.group(1) if m else None


def update_ytdlp(current_version):
    blob = http_get(YTDLP_RELEASE_URL % "yt-dlp", timeout=120)
    sums = http_get(YTDLP_RELEASE_URL % "SHA2-256SUMS").decode("utf-8", "replace")
    expected = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == "yt-dlp":
            expected = parts[0].lower()
    if not expected or hashlib.sha256(blob).hexdigest() != expected:
        raise RuntimeError("Checksum verification failed: update cancelled.")

    folder = data_dir()
    tmp = folder / "yt-dlp.download"
    tmp.write_bytes(blob)
    try:
        version = _zip_version(tmp)
    except Exception:
        version = None
    if not version:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("The downloaded file is not a valid yt-dlp release.")
    if _version_tuple(version) <= _version_tuple(current_version):
        tmp.unlink(missing_ok=True)
        return version, False

    target = folder / ("yt-dlp-%s.zip" % version)
    os.replace(tmp, target)
    write_json(YTDLP_META, {"version": version, "file": target.name})
    return version, True
