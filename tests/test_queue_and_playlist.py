"""The Queue tab and the Download tab's list (a playlist…), with a fake download (no network, no window):
saving, file names, history, parallel limits, cancel, errors, resuming after a restart."""

import asyncio
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import files_in
from yt_dlp.utils import DownloadError

import ui.queue_tab
from core.history import load_history
from core.jobs import entering_conversion
from core.workdirs import load_pending_downloads, save_pending_downloads
from ui.items import ItemList
from ui.queue_tab import QueueTab, link_params

PARAMS = {"url": "https://www.youtube.com/watch?v=abc", "title": "My video", "platform": "Youtube",
          "thumbnail": "", "uploader": "Someone", "upload_date": "20240531", "video_id": "abc",
          "fmt": "mp4", "quality": "best", "sub_key": "", "sub_mode": "file", "seconds": None, "images": [],
          "selected": 0, "post": None, "clip": None, "subs_only": False}


class FakeDownload:
    """Stands in for core.download.download_media: writes a small file instead of downloading."""

    def __init__(self, seconds=0.0, error=None, blocked_calls=0):
        self.seconds, self.error = seconds, error
        self.blocked_calls = blocked_calls  # the first calls fail as when YouTube blocks the app
        self.calls, self.now, self.peak = [], 0, 0
        self.lock = threading.Lock()

    def __call__(self, url, fmt, quality, workdir, cookies_browser="", on_progress=None, cookie_file=None,
                 clip=None, tags=True, info_out=None, before_tags=None, max_bytes=None, tag_edits=None,
                 speed=None, audio_lang=None, sponsorblock=None, remove_ranges=None, check=False,
                 sound=True, cover_url=None, music_tags=False, lyrics=False, edits=None):
        self.calls.append({"url": url, "fmt": fmt, "quality": quality, "workdir": workdir, "clip": clip,
                           "max_bytes": max_bytes, "tag_edits": tag_edits, "speed": speed,
                           "audio_lang": audio_lang, "sponsorblock": sponsorblock,
                           "remove_ranges": remove_ranges, "edits": edits})
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            on_progress(0.5, "Downloading…")
            deadline = time.monotonic() + self.seconds
            while time.monotonic() < deadline:
                on_progress(0.5, "Downloading…")  # raises Cancelled once Cancel is pressed
                time.sleep(0.02)
            if self.error:
                raise self.error
            with self.lock:
                blocked, self.blocked_calls = self.blocked_calls > 0, max(self.blocked_calls - 1, 0)
            if blocked:
                raise DownloadError("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. "
                                    "Use --cookies-from-browser or --cookies for the authentication.")
        finally:
            with self.lock:
                self.now -= 1
        entering_conversion()
        path = Path(workdir, "media." + ("mp3" if fmt == "mp3" else fmt))
        path.write_bytes(b"media")
        if info_out is not None:
            info_out.update({"title": "Title from yt-dlp", "uploader": "Someone", "upload_date": "20240531",
                             "height": 720, "id": "abc"})
        return before_tags(path) if before_tags else path


@pytest.fixture
def fake_download(monkeypatch):
    def install(**kwargs):
        fake = FakeDownload(**kwargs)
        monkeypatch.setattr(ui.queue_tab, "download_media", fake)
        return fake
    return install


def result_text(row):
    """Text shown under a finished download (see ui.widgets.set_status)."""
    column = row.result.content.controls[-1]
    return "\n".join(c.value if hasattr(c, "value") else c.content.value for c in column.controls)


# Queue

async def test_download_is_saved_with_the_template_name(fake_app, fake_download):
    fake_download()
    fake_app.filename_template = lambda fmt=None: "{uploader} - {title} [{resolution}]"
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    assert files_in(fake_app.destination) == ["Someone - Title from yt-dlp [720p].mp4"]
    [job] = queue.jobs
    assert result_text(job).startswith("Saved: ")
    assert queue.summary.value == "All done."
    assert fake_app.badges[-1] == 0
    assert load_pending_downloads() == []  # nothing left to resume
    [entry] = load_history()
    assert entry["title"] == "My video" and entry["format"] == "MP4" and entry["path"].endswith("720p].mp4")
    assert entry["video_id"] == "abc"  # for the duplicates


async def test_size_limit_and_edited_tags_reach_the_download(fake_app, fake_download):
    fake = fake_download()
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS, max_mb=9.5, tag_edits={"title": "Better title"}))
    [call] = fake.calls
    assert call["max_bytes"] == int(9.5 * 1024 * 1024) and call["tag_edits"] == {"title": "Better title"}
    assert call["speed"] is None
    assert files_in(fake_app.destination) == ["Better title.mp4"]  # the corrected title names the file


