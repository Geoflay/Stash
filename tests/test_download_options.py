"""The Download tab's options, from an analyzed link to what is sent to the queue (no network, no window):
size limit, GIF or still image, tags corrected before saving, several clips, chapters left out."""

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

import ui.download_tab
from core.backend import BACKEND
from ui.download_tab import DownloadTab

URL = "https://www.youtube.com/watch?v=abc"
# What core.analyze.fetch_info returns for a two-minute video.
VIDEO = {"url": URL, "title": "Song (Official Video)", "uploader": "Channel", "upload_date": "20240531",
         "video_id": "abc", "duration": 120, "thumbnail": "", "platform": "Youtube", "slideshow": False,
         "images": [], "images_error": "", "audio_url": "", "measured": {}, "subtitles": [], "preview": None,
         "formats": [{"height": 720, "vcodec": "avc1", "acodec": "none", "size": 20_000_000, "tbr": None,
                      "abr": None, "ext": "mp4"},
                     {"height": None, "vcodec": "none", "acodec": "mp4a", "size": 2_000_000, "tbr": None,
                      "abr": 128, "ext": "m4a"}],
         "tags": {"title": "Song", "artist": "Channel", "album": ""},
         "chapters": [{"title": "Intro", "start": 0.0, "end": 30.0}, {"title": "Verse", "start": 30.0, "end": 120.0}]}


@pytest.fixture
async def tab(fake_app, monkeypatch):
    """The Download tab with VIDEO analyzed; `tab.sent` lists what Download sent to the queue."""
    sent = []

    async def run_download(params):
        sent.append(params)
    fake_app.queue = SimpleNamespace(run_download=run_download)
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(VIDEO))
    tab = DownloadTab(fake_app)
    tab.url_input.value = URL
    await tab.analyze()
    assert tab.result_card.visible
    tab.sent = sent
    return tab


def choose(tab, kind, fmt):
    tab.show_kind(kind)
    tab.format_dd.value = fmt
    tab.refresh_options()


def status_text(tab):
    return tab.download_status.content.controls[-1].controls[0].value


async def test_video_without_chapters(fake_app, monkeypatch):
    sent = []

    async def run_download(params):
        sent.append(params)
    fake_app.queue = SimpleNamespace(run_download=run_download)
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(VIDEO, chapters=[]))
    tab = DownloadTab(fake_app)
    tab.url_input.value = URL
    await tab.analyze()
    await tab.download()
    assert len(sent) == 1 and sent[0]["chapters"] is None and sent[0]["remove_ranges"] is None


async def test_only_corrected_tags_are_sent(tab):
    assert {key: field.value for key, field in tab.tag_fields.items() if key in VIDEO["tags"]} == VIDEO["tags"]
    await tab.download()
    assert tab.sent[-1]["tag_edits"] is None  # nothing changed: the site's tags are written
    tab.tag_fields["title"].value = "Song (live)"
    tab.tag_fields["album"].value = "Live album "
    await tab.download()
    assert tab.sent[-1]["tag_edits"] == {"title": "Song (live)", "album": "Live album"}


async def test_size_limit(tab):
    choose(tab, "video", "mp4")
    assert tab.size_box.visible == (BACKEND == "ffmpeg")
    if BACKEND != "ffmpeg":
        pytest.skip("the size limit needs ffmpeg")
    tab.size_field.value = "ten"
    await tab.download()
    assert tab.sent == [] and "number of MB" in status_text(tab)
    tab.size_field.value = "9,5"
    tab.refresh_options()
    assert "bigger than 9.5 MB" in tab.estimate_text.value
    await tab.download()
    assert tab.sent[-1]["max_mb"] == 9.5


