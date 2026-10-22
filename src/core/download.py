"""Downloading one video or its audio in the chosen format and quality."""

import os
from pathlib import Path

from yt_dlp.networking import Request
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import download_range_func

from core.backend import AUDIO_FORMATS, BACKEND, FFMPEG_AUDIO_QUALITY, HAS_PIL, IMAGE_FORMATS, VIDEO_FORMATS
from core.compress import fit_in_size
from core.ffmpeg import convert_container, ensure_compatible, media_codecs, run_ffmpeg, strip_audio
from core.jobs import entering_conversion, ignore_progress
from core.links import is_twitter_url, is_youtube_url
from core.editor import edit_media, probe_media
from core.music import song_tags
from core.subtitles import fetch_several, iso_language
from core.network import first_entry, run_ytdlp
from core.pyav import av_merge, av_to_audio, av_trim, has_audio_stream
from core.speed import change_speed
from core.stills import GIF_QUALITY, grab_frame, make_gif
from core.streams import (
    AUDIO_SELECTORS, AV_AUDIO_SELECTOR, CODEC, av_video_selector, first_language, video_selector,
    with_audio_language,
)
from core.tags import attach_cover, media_metadata, try_tag
from core.units import transfer_text
from core.verify import check_media


# Parts of YouTube videos SponsorBlock can cut out (chosen in Settings).
SPONSORBLOCK_CATEGORIES = {
    "sponsor": "Sponsors", "selfpromo": "Self-promotion", "interaction": "Reminders to like or subscribe",
    "intro": "Intros", "outro": "Endcards and credits", "preview": "Previews and recaps",
    "filler": "Off-topic tangents", "music_offtopic": "Non-music parts of music videos",
}


def make_hooks(notify, audio_label=None, cut_message="Cutting out the SponsorBlock segments…"):
    """yt-dlp progress hooks; `audio_label` (e.g. "MP3") names the audio conversion step, `cut_message` the
    removal of parts (ModifyChapters)."""
    files = {}
    speed_state = {"speed": 0}

    def hook(d):
        if d.get("status") == "downloading":
            key = d.get("filename") or d.get("tmpfilename") or ""
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            files[key] = (done, total)
            if d.get("speed"):
                speed_state["speed"] = d["speed"]
            done_all = sum(f[0] for f in files.values())
            known = all(f[1] for f in files.values())
            total_all = sum(f[1] for f in files.values()) if known else None
            fraction = min(done_all / total_all, 1.0) if total_all else None
            notify(fraction, "Downloading…", transfer_text(done_all, total_all, speed_state["speed"]))
        elif d.get("status") == "finished":
            key = d.get("filename") or d.get("tmpfilename") or ""
            size = d.get("total_bytes") or d.get("downloaded_bytes")
            if key in files and size:
                files[key] = (size, size)
            notify(None, "Finalizing…")

    def pp_hook(d):
        if d.get("status") != "started":
            return
        name = d.get("postprocessor") or ""
        if name == "MoveFiles":  # just moves the finished file, nothing worth showing
            return
        entering_conversion()
        if name == "Merger":
            notify(None, "Merging video and audio…")
        elif name == "ExtractAudio":
            notify(None, "Converting to %s…" % audio_label if audio_label else "Converting audio…")
        elif name.startswith("Fixup"):
            notify(None, "Finalizing the file…")
        elif name == "ModifyChapters":
            notify(None, cut_message)
        else:
            notify(None, "Processing…")

    return hook, pp_hook


def _range_opts(clip):
    start, end = clip
    return {
        "download_ranges": download_range_func(None, [(start, end if end is not None else float("inf"))]),
        "force_keyframes_at_cuts": True,
    }


class EditedTags(PostProcessor):
    """Runs before downloading: the tags typed by the user replace the site's ones in what ffmpeg
    writes into videos (FFmpegMetadata reads the "meta_" fields first)."""

    def __init__(self, edits):
        super().__init__()
        self.edits = edits

    def run(self, info):
        info.update({"meta_" + key: value for key, value in self.edits.items()})
        return [], info


