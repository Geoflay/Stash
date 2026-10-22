"""The Queue tab: every download started from the Download tab (one video, or the items of a list), its
progress and its result.

Downloads wait for a free slot (see core/jobs.py), in the order of the list (dragged to move them). Unfinished
ones are saved in queue.json, so they can be resumed after the app is closed. A paused download, or one that
failed because the connection dropped, starts again from the part already downloaded. The items of a list
downloaded together make one notification once they are all done (Settings), and a playlist file (.m3u8).
"""

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

import flet as ft
from yt_dlp.utils import DownloadError

from core.backend import VIDEO_FORMATS
from core.chapters import save_folder, split_chapters, zip_folder
from core.compress import MB
from core.cookies import saved_cookies_path
from core.download import download_media
from core.errors import Cancelled, clean_error, error_report, is_youtube_block, make_hint
from core.filenames import file_type, filename_fields, render_filename, reserve_file, safe_filename
from core.history import add_history, cache_thumbnail
from core.links import is_youtube_url
from core.live import record_live, wait_for_live
from core.montage import make_montage
from core.mediaserver import episode_place, fetch_thumbnail, write_extras
from core.music import guess_song
from core.network import DEFAULT_RESTARTS, RESTART_CHOICES, call_with_retries, is_transient
from core.playlist import PLAYLIST_PAUSES, add_to_m3u
from core.slideshow import download_photos, download_slideshow
from core.subtitles import (
    TRANSCRIPT_MODES, apply_subtitles, fetch_several, fetch_subtitles, make_transcript, split_subtitle_key,
    subtitle_tag,
)
from core.system import default_download_dir, show_in_folder
from core.units import format_duration
from core.workdirs import (
    WORKDIR_PREFIX, clean_for_resume, load_pending_downloads, save_pending_downloads, workdir_parent,
)
from ui.progress import DownloadRow
from ui.style import M, RADIUS, caption, column, row
from ui.widgets import PauseBanner, empty_state


RESTART_WAIT = 30  # seconds before the first new try after a connection loss, doubled each time
MAX_RESTART_WAIT = 300


def link_params(url, fmt, quality, platform=""):
    """A download of a link that was not analyzed (several links pasted at once, a link sent from a phone):
    the format and quality given, no other option. Its title is the link until yt-dlp reads it."""
    return {"url": url, "title": url, "platform": platform, "thumbnail": "", "fmt": fmt, "quality": quality,
            "sub_key": "", "sub_mode": "file", "seconds": None, "images": [], "selected": 0, "post": None,
            "clip": None, "subs_only": False, "choice": {"fmt": fmt, "quality": quality}, "quick": True}


