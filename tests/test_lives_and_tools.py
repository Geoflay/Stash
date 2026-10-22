"""Lives recorded, montages, file names cleaned up, the queue's tools (all paused, the failed ones again), the
subscriptions' filters, the Library's order and table, the watched folders, the subtitle editor and the phone's
back gesture (no network, no window)."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import needs_ffmpeg

import core.live as live
import test_download_options
import ui.queue_tab
from core.filenames import NAME_RULES, clean_name, filename_fields, format_date, render_filename
from core.history import history_csv
from core.subscriptions import beyond_newest, wanted
from core.watch import watched_folders
from test_queue_and_playlist import PARAMS, FakeDownload
from test_queue_controls import wait_until
from ui.app import App
from ui.queue_tab import QueueTab
from ui.subtitle_editor import SubtitleEditor


# File names

def test_names_cleaned_up(monkeypatch):
    rules = dict(NAME_RULES, emoji=True, ascii=True, noise=True, spaces="_", case="title", remove="feat")
    assert clean_name("Café \U0001F525 song (Official Video) feat Me \U0001F3B5", rules) == "Cafe_Song_Me"
    assert clean_name("A  title", dict(NAME_RULES, case="upper")) == "A TITLE"
    assert format_date("20240531", "dmy") == "31-05-2024" and format_date("20240531", "compact") == "20240531"
    monkeypatch.setitem(NAME_RULES, "max_length", 10)
    monkeypatch.setitem(NAME_RULES, "date", "dots")
    fields = filename_fields({"title": "A long title here", "upload_date": "20240531"}, {})
    assert render_filename("{date} {title}", fields, ".mp4") == "31.05.2024.mp4"


# Lives

def test_live_not_started_is_waited_for(monkeypatch):
    answers = [RuntimeError("This live event will begin in 2 hours."), {"title": "Live", "inputs": []}]

    def streams(*args):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(live, "live_streams", streams)
    monkeypatch.setattr(live, "LIVE_CHECK_EVERY", 0)
    assert live.wait_for_live("u", "best", lambda *a: None) == {"title": "Live", "inputs": []}
    assert live.not_started("The channel is not currently live") and not live.not_started("Video unavailable")


@needs_ffmpeg
def test_live_recording(media_dir, tmp_path):
    out = live.record_live([(str(media_dir / "video.mp4"), {})], tmp_path, lambda *a: None, threading.Event())
    assert out.suffix == ".mp4" and out.stat().st_size > 0


class FakeLive:
    """Stands in for the recording: it goes on until "Stop and save"."""

    def __init__(self):
        self.started = False

    def wait(self, url, quality, notify, *args):
        return {"title": "Live title", "uploader": "Channel", "inputs": [("stream", {})]}

    def record(self, inputs, workdir, notify, stop):
        self.started = True
        while not stop.is_set():
            notify(None, "Recording the live")
            stop.wait(0.05)
        path = Path(workdir, "live.mp4")
        path.write_bytes(b"live")
        return path


async def test_live_in_the_queue(fake_app, monkeypatch):
    fake = FakeLive()
    monkeypatch.setattr(ui.queue_tab, "wait_for_live", fake.wait)
    monkeypatch.setattr(ui.queue_tab, "record_live", fake.record)
    fake_app.download_slots.set_limit(1)
    queue = QueueTab(fake_app)
    task = asyncio.create_task(queue.run_download(dict(PARAMS, live="now")))
    await wait_until(lambda: fake.started)
    job = queue.jobs[0]
    assert job.stop_btn.visible and not job.pause_btn.visible
    assert not fake_app.download_slots.active  # a live holds no download slot
    job.stop_recording()
    await task
    assert job.title.value == "Live title" and (fake_app.destination / "Live title.mp4").exists()


async def test_montage_in_the_queue(fake_app, monkeypatch, tmp_path):
    made = []

    def montage(parts, fmt, quality, workdir, *args):
        made.append([part["url"] for part in parts])
        path = Path(workdir, "joined.mp4")
        path.write_bytes(b"montage")
        return path
    monkeypatch.setattr(ui.queue_tab, "make_montage", montage)
    parts = [{"url": "https://a", "title": "A", "clip": [0, 5]}, {"url": "https://b", "title": "B", "clip": None}]
    await QueueTab(fake_app).run_download(dict(PARAMS, title="Montage (2 passages)", montage=parts))
    assert made == [["https://a", "https://b"]] and (fake_app.destination / "Montage (2 passages).mp4").exists()


# The queue's tools

async def test_pause_all_and_retry(fake_app, monkeypatch):
    fake = FakeDownload(seconds=0.4)
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    queue = QueueTab(fake_app)
    assert queue.empty.visible  # nothing yet: what to do
    tasks = [asyncio.create_task(queue.run_download(dict(PARAMS, url="https://site.test/%d" % i))) for i in range(2)]
    await wait_until(lambda: len(queue.jobs) == 2 and fake.calls)
    assert not queue.empty.visible and queue.pause_all_btn.visible
    queue.pause_all()
    assert all(job.held for job in queue.jobs) and queue.pause_all_btn.content == "Resume all"
    queue.pause_all()
    assert not any(job.held for job in queue.jobs)
    await asyncio.gather(*tasks)
    queue.jobs[0].failed = True  # as if it had failed
    queue.refresh()
    assert queue.retry_btn.visible
    queue.retry_failed()
    await wait_until(lambda: len(queue.jobs) == 2 and not any(job.running for job in queue.jobs))
    assert len(fake.calls) >= 3  # the failed one was downloaded again


# Subscriptions, Library, watched folders

def test_subscription_filters():
    entry = {"title": "Big News today", "duration": 600, "url": "https://youtube.com/watch?v=1"}
    assert wanted(entry, {"include": "news, sport"}) and not wanted(entry, {"include": "sport"})
    assert not wanted(entry, {"exclude": "today"}) and not wanted(entry, {"max_minutes": 5})
    assert not wanted(dict(entry, duration=40), {"no_shorts": True}) and wanted(entry, {"no_shorts": True})
    history = [{"subscription": "s", "title": str(i)} for i in range(4)] + [{"subscription": "other"}]
    assert [e["title"] for e in beyond_newest(history, "s", 2)] == ["2", "3"] and beyond_newest(history, "s", 0) == []


def test_library_order_and_table():
    items = [{"title": "b", "url": "u1"}, {"title": "A", "url": "u2"}]
    sizes = {"u1": 5, "u2": 9}
    table = history_csv(items, lambda e: sizes[e["url"]]).splitlines()
    assert table[0].startswith("Title,Platform") and table[1].startswith("b,,,,5,u1")


def test_watched_folders_of_an_older_version():
    assert watched_folders({"watch_dir": "C:/in", "watch_preset": "Music"}) == [
        {"dir": "C:/in", "preset": "Music", "delete": False}]
    assert watched_folders({"watch_dir": "C:/in", "watch_folders": []}) == []


# The subtitle editor

def test_subtitle_editor(tmp_path, fake_app):
    subs = tmp_path / "film.srt"
    subs.write_text("1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n",
                    encoding="utf-8")
    editor = SubtitleEditor(fake_app, subs)
    from core.subtitles import parse_cues
    editor.rows.controls = [editor.line(*cue) for cue in parse_cues(subs.read_text(encoding="utf-8"))]
    editor.shift.value = "-0.5"
    editor.move_all()
    assert editor.cues() == [(0.5, 2.0, ["Hello"]), (2.5, 3.5, ["Bye"])]
    editor.rows.controls[1].data[1].value = "0:01"  # ends before it starts
    with pytest.raises(ValueError, match="Line 2"):
        editor.cues()


# The phone's back gesture

def test_back_gesture():
    shown = []
    settings = SimpleNamespace(view=SimpleNamespace(visible=True), on_page=True, wide=False,
                               go_home=lambda: shown.append("settings list"))
    home = SimpleNamespace(view=SimpleNamespace(visible=False), current="join", back=lambda: shown.append("tiles"))
    app = SimpleNamespace(settings_tab=settings, download=SimpleNamespace(editing=None), home=home,
                          nav=SimpleNamespace(selected_index=3), show_view=shown.append,
                          page=SimpleNamespace(update=lambda: None))
    App.back_action(app)()
    assert shown == ["settings list"]
    settings.on_page = False
    App.back_action(app)()
    assert shown[-1] == 0  # back to the first tab
    app.nav.selected_index = 0
    settings.view.visible = False
    home.view.visible = True  # a tool's page on Home: back to its field and tiles
    App.back_action(app)()
    assert shown[-1] == "tiles"
    home.current = None
    assert App.back_action(app) is None  # the first tab: the app is left


# The Download tab

tab = test_download_options.tab  # the fixture: the Download tab with a video analyzed


async def test_site_preset_recent_links_and_montage(tab):
    app = tab.app
    app.remember = app.settings.__setitem__
    app.settings["presets"] = [{"name": "Music", "kind": "audio", "fmt": "mp3", "sites": ["YouTube"]}]
    await tab.analyze()  # the same video again: YouTube's preset is used at once
    assert (tab.kind_dd.value, tab.format_dd.value) == ("audio", "mp3")
    assert app.settings["recent"][0] == test_download_options.URL
    tab.url_input.value = "youtube"
    tab.show_recent()
    assert tab.recent_box.visible and tab.recent_list.controls[0].content.controls[1].value == test_download_options.URL
    tab.clip.start.value, tab.clip.end.value = "0:10", "0:20"
    tab.add_to_montage()
    assert tab.montage.parts[0]["clip"] == [10.0, 20.0] and len(tab.montage.list.controls) == 1
    assert tab.montage.format_dd.value == "mp3"  # sound only: the montage too


async def test_gif_back_and_forth(tab):
    test_download_options.choose(tab, "gif", "gif")
    assert tab.bounce_switch.visible
    tab.bounce_switch.value = True
    tab.clip.start.value, tab.clip.end.value = "0:01", "0:05"
    await tab.download()
    assert tab.sent[-1]["edits"] == {"bounce": True}
