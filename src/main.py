import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import ssl
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse


def data_dir():
    base = os.environ.get("FLET_APP_STORAGE_DATA")
    if base:
        path = Path(base)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA") or Path.home()) / "VideoDownloader"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "video-downloader"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


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

try:
    import av
    av.logging.set_level(av.logging.ERROR)
except Exception:
    av = None

import flet as ft
from yt_dlp import YoutubeDL
from yt_dlp.cookies import YoutubeDLCookieJar
from yt_dlp.networking import Request
from yt_dlp.utils import DownloadCancelled, DownloadError
from yt_dlp.version import __version__ as YTDLP_VERSION


class Cancelled(DownloadCancelled):
    pass


ALLOWED_DOMAINS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "twitter.com", "x.com")
TWITTER_DOMAINS = ("twitter.com", "x.com")
QUALITIES = {"best", "1080", "720", "480"}
BROWSERS = ["firefox", "chrome", "edge", "brave"]

HAS_PIL = importlib.util.find_spec("PIL") is not None
ENV_COOKIES_BROWSER = os.environ.get("YTDLP_COOKIES_BROWSER", "").strip()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _encoder_available(name):
    if av is None:
        return False
    try:
        av.Codec(name, "w")
        return True
    except Exception:
        return False


def _pick_video_encoder():
    for name in ("libx264", "libopenh264", "mpeg4"):
        if _encoder_available(name):
            return name
    return None


FFMPEG_BIN = shutil.which("ffmpeg")
BACKEND = "ffmpeg" if FFMPEG_BIN else ("av" if av is not None else None)
VIDEO_ENCODER = _pick_video_encoder() if BACKEND == "av" else None

CAN_MP3 = BACKEND == "ffmpeg" or (BACKEND == "av" and _encoder_available("libmp3lame"))
CAN_SLIDESHOW = BACKEND == "ffmpeg" or (
    BACKEND == "av" and HAS_PIL and VIDEO_ENCODER is not None and _encoder_available("aac")
)


def backend_label():
    if BACKEND == "ffmpeg":
        return "ffmpeg (PATH)"
    if BACKEND == "av":
        return "PyAV (MP3: %s, video: %s)" % ("yes" if CAN_MP3 else "no", VIDEO_ENCODER or "no")
    return "none"


def is_allowed_url(url):
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def is_twitter_url(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in TWITTER_DOMAINS)


def video_selector(quality):
    h = "" if quality == "best" else "[height<=%s]" % quality
    if BACKEND == "ffmpeg":
        return "bv*%s[ext=mp4]+ba[ext=m4a]/bv*%s+ba/b%s/b" % (h, h, h)
    return "b%s[ext=mp4]/b%s/b" % (h, h)


def clean_error(exc):
    text = _ANSI.sub("", str(exc)).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    text = re.sub(r";?\s*please report this issue.*", "", text, flags=re.I)
    return text.strip()[:300] or "Unknown yt-dlp error"