@pytest.mark.skipif(BACKEND != "ffmpeg", reason="speed and chapters need ffmpeg")
async def test_speed_and_chapters(tab):
    choose(tab, "audio", "mp3")
    assert tab.speed_row.visible and tab.chapters_box.visible and tab.clip.box.visible
    assert tab.chapters_note.value == "2 chapters: Intro, Verse"
    await tab.download()
    assert tab.sent[-1]["speed"] is None and tab.sent[-1]["chapters"] is None
    tab.speed.field.value = "1.5"
    tab.chapters_switch.value = True
    tab.refresh_options()
    assert not tab.clip.box.visible and not tab.size_box.visible  # each chapter is its own file
    await tab.download()
    assert tab.sent[-1]["speed"] == 1.5 and tab.sent[-1]["chapters"] == VIDEO["chapters"]


async def test_exact_speed(tab):
    choose(tab, "video", "mp4")
    tab.speed.field.value = "1,37"
    tab.speed.on_field(None)
    assert tab.speed.slider.value == pytest.approx(0.1367, abs=0.001)  # log10(1.37): the slider follows
    await tab.download()
    assert tab.sent[-1]["speed"] == 1.37
    refreshed = []
    tab.speed.on_change = lambda: refreshed.append(tab.speed.field.value)
    tab.speed.slider.value = -1.0  # all the way to the left
    tab.speed.on_slider(None)
    assert tab.speed.field.value == "0.1" and refreshed == []  # only the field follows while dragging
    tab.speed.on_slider_end(None)
    assert refreshed == ["0.1"]  # the tab is refreshed once released
    tab.speed.field.value = "12"
    await tab.download()
    assert "between 0.1 and 10" in status_text(tab) and tab.sent[-1]["speed"] == 1.37


async def test_already_downloaded_asks_first(tab, fake_app):
    from core.history import add_history
    choose(tab, "video", "mp4")
    await tab.download()  # first time: no question
    add_history({"url": URL, "title": "Song", "platform": "Youtube", "format": "MP4", "name": "Song.mp4",
                 "choice": tab.sent[-1]["choice"]})
    tab.sent.clear()
    answers, dialogs = [False, True], []

    def show_dialog(dialog):
        dialogs.append(dialog.content.value)
        click = dialog.actions[1 if answers.pop(0) else 0].on_click
        asyncio.get_running_loop().call_soon(click, None)
    fake_app.page.show_dialog, fake_app.page.pop_dialog = show_dialog, lambda: None
    await tab.download()
    assert tab.sent == [] and "Song.mp4" in dialogs[0]  # Cancel
    await tab.download()
    assert len(tab.sent) == 1  # Download again
    choose(tab, "audio", "mp3")
    await tab.download()  # another file of the same video: no question
    choose(tab, "video", "mp4")
    fake_app.settings["warn_duplicates"] = False  # turned off in Settings
    await tab.download()
    assert len(tab.sent) == 3 and len(dialogs) == 2


PLAYER = {"videos": [{"url": "https://cdn.test/720", "height": 720, "sound": False, "headers": {}, "rank": [0, 0]},
                     {"url": "https://cdn.test/360", "height": 360, "sound": True, "headers": {}, "rank": [0, 0]}],
          "audio_url": "https://cdn.test/a", "audio_headers": {}, "audio_only": False, "silent": False}


@pytest.fixture
async def player_tab(fake_app, monkeypatch):
    from ui.download_tab import DownloadTab
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(VIDEO, thumbnail="t.jpg",
                                                                                  player=PLAYER))
    fake_app.queue = SimpleNamespace(run_download=None)
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    fake_app.settings["preview_volume"] = 30
    fake_app.remember = fake_app.settings.__setitem__
    tab = DownloadTab(fake_app)
    tab.url_input.value = URL
    await tab.analyze()
    return tab


