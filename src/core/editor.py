"""The Edit tab's work: a video or audio file of the computer changed in one ffmpeg pass (format, picture
size, crop, turn, speed, a passage sped up, part kept, silences removed, volume evened out, sound faded in and
out, a text or a logo on the picture, sound and subtitle tracks kept, subtitles added, tags, cover art), then made
to fit a size limit if one is set. Also: several files joined into one, and one picture of the result shown before
saving."""

import collections
import functools
import io
import re
import shutil
import subprocess
from pathlib import Path

from core.backend import AUDIO_FORMATS, BACKEND, FFMPEG_BIN, HAS_PIL, VIDEO_FORMATS
from core.compress import fit_box, fit_in_size
from core.ffmpeg import REENCODE_ARGS, ffmpeg_with_progress, run_ffmpeg
from core.speed import AUDIO_ARGS, atempo_chain
from core.stills import make_gif
from core.subtitles import EMBED_CODECS, cues_to_srt, parse_cues
from core.system import NO_WINDOW
from core.tags import tag_file


# Files the Edit tab opens, and the subtitle files it can add.
MEDIA_EXTENSIONS = ["mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "ts", "mpg", "mpeg", "3gp",
                    "mp3", "m4a", "aac", "opus", "ogg", "flac", "wav", "wma"]
SUBTITLE_EXTENSIONS = ["srt", "vtt", "ass", "ssa"]
EDIT_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240)
# Subtitle tracks made of text (a format change converts them); others are pictures (DVD, Blu-ray).
TEXT_SUBTITLES = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")
# Formats the Edit tab can write ("audio", the untouched original sound, makes no sense here).
EDIT_AUDIO_FORMATS = [f for f in AUDIO_FORMATS if f != "audio"]
# Containers whose tags mutagen rewrites in place (see tag_file): the file is not re-encoded for new tags.
TAGGABLE = (".mp3", ".m4a", ".mp4", ".m4v", ".mov", ".flac", ".opus", ".ogg", ".wav")
# Crops: a frame of the same height, or of the same width, centered (sizes kept even for the encoders).
CROPS = {
    "9:16": "crop=w='trunc(min(iw,ih*9/16)/2)*2':h='trunc(min(ih,iw*16/9)/2)*2'",
    "1:1": "crop=w='trunc(min(iw,ih)/2)*2':h='trunc(min(iw,ih)/2)*2'",
    "16:9": "crop=w='trunc(min(iw,ih*16/9)/2)*2':h='trunc(min(ih,iw*9/16)/2)*2'",
    # The whole picture in a 9:16 frame of the same height, over a blurred and enlarged copy of itself (a graph of
    # its own: joined to the other filters with commas, its labels stay inside it).
    "9:16blur": "split[bgin][fgin];[bgin]crop=w='trunc(min(iw,ih*9/16)/2)*2':h=ih,boxblur=20:2[bgout];"
                "[fgin]scale=w='trunc(min(iw,ih*9/16)/2)*2':h=-2[fgout];[bgout][fgout]overlay=(W-w)/2:(H-h)/2",
}
TURNS = {"right": "transpose=1", "left": "transpose=2", "180": "hflip,vflip", "mirror": "hflip", "flip": "vflip"}
LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11"  # the level podcasts and streaming services use
SILENCE_DB, SILENCE_SECONDS = -35, 0.6  # quieter than this, for longer than this, is a silence
SILENCE_KEPT = 0.15  # of each silence, kept on both sides: speech does not sound cut
# How subtitles drawn onto the picture look (libass styles; colours are &HAABBGGRR).
SUBTITLE_STYLES = {
    "size": {"small": "FontSize=14", "medium": "FontSize=18", "large": "FontSize=24"},
    "color": {"white": "PrimaryColour=&H00FFFFFF", "yellow": "PrimaryColour=&H0000FFFF"},
    "outline": {"outline": "BorderStyle=1,Outline=1.5,Shadow=0,OutlineColour=&H00000000",
                "thick": "BorderStyle=1,Outline=3,Shadow=0,OutlineColour=&H00000000",
                "box": "BorderStyle=3,Outline=2,Shadow=0,OutlineColour=&H80000000,BackColour=&H80000000"},
    "position": {"bottom": "Alignment=2,MarginV=20", "top": "Alignment=8,MarginV=20"},
}
DEFAULT_SUBTITLE_STYLE = {"size": "medium", "color": "white", "outline": "outline", "position": "bottom"}
# Where a text or a logo goes on the picture (ffmpeg's overlay: W, H the picture's size, w, h the overlay's).
OVERLAY_PLACES = {"bottom-right": "W-w-W*0.03:H-h-H*0.03", "bottom-left": "W*0.03:H-h-H*0.03",
                  "top-right": "W-w-W*0.03:H*0.03", "top-left": "W*0.03:H*0.03", "center": "(W-w)/2:(H-h)/2"}
