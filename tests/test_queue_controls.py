"""Pause, order and new tries in the queue, several clips, chapters left out, the progress notification and the
settings file (no network, no window)."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from conftest import files_in
from yt_dlp.utils import DownloadError

import ui.queue_tab
from core.errors import is_youtube_block, make_hint
from core.jobs import Slots
from core.sponsorblock import merge_segments
from core.storage import export_settings, import_settings
from test_queue_and_playlist import PARAMS, FakeDownload, result_text
from ui.app import App
from ui.queue_tab import QueueTab


async def wait_until(condition, seconds=5):
    for _ in range(int(seconds / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("still waiting")


async def test_slots_follow_the_rank():
    slots, order, ranks = Slots(1), [], {"a": 2, "b": 3}
    assert await slots.acquire(threading.Event())

    async def wait(name):
        await slots.acquire(threading.Event(), lambda: ranks[name])
        order.append(name)
    tasks = [asyncio.create_task(wait("a")), asyncio.create_task(wait("b"))]
    await asyncio.sleep(0.05)
    ranks["b"] = 1  # moved up while waiting
    slots.release()
    await wait_until(lambda: order)
    slots.release()
    await asyncio.gather(*tasks)
    assert order == ["b", "a"]


async def test_pause_and_resume(fake_app, monkeypatch):
    fake = FakeDownload(seconds=0.6)
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    queue = QueueTab(fake_app)
    task = asyncio.create_task(queue.run_download(dict(PARAMS)))
    await wait_until(lambda: fake.calls)
    [job] = queue.jobs
    job.toggle_hold()
    await wait_until(lambda: job.text.value.startswith("Paused"))
    assert fake_app.download_slots.active == 0 and "1 paused" in queue.summary.value  # its slot is free
    await asyncio.sleep(0.3)
    assert len(fake.calls) == 1 and job.running
    job.toggle_hold()  # Resume: starts again (yt-dlp would continue the part already downloaded)
    await asyncio.wait_for(task, 5)
    assert len(fake.calls) == 2 and files_in(fake_app.destination) == ["Title from yt-dlp.mp4"]


async def test_cancel_while_paused(fake_app, monkeypatch):
    fake = FakeDownload(seconds=5)
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    queue = QueueTab(fake_app)
    task = asyncio.create_task(queue.run_download(dict(PARAMS)))
    await wait_until(lambda: fake.calls)
    [job] = queue.jobs
    job.toggle_hold()
    await wait_until(lambda: job.text.value.startswith("Paused"))
    job.cancel()
    await asyncio.wait_for(task, 5)
    assert result_text(job) == "Download cancelled." and len(fake.calls) == 1


async def test_drag_in_the_queue(fake_app, monkeypatch):
    fake = FakeDownload(seconds=0.3)
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    fake_app.download_slots.set_limit(1)
    queue = QueueTab(fake_app)
    tasks = [asyncio.create_task(queue.run_download(dict(PARAMS, url="https://site.test/%d" % i)))
             for i in range(3)]
    await wait_until(lambda: len(queue.jobs) == 3 and fake.calls)
    third = queue.jobs[2]
    queue.move_job(2, 0)  # dragged above the one downloading: it is next
    assert queue.jobs[0] is third
    await asyncio.gather(*tasks)
    assert [call["url"] for call in fake.calls] == ["https://site.test/0", "https://site.test/2", "https://site.test/1"]


class FlakyDownload(FakeDownload):
    """The first `failures` calls fail as when the connection drops."""

    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def __call__(self, *args, **kwargs):
        if self.failures:
            self.failures -= 1
            self.calls.append({})
            raise DownloadError("ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>")
        return super().__call__(*args, **kwargs)


@pytest.fixture
def quick_restarts(monkeypatch):
    monkeypatch.setattr(ui.queue_tab, "RESTART_WAIT", 0)
    monkeypatch.setitem(ui.queue_tab.call_with_retries.__globals__["NETWORK"], "retries", 0)


async def test_restarts_after_a_connection_loss(fake_app, monkeypatch, quick_restarts):
    fake = FlakyDownload(failures=2)
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    await QueueTab(fake_app).run_download(dict(PARAMS))
    assert len(fake.calls) == 3 and files_in(fake_app.destination) == ["Title from yt-dlp.mp4"]


async def test_restarts_are_limited(fake_app, monkeypatch, quick_restarts):
    fake = FlakyDownload(failures=5)
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    fake_app.settings["restarts"] = 1
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    assert len(fake.calls) == 2 and "getaddrinfo" in result_text(queue.jobs[0])


def test_refused_translated_subtitles_do_not_pause_youtube():
    message = "Unable to download video subtitles for 'fr': HTTP Error 429: Too Many Requests"
    assert not is_youtube_block("https://www.youtube.com/watch?v=abc", message)
    assert "auto-translated" in make_hint(message, "https://www.youtube.com/watch?v=abc", True)


def test_merge_segments():
    segments = [{"start": 0.0, "end": 5.0, "category": "intro"}, {"start": 4.0, "end": 9.0, "category": "sponsor"},
                {"start": 20.0, "end": 30.0, "category": "outro"}]
    assert merge_segments(segments, ["intro", "sponsor"]) == [(0.0, 9.0, ["intro", "sponsor"])]
    assert merge_segments(segments, ["outro"]) == [(20.0, 30.0, ["outro"])]


# Android's progress notification

async def test_progress_notification_and_its_cancel_button():
    calls = []
    page = SimpleNamespace(run_task=lambda handler, *args: calls.append(args))
    app = SimpleNamespace(settings={}, notifier=object(), notification_id=0, job_notices={}, page=page)
    app.call_notifier = None
    cancelled = []
    job = type("Row", (), {"title": SimpleNamespace(value="Song"), "running": True,
                           "cancel": lambda self: cancelled.append(1)})()
    App.show_job_progress(app, job, "Downloading…", 0.42)
    App.show_job_progress(app, job, "Downloading…", 0.43)  # within a second: not sent again
    App.show_job_progress(app, job, "Merging video and audio…", None)
    assert calls == [("progress", 1, "Song", "Downloading… 42 %", 42),
                     ("progress", 1, "Song", "Merging video and audio…", -1)]
    App.on_notification_cancel(app, SimpleNamespace(id=1))
    assert cancelled == [1]
    App.end_job_progress(app, job)
    assert calls[-1] == ("cancel", 1) and app.job_notices == {}
    app.settings["notify"] = False
    App.show_job_progress(app, job, "Downloading…", 0.5)
    assert len(calls) == 3


# Settings file

def test_settings_export_and_import():
    settings = {"save_dir": "C:/Videos", "quality": "1080", "sponsorblock": ["sponsor"], "ytdlp_checked_at": 1.0}
    data = export_settings(settings)
    phone = {"save_dir": "/storage/emulated/0/Movies", "quality": "720"}
    assert import_settings(data, phone) == 2
    assert phone == {"save_dir": "/storage/emulated/0/Movies", "quality": "1080", "sponsorblock": ["sponsor"]}
    with pytest.raises(ValueError, match="not a settings file"):
        import_settings(b'{"quality": "best"}', phone)
    with pytest.raises(ValueError):
        import_settings(b"\xff\x00", phone)
