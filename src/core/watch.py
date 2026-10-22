"""The watched folders (Settings, computer): every video or sound file put into one is converted with its preset
of the Download tab, into its "Converted" folder. The original is kept unless chosen otherwise; a file already
converted is not converted again."""

import os
from pathlib import Path

from core.backend import VIDEO_FORMATS
from core.compress import MB
from core.editor import EDIT_AUDIO_FORMATS, MEDIA_EXTENSIONS

CONVERTED = "Converted"
WATCH_INTERVAL = 5  # seconds between two looks at the folder


def can_convert(preset):
    """Whether a preset makes a file the Edit tab can write (not "Original", a GIF or an image)."""
    return preset.get("fmt") in VIDEO_FORMATS or preset.get("fmt") in EDIT_AUDIO_FORMATS


def converted_path(file, fmt):
    return Path(file).parent / CONVERTED / (Path(file).stem + "." + fmt)


def ready_files(folder, fmt, sizes):
    """The files of `folder` to convert now: media files not converted yet, whose size has not changed since the
    last look (`sizes`: path -> size, updated here), so that a file still being copied is left alone."""
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in sorted(names):
        path = Path(folder, name)
        if path.suffix.lower().lstrip(".") not in MEDIA_EXTENSIONS or not path.is_file():
            continue
        if converted_path(path, fmt).exists():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size and sizes.get(str(path)) == size:
            found.append(path)
        sizes[str(path)] = size
    return found


def preset_options(preset, info):
    """edit_media's options for a preset: picture size, size limit, sound, crop, turn…"""
    video = preset["fmt"] in VIDEO_FORMATS
    options = {"loudness": preset.get("loudness"), "no_silence": preset.get("no_silence")}
    if video:
        quality = str(preset.get("quality") or "")
        if quality.isdigit() and int(quality) < (info.get("height") or 0):
            options["height"] = int(quality)
        options.update(no_sound=preset.get("no_sound"), crop=preset.get("crop"), turn=preset.get("turn"))
    if preset.get("max_mb"):
        options["max_bytes"] = int(preset["max_mb"] * MB)
    return {key: value for key, value in options.items() if value}


def watched_folders(settings):
    """[{"dir", "preset" (its name), "delete" (the original once converted)}] of Settings; the single folder of
    an older version becomes the first one."""
    folders = settings.get("watch_folders")
    if not isinstance(folders, list):
        folders = [{"dir": settings["watch_dir"], "preset": settings.get("watch_preset"), "delete": False}] \
            if settings.get("watch_dir") else []
    return [f for f in folders if isinstance(f, dict) and f.get("dir")]
