"""The Library (the history), Plex and Jellyfin layout, night hours, the pause between playlist videos, time
left, podcasts from Apple links, burned subtitles' style and "Paste & download" (no network, no window)."""

import asyncio
import functools
import os
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from conftest import files_in, needs_ffmpeg

import core.podcast as podcast
import ui.queue_tab
from core.editor import force_style
from core.history import entry_size, entry_type, file_state, filter_history, forget_missing, load_history
from core.mediaserver import episode_place, write_extras
from core.network import NETWORK, current_ratelimit
from core.schedule import is_night, night_hours
from core.storage import write_json
from test_queue_and_playlist import PARAMS, FakeDownload, download_list, item_list
from ui.app import App
from ui.history_tab import HistoryTab
from ui.progress import TimeLeft
from ui.queue_tab import QueueTab


@pytest.fixture
def fake(monkeypatch):
    download = FakeDownload()
    monkeypatch.setattr(ui.queue_tab, "download_media", download)
    return download


# Library

def test_library_entries(tmp_path):
    song, gone = tmp_path / "song.mp3", tmp_path / "gone.mp4"
    song.write_bytes(b"x" * 1000)
    items = [{"title": "Song", "format": "MP3", "path": str(song)}, {"title": "Clip", "format": "MP4", "path": str(gone)},
             {"title": "List", "format": "PLAYLIST", "path": str(tmp_path)}, {"title": "Web", "format": "JPG"}]
    assert [entry_type(e) for e in items] == ["Music", "Videos", "Playlists", "Images"]
    assert [file_state(e) for e in items] == ["present", "missing", "present", ""]
    assert entry_size(items[0]) == 1000 and entry_size(items[1]) == 0 and entry_size(items[2]) == 1000
    assert [e["title"] for e in filter_history(items, kind="Music")] == ["Song"]
    assert [e["title"] for e in filter_history(items, state="missing")] == ["Clip"]
    write_json("history.json", items)
    assert forget_missing() == 1 and [e["title"] for e in load_history()] == ["Song", "List", "Web"]


