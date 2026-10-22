"""The watched folders of Settings (computer): the files put into one are converted with its preset, one after the
other, sharing the conversion limit with the downloads (see core/watch.py)."""

import asyncio
import shutil
import threading

from core.editor import edit_media, probe_media
from core.errors import clean_error
from core.watch import WATCH_INTERVAL, can_convert, converted_path, preset_options, ready_files, watched_folders
from core.workdirs import work_folder
from ui.presets import saved_presets


def watch_preset(app, name):
    """The preset of a watched folder, when it still exists and can be used."""
    preset = next((p for p in saved_presets(app) if p["name"] == name), None)
    return preset if preset is not None and can_convert(preset) else None


async def convert(app, path, preset, cancelled):
    """Convert one file into the "Converted" folder; returns a line for Settings."""
    slot = await app.conversion_slots.acquire(cancelled)
    try:
        async with work_folder() as workdir:
            info = await asyncio.to_thread(probe_media, path)
            out, _ = await asyncio.to_thread(edit_media, path, preset["fmt"], info, preset_options(preset, info),
                                             workdir, lambda *a: None)
            target = converted_path(path, preset["fmt"])
            await asyncio.to_thread(target.parent.mkdir, exist_ok=True)
            await asyncio.to_thread(shutil.copyfile, str(out), str(target))
    finally:
        if slot:
            app.conversion_slots.release()
    await app.notify_if_away("File converted", str(target))
    return "Converted: %s" % target


async def watch_folder(app):
    """While the app is open, look at the watched folders every few seconds."""
    sizes, failed = {}, set()
    cancelled = threading.Event()  # never set: the conversions finish even if the folder is changed
    while True:
        await asyncio.sleep(WATCH_INTERVAL)
        for folder in watched_folders(app.settings):
            preset = watch_preset(app, folder.get("preset"))
            if preset is None:
                continue
            for path in await asyncio.to_thread(ready_files, folder["dir"], preset["fmt"], sizes):
                if str(path) in failed:
                    continue
                try:
                    note = await convert(app, path, preset, cancelled)
                    if folder.get("delete"):  # chosen in Settings: only the converted copy is kept
                        await asyncio.to_thread(path.unlink, True)
                except Exception as exc:
                    failed.add(str(path))
                    note = "Could not convert %s: %s" % (path.name, clean_error(exc))
                app.settings_tab.set_watch_note(note)
                app.page.update()