def download_thumbnail(url, workdir, cookies_browser="", cookie_file=None, notify=ignore_progress,
                       info_out=None):
    """The video's thumbnail at its full size (the largest the site has), as a JPG or PNG file."""
    def extract(ydl):
        notify(None, "Fetching video info…")
        info = first_entry(ydl.extract_info(url, download=False))
        if info_out is not None and not info_out:
            info_out.update({key: info.get(key) for key in INFO_FIELDS})
        thumbnails = [t for t in info.get("thumbnails") or [] if t.get("url")]
        if not thumbnails and info.get("thumbnail"):
            thumbnails = [{"url": info["thumbnail"]}]
        notify(None, "Downloading the thumbnail…")
        # yt-dlp lists them from the worst to the best; the biggest may not exist (YouTube's maxres).
        for thumbnail in sorted(thumbnails, key=lambda t: (t.get("preference") or 0,
                                                           (t.get("width") or 0) * (t.get("height") or 0)),
                                reverse=True):
            try:
                return ydl.urlopen(Request(thumbnail["url"])).read()
            except Exception:
                continue
        raise RuntimeError("This video has no thumbnail.")

    data = run_ytdlp(extract, {"skip_download": True}, cookies_browser, cookie_file)
    if data[:3] == b"\xff\xd8\xff":
        return _written(Path(workdir, "thumbnail.jpg"), data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _written(Path(workdir, "thumbnail.png"), data)
    source = _written(Path(workdir, "thumbnail.source"), data)  # WebP, most often: made a JPG
    if BACKEND == "ffmpeg":
        return grab_frame(source, Path(workdir, "thumbnail.jpg"), workdir)
    if HAS_PIL:
        from PIL import Image
        with Image.open(source) as image:
            image.convert("RGB").save(Path(workdir, "thumbnail.jpg"), "JPEG", quality=95)
        return Path(workdir, "thumbnail.jpg")
    return source.rename(source.with_suffix(".webp"))


def _written(path, data):
    path.write_bytes(data)
    return path


def download_media(url, fmt, quality, workdir, cookies_browser="", on_progress=None,
                   cookie_file=None, clip=None, tags=True, info_out=None, before_tags=None,
                   max_bytes=None, tag_edits=None, speed=None, audio_lang=None, sponsorblock=None,
                   remove_ranges=None, check=False, sound=True, cover_url=None, music_tags=False,
                   lyrics=False, edits=None):
    """Download `url` as `fmt` into workdir and return the file.

    tags: add title, artist, date, cover art (and chapters for videos) to the file.
    info_out: dict filled with the video's details (title, uploader, upload_date…), for file names.
    before_tags: path -> path step run on the finished file before tagging (embedding subtitles
    rewrites the file and would drop the cover art).
    max_bytes: re-encode the file if needed so it is not bigger (see fit_in_size).
    tag_edits: {"title", "artist", "album"} typed by the user, written instead of the site's ones.
    cover_url: the cover art when the site gives none (a podcast episode, a bare MP3 link).
    music_tags, lyrics: for a sound file, the song's real tags and cover art (MusicBrainz) and its lyrics
    (LRCLIB), when a sure match is found (see core.music).
    speed: play it faster or slower (1.5 = 50 % faster), see change_speed.
    audio_lang: the sound track in this language (YouTube's dubbed tracks), when the video has it; a list for
    several (a video gets them all, the first being the default; a sound file the first).
    edits: the Edit tab's changes made to the downloaded file (ffmpeg): crop, turn, loudness, no_silence (see
    core.editor.edit_media), and burn ({"key": a subtitle language, "style": …}: subtitles drawn onto the
    picture).
    sponsorblock: SponsorBlock categories cut out of the file (sponsors, intros…), YouTube with ffmpeg only.
    remove_ranges: [(start, end)] in seconds cut out of the file (the chapters left out), with ffmpeg only.
    check: read the finished file (see check_media); the problems found go into info_out["problems"].
    sound: False for a video without its sound (the picture stream alone is downloaded: smaller), ffmpeg only.
    For a GIF, `clip` is the passage to animate; for a JPG or PNG image, its start is the moment taken.
    """
    notify = on_progress or ignore_progress
    if fmt == "thumbnail":
        return download_thumbnail(url, workdir, cookies_browser, cookie_file, notify, info_out)
    if clip and BACKEND is None:
        raise RuntimeError("Extracting a clip requires ffmpeg or PyAV.")
    if speed and speed != 1 and BACKEND != "ffmpeg":
        raise RuntimeError("Changing the speed requires ffmpeg.")
    if fmt in IMAGE_FORMATS:
        if BACKEND != "ffmpeg":
            raise RuntimeError("GIFs and images require ffmpeg.")
        gif = fmt == "gif"
        start = clip[0] if clip else 0.0
        # Only the part needed: the passage for a GIF, one second from the chosen moment for an image.
        video = download_media(url, "mp4", GIF_QUALITY if gif else quality, workdir, cookies_browser,
                               on_progress, cookie_file, clip=clip if gif else (start, start + 1.0),
                               tags=False, info_out=info_out)
        out = Path(workdir, "image." + fmt)
        if gif:
            return make_gif(video, out, workdir, notify, max_bytes, bounce=bool((edits or {}).get("bounce")))
        return grab_frame(video, out, workdir)
    label = AUDIO_FORMATS.get(fmt, VIDEO_FORMATS.get(fmt, "")).split(" ")[0]
    sponsorblock = sponsorblock if sponsorblock and is_youtube_url(url) else None
    hook, pp_hook = make_hooks(notify, label, "Cutting out the SponsorBlock segments…" if sponsorblock
                               else "Cutting out the chapters left out…")

    base = {
        "playlist_items": "1",
        "noprogress": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }
    # SponsorBlock: the segments' times are looked up, then cut out of the finished file (with ffmpeg), with
    # the chapters left out.
    cut_segments = []
    if (sponsorblock or remove_ranges) and BACKEND == "ffmpeg" and not clip:
        if sponsorblock:
            cut_segments.append({"key": "SponsorBlock", "categories": list(sponsorblock), "when": "after_filter"})
        cut_segments.append(
            {"key": "ModifyChapters", "remove_sponsor_segments": list(sponsorblock or []),
             "remove_chapters_patterns": [], "remove_ranges": [tuple(r) for r in remove_ranges or []],
             "sponsorblock_chapter_title": "[SponsorBlock]: %(category_names)l", "force_keyframes": False})

    def fetch_and_download(ydl):
        # Same as extract_info(download=True), split so each phase gets its own message.
        notify(None, "Fetching video info…")
        info = ydl.extract_info(url, download=False, process=False)
        # A clip is cut by ffmpeg while downloading, which reports no progress until it is done.
        notify(None, "Downloading the clip…" if clip and BACKEND == "ffmpeg" else "Downloading…")
        return first_entry(ydl.process_ie_result(info, download=True))

    meta = {}
    details = {}  # length and sound of the video, for the check

    def run(opts):
        def extract(ydl):
            if tags and tag_edits:
                ydl.add_post_processor(EditedTags(tag_edits), when="pre_process")
            info = fetch_and_download(ydl)
            details.setdefault("duration", info.get("duration"))
            details.setdefault("acodec", info.get("acodec"))
            if info_out is not None and not info_out:
                info_out.update({key: info.get(key) for key in INFO_FIELDS})
            if tags and not meta:
                if info.get("thumbnail") or cover_url:
                    notify(None, "Fetching the cover art…")
                meta.update(media_metadata(ydl, dict(info, thumbnail=info.get("thumbnail") or cover_url)))
                # Lyrics would not match a part, or another speed.
                with_lyrics = lyrics and not clip and not (speed and speed != 1)
                if fmt in AUDIO_FORMATS and (music_tags or with_lyrics):
                    notify(None, "Looking up the song…")
                    meta.update(song_tags(info, music_tags, with_lyrics))
                meta.update(tag_edits or {})
        return run_ytdlp(extract, opts, cookies_browser, cookie_file)

    def finish(path):
        """Last step for every format: speed, the Edit tab's changes, size limit, then tags and cover art, when
        the container can hold them."""
        if speed and speed != 1:
            path = change_speed(path, speed, workdir, notify, video=fmt in VIDEO_FORMATS)
        if edits and BACKEND == "ffmpeg":
            path = apply_edits(path)
        if max_bytes:
            # The cover art is added afterwards: keep room for it.
            cover = len(meta["cover"][0]) if tags and meta.get("cover") else 0
            path = fit_in_size(path, max_bytes, workdir, notify, reserve=cover)
        if before_tags:
            path = before_tags(path)
        if tags and meta:
            notify(None, "Adding tags and cover art…")
            if path.suffix.lower() == ".mkv" and meta.get("cover") and BACKEND == "ffmpeg":
                path = attach_cover(path, meta["cover"], workdir)
            try_tag(path, meta)
        return path

    def done(path):
        path = finish(path)
        if check and info_out is not None:
            notify(None, "Checking the file…")
            info_out["problems"] = check_media(path, expected_length(), sound=fmt in AUDIO_FORMATS or (
                                               sound and details.get("acodec") not in (None, "none")),
                                               picture=fmt in VIDEO_FORMATS)
        return path

    def apply_edits(path):
        entering_conversion()
        options = {key: edits[key] for key in ("crop", "turn", "loudness", "no_silence") if edits.get(key)}
        burn = edits.get("burn") if fmt in VIDEO_FORMATS else None
        if burn:
            notify(None, "Downloading subtitles…")
            folder = Path(workdir, "burn")
            folder.mkdir(exist_ok=True)
            (_, subs), = fetch_several(url, [burn["key"]], folder, cookies_browser, cookie_file, clip)
            options.update(subs_file=subs, subs_mode="burn", subs_style=burn.get("style"))
        if not options:
            return path
        edited, _ = edit_media(path, path.suffix.lstrip(".").lower(), probe_media(path), options, workdir, notify)
        return edited

    def expected_length():
        """How long the file should last; None when parts were cut out by SponsorBlock (not known here)."""
        length = details.get("duration")
        if not length or (sponsorblock and BACKEND == "ffmpeg" and not clip):
            return None
        if clip:
            length = (clip[1] if clip[1] is not None else length) - clip[0]
        elif remove_ranges and BACKEND == "ffmpeg":
            length -= sum(min(end, length) - start for start, end in remove_ranges if start < length)
        return length / speed if speed else length

    if fmt in AUDIO_FORMATS:
        opts = dict(base, outtmpl=os.path.join(workdir, "media.%(ext)s"),
                    format=with_audio_language(AUDIO_SELECTORS.get(fmt, "bestaudio/best"), first_language(audio_lang)))
        opts["postprocessors"] = []
        if fmt != "audio" and BACKEND == "ffmpeg":
            extract = {"key": "FFmpegExtractAudio", "preferredcodec": fmt}
            if fmt in FFMPEG_AUDIO_QUALITY:
                extract["preferredquality"] = FFMPEG_AUDIO_QUALITY[fmt]
            opts["postprocessors"] = [extract]
        opts["postprocessors"] += cut_segments
        if clip and BACKEND == "ffmpeg":
            opts.update(_range_opts(clip))
        run(opts)
        src = find_file(workdir, "media")
        if src is None:
            raise RuntimeError("No file was produced.")
        if BACKEND == "av":
            if fmt != "audio":
                notify(None, "Converting to %s…" % label)
                dst = Path(workdir, "audio." + fmt)
                av_to_audio(src, dst, fmt, notify, clip)
                return done(dst)
            if clip:
                return done(av_trim(src, Path(workdir, "clip" + src.suffix), clip, notify))
        return done(src)

    if BACKEND == "av":
        path = _download_video_av(run, base, quality, workdir, notify, is_twitter_url(url), fmt,
                                  first_language(audio_lang))
        # MP4 keeps the existing MKV fallback when a codec does not fit; MKV always gets an MKV file.
        suffix = ".mkv" if fmt == "mkv" else path.suffix
        if clip or path.suffix != suffix:
            path = av_trim(path, Path(workdir, "clip" + suffix), clip or (0.0, None), notify,
                           "Cutting clip…" if clip else "Converting to %s…" % label)
        return done(path)

    sound = sound or BACKEND != "ffmpeg"
    opts = dict(base, outtmpl=os.path.join(workdir, "media.%(ext)s"),
                format=video_selector(quality, fmt, audio_lang, sound))
    if isinstance(audio_lang, (list, tuple)) and len(audio_lang) > 1 and sound:
        opts["allow_multiple_audio_streams"] = True  # several sound tracks in one video
    opts["postprocessors"] = list(cut_segments)
    if BACKEND == "ffmpeg":
        # WebM: yt-dlp merges into WebM when the codecs allow it, else MKV (converted below).
        if fmt != "webm":
            opts["merge_output_format"] = "mkv" if fmt == "mkv" else "mp4"
        if clip:
            opts.update(_range_opts(clip))
        if tags:
            # Title, artist, date, description… and the chapters (they would not match a clip).
            opts["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": not clip})
    run(opts)

    files = [p for p in Path(workdir).iterdir() if _is_finished_file(p)]
    if not files:
        raise RuntimeError("No file was produced.")
    path = max(files, key=lambda p: p.stat().st_size)
    if opts.get("allow_multiple_audio_streams"):
        path = name_sound_tracks(path, audio_lang, workdir)
    if not sound and media_codecs(path)[1]:  # only a stream with its sound was offered
        entering_conversion()
        notify(None, "Removing the sound…")
        path = strip_audio(path, workdir)
    if BACKEND == "ffmpeg" and path.suffix.lower() != "." + fmt:
        path = convert_container(path, fmt, workdir, notify)
    if BACKEND == "ffmpeg" and CODEC["preference"] == "compatible" and fmt != "webm":
        path = ensure_compatible(path, workdir, notify)
    return done(path)


