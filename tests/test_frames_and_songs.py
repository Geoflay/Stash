"""The moment of an image chosen to the frame, the image's quality menu, the GIF's single format, and the songs
of a folder given their tags (no network, no window)."""

import pytest
from conftest import ffmpeg, needs_ffmpeg

import core.music as music
import test_download_options
from core.streams import frame_rate
from core.tags import tag_file
from core.units import format_moment, parse_time
from test_download_options import choose

tab = test_download_options.tab  # the fixture: the Download tab with a video analyzed


def test_moments_to_the_frame():
    assert format_moment(83.417) == "1:23.417" and format_moment(65) == "1:05" and format_moment(0) == ""
    assert parse_time("1:23.417") == pytest.approx(83.417)
    assert frame_rate([{"fps": 25}, {"fps": 60.0}, {"fps": None}]) == 60.0 and frame_rate([]) == 30.0


async def test_image_moment_and_menu(tab):
    tab.clip.fps = 25.0
    choose(tab, "image", "png")
    assert tab.clip.steps.visible
    assert all(" · " not in option.text for option in tab.quality_dd.options)  # no video sizes for an image
    tab.clip.set_moment(10.0)
    await tab.clip.step_moment(1)  # one frame after
    assert tab.clip.start.value == "0:10.040"
    await tab.clip.step_moment(-1.0)  # one second before
    assert tab.clip.start.value == "0:09.040"
    await tab.download()
    assert tab.sent[-1]["clip"] == [pytest.approx(9.04), None]
    choose(tab, "video", "mp4")
    assert any(" · " in option.text for option in tab.quality_dd.options)  # the video's sizes again


async def test_gif_has_no_format_menu(tab):
    choose(tab, "gif", "gif")
    assert not tab.format_dd.visible
    choose(tab, "video", "mp4")
    assert tab.format_dd.visible


@needs_ffmpeg
def test_songs_of_a_folder(media_dir, tmp_path, monkeypatch):
    song = tmp_path / "03 - Daft Punk - One More Time.mp3"
    ffmpeg("-i", media_dir / "tone.mp3", song)
    read = music.read_song(song)  # no tags: its name says it
    assert (read["artist"], read["title"], read["cover"]) == ("Daft Punk", "One More Time", False)
    asked = []

    def found(info, tags=True, lyrics=True):
        asked.append((info["artist"], info["track"]))
        return {"title": "One More Time", "artist": "Daft Punk", "album": "Discovery",
                "cover": ((media_dir / "cover.jpg").read_bytes(), "image/jpeg")}
    monkeypatch.setattr(music, "song_tags", found)
    _, new = music.find_song_tags(song)
    assert asked == [("Daft Punk", "One More Time")] and set(new) == {"title", "artist", "album", "cover"}
    tag_file(song, new)
    read = music.read_song(song)
    assert music.is_tagged(read) and read["album"] == "Discovery"
    _, new = music.find_song_tags(song)
    assert new == {}  # nothing more to write: its cover art is kept


def test_music_card_is_built(fake_app):
    from ui.app import App
    from ui.edit_more import MusicCard
    fake_app.card_pad = 12
    fake_app.stretch_card = lambda controls, **kwargs: App.stretch_card(fake_app, controls, **kwargs)  # a real card
    card = MusicCard(fake_app)
    assert card.table.box in card.card.content.content.controls and not card.write_btn.visible
    fake_app.page.run_task = lambda *a: None
    card.add(["a.mp4", "b.mp3"])  # sound files only
    assert [r["path"].name for r in card.files] == ["b.mp3"]


async def test_songs_are_looked_up_a_few_at_a_time(fake_app, monkeypatch, tmp_path):
    import asyncio
    import threading
    import time
    import ui.edit_more
    from ui.edit_more import SONGS_AT_ONCE, MusicCard
    at_once, most = [0], [0]
    lock = threading.Lock()

    def find(path, tags, lyrics):
        with lock:
            at_once[0] += 1
            most[0] = max(most[0], at_once[0])
        time.sleep(0.2)
        with lock:
            at_once[0] -= 1
        return {}, {"title": path.stem}
    song = {"title": "", "artist": "", "album": "", "cover": False, "lyrics": False, "duration": 60, "tags": {}}
    monkeypatch.setattr(ui.edit_more, "find_song_tags", find)
    monkeypatch.setattr(ui.edit_more, "read_song", lambda path: dict(song))
    fake_app.page.run_task = lambda *a: None
    fake_app.notify_if_away = fake_app.check_storage_access
    card = MusicCard(fake_app)
    card.add([tmp_path / ("%d.mp3" % i) for i in range(8)])
    started = time.monotonic()
    await card.run()
    assert most[0] == SONGS_AT_ONCE and time.monotonic() - started < 8 * 0.2  # not one after the other
    assert all(r["found"] == {"title": r["path"].stem} and r["write"] for r in card.files)
    assert card.write_btn.visible and card.write_btn.content == "Write the tags (8)"
    await asyncio.sleep(0)