async def test_watch_before_downloading(player_tab):
    tab = player_tab
    assert tab.can_play and tab.play_badge.visible  # a tap on the thumbnail plays it
    choose(tab, "video", "mp4")
    await tab.toggle_player()
    preview = tab.preview
    assert preview.box.visible and not tab.thumb_box.visible  # the video takes the thumbnail's place
    assert tab.close_btn.visible
    assert not preview.audio_bar.visible  # a video has its own controls
    assert tab.player.volume == 30 and preview.volume.visible  # the level set last time
    assert tab.player.controls.play_and_pause_on_tap  # a click on the picture pauses
    assert tab.player.playlist[0].resource.startswith("edl://")  # best quality: 720p picture + sound
    preview.volume.value = 80
    preview.on_volume(None)
    preview.remember_volume(None)
    assert tab.player.volume == 80 and tab.app.settings["preview_volume"] == 80
    preview.toggle_mute()  # the sound icon mutes
    assert tab.player.muted and preview.mute_btn.icon == ft.Icons.VOLUME_OFF and tab.player.volume == 80
    preview.toggle_mute()
    assert not tab.player.muted
    tab.no_sound_switch.value = True  # the file will have no sound: neither has the preview
    tab.refresh_options()
    assert tab.player.muted
    await tab.analyze()  # another analysis: the player stops
    assert tab.player is None and not preview.box.visible and tab.thumb_box.visible and not tab.close_btn.visible


async def test_preview_follows_the_options(player_tab):
    tab = player_tab
    choose(tab, "video", "mp4")
    tab.quality_dd.value = "360"
    await tab.toggle_player()
    assert tab.player.playlist[0].resource == "https://cdn.test/360"  # the 360p file has its sound
    first = tab.player
    tab.speed.field.value = "1.5"
    tab.speed.on_field(None)
    assert tab.player is first and first.playback_rate == 1.5  # the speed changes at once
    tab.clip.start.value, tab.clip.end.value = "0:10", "0:20"
    tab.refresh_options()
    await asyncio.sleep(1.2)  # restarted once the options stop changing
    assert tab.player is not first
    assert tab.player.playlist[0].resource == "edl://%20%https://cdn.test/360,10,10"
    assert tab.player.playback_rate == 1.5
    choose(tab, "audio", "mp3")
    await asyncio.sleep(1.2)
    assert tab.player.playlist[0].resource == "edl://%18%https://cdn.test/a,10,10"  # the sound alone
    preview = tab.preview
    assert preview.audio_bar.visible and tab.thumb_box.visible  # an audio bar instead of a black frame
    assert preview.box.height == 1 and preview.box.opacity == 0
    tab.close_player()
    assert not preview.audio_bar.visible


async def test_audio_track_and_sponsorblock(fake_app, monkeypatch):
    from ui.download_tab import DownloadTab
    tracks = [{"lang": "en-US", "name": "English (US) original", "default": True},
              {"lang": "fr", "name": "French", "default": False}]
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(VIDEO, audio_tracks=tracks))
    marked = [{"start": 0.0, "end": 10.0, "category": "intro"}, {"start": 50.0, "end": 70.0, "category": "sponsor"},
              {"start": 60.0, "end": 80.0, "category": "selfpromo"}]
    monkeypatch.setattr(ui.download_tab, "fetch_segments", lambda video_id, categories: marked)
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    sent = []

    async def run_download(params):
        sent.append(params)
    fake_app.queue = SimpleNamespace(run_download=run_download)
    fake_app.settings["sponsorblock"] = ["sponsor", "intro"]
    tab = DownloadTab(fake_app)
    tab.url_input.value = URL
    await tab.analyze()
    await asyncio.sleep(0.1)  # the SponsorBlock segments are fetched after the analysis
    choose(tab, "video", "mp4")
    assert tab.audio_track_row.visible and tab.audio_picker.value == ["en-US"]  # the original at first
    assert tab.sponsor_switch.visible == (BACKEND == "ffmpeg")
    if BACKEND == "ffmpeg":
        # Intro 0-10 and sponsor 50-70 (self-promotion is not chosen): cut, gap, cut, gap.
        assert [c.bgcolor == ft.Colors.ORANGE for c in tab.segments_bar.controls] == [True, False, True, False]
        assert [c.expand for c in tab.segments_bar.controls] == [100, 400, 200, 500]
        assert "2 parts (0:30 in all)" in tab.segments_note.value
    await tab.download()
    assert sent[-1]["audio_lang"] is None and "audio" not in sent[-1]["choice"]  # the original track
    assert sent[-1]["sponsorblock"] == (["sponsor", "intro"] if BACKEND == "ffmpeg" else None)
    tab.audio_picker.remove("en-US")
    tab.audio_picker.menu.value = "fr"
    tab.audio_picker.on_add()
    tab.sponsor_switch.value = False  # not for this download
    tab.refresh_options()
    assert not tab.segments_box.visible
    await tab.download()
    assert sent[-1]["audio_lang"] == ["fr"] and sent[-1]["choice"]["audio"] == ["fr"] and sent[-1]["sponsorblock"] is None
    tab.audio_picker.menu.value = "en-US"
    tab.audio_picker.on_add()  # both tracks, French first
    await tab.download()
    assert sent[-1]["audio_lang"] == ["fr", "en-US"]
    tab.clip.start.value = "0:10"
    tab.refresh_options()
    assert not tab.sponsor_switch.visible  # not with a clip