def fake_split(path, chapters, workdir, notify, speed=1.0, tags=None):
    folder = Path(workdir, "chapters")
    folder.mkdir()
    for index, chapter in enumerate(chapters, 1):
        Path(folder, "%02d - %s%s" % (index, chapter["title"], path.suffix)).write_bytes(b"part")
    return folder


CHAPTER_PARAMS = dict(PARAMS, chapters=[{"title": "Intro", "start": 0.0, "end": 60.0},
                                        {"title": "Outro", "start": 60.0, "end": 90.0}])


async def test_one_file_per_chapter_into_a_folder(fake_app, fake_download, monkeypatch):
    fake_download()
    monkeypatch.setattr(ui.queue_tab, "split_chapters", fake_split)
    queue = QueueTab(fake_app)
    await queue.run_download(dict(CHAPTER_PARAMS))
    assert files_in(fake_app.destination) == ["Title from yt-dlp/01 - Intro.mp4", "Title from yt-dlp/02 - Outro.mp4"]
    assert result_text(queue.jobs[0]).endswith("(2 files)")
    assert load_history()[0]["format"] == "MP4"


async def test_one_file_per_chapter_without_destination_is_zipped(fake_app, fake_download, monkeypatch, tmp_path):
    fake_download()
    monkeypatch.setattr(ui.queue_tab, "split_chapters", fake_split)
    chosen = tmp_path / "chosen"
    chosen.mkdir()

    async def save_file(dialog_title, file_name, src_bytes=None):
        return str(chosen / file_name)
    fake_app.destination_folder = lambda: None
    fake_app.picker = SimpleNamespace(save_file=save_file)
    await QueueTab(fake_app).run_download(dict(CHAPTER_PARAMS))
    assert files_in(chosen) == ["Title from yt-dlp.zip"]
    assert zipfile.ZipFile(chosen / "Title from yt-dlp.zip").namelist() == [
        "Title from yt-dlp/01 - Intro.mp4", "Title from yt-dlp/02 - Outro.mp4"]


async def test_same_title_twice_never_overwrites(fake_app, fake_download):
    fake_download()
    queue = QueueTab(fake_app)
    await asyncio.gather(queue.run_download(dict(PARAMS)), queue.run_download(dict(PARAMS)))
    assert files_in(fake_app.destination) == ["Title from yt-dlp (1).mp4", "Title from yt-dlp.mp4"]


async def test_parallel_download_limit(fake_app, fake_download):
    fake = fake_download(seconds=0.3)
    fake_app.download_slots.set_limit(1)
    queue = QueueTab(fake_app)
    await asyncio.gather(*(queue.run_download(dict(PARAMS)) for _ in range(3)))
    assert fake.peak == 1
    assert len(files_in(fake_app.destination)) == 3


async def test_cancel(fake_app, fake_download):
    fake_download(seconds=10)
    queue = QueueTab(fake_app)
    task = asyncio.create_task(queue.run_download(dict(PARAMS)))
    await asyncio.sleep(0.3)
    [job] = queue.jobs
    job.cancel()
    await asyncio.wait_for(task, 5)
    assert result_text(job) == "Download cancelled."
    assert files_in(fake_app.destination) == []
    assert load_pending_downloads() == []
    assert fake_app.download_slots.active == 0 and fake_app.conversion_slots.active == 0


async def test_error_shows_a_hint(fake_app, fake_download):
    fake_download(error=DownloadError("ERROR: [instagram] abc: login required"))
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    [job] = queue.jobs
    text = result_text(job)
    assert "login required" in text and "cookies.txt" in text  # the error, then how to fix it
    assert files_in(fake_app.destination) == []
    assert fake_app.download_slots.active == 0


async def test_subtitles_saved_next_to_the_video(fake_app, fake_download, monkeypatch, tmp_path):
    fake_download()

    def fake_apply_subtitles(url, keys, mode, video, workdir, *args, **kwargs):
        found = []
        for key in keys:
            srt = Path(workdir, key.replace(":", "_") + ".srt")
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
            found.append((key[2:], srt))
        return Path(video), found, ""

    monkeypatch.setattr(ui.queue_tab, "apply_subtitles", fake_apply_subtitles)
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS, sub_key="m:en"))
    assert files_in(fake_app.destination) == ["Title from yt-dlp.en.srt", "Title from yt-dlp.mp4"]
    await queue.run_download(dict(PARAMS, sub_key="m:en", sub_keys=["m:en", "a:fr"]))  # several languages
    assert "Title from yt-dlp (1).fr.srt" in files_in(fake_app.destination)


