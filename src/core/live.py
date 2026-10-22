"""Lives (YouTube, Twitch…): waiting for one to start, then recording it from now until it ends or the user stops
it. ffmpeg reads the live's own streams (yt-dlp finds them) into a .ts file, which survives a stop at any moment,
then the recording is put into an MP4."""

import re
import subprocess
import time
from pathlib import Path

from core.backend import FFMPEG_BIN
from core.errors import Cancelled
from core.ffmpeg import run_ffmpeg
from core.network import run_ytdlp
from core.system import NO_WINDOW
from core.units import format_bytes, format_duration

# What sites say of a live that has not started, or of a channel that is not live now.
NOT_STARTED = re.compile(r"will begin|begins in|premieres? in|not (currently )?live|is offline|has not started|"
                         r"scheduled|upcoming|live event will|waiting for|no live", re.I)
LIVE_CHECK_EVERY = 60  # seconds between two looks at a live that has not started


def not_started(message):
    return bool(NOT_STARTED.search(message or ""))


def live_streams(url, quality="best", cookies_browser="", cookie_file=None):
    """The live now: {"title", "uploader", "thumbnail", "inputs": [(stream url, headers)]} (the picture and the
    sound, or both in one stream), the picture at `quality` (a height) at most. RuntimeError when it is not live
    now (see not_started)."""
    height = "" if quality in (None, "", "best") else "[height<=%s]" % quality
    opts = {"format": "bv*%s+ba/b%s/bv*+ba/b" % (height, height), "noplaylist": True}
    info = run_ytdlp(lambda ydl: ydl.extract_info(url, download=False), opts, cookies_browser, cookie_file)
    if not (info.get("live_status") == "is_live" or info.get("is_live")):
        raise RuntimeError("This video is not live now (%s)." % (info.get("live_status") or "not live"))
    formats = info.get("requested_formats") or [info]
    return {"title": info.get("title") or "Live", "uploader": info.get("uploader") or info.get("channel") or "",
            "thumbnail": info.get("thumbnail") or "",
            "inputs": [(f["url"], f.get("http_headers") or {}) for f in formats if f.get("url")]}


def record_live(inputs, workdir, notify, stop):
    """Record the live's streams until it ends, `stop` is set (the recording is kept) or `notify` raises Cancelled
    (it is not); returns the MP4 made."""
    workdir = Path(workdir)
    ts = workdir / "live.ts"
    args = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y"]
    for url, headers in inputs:
        blob = "".join("%s: %s\r\n" % item for item in headers.items())
        args += (["-headers", blob] if blob else []) + ["-i", url]
    for index in range(len(inputs)):
        args += ["-map", "%d:v?" % index, "-map", "%d:a?" % index]
    args += ["-c", "copy", "-f", "mpegts", str(ts)]
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               cwd=str(workdir), **NO_WINDOW)
    started = time.monotonic()
    try:
        while process.poll() is None:
            if stop.is_set():
                process.communicate(b"q", timeout=30)  # ffmpeg ends the file properly
                break
            size = ts.stat().st_size if ts.exists() else 0
            notify(None, "Recording the live · %s" % (format_duration(time.monotonic() - started) or "0:00"),
                   "%s recorded · Stop keeps what is recorded" % format_bytes(size))
            time.sleep(1)
    except Cancelled:
        process.kill()
        raise
    finally:
        if process.poll() is None:
            process.kill()
    if not ts.exists() or ts.stat().st_size == 0:
        error = (process.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines() if process.stderr else []
        raise RuntimeError("Nothing was recorded%s" % (": %s" % error[-1] if error else "."))
    notify(None, "Saving the recording…")
    out = workdir / "live.mp4"
    try:
        run_ffmpeg(["-i", str(ts), "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart",
                    str(out)], workdir)
    except RuntimeError:  # a sound MP4 cannot hold: an MKV holds anything
        out = workdir / "live.mkv"
        run_ffmpeg(["-i", str(ts), "-map", "0", "-c", "copy", str(out)], workdir)
    return out


def wait_for_live(url, quality, notify, cookies_browser="", cookie_file=None):
    """The live's streams (see live_streams), once it has started: a live not started yet, or a channel not live
    now, is looked at again every minute (`notify` raises Cancelled to stop waiting)."""
    while True:
        try:
            return live_streams(url, quality, cookies_browser, cookie_file)
        except Exception as exc:
            if not not_started(str(exc)):
                raise
        looked = time.strftime("%H:%M")
        for _ in range(LIVE_CHECK_EVERY):
            notify(None, "Waiting for the live to start (looked at %s)…" % looked)
            time.sleep(1)