class QueueTab:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        # Downloads list: every click on Download adds a row; rows wait for a free slot (see Slots).
        self.jobs = []
        # Dragged to change the order (a long press on a phone).
        self.jobs_list = ft.ReorderableListView([], on_reorder=self.on_reorder)
        self.summary = caption()
        # The items of a list downloaded together: id -> {"left", "saved", "failed", "folder"} (see end_of_list).
        self.batches = {}
        self.clear_btn = ft.TextButton("Clear finished", icon=ft.Icons.CLEAR_ALL, visible=False)
        self.pause_all_btn = ft.TextButton("Pause all", icon=ft.Icons.PAUSE, visible=False, on_click=self.pause_all)
        self.retry_btn = ft.TextButton("Retry the failed ones", icon=ft.Icons.REPLAY, visible=False,
                                      on_click=self.retry_failed)
        self.empty = empty_state(ft.Icons.QUEUE, "Nothing in the queue",
                                 "Downloads show here.",
                                 self.paste_link)

        # Unfinished downloads: those running now (work folder -> options), and those left over from the
        # last time the app was closed, until the user resumes or discards them.
        self.pending = {}
        # The items of a list start one after the other, with the pause of Settings between them.
        self.pace_lock = asyncio.Lock()
        self.last_start = None
        self.leftovers = load_pending_downloads()

        self.resume_text = ft.Text("", expand=True)
        resume_btn = ft.Button("Resume", icon=ft.Icons.PLAY_ARROW)
        discard_btn = ft.TextButton("Discard", icon=ft.Icons.DELETE_OUTLINE)
        self.resume_box = ft.Container(
            content=column([self.resume_text, row([resume_btn, discard_btn], wrap=True)]),
            padding=M, border_radius=RADIUS, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), visible=False,
        )
        resume_btn.on_click = self.resume_leftovers
        discard_btn.on_click = self.discard_leftovers
        self.clear_btn.on_click = self.clear_finished
        self.refresh_resume_box()
        self.pause_banner = PauseBanner(app)
        app.youtube_pause.listeners.append(self.refresh)

        jobs_card = app.stretch_card([
            self.pause_banner.box,
            self.resume_box,
            self.summary,
            ft.Row([self.pause_all_btn, self.retry_btn, self.clear_btn], wrap=True, spacing=0,
                   alignment=ft.MainAxisAlignment.END),
            self.empty,
            self.jobs_list,
        ])
        self.view = app.view("Queue", [jobs_card], visible=False)
        self.refresh()

    # Unfinished downloads

    def save_pending(self):
        save_pending_downloads(list(self.pending.values()) + self.leftovers)

    def leftover_workdirs(self):
        return [d["workdir"] for d in self.leftovers]

    async def forget_download(self, workdir):
        self.pending.pop(workdir, None)
        self.save_pending()
        await asyncio.to_thread(shutil.rmtree, workdir, True)

    def refresh_resume_box(self):
        count = len(self.leftovers)
        self.resume_text.value = ("%d download%s did not finish. Resume?" % (count, "s" if count > 1 else ""))
        self.resume_box.visible = bool(count)

    async def resume_leftovers(self, e=None):
        items = list(self.leftovers)
        self.leftovers.clear()
        self.refresh_resume_box()
        self.refresh()
        self.page.update()
        # Each run_download saves its own entry again as it starts; they queue in this order.
        await asyncio.gather(*(self.run_download(params) for params in items))

    async def discard_leftovers(self, e=None):
        items = list(self.leftovers)
        self.leftovers.clear()
        self.save_pending()
        self.refresh_resume_box()
        self.refresh()
        self.page.update()
        for params in items:
            await asyncio.to_thread(shutil.rmtree, params["workdir"], True)

    # The list

    def refresh(self):
        app = self.app
        stages = ["held" if job.held else "paused" if job.paused else job.slots.stage if job.slots else "waiting"
                  for job in self.jobs if job.running]
        labels = (("download", "downloading"), ("live", "recording a live"), ("convert", "converting"),
                  ("waiting", "waiting"),
                  ("held", "paused"), ("paused", "paused (YouTube)"),
                  ("done", "saving"))  # "done": slot already given back, the file is being saved
        parts = ["%d %s" % (stages.count(stage), label) for stage, label in labels if stage in stages]
        if parts:
            self.summary.value = "%s · up to %d downloads and %d conversions at once (see Settings)" % (
                ", ".join(parts), app.download_slots.limit, app.conversion_slots.limit)
        else:
            self.summary.value = "All done." if self.jobs else ""
        self.summary.visible = bool(self.summary.value)  # an empty line would push the rest down
        self.jobs_list.controls = [self.row_of(job) for job in self.jobs]
        self.clear_btn.visible = any(not job.running for job in self.jobs)
        self.empty.visible = not self.jobs and not self.leftovers
        pausable = [job for job in self.jobs if job.running and not job.live and job.pause_btn.visible]
        self.pause_all_btn.visible = bool(pausable)
        resume = bool(pausable) and all(job.held for job in pausable)
        self.pause_all_btn.content = "Resume all" if resume else "Pause all"
        self.pause_all_btn.icon = ft.Icons.PLAY_ARROW if resume else ft.Icons.PAUSE
        self.retry_btn.visible = any(job.failed and job.params for job in self.jobs)
        app.set_queue_badge(len(stages) + len(self.leftovers))
        running = [job for job in self.jobs if job.running]
        app.set_taskbar_progress(sum(job.bar.value or 0 for job in running) / len(running) if running else None)

    def row_of(self, job):
        """A download's row in the list, made once: a computer shows the drag handle at its right, with room for
        it; a phone drags the whole row, and a swipe cancels it (or removes it once finished)."""
        if getattr(job, "queue_row", None) is None:
            if self.app.mobile:
                job.queue_row = ft.Dismissible(
                    content=ft.Container(job.box), on_confirm_dismiss=lambda e: self.page.run_task(self.swiped, e, job),
                    on_dismiss=lambda e: self.remove_job(job),
                    background=ft.Container(ft.Icon(ft.Icons.DELETE_OUTLINE, color=ft.Colors.ON_ERROR),
                                            bgcolor=ft.Colors.ERROR, alignment=ft.Alignment(-0.9, 0)),
                    secondary_background=ft.Container(ft.Icon(ft.Icons.DELETE_OUTLINE, color=ft.Colors.ON_ERROR),
                                                      bgcolor=ft.Colors.ERROR, alignment=ft.Alignment(0.9, 0)))
            else:
                job.queue_row = ft.Container(job.box, padding=ft.Padding(left=0, right=32, top=0, bottom=0))
        return job.queue_row

    async def swiped(self, e, job):
        """A row swiped away: a running download is cancelled (its row stays to say so), a finished one goes."""
        if job.running:
            job.cancel()
        await e.control.confirm_dismiss(not job.running)

    # Several at once

    def pause_all(self, e=None):
        """Pause every download that can be (or resume them all, when they all are)."""
        pausable = [job for job in self.jobs if job.running and not job.live and job.pause_btn.visible]
        resume = all(job.held for job in pausable)
        for job in pausable:
            if job.held == resume:
                job.toggle_hold()
        self.refresh()
        self.page.update()

    def retry_failed(self, e=None):
        """The downloads that failed, started again (their rows are replaced)."""
        failed = [job for job in self.jobs if job.failed and job.params]
        self.jobs[:] = [job for job in self.jobs if job not in failed]
        self.refresh()
        self.page.update()
        for job in failed:
            params = {key: value for key, value in job.params.items() if key != "workdir"}
            self.page.run_task(self.run_download, params)

    async def paste_link(self, e=None):
        self.app.show_view(0)
        await self.app.download.paste_link()
        self.page.update()

    def remove_job(self, job):
        if job in self.jobs:
            self.jobs.remove(job)
        self.refresh()
        self.page.update()

    def move_job(self, old, new):
        """A download dragged from place `old` to `new`: the unfinished ones take their turn for a slot in the
        new order."""
        if not (0 <= old < len(self.jobs) and 0 <= new < len(self.jobs)):
            return
        self.jobs.insert(new, self.jobs.pop(old))
        running = [job for job in self.jobs if job.running]
        for job, order in zip(running, sorted(job.order for job in running)):
            job.order = order
        self.refresh()
        self.page.update()

    def on_reorder(self, e):
        self.move_job(e.old_index, e.new_index)

    def clear_finished(self, e=None):
        before = list(self.jobs)
        self.jobs[:] = [job for job in self.jobs if job.running]
        self.refresh()

        def undo():
            running = [job for job in self.jobs if job not in before]  # added since
            self.jobs[:] = before + running
            self.refresh()
        self.app.offer_undo("%d removed from the list." % (len(before) - len(self.jobs)), undo)
        self.page.update()

    # A list downloaded together (one notification, the folder opened once)

    def end_of_list(self, batch, outcome, saved):
        """One item of a list is done (`outcome`: "saved", "failed" or "cancelled"); returns the list's state
        once all of them are, else None."""
        state = self.batches.setdefault(batch["id"], {"left": batch["total"], "saved": 0, "failed": 0,
                                                      "file": None})
        state["left"] -= 1
        if outcome in ("saved", "failed"):
            state[outcome] += 1
        if saved:
            state["file"] = saved
        if state["left"] > 0:
            return None
        return self.batches.pop(batch["id"])

    async def after_download(self, saved, folder=False):
        """Settings > Files: nothing, the folder opened, or the file itself, once a download (or a list) is
        saved."""
        action = self.app.settings.get("after_download")
        try:
            if action == "folder" and self.app.desktop:
                show_in_folder(saved)
            elif action == "file" and not folder and self.app.can_open_files():
                self.app.open_saved_file(saved)
        except Exception:
            pass

    # One download

    async def wait_for_slot(self, job, url, paced=False):
        """Wait while YouTube downloads are paused (for a YouTube link), then for a free download slot.
        `paced`: an item of a list (Wi-Fi only on a phone, a pause since the last one started, as set in
        Settings). Returns False if the download is cancelled meanwhile."""
        app, page = self.app, self.page
        youtube = is_youtube_url(url)

        def no_wifi():
            job.show("Waiting for Wi-Fi (Wi-Fi only is on in Settings)…")
            page.update()
        if paced and not await app.wait_for_wifi(job.cancelled, no_wifi):
            return False

        def waiting(reason):
            job.show(reason)
            job.bar.value = 0
            job.now_btn.visible = True
            self.refresh()
            page.update()
        started = await app.wait_for_schedule(job.cancelled, waiting, lambda: job.start_now)
        job.now_btn.visible = False
        if not started:
            return False
        while True:
            if youtube and app.youtube_pause.paused:
                job.paused = True
                job.show("Paused: YouTube is blocking downloads. Press Resume above to try again.")
                job.bar.value = 0  # a still bar: nothing is happening
                self.refresh()
                page.update()
                resumed = await app.youtube_pause.wait(job.cancelled)
                job.paused = False
                if not resumed:
                    return False
            job.slots = app.new_job_slots(job.cancelled, lambda: job.order)
            job.show("Waiting for a free download slot…")
            self.refresh()
            page.update()
            if not await job.slots.start():
                return False
            if youtube and app.youtube_pause.paused:  # paused while this one was waiting for its slot
                job.slots.release()
                continue
            if paced and not await self.pause_between(job):
                job.slots.release()
                return False
            job.show("Preparing…")
            self.refresh()
            page.update()
            return True

    async def pause_between(self, job):
        """The pause chosen in Settings since the last item of a list started; False if cancelled meanwhile."""
        pause = self.app.settings.get("playlist_pause")
        pause = pause if pause in PLAYLIST_PAUSES else 0
        async with self.pace_lock:
            while pause and self.last_start is not None and time.monotonic() < self.last_start + pause:
                job.show("Pause between videos (Settings): %d s…" % (self.last_start + pause - time.monotonic() + 1))
                self.page.update()
                if job.cancelled.is_set():
                    return False
                await asyncio.sleep(0.5)
            self.last_start = time.monotonic()
        return True

    async def get_slot(self, job, url, paced=False):
        """wait_for_slot, also through a pause by the user. False if cancelled."""
        while not await self.wait_for_slot(job, url, paced):
            if not job.held or not await self.hold(job):
                return False
        return True

    async def hold(self, job):
        """Paused by the user: wait for Resume. False if cancelled meanwhile."""
        job.show("Paused. Press Resume to continue from where it stopped.")
        job.bar.value = 0
        self.refresh()
        self.page.update()
        while job.held:
            await asyncio.sleep(0.25)
        return not job.cancelled.is_set()

    async def wait_to_restart(self, job, attempt, total):
        """The connection dropped: wait before starting again (Pause or Cancel end the wait)."""
        delay = min(RESTART_WAIT * 2 ** (attempt - 1), MAX_RESTART_WAIT)
        for remaining in range(delay, 0, -1):
            if job.held or job.cancelled.is_set():
                break
            job.show("Connection lost. Trying again in %s (%d of %d)…" % (format_duration(remaining), attempt, total))
            job.bar.value = 0
            self.refresh()
            self.page.update()
            await asyncio.sleep(1)
        if job.held:
            return await self.hold(job)
        return not job.cancelled.is_set()

    def restart_limit(self):
        limit = self.app.settings.get("restarts")
        return limit if limit in RESTART_CHOICES else DEFAULT_RESTARTS

    async def run_download(self, params):
        """Run one queued download: a new one, or one resumed after the app was closed.

        Android: asks once for the notification permission (a notification says when it is done).

        `params` (the options chosen when Download was pressed) is saved in the pending-downloads file
        until the download ends, with its work folder, so it can be resumed from there.
        """
        app, page = self.app, self.page
        url, title, platform = params["url"], params["title"], params["platform"]
        thumbnail_url, fmt, quality = params["thumbnail"], params["fmt"], params["quality"]
        sub_key, sub_mode, seconds = params["sub_key"], params["sub_mode"], params["seconds"]
        images, selected, post = params["images"], params["selected"], params["post"]
        clip = tuple(params["clip"]) if params["clip"] else None
        subs_only = params["subs_only"]
        # An item of a list (pause between them, Wi-Fi only), a podcast's episode (tagged from its feed).
        paced, podcast = bool(params.get("pace")), bool(params.get("podcast"))
        # A live recorded (from now, or once it starts), a montage of passages of several videos.
        live, montage = params.get("live"), params.get("montage")
        # Several subtitle languages (sub_keys); older downloads only have sub_key.
        sub_keys = params.get("sub_keys") or ([sub_key] if sub_key else [])
        # Added later: missing from downloads saved by an older version.
        max_mb, tag_edits = params.get("max_mb"), params.get("tag_edits") or {}
        speed, chapters = params.get("speed"), params.get("chapters")
        audio_lang, remove_ranges = params.get("audio_lang"), params.get("remove_ranges")
        # SponsorBlock would move the chapters' times: not with one file per chapter (or per clip).
        sponsorblock = None if chapters else (params["sponsorblock"] if "sponsorblock" in params
                                              else app.sponsorblock_categories())
        slideshow_mode = fmt in ("cover", "slideshow")
        check = bool(app.settings.get("check_files", True))
        workdir = params.get("workdir")
        if workdir and os.path.isdir(workdir):
            await asyncio.to_thread(clean_for_resume, workdir)
        else:
            workdir = params["workdir"] = tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=workdir_parent())
        self.pending[workdir] = params
        self.save_pending()

        await app.ask_notification_permission()
        job = DownloadRow(page, title, self.remove_job, thumbnail_url, on_show=app.show_job_progress,
                          live=bool(live))
        job.params = params
        job.start()
        self.jobs.append(job)

        # A list downloaded together: one notification (and the folder opened once) when all are done.
        batch = params.get("batch") if app.settings.get("notify_lists_once", True) else None
        outcome, saved = "cancelled", None
        if live:  # recorded as it comes: no download slot is held, and nothing is downloaded faster
            job.slots = app.new_job_slots(job.cancelled)
            job.slots.stage = "live"
        elif not await self.get_slot(job, url, paced):
            app.end_job_progress(job)
            await self.forget_download(workdir)
            job.finish("info", "Download cancelled.")
            self.refresh()
            page.update()
            if batch:
                await self.list_done(batch, outcome, None)
            return

        on_progress = job.reporter(on_update=self.refresh)

        def work(*args, **kwargs):
            """Worker-thread call made on behalf of this job, so it can switch from its download slot to a
            conversion slot when converting starts (see entering_conversion)."""
            return job.slots.run(on_progress, call_with_retries, on_progress, *args, **kwargs)
        video_info = {}  # filled by download_media, for the file name
        subtitles = {"files": [], "note": ""}  # files: [(language, file)] to save next to the video

        def add_subtitles(path):
            """In the worker thread, before tagging: a subtitle problem never fails the download."""
            try:
                path, subtitles["files"], subtitles["note"] = call_with_retries(
                    on_progress, apply_subtitles, url, sub_keys, sub_mode, path, workdir, app.cookies_value(),
                    cookie_file=saved_cookies_path(), clip=clip, notify=on_progress, title=title)
            except Cancelled:
                raise
            except Exception as exc:
                message = clean_error(exc)
                subtitles["note"] = " ".join(filter(None, ["Subtitles were not added: %s" % message,
                                                           make_hint(message, url, app.desktop)]))
            return path

        lang, auto = split_subtitle_key(sub_keys[0]) if subs_only and sub_keys else (None, None)

        def download_and_split(*args, **kwargs):
            """One file per chapter: the whole file first, then cut; returns the folder of parts."""
            path = download_media(*args, **kwargs)
            # An album: "Artist - Album (Full Album)" names the album and the artist of every track.
            artist, album = guess_song(dict(video_info, title=video_info.get("title") or title))
            tags = {"album": tag_edits.get("album") or tag_edits.get("title") or album or title,
                    "artist": tag_edits.get("artist") or artist} if app.embed_tags() else None
            return split_chapters(path, chapters, workdir, on_progress, speed or 1.0, tags)

        def several_subtitles():
            """Only the subtitles, in several languages: a folder of files ("Title.en.srt"…)."""
            folder = Path(workdir, "subtitles")
            folder.mkdir(exist_ok=True)
            for lang, subs in fetch_several(url, sub_keys, workdir, app.cookies_value(), saved_cookies_path(), clip):
                if sub_mode in TRANSCRIPT_MODES:
                    subs = make_transcript(subs, sub_mode, title, url, subs.parent)
                shutil.copyfile(subs, folder / safe_filename(title + subtitle_tag(lang), subs.suffix))
            return folder

        def record():
            """A live: once it has started, recorded until it ends or "Stop and save"."""
            streams = wait_for_live(url, quality, on_progress, app.cookies_value(), saved_cookies_path())
            video_info.update(title=streams["title"], uploader=streams["uploader"])
            return record_live(streams["inputs"], workdir, on_progress, job.stop)

        async def fetch():
            if live:
                return await asyncio.to_thread(work, record)
            if montage:
                return await asyncio.to_thread(work, make_montage, montage, fmt, quality, workdir,
                                               app.cookies_value(), on_progress, saved_cookies_path())
            if subs_only and len(sub_keys) > 1:
                on_progress(None, "Downloading subtitles…")
                return await asyncio.to_thread(work, several_subtitles)
            if subs_only:
                on_progress(None, "Downloading subtitles…")
                path = await asyncio.to_thread(
                    work,
                    fetch_subtitles, url, lang, auto, workdir, app.cookies_value(),
                    cookie_file=saved_cookies_path(), clip=clip,
                )
                job.check()
                if sub_mode in TRANSCRIPT_MODES:
                    path = await asyncio.to_thread(make_transcript, path, sub_mode, title, url, workdir)
                return path
            if slideshow_mode:
                return await asyncio.to_thread(
                    work,
                    download_slideshow, url, fmt, images, selected, seconds, workdir,
                    app.cookies_value(), on_progress, cookie_file=saved_cookies_path(), post=post,
                )
            if fmt == "photos":
                return await asyncio.to_thread(work, download_photos, images, platform, workdir, app.cookies_value(),
                                               on_progress, cookie_file=saved_cookies_path())
            return await asyncio.to_thread(
                work,
                download_and_split if chapters else download_media, url, fmt, quality, workdir,
                app.cookies_value(), on_progress, cookie_file=saved_cookies_path(), clip=clip,
                tags=app.embed_tags(), info_out=video_info,
                before_tags=add_subtitles if sub_keys and not subs_only else None, edits=params.get("edits"),
                max_bytes=int(max_mb * MB) if max_mb else None, tag_edits=tag_edits, speed=speed,
                audio_lang=audio_lang, sponsorblock=sponsorblock, remove_ranges=remove_ranges, check=check,
                sound=not params.get("no_sound"), cover_url=params.get("cover_url"),
                music_tags=app.music_tags() and not chapters and not podcast,
                lyrics=app.music_lyrics() and not chapters and not podcast,
            )

        try:
            restarts = 0
            while True:
                try:
                    path = await fetch()
                    break
                except Cancelled:
                    if job.cancelled.is_set() and not job.held:
                        raise
                    # Paused (or resumed right after Pause): starts again once resumed.
                    job.slots.release()
                    if job.held and not await self.hold(job):
                        raise
                except Exception as exc:
                    message = clean_error(exc)
                    job.slots.release()
                    if live:  # a live goes on without us: no new try from the start
                        raise
                    if is_youtube_block(url, message):
                        # YouTube takes the app for a bot: every YouTube download waits for Resume.
                        await app.pause_youtube(message)
                    elif is_transient(exc) and restarts < self.restart_limit():
                        restarts += 1
                        if not await self.wait_to_restart(job, restarts, self.restart_limit()):
                            raise Cancelled()
                    else:
                        raise
                # yt-dlp continues the part already downloaded.
                await asyncio.to_thread(clean_for_resume, workdir)
                if not await self.get_slot(job, url, paced):
                    raise Cancelled()
            if (params.get("quick") or live) and video_info.get("title"):
                title = job.title.value = video_info["title"]
            sub_files, sub_note = subtitles["files"], subtitles["note"]
            # "Title.en.srt": the language stays in the name, like the separate-file mode.
            # A podcast's episode: its file knows nothing about it, the feed's details are used.
            fields = filename_fields({} if podcast else video_info, params, fmt in VIDEO_FORMATS)
            if tag_edits.get("title"):  # the title corrected by the user names the file too
                fields["title"] = tag_edits["title"]
            # One file per chapter or clip, the photos of a post: a folder of files, named like the file would
            # be (a .zip without a destination folder).
            many = path.is_dir()
            parts = len(list(path.iterdir())) if many else 1
            file_suffix = sorted(path.iterdir())[0].suffix if many else path.suffix
            name = render_filename(app.filename_template(fmt), fields,
                                   ".zip" if many else (subtitle_tag(lang) if subs_only else "") + path.suffix)
            folder_name = name[:-len(".zip")] if many else None
            problems = video_info.get("problems") or []

            # Downloading and converting are done: free the slot now, saving may wait for the user.
            job.slots.release()
            job.saving()
            if app.android:
                await app.check_storage_access()
            target_dir = app.destination_folder()
            auto_save = ((app.desktop and (target_dir is not None or app.user_is_away()))
                         or (app.android and target_dir is not None))
            job.show("Saving…" if auto_save else "Waiting for the save dialog…")
            page.update()
            if many and not auto_save:
                path = await asyncio.to_thread(zip_folder, path, Path(workdir, "chapters.zip"), folder_name)

            # Plex or Jellyfin (Settings): a video goes to Show/Season as an episode, with its .nfo and thumbnail.
            episode = None
            if auto_save and app.settings.get("media_server") and fmt in VIDEO_FORMATS and not many and not subs_only:
                folder, name, nfo = episode_place(target_dir or default_download_dir(), fields,
                                                  video_info.get("description") or "", path.suffix)
                await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
                episode = nfo
            elif auto_save:
                # Sorted into sub-folders by the template of Settings (none by default).
                folder = await asyncio.to_thread(app.sorted_folder, target_dir or default_download_dir(),
                                                 dict(fields, type=file_type(fmt, subs_only)))
                if params.get("folder"):  # the items of a list, in a folder named after it
                    folder = Path(folder, safe_filename(params["folder"], ""))
                    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            if auto_save and many:
                target = await asyncio.to_thread(save_folder, path, folder, folder_name)
                saved, saved_ok = str(target), True
                message = "Saved: %s (%d files)" % (saved, parts)
            elif auto_save:
                # A destination folder is set, or nobody is looking at the window (a save dialog
                # would sit hidden): the file goes straight to that folder, else to Downloads.
                target = await asyncio.to_thread(reserve_file, folder, name)
                await asyncio.to_thread(shutil.copyfile, str(path), str(target))
                saved, saved_ok = str(target), True
                message = "Saved: %s" % saved
                if episode is not None:
                    thumb = await asyncio.to_thread(fetch_thumbnail, video_info.get("thumbnail") or thumbnail_url,
                                                    workdir)
                    await asyncio.to_thread(write_extras, target, episode, thumb)
                    message += "\nWith its .nfo and thumbnail, for Plex or Jellyfin."
            elif app.needs_bytes:
                data = await asyncio.to_thread(path.read_bytes)
                async with app.save_dialog_lock:
                    job.show("Choose where to save…")
                    page.update()
                    saved = await app.picker.save_file(dialog_title="Save file", file_name=name, src_bytes=data)
                saved_ok = saved is not None or bool(page.web)
                message = "Download complete: " + name
            else:
                async with app.save_dialog_lock:
                    job.show("Choose where to save…")
                    page.update()
                    saved = await app.picker.save_file(dialog_title="Save file", file_name=name)
                saved_ok = bool(saved)
                if saved_ok:
                    await asyncio.to_thread(shutil.copyfile, str(path), saved)
                message = "Saved: %s" % saved

            for sub_lang, sub_path in sub_files if saved_ok else []:
                sub_suffix = subtitle_tag(sub_lang) + sub_path.suffix
                try:
                    if (auto_save or not app.needs_bytes) and saved:
                        sub_target = Path(saved).with_name(Path(saved).stem + sub_suffix)
                        await asyncio.to_thread(shutil.copyfile, str(sub_path), str(sub_target))
                        message += "\nSubtitles: %s" % sub_target
                    else:
                        sub_bytes = await asyncio.to_thread(sub_path.read_bytes)
                        async with app.save_dialog_lock:
                            sub_saved = await app.picker.save_file(dialog_title="Save subtitles",
                                                                   file_name=Path(name).stem + sub_suffix,
                                                                   src_bytes=sub_bytes)
                        message += "\nSubtitles: " + ("saved" if (sub_saved is not None or page.web) else "not saved")
                except Exception as exc:
                    sub_note = "The subtitles could not be saved: %s" % exc
            if saved_ok and sub_note:
                message += "\n" + sub_note
            if saved_ok:
                backup = await app.copy_to_backup(path, folder_name if path.is_dir() else name,
                                                  dict(fields, type=file_type(fmt, subs_only)))
                if backup:
                    message += "\n" + backup
            if saved_ok and problems:
                message += "\nThe file may be damaged: %s" % " ".join(problems)

            # The items of a list, in its folder: a playlist file keeps their order for music players.
            if saved_ok and auto_save and params.get("folder") and not many and episode is None \
                    and app.settings.get("m3u", True) and params.get("position") is not None:
                try:
                    await asyncio.to_thread(add_to_m3u, Path(saved).parent / safe_filename(params["folder"], ".m3u8"),
                                            saved, params["position"], title, video_info.get("duration"))
                except OSError:
                    pass

            if saved_ok:
                outcome = "saved"
                job.finish("warn" if problems else "ok", message)
                if app.can_open_files() and saved and (auto_save or not app.needs_bytes):
                    job.show_saved(saved, app.open_saved_file, app.desktop)
                    job.show_share(app.share_file if app.mobile else app.send_to_phone if app.desktop else None)
                thumb_name = "" if page.web else await asyncio.to_thread(cache_thumbnail, thumbnail_url)
                add_history({
                    "thumbnail": thumbnail_url,
                    "thumb": thumb_name,
                    "title": title,
                    "platform": platform,
                    "url": url,
                    "format": file_suffix.lstrip(".").upper(),
                    "video_id": params.get("video_id") or "",
                    "choice": params.get("choice"),
                    "name": name,
                    "path": saved if ((auto_save or not app.needs_bytes) and saved) else "",
                    "list": params.get("list") or "",
                    "subscription": params.get("subscription") or "",
                })
                app.history.refresh()
                saved = saved if ((auto_save or not app.needs_bytes) and saved) else None
                if not batch:
                    if auto_save:
                        await app.notify_if_away("Download complete", "%s\n%s" % (title, saved))
                    await app.download_done()
                    if saved:
                        await self.after_download(saved)
            else:
                outcome = "failed"
                job.finish("err", "No location chosen: the file was not saved.")
        except Cancelled:
            job.finish("info", "Download cancelled.")
        except DownloadError as exc:
            outcome = "failed"
            message = clean_error(exc)
            hint = make_hint(message, url, app.desktop)
            job.finish("err", message, hint, error_report(exc, url, hint))
            if not batch:
                await app.notify_if_away("Download failed", "%s\n%s" % (title, message))
        except Exception as exc:
            outcome = "failed"
            job.finish("err", "Error: %s" % exc, details=error_report(exc, url))
            if not batch:
                await app.notify_if_away("Download failed", "%s\n%s" % (title, exc))
        finally:
            job.slots.release()
            app.end_job_progress(job)
            await self.forget_download(workdir)
            self.refresh()
            page.update()
        if batch:
            await self.list_done(batch, outcome, saved)

    async def list_done(self, batch, outcome, saved):
        """An item of a list is done: once they all are, one notification, the sound, and the folder opened."""
        state = self.end_of_list(batch, outcome, saved)
        if state is None:
            return
        app = self.app
        if state["saved"] or state["failed"]:
            details = ", ".join(x for x in ("%d saved" % state["saved"],
                                            "%d failed" % state["failed"] if state["failed"] else "") if x)
            await app.notify_if_away("List downloaded: %s" % batch["title"], details)
            await app.download_done()
        if state["file"]:
            await self.after_download(state["file"], folder=True)