async def test_clipboard_watch(tab, fake_app):
    texts = ["copied before the watch", "look: https://www.youtube.com/watch?v=xyz"]

    async def clipboard_text():
        return texts.pop(0) if len(texts) > 1 else texts[0]
    fake_app.clipboard = SimpleNamespace(get=clipboard_text)
    fake_app.show_view = lambda index: None
    fake_app.settings["watch_clipboard"] = True
    analyzed = []

    async def analyze_link(link):
        analyzed.append(link)
    tab.analyze_link = analyze_link
    watch = asyncio.create_task(tab.watch_clipboard())
    try:
        for _ in range(60):
            if analyzed:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.2)  # the same text again: not analyzed twice
    finally:
        watch.cancel()
    assert analyzed == ["https://www.youtube.com/watch?v=xyz"]  # not what was copied before


@pytest.mark.skipif(BACKEND != "ffmpeg", reason="GIFs and images need ffmpeg")
async def test_gif_needs_a_short_passage(tab):
    choose(tab, "gif", "gif")
    assert [o.key for o in tab.format_dd.options] == ["gif"]  # its own type, apart from the images
    assert tab.clip.box.visible and tab.clip.end.visible and tab.size_box.visible
    assert not tab.quality_row.visible and not tab.tags_box.visible
    await tab.download()  # the whole two-minute video
    assert tab.sent == [] and "30 seconds" in status_text(tab)
    tab.clip.start.value, tab.clip.end.value = "1:00", "1:12"
    await tab.download()
    assert tab.sent[-1]["fmt"] == "gif" and tab.sent[-1]["clip"] == [60.0, 72.0]


@pytest.mark.skipif(BACKEND != "ffmpeg", reason="GIFs and images need ffmpeg")
async def test_still_image_takes_one_moment(tab):
    tab.clip.end.value = "0:30"  # typed for a clip earlier: an image has no end
    choose(tab, "image", "jpg")
    assert [o.key for o in tab.format_dd.options] == ["thumbnail", "jpg", "png"]  # the full-size thumbnail first
    assert tab.clip.moment.visible and not tab.clip.slider.visible and not tab.clip.end.visible
    assert tab.quality_row.visible and not tab.size_box.visible
    tab.clip.start.value = "1:05"
    await tab.download()
    assert tab.sent[-1]["fmt"] == "jpg" and tab.sent[-1]["clip"] == [65.0, None]
    choose(tab, "video", "mp4")  # back to a video: the clip has its end again
    assert tab.clip.end.visible and tab.clip.slider.visible


