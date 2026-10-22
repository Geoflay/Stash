"""Photo posts (TikTok, Instagram, Twitter): a video made of the photos and the music, or the photos
themselves."""

import os
from pathlib import Path

from yt_dlp.networking import Request

from core.backend import BACKEND, CAN_SLIDESHOW
from core.download import find_file, make_hooks
from core.ffmpeg import run_ffmpeg
from core.jobs import entering_conversion, ignore_progress
from core.network import first_entry, run_ytdlp
from core.pyav import av_make_video, probe_duration


def _image_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _write_concat_list(workdir, names, per):
    """ffmpeg concat list showing each photo for `per` seconds (the last one is repeated, as concat requires)."""
    lines = []
    for name in names:
        lines += ["file '%s'" % name, "duration %.3f" % per]
    lines.append("file '%s'" % names[-1])
    Path(workdir, "list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# Sent with the photo requests: the sites refuse them without it.
REFERERS = {"Instagram": "https://www.instagram.com/", "TikTok": "https://www.tiktok.com/",
            "Twitter": "https://x.com/"}


def download_photos(images, platform, workdir, cookies_browser="", on_progress=None, cookie_file=None):
    """Every photo of the post as its own file ("01.jpg", "02.png"…), in a new folder that is returned."""
    notify = on_progress or ignore_progress
    folder = Path(workdir, "photos")
    folder.mkdir(exist_ok=True)
    headers = {"Referer": REFERERS[platform]} if platform in REFERERS else {}

    def extract(ydl):
        for i, im in enumerate(images, 1):
            notify((i - 1) / len(images), "Downloading photos %d/%d…" % (i, len(images)))
            data = ydl.urlopen(Request(im["url"], headers=headers)).read()
            Path(folder, "%02d%s" % (i, _image_ext(data))).write_bytes(data)

    if not images:
        raise RuntimeError("No photo to download.")
    run_ytdlp(extract, {}, cookies_browser, cookie_file)
    return folder


def download_slideshow(url, mode, images, selected, seconds, workdir, cookies_browser="",
                       on_progress=None, cookie_file=None, post=None):
    if not CAN_SLIDESHOW:
        raise RuntimeError("Creating a video from photos requires ffmpeg, or PyAV + Pillow "
                           "with a video encoder (none available here).")
    notify = on_progress or ignore_progress
    chosen = [images[selected]] if mode == "cover" else list(images)
    if not chosen:
        raise RuntimeError("No photo to use.")
    hook, _ = make_hooks(notify)
    found = {}

    referer = REFERERS.get(post.get("platform"), REFERERS["Instagram"]) if post is not None else REFERERS["TikTok"]

    def extract(ydl):
        if post is None:
            notify(None, "Fetching post info…")
            info = ydl.extract_info(url, download=False, process=False)
            notify(None, "Downloading audio…")
            info = first_entry(ydl.process_ie_result(info, download=True))
            found["duration"] = info.get("duration")
        else:
            found["duration"] = None
            if post.get("audio_url"):
                notify(None, "Downloading audio…")
                resp = ydl.urlopen(Request(post["audio_url"], headers={"Referer": referer}))
                Path(workdir, "audio.m4a").write_bytes(resp.read())
        names = []
        for i, im in enumerate(chosen, 1):
            notify(None if len(chosen) == 1 else (i - 1) / len(chosen),
                   "Downloading photos %d/%d…" % (i, len(chosen)))
            resp = ydl.urlopen(Request(im["url"], headers={"Referer": referer}))
            data = resp.read()
            name = "img_%03d%s" % (i, _image_ext(data))
            Path(workdir, name).write_bytes(data)
            names.append(name)
        found["names"] = names

    run_ytdlp(extract, {
        "outtmpl": os.path.join(workdir, "audio.%(ext)s"),
        "format": "ba/b",
        "playlist_items": "1",
        "noprogress": True,
        "progress_hooks": [hook],
    }, cookies_browser, cookie_file)

    audio = find_file(workdir, "audio")
    if audio is None and post is None:
        raise RuntimeError("The post's audio could not be downloaded.")
    names = found["names"]

    w, h = chosen[0].get("width") or 0, chosen[0].get("height") or 0
    fw, fh = (1920, 1080) if w > h else (1080, 1920)
    vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p" % (fw, fh, fw, fh))
    entering_conversion()
    notify(None, "Creating video…")

    if BACKEND == "av":
        photos = [str(Path(workdir, n)) for n in names]
        audio_dur = (probe_duration(audio) if audio else None) or found.get("duration")
        per = float(seconds) if seconds else ((audio_dur / len(photos)) if audio_dur else 3.0)
        if audio is None and mode == "cover":
            per = max(per, 5.0)
        dest = Path(workdir, "photo.mp4" if mode == "cover" else "slideshow.mp4")
        return av_make_video(photos, mode, audio, max(per, 0.5), audio_dur, fw, fh, dest, notify)

    if audio is None:
        silent = max(float(seconds), 0.5) if seconds else 3.0
        if mode == "cover":
            run_ffmpeg(["-loop", "1", "-framerate", "25", "-i", names[0], "-t", "%.3f" % max(silent, 5.0),
                         "-vf", vf, "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
                         "-movflags", "+faststart", "photo.mp4"], workdir)
            return Path(workdir, "photo.mp4")
        _write_concat_list(workdir, names, silent)
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-vf", vf + ",fps=25",
                     "-c:v", "libx264", "-preset", "veryfast", "-t", "%.3f" % (silent * len(names)),
                     "-movflags", "+faststart", "slideshow.mp4"], workdir)
        return Path(workdir, "slideshow.mp4")

    if mode == "cover":
        run_ffmpeg(["-loop", "1", "-framerate", "25", "-i", names[0], "-i", audio.name,
                     "-vf", vf, "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
                     "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                     "photo.mp4"], workdir)
        return Path(workdir, "photo.mp4")

    total_audio = found.get("duration")
    per = float(seconds) if seconds else ((total_audio / len(names)) if total_audio else 3.0)
    per = max(per, 0.5)
    _write_concat_list(workdir, names, per)
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt",
                 "-stream_loop", "-1", "-i", audio.name,
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-vf", vf + ",fps=25", "-c:v", "libx264", "-preset", "veryfast",
                 "-c:a", "aac", "-b:a", "192k", "-t", "%.3f" % (per * len(names)),
                 "-movflags", "+faststart", "slideshow.mp4"], workdir)
    return Path(workdir, "slideshow.mp4")