def _run(extract, extra_opts, cookies_browser="", cookie_file=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 30,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    opts.update(extra_opts)
    with YoutubeDL(opts) as ydl:
        return extract(ydl)


_TT_POST = re.compile(
    r"tiktok(?:v)?\.com/(?:@(?P<user>[\w.\-]*)/)?(?:share/)?(?:video|photo|v)/(?P<id>\d{8,})"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _next_location(url):
    handlers = [_NoRedirect()]
    ctx = _ssl_context()
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": "facebookexternalhit/1.1"})
        try:
            with opener.open(req, timeout=15):
                return None
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location")
            exc.close()
            if exc.code in (301, 302, 303, 307, 308) and location:
                return urljoin(url, location)
        except Exception:
            pass
    return None


def resolve_tiktok(url):
    current = url
    for _ in range(6):
        m = _TT_POST.search(unquote(current))
        if m:
            return "https://www.tiktok.com/@%s/video/%s" % (m.group("user") or "_", m.group("id"))
        current = _next_location(current)
        if not current:
            break
    return url


def normalize_url(url):
    host = (urlparse(url).hostname or "").lower()
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return resolve_tiktok(url)
    return url


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


def fetch_info(url, cookies_browser="", cookie_file=None):
    photo_hint = "/photo/" in unquote(url)
    url = normalize_url(url)
    extra = {}

    def extract(ydl):
        data = ydl.extract_info(url, download=False)
        if data.get("_type") == "playlist":
            entries = list(data.get("entries") or [])
            data = entries[0] if entries else {}
        extra.clear()
        if data.get("extractor_key") == "TikTok" and (photo_hint or _is_audio_only(data)):
            extra["slideshow"] = True
            try:
                extra["images"] = tiktok_images(ydl, url)
            except Exception as exc:
                extra["images_error"] = clean_error(exc)
        return data

    data = _run(extract, {"skip_download": True, "playlist_items": "1"}, cookies_browser, cookie_file)
    return {
        "url": url,
        "title": data.get("title") or "video",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "platform": data.get("extractor_key") or "",
        "slideshow": bool(extra.get("slideshow")),
        "images": extra.get("images") or [],
        "images_error": extra.get("images_error") or "",
        "formats": compact_formats(data),
    }


def _make_hooks(notify):
    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            notify(min(done / total, 1.0) if total else None, "Downloading…")
        elif d.get("status") == "finished":
            notify(None, "Finalizing…")

    def pp_hook(d):
        if d.get("status") == "started":
            notify(None, "Converting…")

    return hook, pp_hook


def download_media(url, fmt, quality, workdir, cookies_browser="", on_progress=None,
                   cookie_file=None):
    notify = on_progress or (lambda fraction, message: None)
    hook, pp_hook = _make_hooks(notify)

    base = {
        "playlist_items": "1",
        "noprogress": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }

    def run(opts):
        return _run(lambda ydl: ydl.download([url]), opts, cookies_browser, cookie_file)

    if fmt in ("mp3", "audio"):
        opts = dict(base, outtmpl=os.path.join(workdir, "media.%(ext)s"), format="bestaudio/best")
        if fmt == "mp3" and BACKEND == "ffmpeg":
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
            ]
        run(opts)
        src = _find_file(workdir, "media")
        if src is None:
            raise RuntimeError("No file was produced.")
        if fmt == "mp3" and BACKEND == "av":
            notify(None, "Converting…")
            dst = Path(workdir, "audio.mp3")
            av_to_mp3(src, dst, notify)
            return dst
        return src

    if BACKEND == "av":
        return _download_video_av(run, base, quality, workdir, notify, is_twitter_url(url))

    opts = dict(base, outtmpl=os.path.join(workdir, "media.%(ext)s"), format=video_selector(quality))
    if BACKEND == "ffmpeg":
        opts["merge_output_format"] = "mp4"
    run(opts)

    files = [p for p in Path(workdir).iterdir()
             if p.is_file() and p.suffix not in (".part", ".ytdl")]
    if not files:
        raise RuntimeError("No file was produced.")
    return max(files, key=lambda p: p.stat().st_size)


def _find_file(workdir, stem):
    for p in sorted(Path(workdir).glob(stem + ".*")):
        if p.is_file() and p.suffix not in (".part", ".ytdl"):
            return p
    return None


def _download_video_av(run, base, quality, workdir, notify, complete_first=False):
    h = "" if quality == "best" else "[height<=%s]" % quality
    selector = "bv*%s[ext=mp4]/bv*%s/b%s[ext=mp4]/b%s/b" % (h, h, h, h)
    if complete_first:
        selector = "b%s[ext=mp4]/" % h + selector
    run(dict(base, outtmpl=os.path.join(workdir, "video.%(ext)s"), format=selector))
    video = _find_file(workdir, "video")
    if video is None:
        raise RuntimeError("No file was produced.")
    if _has_audio_stream(video):
        return video

    notify(None, "Downloading audio…")
    run(dict(base, outtmpl=os.path.join(workdir, "audio.%(ext)s"), format="ba[ext=m4a]/ba/b"))
    audio = _find_file(workdir, "audio")
    if audio is None:
        raise RuntimeError("The audio track could not be downloaded.")

    notify(None, "Merging video + audio…")
    out = Path(workdir, "media.mp4")
    try:
        av_merge(video, audio, out, notify)
    except Exception:
        out.unlink(missing_ok=True)
        out = Path(workdir, "media.mkv")
        av_merge(video, audio, out, notify)
    return out


AUDIO_RATE = 44100


def _probe_duration(path):
    try:
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1_000_000
            streams = list(c.streams.audio) or list(c.streams.video)
            if streams and streams[0].duration and streams[0].time_base:
                return float(streams[0].duration * streams[0].time_base)
    except Exception:
        pass
    return None


def _has_audio_stream(path):
    with av.open(str(path)) as c:
        return len(c.streams.audio) > 0


def _add_copy_stream(out, template):
    if hasattr(out, "add_stream_from_template"):
        return out.add_stream_from_template(template)
    return out.add_stream(template=template)


def _timed_packets(container, stream):
    for p in container.demux(stream):
        if p.dts is not None:
            yield p


def av_merge(video_path, audio_path, out_path, notify):
    out_path = Path(out_path)
    options = {"movflags": "+faststart"} if out_path.suffix == ".mp4" else None
    with av.open(str(video_path)) as vin, av.open(str(audio_path)) as ain, \
            av.open(str(out_path), "w", options=options) as out:
        vs, as_ = vin.streams.video[0], ain.streams.audio[0]
        vo, ao = _add_copy_stream(out, vs), _add_copy_stream(out, as_)
        total = (vin.duration or 0) / 1_000_000
        vi, ai = _timed_packets(vin, vs), _timed_packets(ain, as_)
        nv, na = next(vi, None), next(ai, None)
        while nv is not None or na is not None:
            take_video = na is None or (nv is not None and nv.dts * nv.time_base <= na.dts * na.time_base)
            if take_video:
                pkt, target = nv, vo
                nv = next(vi, None)
                if total:
                    notify(min(float(pkt.dts * pkt.time_base) / total, 1.0), "Merging video + audio…")
            else:
                pkt, target = na, ao
                na = next(ai, None)
            pkt.stream = target
            out.mux(pkt)


def _audio_frames(path, target_samples=None):
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=AUDIO_RATE)
    done = 0
    while True:
        produced = False
        with av.open(str(path)) as c:
            if not c.streams.audio:
                raise RuntimeError("No audio track in the file.")
            for frame in c.decode(c.streams.audio[0]):
                for rf in resampler.resample(frame):
                    rf.pts = done
                    rf.time_base = Fraction(1, AUDIO_RATE)
                    done += rf.samples
                    produced = True
                    yield rf
                    if target_samples is not None and done >= target_samples:
                        return
        if not produced:
            raise RuntimeError("The audio track is empty.")
        if target_samples is None:
            return


def _new_audio_stream(out, codec, bit_rate):
    stream = out.add_stream(codec, rate=AUDIO_RATE)
    try:
        stream.layout = "stereo"
    except Exception:
        pass
    stream.bit_rate = bit_rate
    return stream


def av_to_mp3(src, dst, notify):
    total = _probe_duration(src)
    with av.open(str(dst), "w", format="mp3") as out:
        stream = _new_audio_stream(out, "libmp3lame", 256_000)
        for frame in _audio_frames(src):
            out.mux(stream.encode(frame))
            if total:
                notify(min(frame.pts / AUDIO_RATE / total, 1.0), "Converting to MP3…")
        out.mux(stream.encode(None))