OVERLAY_FILE = "overlay.png"


def force_style(style):
    """The libass style of `style` ({"size": "large"…}, the missing ones as DEFAULT_SUBTITLE_STYLE)."""
    chosen = dict(DEFAULT_SUBTITLE_STYLE, **(style or {}))
    return ",".join(SUBTITLE_STYLES[key][chosen[key]] for key in SUBTITLE_STYLES)


@functools.lru_cache(maxsize=1)
def can_burn_subtitles():
    """Whether this ffmpeg can draw subtitles onto the picture (it needs to be built with libass)."""
    try:
        result = subprocess.run([FFMPEG_BIN, "-hide_banner", "-filters"], capture_output=True, text=True,
                                timeout=30, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError, TypeError):
        return False
    return re.search(r"^\s*\S+\s+subtitles\s", result.stdout, re.M) is not None


def track_label(track, index):
    """"2. Commentary · fre · aac": how a sound or subtitle track is listed in the Edit tab."""
    return "%d. %s" % (index + 1, " · ".join(x for x in (track["title"], track["lang"], track["codec"]) if x))


def probe_media(path):
    """What the Edit tab shows and needs about a file: length, picture size, codecs, its sound and subtitle
    tracks ({"codec", "lang", "title"} each) and its title/artist/album/date tags (read from what ffmpeg
    prints; by PyAV without ffmpeg)."""
    if BACKEND != "ffmpeg":
        from core.pyav import av_probe
        return av_probe(path)
    text = subprocess.run([FFMPEG_BIN, "-hide_banner", "-i", str(path)], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=60, **NO_WINDOW).stderr
    info = {"duration": None, "width": None, "height": None, "video": "", "audio": "", "audio_tracks": [],
            "subtitles": [], "tags": {}, "cover": False, "fps": None}
    found = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if found:
        info["duration"] = int(found.group(1)) * 3600 + int(found.group(2)) * 60 + float(found.group(3))
    header = text.split("Stream #", 1)[0]  # the file's own tags come before its streams
    for key in ("title", "artist", "album", "album_artist", "genre", "track", "date"):
        tag = re.search(r"^ {4}%s\s*: ?(.*)$" % key, header, re.M | re.I)
        if tag:
            info["tags"][key] = tag.group(1).strip()
    for block in text.split("Stream #")[1:]:  # a stream's line, then its own tags (title…)
        found = re.match(r"\d+:\d+(?:\[\w+\])?(?:\((\w+)\))?.*?: (Video|Audio|Subtitle): (\w+)(.*)", block)
        if not found:
            continue
        lang, kind, codec, rest = found.groups()
        title = re.search(r"^\s+title\s*: ?(.*)$", block, re.M)
        track = {"codec": codec, "lang": "" if lang in (None, "und") else lang,
                 "title": title.group(1).strip() if title else ""}
        if kind == "Video" and not info["video"] and "attached pic" not in rest:
            info["video"] = codec
            size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", rest)
            if size:
                info["width"], info["height"] = int(size.group(1)), int(size.group(2))
            fps = re.search(r"([\d.]+) fps", rest)
            if fps:
                info["fps"] = float(fps.group(1))
        elif kind == "Video" and "attached pic" in rest:
            info["cover"] = True
        elif kind == "Audio":
            info["audio"] = info["audio"] or codec
            info["audio_tracks"].append(track)
        elif kind == "Subtitle":
            info["subtitles"].append(track)
    if not info["video"] and not info["audio"]:
        raise RuntimeError("ffmpeg finds no video or sound in this file.")
    return info


