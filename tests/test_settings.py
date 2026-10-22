"""The Settings tab in categories, the theme, and finding or downloading ffmpeg (no network, no window)."""

import functools
import http.server
import io
import sys
import threading
import zipfile
from types import SimpleNamespace

import flet as ft
import pytest

import core.ffmpeg_setup as setup
from core.storage import save_settings
from ui.app import App
from ui.settings_tab import CATEGORIES, SettingsTab


@pytest.fixture
def settings_tab(fake_app):
    fake_app.edit = None
    fake_app.can_pick_folder, fake_app.storage_access = True, None
    fake_app.remember = fake_app.settings.__setitem__
    fake_app.section = lambda title, description, *controls: SimpleNamespace(title=title, controls=controls,
                                                                             description=description)
    fake_app.backup_folder = lambda: App.backup_folder(fake_app)
    fake_app.cookies_browser, fake_app.cookies_browsers = (lambda: "none"), (lambda: ["firefox"])
    fake_app.playlist_limit = lambda site: 100
    fake_app.page.theme_mode = ft.ThemeMode.SYSTEM
    return SettingsTab(fake_app)


def titles(tab):
    """The names of the tiles shown (categories, or sub-categories)."""
    return [tile.content.controls[1].controls[0].value for tile in tab.body.controls]


def test_categories(settings_tab):
    tab = settings_tab
    assert [tile.content.controls[1].controls[0].value for tile in tab.tiles.controls] == [c[1] for c in CATEGORIES]
    assert titles(tab) == ["Destination folder", "Files", "Background downloads", "Other devices",
                           "Keyboard shortcuts"]  # General first: its sub-categories
    tab.select("advanced")
    assert titles(tab) == ["ffmpeg", "yt-dlp", "Video codec", "App updates"]
    assert tab.app.settings["settings_category"] == "advanced"
    tab.open_group("ytdlp")  # a sub-category: its settings, one per line
    assert tab.page_title.value == "yt-dlp" and tab.body.controls == [tab.cards["ytdlp"]] and tab.back_btn.visible
    tab.back()
    assert titles(tab) == ["ffmpeg", "yt-dlp", "Video codec", "App updates"]
    tab.select("appearance")
    assert titles(tab) == ["Language", "Theme", "Simple mode"]


def test_theme(settings_tab):
    tab = settings_tab
    tab.app.apply_theme = lambda: App.apply_theme(tab.app)
    tab.app.corner_radius = lambda default=None: App.corner_radius(tab.app, default)
    theme = tab.cards["appearance"].controls[0].controls[0]
    theme.value = "dark"
    theme.on_select(SimpleNamespace(control=theme))
    swatches = tab.cards["appearance"].controls[1].content.controls[1].controls  # the accent colours: a tap chooses one
    swatches[2].on_click(None)
    assert tab.app.settings["accent"] == "teal" and swatches[2].content is not None and swatches[0].content is None
    pure_black = tab.cards["appearance"].controls[-1]
    pure_black.value = True
    pure_black.on_change(SimpleNamespace(control=pure_black))
    assert tab.page.theme_mode == ft.ThemeMode.DARK and tab.app.settings["theme"] == "dark"
    assert tab.page.dark_theme.color_scheme_seed == "#4db6ac" and tab.page.dark_theme.scaffold_bgcolor == ft.Colors.BLACK


def test_find_ffmpeg(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen" / "bin"
    chosen.mkdir(parents=True)
    (chosen / setup.program("ffmpeg")).write_bytes(b"")
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert setup.find_ffmpeg() == ("/usr/bin/ffmpeg", "path")
    setup.downloaded_dir().mkdir(parents=True)
    (setup.downloaded_dir() / setup.program("ffmpeg")).write_bytes(b"")
    assert setup.find_ffmpeg()[1] == "downloaded"
    save_settings({"ffmpeg_dir": str(tmp_path / "chosen")})  # its "bin" folder is looked into too
    assert setup.find_ffmpeg() == (str(chosen / setup.program("ffmpeg")), "folder")


def test_download_ffmpeg(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ffmpeg-9.0-essentials_build/bin/%s" % setup.program("ffmpeg"), b"ffmpeg program")
        z.writestr("ffmpeg-9.0-essentials_build/bin/%s" % setup.program("ffprobe"), b"ffprobe program")
        z.writestr("ffmpeg-9.0-essentials_build/README.txt", b"read me")
    (tmp_path / "build.zip").write_bytes(archive.getvalue())
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setitem(setup.DOWNLOADS, sys.platform, ["http://127.0.0.1:%d/build.zip" % server.server_port])
    messages = []
    try:
        program = setup.download_ffmpeg(lambda fraction, message: messages.append((fraction, message)))
    finally:
        server.shutdown()
    assert program == str(setup.downloaded_dir() / setup.program("ffmpeg"))
    assert sorted(p.name for p in setup.downloaded_dir().iterdir()) == sorted(
        [setup.program("ffmpeg"), setup.program("ffprobe")])
    assert messages[-2][0] == 1.0 and messages[-1] == (None, "Unpacking ffmpeg…")


def test_phone_settings_list(settings_tab, fake_app):
    fake_app.needs_bytes, fake_app.desktop = True, False
    tab = SettingsTab(fake_app)
    assert tab.home.visible and not tab.category_page.visible  # the list of categories first
    tab.select("network")
    assert not tab.home.visible and tab.category_page.visible and tab.page_title.value == "Network"
    assert titles(tab) == ["Network", "Cookies (optional)"]
    tab.open_group("cookies")
    tab.back()  # back to the category
    assert tab.category_page.visible and titles(tab) == ["Network", "Cookies (optional)"]
    tab.back()  # back to the list
    assert tab.home.visible and not tab.category_page.visible
