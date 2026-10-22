"""One file per chapter: a video with chapters (an album, a course…) cut into numbered files. Chapters are also
read from the times listed in a description ("0:00 Intro", "3:12 Second song"…) when the site gives none."""

import re
import shutil
import zipfile
from pathlib import Path

from core.filenames import safe_filename, unique_path
from core.ffmpeg import run_ffmpeg
from core.jobs import entering_conversion
from core.music import track_tags

# "0:00 Title", "[1:02:03] Title", "Title - 3:12": a time at the start or at the end of a line.
_TIME = r"[(\[]?((?:\d{1,2}:)?\d{1,2}:\d{2})[)\]]?"
_LINE_STARTS = re.compile(r"^\s*%s\s*[-–—:.|]?\s*(.+?)\s*$" % _TIME)
_LINE_ENDS = re.compile(r"^\s*(.+?)\s*[-–—:|]?\s*%s\s*$" % _TIME)


def chapter_list(info):
    """The chapters of yt-dlp's info as [{"title", "start", "end"}] (seconds), without empty ones."""
    chapters = []
    for index, chapter in enumerate(info.get("chapters") or [], 1):
        start, end = chapter.get("start_time"), chapter.get("end_time")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            chapters.append({"title": str(chapter.get("title") or "").strip() or "Chapter %d" % index,
                             "start": float(start), "end": float(end)})
    return chapters or chapters_from_description(info.get("description"), info.get("duration"))


def _seconds(text):
    total = 0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


def chapters_from_description(description, duration):
    """The chapters listed in a description, one time per line (at least 3, from 0:00, in order); []."""
    if not description or not duration:
        return []
    marks = []
    for line in description.splitlines():
        found = _LINE_STARTS.match(line)
        start, title = (found.group(1), found.group(2)) if found else (None, None)
        if not found:
            found = _LINE_ENDS.match(line)
            if found:
                title, start = found.group(1), found.group(2)
        if found:
            marks.append((_seconds(start), title.strip(" -–—:|")))
    starts = [start for start, _ in marks]
    if len(marks) < 3 or starts[0] > 1 or starts != sorted(set(starts)) or starts[-1] >= duration:
        return []
    return [{"title": title or "Chapter %d" % index, "start": float(start),
             "end": float(marks[index][0] if index < len(marks) else duration)}
            for index, (start, title) in enumerate(marks, 1)]


def split_chapters(path, chapters, workdir, notify, speed=1.0, tags=None):
    """Cut `path` into one file per chapter, named "01 - Title.ext", in a new folder that is returned.

    The parts are copied, not re-encoded: fast and lossless, but a video part starts at the keyframe
    before its chapter (a second or two early), like yt-dlp's own split. `speed`: the file was sped up,
    so the chapter times are divided by it. `tags`: {"album": …, "artist": …} written into every part, with
    the chapter's title and track number; a chapter named "Artist - Title" has its own artist (None: no tags).
    """
    entering_conversion()
    path = Path(path)
    out_dir = Path(workdir, "chapters")
    out_dir.mkdir(exist_ok=True)
    total = len(chapters)
    for index, chapter in enumerate(chapters, 1):
        notify((index - 1) / total, "Splitting into chapters (%d of %d)…" % (index, total))
        start, end = chapter["start"] / speed, chapter["end"] / speed
        out = out_dir / safe_filename("%02d - %s" % (index, chapter["title"]), path.suffix)
        metadata = []
        if tags is not None:
            artist, title = track_tags(chapter["title"])
            values = dict(tags, title=title, track="%d/%d" % (index, total))
            if artist:
                values["artist"] = artist
            for key, value in values.items():
                if value:
                    metadata += ["-metadata", "%s=%s" % (key, value)]
        # The cover art (a picture stream, or an MKV attachment) is copied into every part.
        run_ffmpeg(["-ss", "%.3f" % start, "-i", str(path), "-t", "%.3f" % (end - start),
                    "-map", "0:v?", "-map", "0:a?", "-map", "0:t?", "-map_chapters", "-1", "-c", "copy"]
                   + metadata + [str(out)], workdir)
    return out_dir


def save_folder(folder, parent, name):
    """Copy the parts into a new folder `name` inside `parent` ("name (1)" if it exists); returns it."""
    while True:
        target = unique_path(parent, name)
        try:
            shutil.copytree(folder, target)
            return target
        except FileExistsError:  # made at the same moment by another download
            continue


def zip_folder(folder, zip_path, name):
    """The files of `folder` packed into one .zip, inside a folder called `name` (no compression: media
    files do not shrink)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(Path(folder).iterdir()):
            archive.write(str(path), "%s/%s" % (name, path.name))
    return Path(zip_path)