def _frame_from_photo(path, fw, fh):
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.contain(im, (fw, fh), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (fw, fh), (0, 0, 0))
        canvas.paste(im, ((fw - im.width) // 2, (fh - im.height) // 2))
    return av.VideoFrame.from_image(canvas).reformat(format="yuv420p")


def av_make_video(photos, mode, audio, per, audio_dur, fw, fh, out_path, notify):
    fps = 10
    audio_dur = _probe_duration(audio) or audio_dur
    total = audio_dur if mode == "cover" else per * len(photos)
    if not total or total <= 0:
        raise RuntimeError("Could not read the audio duration.")
    total_frames = max(1, round(total * fps))
    frames = [_frame_from_photo(p, fw, fh) for p in photos]
    time_base = Fraction(1, fps)

    options = {"preset": "veryfast", "tune": "stillimage"} if VIDEO_ENCODER == "libx264" else None
    with av.open(str(out_path), "w", format="mp4", options={"movflags": "+faststart"}) as out:
        vs = out.add_stream(VIDEO_ENCODER, rate=fps, options=options)
        vs.width, vs.height, vs.pix_fmt = fw, fh, "yuv420p"
        vs.bit_rate = 2_500_000
        vs.gop_size = fps
        if VIDEO_ENCODER == "mpeg4":
            try:
                vs.max_b_frames = 0
            except Exception:
                pass
        aus = _new_audio_stream(out, "aac", 192_000)

        audio_iter = _audio_frames(audio, int(total * AUDIO_RATE))
        af = next(audio_iter, None)
        n = 0
        while n < total_frames or af is not None:
            v_time = n / fps if n < total_frames else None
            if af is None or (v_time is not None and v_time <= af.pts / AUDIO_RATE):
                idx = 0 if mode == "cover" else min(int(v_time // per), len(frames) - 1)
                frame = frames[idx]
                frame.pts, frame.time_base = n, time_base
                out.mux(vs.encode(frame))
                n += 1
                if n % fps == 0:
                    notify(n / total_frames, "Creating video…")
            else:
                out.mux(aus.encode(af))
                af = next(audio_iter, None)
        out.mux(vs.encode(None))
        out.mux(aus.encode(None))
    return Path(out_path)


def _image_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _run_ffmpeg(args, workdir):
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    r = subprocess.run([FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error"] + args,
                       cwd=workdir, capture_output=True, text=True, timeout=900, **kwargs)
    if r.returncode != 0:
        lines = [l for l in (r.stderr or "").splitlines() if l.strip()]
        raise RuntimeError("Could not create the video: " + (lines[-1] if lines else "ffmpeg error"))


def download_slideshow(url, mode, images, selected, seconds, workdir, cookies_browser="",
                       on_progress=None, cookie_file=None):
    if not CAN_SLIDESHOW:
        raise RuntimeError("Creating a video from photos requires ffmpeg, or PyAV + Pillow "
                           "with a video encoder (none available here).")
    notify = on_progress or (lambda fraction, message: None)
    chosen = [images[selected]] if mode == "cover" else list(images)
    if not chosen:
        raise RuntimeError("No photo to use.")
    hook, _ = _make_hooks(notify)
    found = {}

    def extract(ydl):
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist":
            info = (list(info.get("entries") or [{}]))[0]
        found["duration"] = info.get("duration")
        names = []
        for i, im in enumerate(chosen, 1):
            notify(None if len(chosen) == 1 else (i - 1) / len(chosen),
                   "Downloading photos %d/%d…" % (i, len(chosen)))
            resp = ydl.urlopen(Request(im["url"], headers={"Referer": "https://www.tiktok.com/"}))
            data = resp.read()
            name = "img_%03d%s" % (i, _image_ext(data))
            Path(workdir, name).write_bytes(data)
            names.append(name)
        found["names"] = names

    _run(extract, {
        "outtmpl": os.path.join(workdir, "audio.%(ext)s"),
        "format": "ba/b",
        "playlist_items": "1",
        "noprogress": True,
        "progress_hooks": [hook],
    }, cookies_browser, cookie_file)

    audio = next((p for p in Path(workdir).glob("audio.*") if p.suffix not in (".part", ".ytdl")), None)
    if audio is None:
        raise RuntimeError("The post's audio could not be downloaded.")
    names = found["names"]

    w, h = chosen[0].get("width") or 0, chosen[0].get("height") or 0
    fw, fh = (1920, 1080) if w > h else (1080, 1920)
    vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
          "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p" % (fw, fh, fw, fh))
    notify(None, "Creating video…")

    if BACKEND == "av":
        photos = [str(Path(workdir, n)) for n in names]
        audio_dur = _probe_duration(audio) or found.get("duration")
        per = float(seconds) if seconds else ((audio_dur / len(photos)) if audio_dur else 3.0)
        dest = Path(workdir, "photo.mp4" if mode == "cover" else "slideshow.mp4")
        return av_make_video(photos, mode, audio, max(per, 0.5), audio_dur, fw, fh, dest, notify)

    if mode == "cover":
        _run_ffmpeg(["-loop", "1", "-framerate", "25", "-i", names[0], "-i", audio.name,
                     "-vf", vf, "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
                     "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                     "photo.mp4"], workdir)
        return Path(workdir, "photo.mp4")

    total_audio = found.get("duration")
    per = float(seconds) if seconds else ((total_audio / len(names)) if total_audio else 3.0)
    per = max(per, 0.5)
    lines = []
    for name in names:
        lines += ["file '%s'" % name, "duration %.3f" % per]
    lines.append("file '%s'" % names[-1])
    Path(workdir, "list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt",
                 "-stream_loop", "-1", "-i", audio.name,
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-vf", vf + ",fps=25", "-c:v", "libx264", "-preset", "veryfast",
                 "-c:a", "aac", "-b:a", "192k", "-t", "%.3f" % (per * len(names)),
                 "-movflags", "+faststart", "slideshow.mp4"], workdir)
    return Path(workdir, "slideshow.mp4")


def safe_filename(title, ext):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title or "").strip(" .")[:120]
    return (base or "video") + ext.lower()


def workdir_parent():
    parent = os.environ.get("FLET_APP_STORAGE_TEMP")
    if parent:
        os.makedirs(parent, exist_ok=True)
        return parent
    return None


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


def remove_cookies():
    path = data_dir() / "cookies.txt"
    path.unlink(missing_ok=True)


def _has_video(f):
    return f.get("vcodec") not in (None, "none")


def _has_audio(f):
    return f.get("acodec") not in (None, "none")


def compact_formats(data):
    return [
        {
            "height": f.get("height"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "size": f.get("filesize") or f.get("filesize_approx"),
            "tbr": f.get("tbr"),
            "abr": f.get("abr"),
        }
        for f in (data.get("formats") or [])
    ]


def format_size(n):
    size = float(n) / 1024
    for unit in ("KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit)
        size /= 1024


def _stream_size(f, duration):
    if f.get("size"):
        return f["size"]
    rate = f.get("tbr") or f.get("abr")
    if rate and duration:
        return int(rate * 125 * duration)
    return None


def estimate_choice(formats, duration, fmt, quality, can_merge):
    if not formats:
        return ""
    audios = [f for f in formats if _has_audio(f) and not _has_video(f)]
    if fmt in ("mp3", "audio"):
        if fmt == "mp3":
            return "MP3 · ~%s" % format_size(duration * 32000) if duration else "MP3"
        pool = audios or [f for f in formats if _has_audio(f)]
        if not pool:
            return ""
        best = max(pool, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        size = _stream_size(best, duration)
        return "Audio" + (" · ~" + format_size(size) if size else "")
    videos = [f for f in formats if _has_video(f)]
    if not can_merge:
        videos = [f for f in videos if _has_audio(f)]
    limit = None if quality == "best" else int(quality)
    pool = [f for f in videos if limit is None or (f.get("height") or 0) <= limit] or videos
    if not pool:
        return ""
    top = max(pool, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    size = _stream_size(top, duration)
    if size and not _has_audio(top) and audios:
        extra = _stream_size(max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0), duration)
        size = size + extra if extra else None
    parts = ["%dp" % top["height"] if top.get("height") else "Video"]
    if size:
        parts.append("~" + format_size(size))
    return " · ".join(parts)


HISTORY_LIMIT = 50


def _read_json(name, default):
    try:
        return json.loads((data_dir() / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(name, value):
    folder = data_dir()
    tmp = folder / (name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / name)


def load_settings():
    data = _read_json("settings.json", {})
    return data if isinstance(data, dict) else {}


def save_settings(settings):
    try:
        _write_json("settings.json", settings)
    except OSError:
        pass


def load_history():
    data = _read_json("history.json", [])
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def add_history(entry):
    entry = dict(entry, time=datetime.now().strftime("%Y-%m-%d %H:%M"))
    items = [e for e in load_history()
             if not (e.get("url") == entry.get("url") and e.get("format") == entry.get("format"))]
    items.insert(0, entry)
    try:
        _write_json("history.json", items[:HISTORY_LIMIT])
    except OSError:
        pass


def clear_history():
    try:
        _write_json("history.json", [])
    except OSError:
        pass


YTDLP_RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/%s"


def _http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "video-downloader"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read()


def _zip_version(path):
    with zipfile.ZipFile(path) as z:
        text = z.read("yt_dlp/version.py").decode("utf-8", "replace")
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return m.group(1) if m else None


def update_ytdlp(current_version):
    blob = _http_get(YTDLP_RELEASE_URL % "yt-dlp", timeout=120)
    sums = _http_get(YTDLP_RELEASE_URL % "SHA2-256SUMS").decode("utf-8", "replace")
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
    _write_json(YTDLP_META, {"version": version, "file": target.name})
    return version, True


def make_hint(message, url, desktop):
    low = (message or "").lower()
    host = (urlparse(url).hostname or "").lower()
    is_tiktok = host == "tiktok.com" or host.endswith(".tiktok.com")

    if "login" in low or "cookies" in low or "rate-limit" in low or "empty media response" in low:
        text = "This platform requires a login. Import your cookies.txt file (Cookies card)"
        if desktop:
            text += " or pick the browser you are logged in to"
        return text + ", then try again."
    if is_tiktok:
        return ("TikTok regularly blocks downloaders. Try importing your cookies.txt file "
                "(Cookies card), or update yt-dlp (yt-dlp card) and restart the app.")
    if "unable to extract" in low or "unexpected response" in low:
        return ("yt-dlp can no longer read this page, which happens when the platform changes. "
                "Update yt-dlp (yt-dlp card), then restart the app.")
    return None


def hint_box(hint):
    return ft.Container(
        content=ft.Text(hint, size=13, selectable=True),
        padding=12,
        border_radius=10,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
    )


def set_status(holder, kind, text, hint=None):
    color = {"err": ft.Colors.ERROR, "ok": ft.Colors.GREEN_600}.get(kind)
    body = [ft.Text(text, color=color, selectable=True)]
    if hint:
        body.append(hint_box(hint))
    row = []
    if kind == "loading":
        row.append(ft.Container(ft.ProgressRing(width=16, height=16, stroke_width=2),
                                margin=ft.Margin(left=0, right=0, top=2, bottom=0)))
    row.append(ft.Column(body, spacing=8, tight=True, expand=True))
    holder.content = ft.Row(row, spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)
    holder.visible = True


def hide_status(holder):
    holder.visible = False
    holder.content = None


def format_duration(sec):
    if not sec:
        return ""
    sec = round(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


async def main(page: ft.Page):
    needs_bytes = bool(page.web) or page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
    desktop = not needs_bytes

    page.title = "Video downloader"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = ft.Theme(color_scheme_seed="#4f46e5")
    page.dark_theme = ft.Theme(color_scheme_seed="#7c75ff")
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    if desktop:
        page.window.width = 620
        page.window.height = 840

    picker = ft.FilePicker()
    page.services.append(picker)

    settings = load_settings()
    cancel_event = threading.Event()
    yt_state = {"pending": None}
    state = {"url": None, "title": None, "platform": "", "slideshow": False, "images": [],
             "selected": 0, "formats": [], "duration": None}

    def remember(key, value):
        settings[key] = value
        save_settings(settings)

    notes = []
    if BACKEND is None:
        notes.append("Neither ffmpeg nor PyAV is available: no MP3, and quality is limited to "
                     "files that are already complete (often 720p max).")
    elif not CAN_MP3:
        notes.append("This ffmpeg build has no MP3 encoder: audio-only is offered without conversion.")
    if BACKEND == "av" and not CAN_SLIDESHOW:
        notes.append("Photo slideshows are unavailable (Pillow and a video encoder are required).")
    banner_kind = "warn" if notes else "ok"
    ytdlp_label = YTDLP_VERSION + (" (updated)" if YTDLP_UPDATE_ACTIVE else "")
    banner_text = "Ready · yt-dlp %s · media: %s" % (ytdlp_label, backend_label())
    if notes:
        banner_text += "\n" + "\n".join(notes)
    banner_colors = {
        "ok": (ft.Colors.GREEN_100, ft.Colors.GREEN_900),
        "warn": (ft.Colors.ORANGE_100, ft.Colors.ORANGE_900),
    }[banner_kind]
    banner = ft.Container(
        content=ft.Text(banner_text, color=banner_colors[1], size=13),
        bgcolor=banner_colors[0],
        padding=ft.Padding(left=14, right=14, top=10, bottom=10),
        border_radius=10,
    )

    url_input = ft.TextField(
        hint_text="https://www.youtube.com/watch?v=…",
        keyboard_type=ft.KeyboardType.URL,
        autocorrect=False,
        enable_suggestions=False,
        expand=True,
    )
    paste_btn = ft.IconButton(icon=ft.Icons.CONTENT_PASTE, tooltip="Paste link from clipboard")
    analyze_btn = ft.Button("Analyze", icon=ft.Icons.SEARCH)
    analyze_status = ft.Container(visible=False)

    thumb = ft.Image(src="", fit=ft.BoxFit.COVER, expand=True)
    thumb_box = ft.Container(
        content=thumb,
        aspect_ratio=16 / 9,
        border_radius=10,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
    )
    video_title = ft.Text("", size=17, weight=ft.FontWeight.W_600, selectable=True)
    video_meta = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT)
    estimate_text = ft.Text("", size=13, weight=ft.FontWeight.W_500)

    format_dd = ft.Dropdown(label="Format", value="mp4", options=[], expand=True)

    def set_formats(slideshow, has_images=False):
        if slideshow:
            choices = [
                ("cover", "Chosen photo + audio (MP4)", CAN_SLIDESHOW and has_images),
                ("slideshow", "Slideshow: all photos + audio (MP4)", CAN_SLIDESHOW and has_images),
                ("mp3", "Audio only (MP3)", CAN_MP3),
            ]
            if not CAN_MP3:
                choices.append(("audio", "Audio only (no conversion)", True))
        else:
            choices = [("mp4", "Video (MP4)", True), ("mp3", "Audio only (MP3)", CAN_MP3)]
            if not CAN_MP3:
                choices.append(("audio", "Audio only (no conversion)", True))
        format_dd.options = [ft.DropdownOption(key=k, text=t, disabled=not ok) for k, t, ok in choices]
        enabled = [k for k, _, ok in choices if ok]
        remembered = settings.get("slide_format" if slideshow else "format")
        format_dd.value = remembered if remembered in enabled else (enabled[0] if enabled else None)

    set_formats(False)

    duration_dd = ft.Dropdown(
        label="Seconds per photo",
        value=settings.get("photo_seconds") if settings.get("photo_seconds") in ("auto", "2", "3", "5") else "auto",
        options=[
            ft.DropdownOption(key="auto", text="Match audio length"),
            ft.DropdownOption(key="2", text="2 seconds"),
            ft.DropdownOption(key="3", text="3 seconds"),
            ft.DropdownOption(key="5", text="5 seconds"),
        ],
        expand=True,
        visible=False,
    )
    slideshow_note = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
    photos_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
    photos_box = ft.Column(
        [ft.Text("Photo to show (tap to choose)", size=13,
                 color=ft.Colors.ON_SURFACE_VARIANT), photos_row],
        spacing=6, visible=False,
    )
    photo_boxes = []

    def select_photo(i):
        state["selected"] = i
        for j, box in enumerate(photo_boxes):
            box.border = ft.Border.all(3, ft.Colors.PRIMARY if j == i else ft.Colors.TRANSPARENT)

    def build_photos(images):
        photo_boxes.clear()
        for i, im in enumerate(images):
            photo_boxes.append(ft.Container(
                content=ft.Image(src=im["url"], fit=ft.BoxFit.COVER, width=72, height=96,
                                 error_content=ft.Icon(ft.Icons.IMAGE)),
                width=72, height=96, border_radius=8, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                on_click=lambda e, i=i: (select_photo(i), page.update()),
            ))
        photos_row.controls = list(photo_boxes)
        select_photo(0)

    def refresh_options():
        fmt = format_dd.value
        slide = state["slideshow"]
        quality_dd.visible = (not slide) and fmt == "mp4"
        duration_dd.visible = slide and fmt == "slideshow"
        photos_box.visible = slide and fmt == "cover" and bool(photo_boxes)
        estimate = ""
        if fmt in ("mp4", "mp3", "audio"):
            estimate = estimate_choice(state["formats"], state["duration"], fmt,
                                       quality_dd.value, BACKEND is not None)
        estimate_text.value = ("Estimated download: " + estimate) if estimate else ""
        estimate_text.visible = bool(estimate)

    quality_dd = ft.Dropdown(
        label="Maximum quality",
        value=settings.get("quality") if settings.get("quality") in QUALITIES else "best",
        options=[
            ft.DropdownOption(key="best", text="Best"),
            ft.DropdownOption(key="1080", text="1080p"),
            ft.DropdownOption(key="720", text="720p"),
            ft.DropdownOption(key="480", text="480p"),
        ],
        expand=True,
    )

    cookies_dd = None
    if desktop:
        keys = list(BROWSERS)
        if ENV_COOKIES_BROWSER and ENV_COOKIES_BROWSER not in keys:
            keys.append(ENV_COOKIES_BROWSER)
        remembered_browser = settings.get("cookies_browser")
        cookies_dd = ft.Dropdown(
            label="Browser cookies (desktop)",
            value=remembered_browser if remembered_browser in keys + ["none"] else (ENV_COOKIES_BROWSER or "none"),
            options=[ft.DropdownOption(key="none", text="None")]
            + [ft.DropdownOption(key=k, text=k.capitalize()) for k in keys],
        )

    def cookies_value():
        if cookies_dd is None or cookies_dd.value in (None, "", "none"):
            return ""
        return cookies_dd.value

    cookies_info = ft.Text("", size=13, selectable=True)
    cookies_msg = ft.Container(visible=False)
    cookies_import_btn = ft.Button("Import cookies.txt", icon=ft.Icons.UPLOAD_FILE)
    cookies_remove_btn = ft.TextButton("Remove", icon=ft.Icons.DELETE_OUTLINE, visible=False)

    def refresh_cookies():
        path = saved_cookies_path()
        if path is None:
            cookies_info.value = "No cookies file imported."
            cookies_info.color = ft.Colors.ON_SURFACE_VARIANT
            cookies_remove_btn.visible = False
            return
        try:
            cookies_info.value = "Cookies file saved in the app: " + describe_cookies(path)
            cookies_info.color = None
        except Exception as exc:
            cookies_info.value = "The saved cookies file is unreadable: %s" % clean_error(exc)
            cookies_info.color = ft.Colors.ERROR
        cookies_remove_btn.visible = True

    async def pick_cookies(e=None):
        try:
            files = await picker.pick_files(
                dialog_title="Choose your cookies.txt file", allow_multiple=False, with_data=page.web
            )
            if not files:
                return
            f = files[0]
            content = Path(f.path).read_bytes() if f.path else bytes(f.bytes or b"")
            summary = await asyncio.to_thread(import_cookies, content)
            refresh_cookies()
            set_status(cookies_msg, "ok", "Imported: %s. The original file can be deleted." % summary)
        except ValueError as exc:
            set_status(cookies_msg, "err", str(exc))
        except Exception as exc:
            set_status(cookies_msg, "err", "Could not read the file: %s" % exc)
        page.update()

    def clear_cookies(e=None):
        remove_cookies()
        refresh_cookies()
        hide_status(cookies_msg)
        page.update()

    cookies_import_btn.on_click = pick_cookies
    cookies_remove_btn.on_click = clear_cookies
    refresh_cookies()

    download_btn = ft.Button("Download", icon=ft.Icons.DOWNLOAD)
    progress_bar = ft.ProgressBar(value=None)
    progress_text = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT, expand=True)
    cancel_btn = ft.TextButton("Cancel", icon=ft.Icons.CANCEL)
    progress_box = ft.Column(
        [ft.Row([progress_text, cancel_btn], vertical_alignment=ft.CrossAxisAlignment.CENTER),
         progress_bar],
        spacing=6, visible=False,
    )
    download_status = ft.Container(visible=False)

    result_card = ft.Card(
        visible=False,
        content=ft.Container(
            padding=20,
            content=ft.Column(
                [
                    thumb_box,
                    video_title,
                    video_meta,
                    estimate_text,
                    slideshow_note,
                    ft.Row([format_dd, quality_dd, duration_dd], spacing=12),
                    photos_box,
                    download_btn,
                    progress_box,
                    download_status,
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ),
    )

    def on_format_change(e):
        remember("slide_format" if state["slideshow"] else "format", format_dd.value)
        refresh_options()
        page.update()

    def on_quality_change(e):
        remember("quality", quality_dd.value)
        refresh_options()
        page.update()

    format_dd.on_select = on_format_change
    quality_dd.on_select = on_quality_change
    duration_dd.on_select = lambda e: remember("photo_seconds", duration_dd.value)
    if cookies_dd is not None:
        cookies_dd.on_select = lambda e: remember("cookies_browser", cookies_dd.value)

    async def analyze(e=None):
        url = (url_input.value or "").strip()
        if not url:
            return
        result_card.visible = False
        hide_status(download_status)
        if not is_allowed_url(url):
            set_status(analyze_status, "err",
                       "Unrecognized link. Use a YouTube, TikTok, Instagram or Twitter link.")
            page.update()
            return

        analyze_btn.disabled = True
        set_status(analyze_status, "loading", "Analyzing link…")
        page.update()
        try:
            data = await asyncio.to_thread(
                fetch_info, url, cookies_value(), cookie_file=saved_cookies_path()
            )
            state["url"], state["title"], state["platform"] = data["url"], data["title"], data["platform"]
            state["slideshow"], state["images"] = data["slideshow"], data["images"]
            state["formats"], state["duration"] = data["formats"], data["duration"]
            video_title.value = data["title"]
            video_meta.value = " · ".join(
                x for x in (
                    data["platform"], data["uploader"], format_duration(data["duration"]),
                    ("Slideshow of %d photos" % len(data["images"])) if data["images"] else "",
                ) if x
            )
            set_formats(data["slideshow"], bool(data["images"]))
            build_photos(data["images"])
            slideshow_note.visible = data["slideshow"]
            if data["images_error"]:
                slideshow_note.value = ("Photo post, but the photos are unreadable (%s). "
                                        "Only the audio can be downloaded." % data["images_error"])
            elif data["slideshow"] and not CAN_SLIDESHOW:
                slideshow_note.value = ("Photo post: creating a video requires ffmpeg, or PyAV + Pillow "
                                        "(unavailable here). Only the audio can be downloaded.")
            elif data["slideshow"]:
                slideshow_note.value = ("Photo post: choose a single photo + the audio, "
                                        "or all photos as a slideshow with the audio.")
            refresh_options()
            thumb.src = data["thumbnail"] or ""
            thumb_box.visible = bool(data["thumbnail"])
            result_card.visible = True
            hide_status(analyze_status)
        except DownloadError as exc:
            message = clean_error(exc)
            set_status(analyze_status, "err", message, make_hint(message, url, desktop))
        except Exception as exc:
            set_status(analyze_status, "err", "Error: %s" % exc)
        finally:
            analyze_btn.disabled = False
            page.update()

    async def download(e=None):
        if not state["url"]:
            return
        url, title = state["url"], state["title"]
        fmt, quality = format_dd.value or "mp4", quality_dd.value
        if quality not in QUALITIES:
            quality = "best"
        slideshow_mode = state["slideshow"] and fmt in ("cover", "slideshow")
        seconds = None if duration_dd.value in (None, "auto") else float(duration_dd.value)
        images, selected = list(state["images"]), state["selected"]

        cancel_event.clear()
        download_btn.disabled = True
        cancel_btn.disabled = False
        cancel_btn.visible = True
        hide_status(download_status)
        progress_text.value = "Preparing…"
        progress_bar.value = None
        progress_box.visible = True
        page.update()

        loop = asyncio.get_running_loop()
        last = {"pct": -1, "msg": ""}

        def apply_progress(fraction, message):
            progress_bar.value = fraction
            progress_text.value = message if fraction is None else "%s %d %%" % (message, round(fraction * 100))
            page.update()

        def on_progress(fraction, message):
            if cancel_event.is_set():
                raise Cancelled()
            pct = -1 if fraction is None else int(fraction * 100)
            if pct == last["pct"] and message == last["msg"]:
                return
            last["pct"], last["msg"] = pct, message
            loop.call_soon_threadsafe(apply_progress, fraction, message)

        tmp = tempfile.TemporaryDirectory(prefix="videodl_", dir=workdir_parent())
        try:
            if slideshow_mode:
                path = await asyncio.to_thread(
                    download_slideshow, url, fmt, images, selected, seconds, tmp.name,
                    cookies_value(), on_progress, cookie_file=saved_cookies_path(),
                )
            else:
                path = await asyncio.to_thread(
                    download_media, url, fmt if fmt in ("mp3", "audio") else "mp4", quality, tmp.name,
                    cookies_value(), on_progress, cookie_file=saved_cookies_path(),
                )
            name = safe_filename(title, path.suffix)

            cancel_btn.visible = False
            progress_text.value = "Choose where to save…"
            progress_bar.value = None
            page.update()

            if needs_bytes:
                data = await asyncio.to_thread(path.read_bytes)
                saved = await picker.save_file(dialog_title="Save file", file_name=name, src_bytes=data)
                saved_ok = saved is not None or bool(page.web)
                message = "Download complete: " + name
            else:
                saved = await picker.save_file(dialog_title="Save file", file_name=name)
                saved_ok = bool(saved)
                if saved_ok:
                    await asyncio.to_thread(shutil.copyfile, str(path), saved)
                message = "Saved: %s" % saved

            if saved_ok:
                set_status(download_status, "ok", message)
                add_history({
                    "title": title,
                    "platform": state["platform"],
                    "url": url,
                    "format": path.suffix.lstrip(".").upper(),
                    "name": name,
                    "path": saved if (not needs_bytes and saved) else "",
                })
                refresh_history()
            else:
                set_status(download_status, "err", "No location chosen: the file was not saved.")
        except Cancelled:
            set_status(download_status, "info", "Download cancelled.")
        except DownloadError as exc:
            message = clean_error(exc)
            set_status(download_status, "err", message, make_hint(message, url, desktop))
        except Exception as exc:
            set_status(download_status, "err", "Error: %s" % exc)
        finally:
            tmp.cleanup()
            progress_box.visible = False
            download_btn.disabled = False
            page.update()

    def cancel_download(e=None):
        cancel_event.set()
        cancel_btn.disabled = True
        progress_text.value = "Cancelling…"
        page.update()

    async def paste_link(e=None):
        try:
            text = await ft.Clipboard().get() or ""
        except Exception:
            text = ""
        m = re.search(r"https?://\S+", text)
        if not m:
            set_status(analyze_status, "err", "No link found in the clipboard.")
            page.update()
            return
        url_input.value = m.group(0)
        page.update()
        await analyze()

    history_list = ft.Column(spacing=8)
    history_clear_btn = ft.TextButton("Clear history", icon=ft.Icons.DELETE_OUTLINE, visible=False)

    def make_reuse(url):
        async def reuse(e=None):
            url_input.value = url
            page.update()
            await analyze()
        return reuse

    def refresh_history():
        items = load_history()
        rows = []
        for entry in items[:15]:
            details = " · ".join(
                x for x in (entry.get("platform"), entry.get("format"), entry.get("time")) if x
            )
            rows.append(ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(entry.get("title") or "video", max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_600),
                            ft.Text(details, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2, tight=True, expand=True,
                    ),
                    ft.IconButton(icon=ft.Icons.REPLAY, tooltip="Download again",
                                  on_click=make_reuse(entry.get("url") or "")),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ))
        if not rows:
            rows = [ft.Text("No downloads yet.", size=13, color=ft.Colors.ON_SURFACE_VARIANT)]
        history_list.controls = rows
        history_clear_btn.visible = bool(items)

    def on_clear_history(e=None):
        clear_history()
        refresh_history()
        page.update()

    yt_info = ft.Text("Running yt-dlp %s (%s)." % (YTDLP_VERSION, "updated" if YTDLP_UPDATE_ACTIVE else "bundled"),
                      size=13)
    yt_msg = ft.Container(visible=False)
    yt_update_btn = ft.Button("Update yt-dlp", icon=ft.Icons.REFRESH)
    yt_reset_btn = ft.TextButton("Use bundled version", icon=ft.Icons.UNDO, visible=YTDLP_UPDATE_ACTIVE)

    async def update_ytdlp_click(e=None):
        yt_update_btn.disabled = True
        set_status(yt_msg, "loading", "Downloading the latest yt-dlp…")
        page.update()
        try:
            known = yt_state["pending"] or YTDLP_VERSION
            version, installed = await asyncio.to_thread(update_ytdlp, known)
            if installed:
                yt_state["pending"] = version
                yt_reset_btn.visible = True
                set_status(yt_msg, "ok", "yt-dlp %s downloaded. Restart the app to use it." % version)
            else:
                set_status(yt_msg, "ok", "yt-dlp is already up to date (%s)." % known)
        except Exception as exc:
            set_status(yt_msg, "err", "Update failed: %s" % clean_error(exc))
        finally:
            yt_update_btn.disabled = False
            page.update()

    def reset_ytdlp_click(e=None):
        remove_ytdlp_update()
        yt_state["pending"] = None
        yt_reset_btn.visible = False
        set_status(yt_msg, "ok", "Done. Restart the app to use the bundled yt-dlp.")
        page.update()

    analyze_btn.on_click = analyze
    url_input.on_submit = analyze
    download_btn.on_click = download
    cancel_btn.on_click = cancel_download
    paste_btn.on_click = paste_link
    history_clear_btn.on_click = on_clear_history
    yt_update_btn.on_click = update_ytdlp_click
    yt_reset_btn.on_click = reset_ytdlp_click
    refresh_history()

    body = ft.Column(
        [
            ft.Text("Video downloader", size=26, weight=ft.FontWeight.BOLD),
            ft.Text("YouTube, TikTok, Instagram and Twitter: paste the link, choose the format, download.", color=ft.Colors.ON_SURFACE_VARIANT),
            banner,
            ft.Card(content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Row([url_input, paste_btn, analyze_btn], spacing=4),
                        ft.Text("One link at a time. Playlists are not supported.",
                                size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        analyze_status,
                    ],
                    spacing=10,
                ),
            )),
            result_card,
            ft.Card(content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Row(
                            [ft.Text("History", size=16, weight=ft.FontWeight.W_600, expand=True),
                             history_clear_btn],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        history_list,
                    ],
                    spacing=10,
                ),
            )),
            ft.Card(content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Cookies (optional)", size=16, weight=ft.FontWeight.W_600),
                        ft.Text("For content that requires a login (Instagram, Twitter, TikTok, YouTube…). "
                                "The file is copied into the app: it is kept after you close the app, "
                                "even if the original is deleted.",
                                size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        cookies_info,
                        ft.Row([cookies_import_btn, cookies_remove_btn], wrap=True, spacing=8),
                        cookies_msg,
                    ] + ([cookies_dd] if cookies_dd is not None else []),
                    spacing=10,
                ),
            )),
            ft.Card(content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("yt-dlp", size=16, weight=ft.FontWeight.W_600),
                        ft.Text("Platforms change often and can break downloads. Update yt-dlp here "
                                "without rebuilding the app; the update is used after a restart.",
                                size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        yt_info,
                        ft.Row([yt_update_btn, yt_reset_btn], wrap=True, spacing=8),
                        yt_msg,
                    ],
                    spacing=10,
                ),
            )),
        ],
        spacing=16,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    holder = ft.Container(content=body, width=640)

    def on_resize(e=None):
        width = page.width or 640
        holder.width = max(280, min(640, width - 32))
        page.update()

    page.on_resize = on_resize
    page.add(ft.SafeArea(content=holder))
    on_resize()

    if desktop:
        try:
            clip = await ft.Clipboard().get() or ""
            m = re.search(r"https?://\S+", clip)
            if m and is_allowed_url(m.group(0)):
                url_input.value = m.group(0)
                page.update()
        except Exception:
            pass


if __name__ == "__main__":
    ft.run(main)