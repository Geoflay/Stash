"""The welcome pages, finding a setting, the preferred languages, the music file names, presets shared and
arranged, the searches, the watched folder, files sent to a phone, the app's own updates and the Edit tab's new
changes (no network, no window)."""

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
from conftest import needs_ffmpeg

import core.app_update as app_update
import ui.download_tab
from core.editor import edit_media, preview_picture, probe_media
from core.link_server import LinkServer
from core.search import search_query
from core.storage import export_presets, import_presets
from core.watch import converted_path, preset_options, ready_files
from test_download_options import URL, VIDEO
import test_settings
from ui.app import App
from ui.download_tab import DownloadTab
from ui.welcome import Welcome

settings_tab = test_settings.settings_tab  # the fixture


# The welcome pages

def test_welcome_pages(fake_app):
    fake_app.remember = fake_app.settings.__setitem__
    fake_app.can_pick_folder = True
    fake_app.cookies_browsers, fake_app.cookies_browser = (lambda: ["firefox"]), (lambda: "none")
    done = []
    welcome = Welcome(fake_app, lambda: done.append(True))
    assert [p[0] for p in welcome.pages] == ["Welcome", "Where to save", "Simple or complete", "Notifications",
                                             "Access"]
    assert not welcome.back_btn.visible and welcome.next_btn.content == "Next"
    for _ in welcome.pages[1:]:
        welcome.next()
    assert welcome.next_btn.content == "Start" and welcome.back_btn.visible
    welcome.next()
    assert done and fake_app.settings["welcomed"] is True


def test_welcome_can_be_skipped(fake_app):
    fake_app.remember = fake_app.settings.__setitem__
    fake_app.desktop, fake_app.can_pick_folder = False, False  # the web: nothing to choose
    welcome = Welcome(fake_app, lambda: None)
    assert [p[0] for p in welcome.pages] == ["Welcome", "Simple or complete"]
    welcome.skip()
    assert fake_app.settings == {"welcomed": True, "notify": False}  # every default kept: no notification


# Settings

def test_find_a_setting(settings_tab):
    tab = settings_tab
    tab.search.value = "proxy"
    tab.on_search()
    assert tab.results.visible and not tab.tiles.visible
    (tile,) = tab.results.controls
    tab.search.value = "cover art size"  # words of a dropdown inside a card
    tab.on_search()
    assert [r.content.controls[1].controls[0].value for r in tab.results.controls] == ["Metadata"]
    tab.results.controls[0].on_click(None)  # opens it
    assert (tab.category, tab.group) == ("downloads", "metadata") and not tab.results.visible
    assert tab.search.value == ""


def test_music_file_names(fake_app):
    app = SimpleNamespace(settings={"filename_template": "{title}", "music_template": "{artist} - {song}"})
    assert App.filename_template(app, "mp3") == "{artist} - {song}"
    assert App.filename_template(app, "mp4") == "{title}"
    app.settings["music_template"] = ""
    assert App.filename_template(app, "mp3") == "{title}"


async def test_preferred_languages(fake_app, monkeypatch):
    tracks = [{"lang": "en-US", "name": "English original", "default": True}, {"lang": "fr", "name": "French",
                                                                             "default": False}]
    subs = [{"lang": "en", "name": "English", "auto": False}, {"lang": "fr", "name": "French", "auto": True}]
    monkeypatch.setattr(ui.download_tab, "fetch_info",
                        lambda url, *a, **k: dict(VIDEO, audio_tracks=tracks, subtitles=subs))
    fake_app.settings.update(pref_audio="fr", pref_subs=["fr"])
    tab = DownloadTab(fake_app)
    tab.url_input.value = URL
    await tab.analyze()
    assert tab.audio_picker.value == ["fr"]
    assert tab.subs_picker.value == ["a:fr"]  # YouTube's automatic ones: the video has no French of its own


async def test_presets_keep_the_picture_and_sound(fake_app, monkeypatch):
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(VIDEO))
    fake_app.remember = fake_app.settings.__setitem__
    tab = DownloadTab(fake_app)
    tab.url_input.value = URL
    await tab.analyze()
    tab.loudness.value = True
    tab.crop_dd.value = "9:16"
    tab.refresh_options()
    preset = tab.current_preset()
    if tab.picture_box.visible:  # with ffmpeg
        assert preset["loudness"] and preset["crop"] == "9:16"
    tab.apply_preset({"kind": "video", "fmt": "mp4"})
    assert not tab.loudness.value and tab.crop_dd.value == ""
    fake_app.settings["presets"] = [{"name": "A", "kind": "video", "fmt": "mp4"}]
    tab.presets.remove(fake_app.settings["presets"][0])  # with Undo
    assert fake_app.settings["presets"] == []
    fake_app.undo[-1][1]()
    assert [p["name"] for p in fake_app.settings["presets"]] == ["A"]