def output_size(info, options):
    """About the size of the edited picture (crop, turn, smaller picture), to size a text or a logo on it."""
    w, h = info.get("width") or 1280, info.get("height") or 720
    if options.get("turn") in ("right", "left"):
        w, h = h, w
    ratio = {"9:16": 9 / 16, "9:16blur": 9 / 16, "1:1": 1.0, "16:9": 16 / 9}.get(options.get("crop"))
    if ratio:
        w, h = (h * ratio, h) if w / h > ratio else (w, w / ratio)
    height = options.get("height")
    if height and max(w, h) > height * 16 / 9 or height and min(w, h) > height:
        scale = min(height * 16 / 9 / max(w, h), height / min(w, h))
        w, h = w * scale, h * scale
    return int(w), int(h)


def make_overlay(overlay, size, workdir):
    """The text or logo to put on the picture, as a PNG file in workdir, sized for a picture of `size`:
    `overlay` {"text": "…"} (white letters with a dark outline) or {"picture": path} (a logo, a fifth of the
    width)."""
    from PIL import Image, ImageDraw, ImageFont
    width, height = size
    out = Path(workdir, OVERLAY_FILE)
    if overlay.get("picture"):
        with Image.open(overlay["picture"]) as image:
            image = image.convert("RGBA")
            target = max(16, width // 5)
            image.resize((target, max(1, round(image.height * target / image.width))), Image.LANCZOS).save(out)
        return out
    text = str(overlay.get("text") or "").strip()
    font_size = max(14, min(width, height) // 14)
    font = None
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Roboto-Regular.ttf", "Helvetica.ttc"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(font_size)
    stroke = max(1, font_size // 12)
    box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font, stroke_width=stroke)
    image = Image.new("RGBA", (box[2] - box[0] + 2 * stroke, box[3] - box[1] + 2 * stroke), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((stroke - box[0], stroke - box[1]), text, font=font, fill="white",
                               stroke_width=stroke, stroke_fill="black")
    image.save(out)
    return out


def video_graph(filters, overlay_place=None, passage=None, tail=()):
    """The -vf of the picture: a passage sped up (start, end, speed), then `filters`, then the overlay file
    (OVERLAY_FILE in the work folder) at `overlay_place`, then `tail`."""
    graph = ""
    if passage:
        graph = _passage_graph(passage, video=True) + "[pv];[pv]"
    graph += ",".join(filters or ["null"])
    if overlay_place:
        graph += "[base];movie=%s[logo];[base][logo]overlay=%s" % (OVERLAY_FILE, OVERLAY_PLACES[overlay_place])
    if tail:
        graph += "[top];[top]" + ",".join(tail)
    return graph


def _passage_graph(passage, video):
    """A passage of the input played at another speed: the parts before, during and after it, joined."""
    start, end, speed = passage
    parts = ([(0.0, start, 1.0)] if start > 0 else []) + [(start, end, speed)] + (
        [(end, None, 1.0)] if end is not None else [])
    split, trim, setpts = ("split", "trim", "setpts") if video else ("asplit", "atrim", "asetpts")
    chains = ["%s=%d%s" % (split, len(parts), "".join("[s%d]" % i for i in range(len(parts))))]
    for i, (a, b, rate) in enumerate(parts):
        bounds = ":".join(x for x in ("start=%.3f" % a if a else "", "end=%.3f" % b if b is not None else "") if x)
        chain = "[s%d]%s%s,%s=PTS-STARTPTS" % (i, trim, "=" + bounds if bounds else "", setpts)
        if rate != 1:
            chain += ",setpts=PTS/%.6g" % rate if video else "," + atempo_chain(rate)
        chains.append(chain + "[p%d]" % i)
    chains.append("".join("[p%d]" % i for i in range(len(parts)))
                  + "concat=n=%d:v=%d:a=%d" % (len(parts), 1 if video else 0, 0 if video else 1))
    return ";".join(chains)


def preview_picture(src, info, options, seconds, workdir):
    """One picture of the edited video at `seconds` of the file (crop, turn, smaller picture, text or logo), as
    JPG bytes, to see it before saving."""
    workdir = Path(workdir)
    filters = []
    if options.get("turn"):
        filters.append(TURNS[options["turn"]])
    crop = options.get("crop")
    if crop == "bars":
        bars = detect_black_bars(src, seconds, 60)
        if bars:
            filters.append(bars)
    elif crop:
        filters.append(CROPS[crop])
    if options.get("height"):
        filters.append(fit_box(options["height"] * 16 // 9) + ":force_divisible_by=2")
    place = None
    if options.get("overlay"):
        make_overlay(options["overlay"], output_size(info, options), workdir)
        place = options.get("overlay_place") or "bottom-right"
    out = workdir / "picture.jpg"
    run_ffmpeg(["-ss", "%.3f" % seconds, "-i", str(src), "-frames:v", "1",
                "-vf", video_graph(filters, place, tail=["scale=w='min(960,iw)':h=-2"]), "-q:v", "3", "-y",
                str(out)], workdir)
    return out.read_bytes()


def small_picture(src, seconds, workdir, width=160):
    """A small picture of `src` at `seconds` (a song: its cover art), as JPG bytes, for the row of a list."""
    out = Path(workdir, "small.jpg")
    run_ffmpeg(["-ss", "%.3f" % seconds, "-i", str(src), "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-q:v", "5",
                "-y", str(out)], workdir)
    return out.read_bytes()


def save_frame(src, seconds, out, workdir):
    """The picture of a video at `seconds`, at full size (a PNG): a frame taken from the preview."""
    run_ffmpeg(["-ss", "%.3f" % seconds, "-i", str(src), "-frames:v", "1", "-y", str(out)], workdir)
    return out


# A subtitle track's codec (as ffmpeg names it) -> the file it is saved as; pictures of text (Blu-ray) stay
# pictures (.sup), a DVD's cannot be saved on their own.
SUBTITLE_FILES = {"subrip": "srt", "srt": "srt", "mov_text": "srt", "text": "srt", "ass": "ass", "ssa": "ass",
                  "webvtt": "vtt", "hdmv_pgs_subtitle": "sup"}


def extract_subtitles(src, index, codec, workdir):
    """The subtitle track `index` of `src` (its codec: `codec`) as a file of its own; returns it."""
    ext = SUBTITLE_FILES.get(codec)
    if ext is None:
        raise RuntimeError("These subtitles are pictures (%s): they cannot be saved as a file." % codec)
    out = Path(workdir, "subtitles." + ext)
    same = codec not in ("mov_text", "text")  # copied as they are; an MP4's own are made into .srt
    run_ffmpeg(["-i", str(src), "-map", "0:s:%d" % index] + (["-c", "copy"] if same else []) + [str(out)], workdir)
    return out


def _subtitles_for_output(subs, start, speed, workdir):
    """The subtitle file to add, with its times moved like the picture (part kept, speed): a new .srt.
    An .ass file keeps its styles, so it can only be used as it is."""
    subs = Path(subs)
    if subs.suffix.lower() in (".ass", ".ssa"):
        if start or speed != 1:
            raise RuntimeError("Styled subtitles (.ass) can only be added to the whole video at normal speed: "
                               "use an .srt or .vtt file to cut or change the speed.")
        out = Path(workdir, "added" + subs.suffix.lower())
        shutil.copyfile(subs, out)
        return out
    cues = [(s / speed, e / speed, body) for s, e, body in
            parse_cues(subs.read_text(encoding="utf-8", errors="replace"))]
    text = cues_to_srt(cues, start / speed)
    if not text.strip():
        raise RuntimeError("The subtitle file has no subtitles for this part of the video.")
    out = Path(workdir, "added.srt")
    out.write_text(text, encoding="utf-8")
    return out


def _run_for_log(args):
    """ffmpeg's messages (what the cropdetect and silencedetect filters print)."""
    return subprocess.run([FFMPEG_BIN, "-hide_banner", "-nostats"] + args, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=900, **NO_WINDOW).stderr


def detect_black_bars(src, start, length):
    """The crop removing the black bars of the picture ("crop=w:h:x:y"), read in a minute of it; None when
    there are none."""
    skip = start + min(30.0, (length or 0) * 0.1)  # past a black opening
    text = _run_for_log(["-ss", "%.3f" % skip, "-t", "60", "-i", str(src), "-map", "0:v:0",
                         "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"])
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", text)
    if not found:
        return None
    w, h, x, y = collections.Counter(found[len(found) // 2:]).most_common(1)[0][0]
    if x == "0" and y == "0":
        return None
    return "crop=%s:%s:%s:%s" % (w, h, x, y)


def detect_silences(src, input_args, audio_index, length):
    """The parts kept once the silences are removed, as [(start, end)] in seconds of the input (after the
    part chosen); None when there is no silence."""
    text = _run_for_log(input_args + ["-map", "0:a:%d" % audio_index, "-af",
                                      "silencedetect=noise=%ddB:d=%g" % (SILENCE_DB, SILENCE_SECONDS),
                                      "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", text)]
    if not starts:
        return None
    kept, position = [], 0.0
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else length
        if start + SILENCE_KEPT > position:
            kept.append((position, start + SILENCE_KEPT))
        position = max(position, (end or start) - SILENCE_KEPT)
    if length is None or length > position:
        kept.append((position, length if length is not None else 10 ** 7))
    return [(a, b) for a, b in kept if b - a > 0.05] or None


COVER_SIDE = 1400  # pixels at most on the longer side of a cover art chosen in the Edit tab


def cover_picture(path):
    """A picture file as cover art for tag_file: (bytes, mime). It is checked, then written again as a plain JPEG
    (colours on screen, without transparency, COVER_SIDE pixels at most): some players stop playing a sound file
    whose cover is a huge, CMYK or unusual picture. RuntimeError when it is not a picture that can be read."""
    data = Path(path).read_bytes()
    if not HAS_PIL:
        if data[:3] == b"\xff\xd8\xff":
            return data, "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data, "image/png"
        raise RuntimeError("Choose a JPG or PNG picture for the cover art.")
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()  # a damaged or cut picture fails here
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, "white")
                flat.paste(image, mask=image.getchannel("A"))
            else:
                flat = image.convert("RGB")
            flat.thumbnail((COVER_SIDE, COVER_SIDE))
            out = io.BytesIO()
            flat.save(out, "JPEG", quality=90)
    except Exception as exc:
        raise RuntimeError("This picture cannot be used as cover art (%s): choose a JPG or PNG picture." % exc)
    return out.getvalue(), "image/jpeg"


def _source_cover(src, workdir):
    """The cover art of `src` (an attached picture), as (bytes, mime), or None."""
    out = Path(workdir, "source_cover.img")
    try:
        run_ffmpeg(["-i", str(src), "-map", "0:v:0", "-frames:v", "1", "-c", "copy", "-f", "image2", str(out)],
                   workdir)
        return cover_picture(out)
    except (RuntimeError, OSError):
        return None


def edit_media(src, fmt, info, options, workdir, notify):
    """Write `src` as `fmt` with the changes of `options`; returns (the new file in workdir, a note for the
    user or "").

    options: clip (start, end or None), height (picture height, smaller only), speed, audio_keep and
    subs_keep (the indexes of the sound and subtitle tracks kept, all of them when missing; an audio file
    takes the first sound track kept), subs_file + subs_mode ("burn": drawn onto the picture, "track": a track
    players can turn off), tags ({"title"…} to write; "" removes one), max_bytes, no_sound (a video without
    its sound: smaller), crop ("9:16", "1:1", "16:9" or "bars": the black bars found), turn (see TURNS),
    loudness (the volume evened out), no_silence (the silences cut out), cover (a sound file's new cover art:
    a picture's path, or "remove"; else the file's own is kept), subs_style (how subtitles drawn onto the
    picture look: see SUBTITLE_STYLES), passage ((start, end, speed) in the file: a part played
    faster or slower), fade_in and fade_out (seconds of sound faded at the start and the end), overlay ({"text"}
    or {"picture": path}: put on the picture at overlay_place, see OVERLAY_PLACES).
    """
    src, workdir = Path(src), Path(workdir)
    clip, height, speed = options.get("clip"), options.get("height"), options.get("speed") or 1.0
    audio_keep, subs_keep = options.get("audio_keep"), options.get("subs_keep")
    subs_file, subs_mode = options.get("subs_file"), options.get("subs_mode")
    tags, max_bytes = options.get("tags") or {}, options.get("max_bytes")
    no_sound = (bool(options.get("no_sound")) or audio_keep == []) and fmt in VIDEO_FORMATS
    tracks_kept = audio_keep is None and subs_keep is None
    start, end = clip or (0.0, None)
    audio_out, gif = fmt in AUDIO_FORMATS, fmt == "gif"
    same_format = src.suffix.lower() == "." + fmt
    burn = bool(subs_file) and subs_mode == "burn" and not audio_out
    track = bool(subs_file) and subs_mode == "track" and not audio_out and not gif
    crop, turn = (options.get("crop"), options.get("turn")) if not audio_out else (None, None)
    has_sound = bool(info.get("audio")) and not no_sound
    loudness = bool(options.get("loudness")) and has_sound
    no_silence = bool(options.get("no_silence")) and has_sound
    if no_silence and subs_file:
        raise RuntimeError("Subtitles cannot be added while the silences are cut out: their times would not "
                           "match any more.")
    passage = None
    if options.get("passage") and not gif:
        a, b, rate = options["passage"]
        a, b = max(a - start, 0.0), (b - start) if b is not None else None
        whole = (end or info.get("duration") or 0) - start
        if b is not None and whole and b >= whole:
            b = None  # to the end
        if (b is None or b > a) and rate != 1:
            passage = (a, b, rate)
    if passage and (no_silence or subs_file):
        raise RuntimeError("A passage at another speed cannot go with the silences cut out or subtitles added: "
                           "their times would not match any more.")
    overlay = options.get("overlay") or {}
    overlay = overlay if not audio_out and (str(overlay.get("text") or "").strip() or overlay.get("picture")) else None
    picture_changes = bool(clip or height or speed != 1 or burn or crop or turn or (no_silence and not audio_out)
                           or passage or overlay)
    cover = options.get("cover") if audio_out else None
    new_cover = cover_picture(cover) if cover not in (None, "remove") else None

    # Only new tags or cover art, in a format that holds them: a copy with its tags rewritten.
    fades = has_sound and bool(options.get("fade_in") or options.get("fade_out"))
    if (same_format and not picture_changes and tracks_kept and not subs_file and not no_sound and not loudness
            and not no_silence and not fades and cover != "remove" and (tags or new_cover)
            and src.suffix.lower() in TAGGABLE
            and all(tags.values())):
        notify(None, "Writing the tags…")
        out = workdir / ("edited" + src.suffix.lower())
        shutil.copyfile(src, out)
        tag_file(out, dict(tags, cover=new_cover) if new_cover else tags)
        return (fit_in_size(out, max_bytes, workdir, notify) if max_bytes else out), ""
    if gif and not picture_changes:
        return make_gif(src, workdir / "edited.gif", workdir, notify, max_bytes, options.get("bounce")), ""
    if BACKEND != "ffmpeg":
        return _edit_with_pyav(src, fmt, info, options, workdir, notify), ""

    # A GIF is made from a video with the other changes already done.
    middle_fmt = "mp4" if gif else fmt
    out = workdir / ("edited." + middle_fmt)
    length = ((end or info.get("duration") or 0) - start) / speed or None
    added = _subtitles_for_output(subs_file, start, speed, workdir) if (burn or track) else None

    args = (["-ss", "%.3f" % start] if start else []) + (["-to", "%.3f" % end] if end else []) + ["-i", str(src)]
    if track:
        args += ["-i", str(added)]
    video_filters, audio_filters = [], []
    if no_silence:
        notify(None, "Finding the silences…")
        kept_parts = detect_silences(src, args[:-2] + ["-i", str(src)], audio_keep[0] if audio_keep else 0,
                                     (end or info.get("duration") or 0) - start or None)
        if kept_parts:
            between = "+".join("between(t,%.3f,%.3f)" % part for part in kept_parts)
            video_filters.append("select='%s',setpts=N/FRAME_RATE/TB" % between)
            audio_filters.append("aselect='%s',asetpts=N/SR/TB" % between)
            length = sum(b - a for a, b in kept_parts) / speed
    if passage and length:
        a, b, rate = passage
        b = b if b is not None else length * speed
        length = (length * speed - (b - a) + (b - a) / rate) / speed
    if turn:
        video_filters.append(TURNS[turn])
    if crop == "bars":
        notify(None, "Finding the black bars…")
        bars = detect_black_bars(src, start, (end or info.get("duration") or 0) - start)
        if bars:
            video_filters.append(bars)
    elif crop:
        video_filters.append(CROPS[crop])
    if height:
        video_filters.append(fit_box(height * 16 // 9) + ":force_divisible_by=2")
    if speed != 1:
        video_filters.append("setpts=PTS/%.6g" % speed)
        if not no_sound:
            audio_filters.append(atempo_chain(speed))
    if burn:
        # ffmpeg runs in workdir: no path to escape.
        video_filters.append("subtitles=%s:force_style='%s'" % (added.name, force_style(options.get("subs_style"))))
    if loudness:
        audio_filters.append(LOUDNESS)
    fade_in, fade_out = (options.get("fade_in"), options.get("fade_out")) if has_sound else (None, None)
    if fade_in:
        audio_filters.append("afade=t=in:st=0:d=%g" % fade_in)
    if fade_out and length:
        audio_filters.append("afade=t=out:st=%.3f:d=%g" % (max(length - fade_out, 0), fade_out))
    place = None
    if overlay:
        make_overlay(overlay, output_size(info, options), workdir)
        place = options.get("overlay_place") or "bottom-right"
    note = ""
    if audio_out:
        streams = ["-map", "0:a:%d" % (audio_keep[0] if audio_keep else 0), "-vn"]
        codecs = AUDIO_ARGS.get("." + fmt, [])
    else:
        streams = ["-map", "0:v:0"]
        if no_sound:
            pass
        elif audio_keep is None:
            streams += ["-map", "0:a?"]
        else:
            for index in audio_keep:
                streams += ["-map", "0:a:%d" % index]
        tracks = info.get("subtitles") or []
        # Subtitle tracks would not match the picture once the silences are cut out.
        kept = [] if gif or no_silence else [i for i in range(len(tracks)) if subs_keep is None or i in subs_keep]
        text_codec = EMBED_CODECS.get("." + middle_fmt)
        # Kept as they are in the same format; text subtitles are converted for another one (picture
        # subtitles, like a DVD's, cannot be).
        keep = bool(kept) and (same_format or (
            text_codec is not None and all(tracks[i]["codec"] in TEXT_SUBTITLES for i in kept)))
        if keep:
            for index in kept:
                streams += ["-map", "0:s:%d" % index]
        elif kept:
            note = "The subtitle tracks were left out: they do not fit the new format."
        if track:
            streams += ["-map", "1:0"]
        extra = []  # subtitle codecs and MP4 options, the same whether the streams are copied or not
        if keep:
            extra += ["-c:s", "copy" if same_format else text_codec]
        if track:
            extra += ["-c:s:%d" % (len(kept) if keep else 0), text_codec or "mov_text"]
        if middle_fmt in ("mp4", "mov"):
            extra += ["-movflags", "+faststart"]
        reencode = REENCODE_ARGS["webm" if middle_fmt == "webm" else "mp4"] + extra
        sound_codec = reencode[reencode.index("-c:a"):]  # the volume evened out: only the sound is re-encoded
        codecs = (reencode if picture_changes else ["-c:v", "copy"] + sound_codec if audio_filters
                  else ["-c:v", "copy", "-c:a", "copy"] + extra)
    sound_graph = ",".join(audio_filters)
    if passage and has_sound:
        sound_graph = _passage_graph(passage, video=False) + ("[pa];[pa]" + sound_graph if sound_graph else "")
    filters = (["-vf", video_graph(video_filters, place, passage)]
               if (video_filters or place or passage) and not audio_out else []) + \
              (["-af", sound_graph] if sound_graph else [])
    metadata = []
    for key, value in tags.items():
        metadata += ["-metadata", "%s=%s" % (key, value)]
    if clip or speed != 1 or no_silence or passage:
        metadata += ["-map_chapters", "-1"]  # the chapters would point to the wrong moments

    def encode(codec_args, message):
        ffmpeg_with_progress(args + streams + filters + codec_args + metadata + [str(out)], workdir, notify,
                             message, length)

    if audio_out or picture_changes or sound_graph:
        encode(codecs, "Editing…")
    else:
        # Nothing changes in the picture or the sound: streams copied (fast, lossless) when the new
        # format can hold them, re-encoded otherwise.
        try:
            notify(None, "Copying the streams…")
            run_ffmpeg(args + streams + codecs + metadata + [str(out)], workdir)
        except RuntimeError:
            out.unlink(missing_ok=True)
            encode(reencode, "Re-encoding (slow)…")

    if gif:
        out = make_gif(out, workdir / "edited.gif", workdir, notify, max_bytes, options.get("bounce"))
    elif max_bytes:
        out = fit_in_size(out, max_bytes, workdir, notify)
    if audio_out and cover != "remove":
        # A sound file loses its cover art when converted: the file's own goes back in, or the new one.
        picture = new_cover or (_source_cover(src, workdir) if info.get("cover") else None)
        if picture:
            tag_file(out, {"cover": picture})
    return out, note


def _edit_with_pyav(src, fmt, info, options, workdir, notify):
    """Without ffmpeg (a phone): the part kept, another format (a sound file is converted, a video copied into
    another container), no sound, tags, cover art and a size limit. The other changes need ffmpeg (the Edit tab
    does not offer them)."""
    from core.pyav import av_cover, av_to_audio, av_trim
    clip = options.get("clip")
    out = Path(workdir, "edited." + fmt)
    if fmt in AUDIO_FORMATS:
        av_to_audio(src, out, fmt, notify, clip=clip)
    else:
        av_trim(src, out, clip or (0.0, None), notify, "Copying…" if not clip else "Cutting…",
                sound=not options.get("no_sound"))
    tags = options.get("tags") or {}
    cover = options.get("cover") if fmt in AUDIO_FORMATS else None
    picture = cover_picture(cover) if cover not in (None, "remove") else None
    if fmt in AUDIO_FORMATS and cover is None and info.get("cover"):
        picture = av_cover(src)  # a sound file keeps its own cover art when converted
    if out.suffix.lower() in TAGGABLE and (tags or picture):
        notify(None, "Writing the tags…")
        tag_file(out, dict({k: v for k, v in tags.items() if v}, **({"cover": picture} if picture else {})))
    if options.get("max_bytes"):
        out = fit_in_size(out, options["max_bytes"], workdir, notify)
    return out


def join_media(paths, fmt, infos, workdir, notify):
    """`paths` one after the other in a single `fmt` file (infos: their probe_media); returns it.

    The same kind of files (same container, codecs and picture size) are joined as they are: fast and
    lossless. Others are re-encoded, each picture fitted in the first one's frame (black bars around), a file
    without sound getting silence."""
    workdir = Path(workdir)
    audio_out = fmt in AUDIO_FORMATS
    if not audio_out and not all(info["video"] for info in infos):
        names = [Path(p).name for p, info in zip(paths, infos) if not info["video"]]
        raise RuntimeError("%s has no picture: join the files as sound (MP3, M4A…) instead." % names[0])
    out = workdir / ("joined." + fmt)
    length = sum(info["duration"] or 0 for info in infos) or None
    same = len({(Path(p).suffix.lower(), i["video"], i["audio"], i["width"], i["height"]) for p, i in zip(paths, infos)}) == 1
    if same and Path(paths[0]).suffix.lower() == "." + fmt:
        listing = workdir / "files.txt"
        listing.write_text("".join("file '%s'\n" % str(Path(p).resolve()).replace("'", "'\\''") for p in paths),
                           encoding="utf-8")
        try:
            notify(None, "Joining the files…")
            run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v?", "-map", "0:a?",
                        "-c", "copy", str(out)], workdir)
            return out
        except RuntimeError:
            out.unlink(missing_ok=True)
    args, chains, labels = [], [], []
    width, height = next(((i["width"], i["height"]) for i in infos if i["height"]), (1280, 720))
    width, height = width - width % 2, height - height % 2
    for index, (path, info) in enumerate(zip(paths, infos)):
        args += ["-i", str(path)]
    silent = len(paths)  # the inputs of silence, after the files
    for index, (path, info) in enumerate(zip(paths, infos)):
        if info["audio"]:
            sound = "[%d:a:0]" % index
        else:
            args += ["-f", "lavfi", "-t", "%.3f" % (info["duration"] or 1), "-i", "anullsrc=r=48000:cl=stereo"]
            sound, silent = "[%d:a]" % silent, silent + 1
        chains.append("%saresample=48000,aformat=channel_layouts=stereo[a%d]" % (sound, index))
        if not audio_out:
            chains.append("[%d:v:0]scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,"
                          "setsar=1,fps=30,format=yuv420p[v%d]" % (index, width, height, width, height, index))
            labels.append("[v%d][a%d]" % (index, index))
        else:
            labels.append("[a%d]" % index)
    chains.append("%sconcat=n=%d:v=%d:a=1%s" % ("".join(labels), len(paths), 0 if audio_out else 1,
                                                "[a]" if audio_out else "[v][a]"))
    if audio_out:
        streams, codecs = ["-map", "[a]"], AUDIO_ARGS.get("." + fmt, [])
    else:
        streams = ["-map", "[v]", "-map", "[a]"]
        codecs = REENCODE_ARGS["webm" if fmt == "webm" else "mp4"] + (
            ["-movflags", "+faststart"] if fmt in ("mp4", "mov") else [])
    ffmpeg_with_progress(args + ["-filter_complex", ";".join(chains)] + streams + codecs + [str(out)], workdir,
                         notify, "Joining the files…", length)
    return out