@pytest.mark.skipif(BACKEND != "ffmpeg", reason="clips and chapters need ffmpeg")
async def test_several_clips(tab):
    choose(tab, "video", "mp4")
    tab.clip.start.value, tab.clip.end.value = "0:10", "0:20"
    tab.clip.add_clip()
    assert tab.clip.clips == [(10.0, 20.0)] and tab.clip.start.value == "" and tab.clip.clips_row.visible
    tab.clip.start.value = "1:30"  # until the end
    tab.refresh_options()
    assert not tab.size_box.visible and not tab.sponsor_switch.visible
    await tab.download()
    sent = tab.sent[-1]
    assert sent["clip"] is None and sent["chapters"] == [
        {"title": "From 0.10 to 0.20", "start": 10.0, "end": 20.0},
        {"title": "From 1.30 to 2.00", "start": 90.0, "end": 120.0}]
    assert sent["choice"]["clips"] == [[10.0, 20.0], [90.0, None]]
    tab.clip.remove_clip(0)
    tab.refresh_options()
    assert not tab.clip.several_clips() and tab.size_box.visible


@pytest.mark.skipif(BACKEND != "ffmpeg", reason="chapters need ffmpeg")
async def test_chapters_left_out(tab):
    choose(tab, "audio", "mp3")
    assert tab.chapters_btn.content == "Choose the chapters (2 of 2)"
    tab.chapter_checks[0].value = False  # no intro
    tab.refresh_options()
    assert not tab.clip.box.visible and tab.chapters_note.value == "The unchecked chapters are cut out of the file."
    await tab.download()
    assert tab.sent[-1]["remove_ranges"] == [[0.0, 30.0]] and tab.sent[-1]["chapters"] is None
    tab.chapters_switch.value = True  # one file per chapter: only the checked ones
    tab.refresh_options()
    await tab.download()
    assert tab.sent[-1]["remove_ranges"] is None and [c["title"] for c in tab.sent[-1]["chapters"]] == ["Verse"]
    tab.toggle_all_chapters()  # all again
    assert len(tab.kept_chapters()) == 2
    tab.toggle_all_chapters()
    await tab.download()
    assert status_text(tab) == "Check at least one chapter." and len(tab.sent) == 2


async def test_clipboard_link_goes_to_the_queue(tab, fake_app):
    texts = ["copied before the watch", "https://www.youtube.com/watch?v=xyz"]

    async def clipboard_text():
        return texts.pop(0) if len(texts) > 1 else texts[0]
    fake_app.clipboard = SimpleNamespace(get=clipboard_text)
    fake_app.show_view = lambda index: None
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    fake_app.settings.update(watch_clipboard=True, clipboard_queue=True)
    watch = asyncio.create_task(tab.watch_clipboard())
    try:
        for _ in range(80):
            if tab.sent:
                break
            await asyncio.sleep(0.1)
    finally:
        watch.cancel()
    assert len(tab.sent) == 1 and tab.sent[0]["url"] == "https://www.youtube.com/watch?v=abc"  # what the (fake) analysis found


async def test_photos_of_a_post_as_files(fake_app, monkeypatch):
    post = dict(VIDEO, url="https://x.com/someone/status/1", platform="Twitter", slideshow=True, formats=[],
                duration=None, chapters=[], images=[{"url": "https://pbs.twimg.com/media/A.jpg?name=orig"}] * 3)
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(post))
    sent = []

    async def run_download(params):
        sent.append(params)
    fake_app.queue = SimpleNamespace(run_download=run_download)
    tab = DownloadTab(fake_app)
    tab.url_input.value = post["url"]
    await tab.analyze()
    assert "without sound" in tab.slideshow_note.value
    choose(tab, "image", "photos")
    assert [o.key for o in tab.format_dd.options] == ["photos"] and not tab.quality_row.visible
    await tab.download()
    assert sent[-1]["fmt"] == "photos" and len(sent[-1]["images"]) == 3
    assert sent[-1]["post"] == {"audio_url": "", "platform": "Twitter"}
