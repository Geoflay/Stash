"""Where ffmpeg is (computer only): the folder chosen in Settings, else the copy the app downloaded, else the
one in the PATH; and downloading a copy for the app. Used at startup (see core.backend): a change needs a
restart."""

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from core.storage import data_dir, load_settings


DOWNLOADED_DIR = "ffmpeg"  # the app's own copy, in its data folder
# Builds with every encoder the app uses (x264, VP9, Opus, MP3, libass…), for each computer system.
DOWNLOADS = {
    "win32": ["https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"],
    "linux": ["https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"],
    "darwin": ["https://evermeet.cx/ffmpeg/getrelease/zip", "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"],
}
CHUNK = 256 * 1024


def program(name):
    return name + (".exe" if os.name == "nt" else "")


def ffmpeg_in(folder):
    """The ffmpeg program in `folder` or its "bin" sub-folder (as the downloaded builds have it), or None."""
    if not folder:
        return None
    for place in (Path(folder), Path(folder, "bin")):
        if Path(place, program("ffmpeg")).is_file():
            return str(Path(place, program("ffmpeg")))
    return None


def downloaded_dir():
    return data_dir() / DOWNLOADED_DIR


def find_ffmpeg():
    """(the ffmpeg program or None, where it was found: "folder", "downloaded", "path" or "")."""
    chosen = ffmpeg_in(load_settings().get("ffmpeg_dir"))
    if chosen:
        return chosen, "folder"
    downloaded = ffmpeg_in(downloaded_dir())
    if downloaded:
        return downloaded, "downloaded"
    in_path = shutil.which("ffmpeg")
    return (in_path, "path") if in_path else (None, "")


def can_download():
    return sys.platform in DOWNLOADS


def _fetch(url, target, notify, done, total):
    """Download `url` into the file `target`; notify(fraction, message) after each piece (it raises
    Cancelled once Cancel is pressed)."""
    from core.web import urlopen
    req = urllib.request.Request(url, headers={"User-Agent": "stash"})
    with urlopen(req, timeout=60) as resp, open(target, "wb") as out:
        size = int(resp.headers.get("Content-Length") or 0) or None
        got = 0
        while True:
            piece = resp.read(CHUNK)
            if not piece:
                break
            out.write(piece)
            got += len(piece)
            fraction = (done + got / size) / total if size else None
            notify(fraction, "Downloading ffmpeg (%d MB)…" % (got // 2**20))


def _extract(archive, folder):
    """Copy ffmpeg and ffprobe out of a .zip or .tar.xz archive into `folder`; returns how many were found."""
    wanted = {program("ffmpeg"), program("ffprobe")}
    found = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                name = member.rsplit("/", 1)[-1]
                if name in wanted:
                    with z.open(member) as src, open(Path(folder, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found += 1
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                name = member.name.rsplit("/", 1)[-1]
                if member.isfile() and name in wanted:
                    with t.extractfile(member) as src, open(Path(folder, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found += 1
    return found


def download_ffmpeg(notify):
    """Download ffmpeg (and ffprobe) for this computer into the app's data folder; returns the ffmpeg
    program. The copy being made only replaces the old one once it is complete."""
    urls = DOWNLOADS.get(sys.platform)
    if not urls:
        raise RuntimeError("No ffmpeg download for this system: install it, then choose its folder.")
    target = downloaded_dir()
    work = Path(tempfile.mkdtemp(prefix="videodl_ffmpeg_", dir=data_dir()))
    try:
        fresh = work / "ffmpeg"
        fresh.mkdir()
        for index, url in enumerate(urls):
            archive = work / ("archive%d" % index)
            _fetch(url, archive, notify, index, len(urls))
            notify(None, "Unpacking ffmpeg…")
            if not _extract(archive, fresh):
                raise RuntimeError("ffmpeg was not found in the downloaded file.")
            archive.unlink()
        if not Path(fresh, program("ffmpeg")).is_file():
            raise RuntimeError("ffmpeg was not found in the downloaded file.")
        for path in fresh.iterdir():
            path.chmod(0o755)
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(fresh), str(target))
        return str(Path(target, program("ffmpeg")))
    finally:
        shutil.rmtree(work, ignore_errors=True)
