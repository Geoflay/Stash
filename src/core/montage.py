"""A montage: passages of several videos (or several passages of one) one after the other in a single file. Each
passage is downloaded on its own (only that part), or cut from a file of the device, then they are joined (see
editor.join_media)."""

from pathlib import Path

from core.download import download_media
from core.editor import edit_media, join_media, probe_media


def make_montage(parts, fmt, quality, workdir, cookies_browser="", notify=None, cookie_file=None):
    """`parts`: [{"url" (or "path": a file of the device), "title", "clip": [start, end or None] or None}], in their
    order; returns the file made."""
    notify = notify or (lambda *a: None)
    paths = []
    for index, part in enumerate(parts):
        folder = Path(workdir, "part%d" % index)
        folder.mkdir(parents=True, exist_ok=True)

        def report(fraction, message, detail=None, index=index):
            notify(fraction, "Part %d of %d: %s" % (index + 1, len(parts), message), detail)
        clip = tuple(part["clip"]) if part.get("clip") else None
        if part.get("path"):  # a file of the device: its passage cut (all of it as it is)
            path = Path(part["path"])
            if clip:
                path, _ = edit_media(path, fmt, probe_media(path), {"clip": clip}, folder, report)
            paths.append(path)
            continue
        paths.append(download_media(part["url"], fmt, quality, folder, cookies_browser, report, cookie_file,
                                    clip=clip, tags=False))
    infos = [probe_media(path) for path in paths]
    return join_media(paths, fmt, infos, workdir, notify)