async def test_transcript_alone(fake_app, fake_download, monkeypatch):
    fake_download()

    def fake_fetch_subtitles(url, lang, auto, workdir, *args, **kwargs):
        srt = Path(workdir, "subtitles.srt")
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello there.\n", encoding="utf-8")
        return srt

    monkeypatch.setattr(ui.queue_tab, "fetch_subtitles", fake_fetch_subtitles)
    await QueueTab(fake_app).run_download(dict(PARAMS, sub_key="a:en-orig", sub_mode="html", subs_only=True))
    assert files_in(fake_app.destination) == ["My video.en.html"]
    page = (fake_app.destination / "My video.en.html").read_text(encoding="utf-8")
    assert "<h1>My video</h1>" in page and "Hello there." in page


async def test_open_buttons_once_saved(fake_app, fake_download):
    fake_download()
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    [job] = queue.jobs
    assert job.folder_btn.visible and job.play_btn.visible  # like in the Library tab


async def test_resume_after_restart(fake_app, fake_download, tmp_path):
    fake = fake_download()
    workdir = tmp_path / "videodl_resume"
    workdir.mkdir()
    (workdir / "media.f399.mp4.part").write_bytes(b"half")  # yt-dlp continues this one
    (workdir / "converted.mkv").write_bytes(b"broken")  # a conversion stopped halfway
    save_pending_downloads([dict(PARAMS, workdir=str(workdir))])

    queue = QueueTab(fake_app)  # the app starts again
    assert queue.resume_box.visible and "1 download did not finish" in queue.resume_text.value
    assert fake_app.badges[-1] == 1
    await queue.resume_leftovers()
    assert fake.calls[0]["workdir"] == str(workdir)
    assert files_in(fake_app.destination) == ["Title from yt-dlp.mp4"]
    assert not queue.resume_box.visible and load_pending_downloads() == []


async def test_discard_leftovers(fake_app, tmp_path):
    workdir = tmp_path / "videodl_old"
    workdir.mkdir()
    save_pending_downloads([dict(PARAMS, workdir=str(workdir))])
    queue = QueueTab(fake_app)
    await queue.discard_leftovers()
    assert not workdir.exists() and load_pending_downloads() == []


# Playlist

PLAYLIST = {"title": "My playlist", "uploader": "Someone", "total": 3, "limit": 100, "entries": [
    {"url": "https://www.youtube.com/watch?v=%d" % i, "title": "Video %d" % i, "duration": 60, "thumbnail": ""}
    for i in range(3)]}


def item_list(fake_app, data=PLAYLIST):
    """The Download tab's list of `data`, sending to a real queue (fake_app.queue)."""
    fake_app.queue = QueueTab(fake_app)
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    items = ItemList(fake_app, SimpleNamespace())
    items.build(data, "u")
    return items


async def download_list(items, fake_app):
    """Download the selection, and wait until the queue has done every item."""
    count = len(items.checked())
    await items.download()
    queue = fake_app.queue
    for _ in range(200):
        if len(queue.jobs) == count and not any(job.running for job in queue.jobs):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the queue did not finish")


async def test_list_into_a_subfolder(fake_app, fake_download):
    fake = fake_download()
    items = item_list(fake_app)
    items.items[1]["check"].value = False  # unselect the second video
    items.refresh_count()
    assert items.count.value == "2 of 3 selected · about 64.1 MB"  # two minutes at about 1080p
    items.quality_dd.value = "360"
    items.refresh_count()
    assert items.count.value == "2 of 3 selected · about 10.3 MB"
    await download_list(items, fake_app)
    assert sorted(call["url"][-1] for call in fake.calls) == ["0", "2"]  # through the queue
    assert {call["quality"] for call in fake.calls} == {"360"}
    assert files_in(fake_app.destination) == ["My playlist/My playlist.m3u8", "My playlist/Title from yt-dlp (1).mp4",
                                              "My playlist/Title from yt-dlp.mp4"]
    assert len(load_history()) == 2
    # The playlist file lists them in the list's order, whatever order they were saved in.
    playlist = (fake_app.destination / "My playlist" / "My playlist.m3u8").read_text(encoding="utf-8")
    names = [line for line in playlist.splitlines() if line.endswith(".mp4")]
    assert playlist.startswith("#EXTM3U") and sorted(names) == ["Title from yt-dlp (1).mp4", "Title from yt-dlp.mp4"]
    assert [int(line[7:]) for line in playlist.splitlines() if line.startswith("#EXTVD:")] == [0, 2]