def name_sound_tracks(path, langs, workdir):
    """Several sound tracks, in the order of `langs`: each one gets its language, which players show to choose
    it (yt-dlp leaves them unnamed)."""
    entering_conversion()
    out = Path(workdir, "named" + path.suffix)
    names = []
    for index, lang in enumerate(langs):
        if iso_language(lang):
            names += ["-metadata:s:a:%d" % index, "language=" + iso_language(lang)]
    try:
        run_ffmpeg(["-i", str(path), "-map", "0", "-c", "copy"] + names + [str(out)], workdir)
    except RuntimeError:
        out.unlink(missing_ok=True)
        return path
    return out


# Details of the downloaded video kept for file names (see filename_fields).
INFO_FIELDS = ("title", "uploader", "channel", "upload_date", "id", "extractor_key", "height", "webpage_url",
               "description", "thumbnail")


def _is_finished_file(path):
    return path.is_file() and path.suffix not in (".part", ".ytdl")


def find_file(workdir, stem):
    return next((p for p in sorted(Path(workdir).glob(stem + ".*")) if _is_finished_file(p)), None)


def _download_video_av(run, base, quality, workdir, notify, complete_first=False, container="mp4", audio_lang=None):
    selector = av_video_selector(quality, complete_first)
    run(dict(base, outtmpl=os.path.join(workdir, "video.%(ext)s"), format=selector))
    video = find_file(workdir, "video")
    if video is None:
        raise RuntimeError("No file was produced.")
    if has_audio_stream(video):
        return video

    notify(None, "Downloading audio…")
    run(dict(base, outtmpl=os.path.join(workdir, "audio.%(ext)s"), format=with_audio_language(AV_AUDIO_SELECTOR, audio_lang)))
    audio = find_file(workdir, "audio")
    if audio is None:
        raise RuntimeError("The audio track could not be downloaded.")

    entering_conversion()
    notify(None, "Merging video + audio…")
    out = Path(workdir, "media.mkv" if container == "mkv" else "media.mp4")
    try:
        av_merge(video, audio, out, notify)
    except Exception:
        if out.suffix == ".mkv":
            raise
        out.unlink(missing_ok=True)
        out = Path(workdir, "media.mkv")
        av_merge(video, audio, out, notify)
    return out
