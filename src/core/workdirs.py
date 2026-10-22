"""Work folders where downloads are prepared, and the unfinished downloads kept for
resuming them after the app is closed (queue.json)."""

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from core.storage import read_json, write_json


def workdir_parent():
    parent = os.environ.get("FLET_APP_STORAGE_TEMP")
    if parent:
        os.makedirs(parent, exist_ok=True)
        return parent
    return None


WORKDIR_PREFIX = "videodl_"
STALE_WORKDIR_AGE = 24 * 3600


def purge_stale_workdirs(keep=()):
    """Remove work folders left behind by a crash or a forced close (previews are never cleaned otherwise),
    except `keep`: the folders of unfinished downloads that can still be resumed."""
    limit = time.time() - STALE_WORKDIR_AGE
    keep = {os.path.normcase(os.path.abspath(k)) for k in keep}
    try:
        for folder in Path(workdir_parent() or tempfile.gettempdir()).glob(WORKDIR_PREFIX + "*"):
            if os.path.normcase(os.path.abspath(folder)) in keep:
                continue
            if folder.is_dir() and folder.stat().st_mtime < limit:
                shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


def clean_temporary_files(keep=()):
    """Settings > Clean up: every work folder (downloads, previews) except `keep`; returns (folders, bytes)."""
    keep = {os.path.normcase(os.path.abspath(k)) for k in keep}
    count = size = 0
    for folder in Path(workdir_parent() or tempfile.gettempdir()).glob(WORKDIR_PREFIX + "*"):
        if not folder.is_dir() or os.path.normcase(os.path.abspath(folder)) in keep:
            continue
        size += sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        shutil.rmtree(folder, ignore_errors=True)
        count += 1
    return count, size


# Downloads of the queue that have not finished, saved so they can be resumed after the app is closed.
PENDING_FILE = "queue.json"
_FORMAT_PART = re.compile(r"\.f[\w-]+\.\w+$")  # "media.f399.mp4": one stream, complete, of a split download


def load_pending_downloads():
    data = read_json(PENDING_FILE, [])
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("url") and d.get("fmt") and d.get("workdir")]


def save_pending_downloads(items):
    try:
        write_json(PENDING_FILE, list(items))
    except OSError:
        pass


def clean_for_resume(workdir):
    """Keep what yt-dlp resumes on its own (partial downloads, complete streams of a split download) and
    delete the rest: a merge or conversion stopped halfway would otherwise be taken as the result."""
    for path in Path(workdir).iterdir():
        name = path.name
        if path.is_file() and not (".part" in name or name.endswith(".ytdl") or _FORMAT_PART.search(name)):
            path.unlink(missing_ok=True)


@contextlib.asynccontextmanager
async def work_folder():
    """A work folder of its own for one job (an edit, a picture…), deleted once it is done."""
    path = tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=workdir_parent())
    try:
        yield Path(path)
    finally:
        await asyncio.to_thread(shutil.rmtree, path, True)