async def test_list_item_with_its_own_options(fake_app, fake_download):
    fake = fake_download()
    items = item_list(fake_app)
    items.size_field.value = "5"
    items.loudness.value = True
    own = dict(link_params(PLAYLIST["entries"][0]["url"], "mp3", "best"), title="Video 0", quick=False)
    items.set_own(items.items[0], own, None)
    assert items.items[0]["own"].visible and items.items[0]["own"].value == "Own options: MP3"
    await download_list(items, fake_app)
    by_url = {call["url"][-1]: call for call in fake.calls}
    assert by_url["0"]["fmt"] == "mp3" and by_url["0"]["max_bytes"] is None  # its own options
    assert by_url["1"]["fmt"] == "mp4" and by_url["1"]["max_bytes"] == 5 * 1024 * 1024
    assert by_url["1"]["edits"] == ({"loudness": True} if items.loudness.visible else None)


async def test_list_failure_does_not_stop_the_others(fake_app, fake_download):
    fake_download(error=DownloadError("ERROR: Private video"))
    items = item_list(fake_app)
    await download_list(items, fake_app)
    assert len(fake_app.queue.jobs) == 3
    assert all("Private video" in result_text(job) for job in fake_app.queue.jobs)


async def test_list_without_destination_asks_for_each_file(fake_app, fake_download, tmp_path):
    fake_download()
    saved = []

    async def save_file(dialog_title, file_name, src_bytes):
        saved.append(file_name)
        return str(tmp_path / file_name)

    fake_app.desktop, fake_app.needs_bytes = False, True  # phone or web: no folder to write to
    fake_app.destination_folder = lambda: None
    fake_app.picker = SimpleNamespace(save_file=save_file)
    items = item_list(fake_app)
    await download_list(items, fake_app)
    assert len(saved) == 3
    assert "each file asks where to be saved" in items.status.content.controls[-1].controls[0].value


# YouTube blocking the app ("Sign in to confirm you're not a bot")

async def wait_until(condition, seconds=5.0):
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_youtube_block_pauses_the_queue_instead_of_failing(fake_app, fake_download):
    fake_download(seconds=0.3, blocked_calls=1)
    queue = QueueTab(fake_app)
    tasks = [asyncio.create_task(queue.run_download(dict(PARAMS))) for _ in range(3)]
    # The first download is blocked; the one already downloading finishes; the third does not start.
    await wait_until(lambda: sum(job.paused for job in queue.jobs) == 2)
    assert fake_app.youtube_pause.paused and queue.pause_banner.box.visible
    assert "not a bot" in queue.pause_banner.reason.value
    assert "2 paused (YouTube)" in queue.summary.value
    assert len(files_in(fake_app.destination)) == 1
    assert fake_app.download_slots.active == 0  # paused downloads hold no slot

    queue.pause_banner.resume()
    await asyncio.wait_for(asyncio.gather(*tasks), 10)
    assert not queue.pause_banner.box.visible
    assert len(files_in(fake_app.destination)) == 3
    assert all(result_text(job).startswith("Saved: ") for job in queue.jobs)
    assert load_pending_downloads() == []


async def test_other_sites_keep_downloading_while_youtube_is_paused(fake_app, fake_download):
    fake_download()
    fake_app.youtube_pause.pause("Sign in to confirm you're not a bot")
    queue = QueueTab(fake_app)
    youtube = asyncio.create_task(queue.run_download(dict(PARAMS)))
    await queue.run_download(dict(PARAMS, url="https://www.dailymotion.com/video/x5kesuj"))
    assert len(files_in(fake_app.destination)) == 1
    assert not youtube.done() and [job.paused for job in queue.jobs].count(True) == 1
    fake_app.youtube_pause.resume()
    await asyncio.wait_for(youtube, 5)
    assert len(files_in(fake_app.destination)) == 2


async def test_cancel_a_paused_download(fake_app, fake_download):
    fake_download()
    fake_app.youtube_pause.pause("blocked")
    queue = QueueTab(fake_app)
    task = asyncio.create_task(queue.run_download(dict(PARAMS)))
    await wait_until(lambda: queue.jobs and queue.jobs[0].paused)
    queue.jobs[0].cancel()
    await asyncio.wait_for(task, 5)
    assert result_text(queue.jobs[0]) == "Download cancelled."
    assert load_pending_downloads() == []


async def test_youtube_block_pauses_a_list(fake_app, fake_download):
    fake_download(seconds=0.2, blocked_calls=1)
    fake_app.download_slots.set_limit(1)
    items = item_list(fake_app)
    task = asyncio.create_task(download_list(items, fake_app))
    await wait_until(lambda: fake_app.youtube_pause.paused)
    fake_app.queue.pause_banner.resume()
    await asyncio.wait_for(task, 15)
    assert all(result_text(job).startswith("Saved") for job in fake_app.queue.jobs)