def test_library_tab(fake_app, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x" * 2048)
    write_json("history.json", [{"title": "Song", "format": "MP3", "path": str(song)},
                                {"title": "Clip", "format": "MP4", "path": str(tmp_path / "gone.mp4")}])
    fake_app.can_open_files = lambda: True
    tab = HistoryTab(fake_app)
    assert tab.count.value == "2 downloads · 2.0 KB on this device · 1 deleted since" and tab.forget_btn.visible
    tab.type_dd.value = "Videos"
    tab.on_filter()
    assert tab.count.value == "1 of 2 downloads · 1 deleted since"
    tab.on_forget()
    assert len(tab.items) == 1 and not tab.forget_btn.visible


# Plex and Jellyfin

def test_episode_layout(tmp_path):
    fields = {"title": "First: video", "uploader": "My Channel", "date": "2024-05-31", "platform": "Youtube",
              "id": "abc"}
    season, name, nfo = episode_place(tmp_path, fields, "About <it>")
    assert season == tmp_path / "My Channel" / "Season 2024"
    assert name == "My Channel - S2024E053101 - First video.mp4"
    assert "<episode>053101</episode>" in nfo and "<plot>About &lt;it&gt;</plot>" in nfo
    assert '<uniqueid type="youtube" default="true">abc</uniqueid>' in nfo
    season.mkdir(parents=True)
    (season / name).write_bytes(b"")
    write_extras(season / name, nfo, b"jpeg")
    assert episode_place(tmp_path, fields)[1].startswith("My Channel - S2024E053102")  # the same day
    assert files_in(tmp_path) == ["My Channel/Season 2024/My Channel - S2024E053101 - First video-thumb.jpg",
                                  "My Channel/Season 2024/My Channel - S2024E053101 - First video.mp4",
                                  "My Channel/Season 2024/My Channel - S2024E053101 - First video.nfo",
                                  "My Channel/tvshow.nfo"]


async def test_queue_lays_out_for_plex(fake_app, fake):
    fake_app.settings["media_server"] = True
    await QueueTab(fake_app).run_download(dict(PARAMS))
    assert files_in(fake_app.destination) == [
        "Someone/Season 2024/Someone - S2024E053101 - Title from yt-dlp.mp4",
        "Someone/Season 2024/Someone - S2024E053101 - Title from yt-dlp.nfo", "Someone/tvshow.nfo"]


# Night hours

@pytest.mark.parametrize("hours, hour, night", [((1, 7), 3, True), ((1, 7), 7, False), ((22, 6), 23, True),
                                                ((22, 6), 5, True), ((22, 6), 12, False)])
def test_is_night(hours, hour, night):
    assert is_night(hours, datetime(2026, 1, 1, hour)) == night


def test_night_speed_limit(monkeypatch):
    monkeypatch.setitem(NETWORK, "ratelimit", 1000)
    monkeypatch.setitem(NETWORK, "night", (22, 6))
    monkeypatch.setitem(NETWORK, "night_ratelimit", None)
    assert current_ratelimit(datetime(2026, 1, 1, 23)) == 1000  # not turned on
    monkeypatch.setitem(NETWORK, "night_speed", True)
    assert current_ratelimit(datetime(2026, 1, 1, 23)) is None and current_ratelimit(datetime(2026, 1, 1, 12)) == 1000
    assert night_hours({"night_start": 22, "night_end": 99}) == (22, 7)


async def test_waits_for_the_night_unless_started_now(fake_app, fake):
    hour = datetime.now().hour
    fake_app.settings.update(night_downloads=True, night_start=(hour + 2) % 24, night_end=(hour + 3) % 24)
    fake_app.battery = None
    fake_app.schedule_wait_reason = functools.partial(App.schedule_wait_reason, fake_app)
    fake_app.wait_for_schedule = functools.partial(App.wait_for_schedule, fake_app)
    queue = QueueTab(fake_app)
    task = asyncio.ensure_future(queue.run_download(dict(PARAMS)))
    await asyncio.sleep(0.3)
    [job] = queue.jobs
    assert job.text.value.startswith("Waiting for the night") and job.now_btn.visible and not fake.calls
    job.force_start()
    await asyncio.wait_for(task, 5)
    assert len(fake.calls) == 1 and not job.now_btn.visible


async def test_pause_between_playlist_videos(fake_app, fake, monkeypatch):
    monkeypatch.setattr(ui.queue_tab, "PLAYLIST_PAUSES", (0, 1))
    fake_app.settings["playlist_pause"] = 1
    items = item_list(fake_app)
    started = time.monotonic()
    await download_list(items, fake_app)
    assert len(fake.calls) == 3 and time.monotonic() - started >= 2  # two pauses of a second


def test_time_left(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    left = TimeLeft()
    assert left.text(0.0, "Converting…") == ("", None)
    clock[0] += 60
    assert left.text(0.5, "Converting…") == ("about 1 min left", None)  # half done in a minute
    assert left.text(0.5, "Downloading…", "1 MB / 2 MB - 1 MB/s - about 5 s left") == (
        "about 5 s left", "1 MB / 2 MB - 1 MB/s")  # yt-dlp's own, from the speed


# Podcasts

def test_apple_podcasts_link(monkeypatch):
    asked = []
    monkeypatch.setattr(podcast, "http_get", lambda url: asked.append(url) or
                        b'{"results": [{"feedUrl": "https://feeds.test/show.rss"}]}')
    assert podcast.feed_address("https://podcasts.apple.com/fr/podcast/a-show/id123456") == "https://feeds.test/show.rss"
    assert asked == ["https://itunes.apple.com/lookup?id=123456&entity=podcast"]
    assert podcast.maybe_feed("https://podcasts.apple.com/fr/podcast/a-show/id123456")
    assert not podcast.maybe_feed("https://podcasts.apple.com/fr/podcast/a-show/id123456?i=1000")  # one episode


def test_episode_description():
    feed = podcast.parse_feed(b'<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>S</title>'
                              b'<item><title>E</title><description>&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;'
                              b'&lt;p&gt;Part two&lt;/p&gt;</description><enclosure url="https://x.test/e.mp3" '
                              b'type="audio/mpeg"/></item></channel></rss>')
    assert feed["entries"][0]["description"] == "Hello & welcome\nPart two"


# Subtitles drawn onto the picture

def test_subtitle_style():
    assert force_style({}) == ("FontSize=18,PrimaryColour=&H00FFFFFF,BorderStyle=1,Outline=1.5,Shadow=0,"
                               "OutlineColour=&H00000000,Alignment=2,MarginV=20")
    assert "FontSize=24" in force_style({"size": "large", "position": "top"}) and "Alignment=8" in force_style(
        {"position": "top"})


@needs_ffmpeg
def test_styled_burned_subtitles(media_dir, tmp_path):
    from core.editor import can_burn_subtitles, edit_media, probe_media
    if not can_burn_subtitles():
        pytest.skip("this ffmpeg cannot draw subtitles (no libass)")
    out, _ = edit_media(media_dir / "video.mp4", "mp4", probe_media(media_dir / "video.mp4"),
                        {"subs_file": media_dir / "subs.srt", "subs_mode": "burn",
                         "subs_style": {"size": "large", "color": "yellow", "outline": "box", "position": "top"}},
                        tmp_path, lambda *a: None)
    assert out.exists() and os.path.getsize(out) > 0


# "Paste & download" (Android tile and widget)

async def test_paste_and_download():
    clipboard = ["", "https://www.youtube.com/watch?v=abc"]  # empty at first: the app was not on screen yet
    done = []

    async def get():
        return clipboard.pop(0)

    async def analyze_link(text):
        done.append(("analyze", text))
        download.result_card.visible = True

    async def start_download():
        done.append("download")
    download = SimpleNamespace(analyze_link=analyze_link, download=start_download,
                               result_card=SimpleNamespace(visible=False),
                               items=SimpleNamespace(card=SimpleNamespace(visible=False)))
    app = SimpleNamespace(show_view=lambda i: None, page=SimpleNamespace(update=lambda: None),
                          clipboard=SimpleNamespace(get=get), download=download)
    await App.paste_and_download(app)
    assert done == [("analyze", "https://www.youtube.com/watch?v=abc"), "download"]