def test_presets_shared_as_text():
    text = export_presets([{"name": "Music", "kind": "audio", "fmt": "mp3"}])
    assert import_presets(text) == [{"name": "Music", "kind": "audio", "fmt": "mp3"}]
    with pytest.raises(ValueError):
        import_presets("hello")


# Searches

def test_search_queries():
    assert search_query("a song") == "ytsearch10:a song"
    assert search_query("a song", "soundcloud", count=20) == "scsearch20:a song"
    assert search_query("a song", kind="playlist") == \
        "https://www.youtube.com/results?search_query=a+song&sp=EgIQAw%3D%3D"


# The watched folder

def test_watched_folder(tmp_path):
    sizes = {}
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 10)
    (tmp_path / "notes.txt").write_text("not a video")
    assert ready_files(tmp_path, "mp3", sizes) == []  # seen once: it may still be copied
    assert ready_files(tmp_path, "mp3", sizes) == [clip]
    converted_path(clip, "mp3").parent.mkdir()
    converted_path(clip, "mp3").write_bytes(b"")
    assert ready_files(tmp_path, "mp3", sizes) == []  # already converted
    preset = {"fmt": "mp4", "quality": "720", "max_mb": 8, "loudness": True, "crop": "1:1"}
    assert preset_options(preset, {"height": 1080}) == {"height": 720, "max_bytes": 8 * 1024 * 1024,
                                                        "loudness": True, "crop": "1:1"}
    assert preset_options(dict(preset, fmt="mp3"), {"height": 1080}) == {"loudness": True,
                                                                         "max_bytes": 8 * 1024 * 1024}


# Sharing

def test_file_sent_to_a_phone(tmp_path):
    video = tmp_path / "my video.mp4"
    video.write_bytes(b"0123456789")
    server = LinkServer("secret", lambda link: None)
    server.start()
    try:
        address = server.share_file(video)
        assert address.startswith(server.address + "/file/") and address.endswith("/my%20video.mp4")
        with urllib.request.urlopen(address.replace(address.split("/")[2], "127.0.0.1:%d"
                                                    % server.server.server_port), timeout=5) as response:
            assert response.read() == b"0123456789"
            assert "attachment" in response.headers["Content-Disposition"]
        with pytest.raises(urllib.error.HTTPError):  # without its secret: nothing
            urllib.request.urlopen("http://127.0.0.1:%d/secret/file/nope/x" % server.server.server_port, timeout=5)
    finally:
        server.stop()


def test_app_updates(monkeypatch):
    assert app_update.is_newer("0.2.0", "0.1.0") and not app_update.is_newer("0.1.0", "0.1.0")

    def no_release(url, timeout=60):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(app_update, "http_get", no_release)
    assert app_update.latest_app_release() is None
    monkeypatch.setattr(app_update, "http_get", lambda url, timeout=60: json.dumps(
        {"tag_name": "v1.2.0", "html_url": "https://github.com/x/releases/tag/v1.2.0"}).encode())
    assert app_update.latest_app_release()["version"] == "1.2.0"


# The Edit tab's new changes

@needs_ffmpeg
def test_passage_speed_fades_and_text(media_dir, tmp_path):
    src = media_dir / "video.mp4"  # 4 seconds
    info = probe_media(src)
    out, _ = edit_media(src, "mp4", info, {"passage": (1.0, 3.0, 2.0), "fade_in": 1, "fade_out": 1,
                                           "overlay": {"text": "Hello"}, "overlay_place": "top-left"},
                        tmp_path, lambda *a: None)
    assert abs(probe_media(out)["duration"] - 3.0) < 0.2  # 1 s + 2 s played twice as fast + 1 s
    assert preview_picture(src, info, {"crop": "1:1", "overlay": {"text": "Hi"}}, 1.0, tmp_path)[:3] == \
        b"\xff\xd8\xff"  # a JPG


def test_queue_progress_on_the_taskbar(fake_app):
    from ui.queue_tab import QueueTab
    seen = []
    fake_app.set_taskbar_progress = seen.append
    queue = QueueTab(fake_app)
    queue.refresh()
    assert seen[-1] is None  # nothing running: no bar
