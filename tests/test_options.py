"""Exact speed, time left, proxy, duplicates, Wi-Fi only and Android notifications (no network, no phone:
Flet's services are replaced by stand-ins)."""

import asyncio
import http.server
import threading
from types import SimpleNamespace

import flet as ft
import pytest

import core.network
from core.history import find_downloaded
from core.network import NETWORK, parse_proxy, run_ytdlp
from core.speed import atempo_chain, parse_speed
from core.units import time_left_text, transfer_text
from core.web import http_get
from ui.app import App


# Speed

@pytest.mark.parametrize("text, speed", [("1", 1.0), ("1,25", 1.25), ("0.333", 0.33), ("2x", 2.0), ("10×", 10.0),
                                         ("0.1", 0.1)])
def test_parse_speed(text, speed):
    assert parse_speed(text) == speed


@pytest.mark.parametrize("text", ["", "fast", "0.05", "11", "-1", "nan"])
def test_parse_speed_refuses(text):
    with pytest.raises(ValueError, match="between 0.1 and 10"):
        parse_speed(text)


def test_atempo_chain():
    # Each atempo stays between 0.5 and 2, and together they give the speed.
    assert atempo_chain(1.25) == "atempo=1.25"
    assert atempo_chain(0.1) == "atempo=0.5,atempo=0.5,atempo=0.5,atempo=0.8"
    assert atempo_chain(10) == "atempo=2,atempo=2,atempo=2,atempo=1.25"


# Time left

def test_time_left():
    assert [time_left_text(s) for s in (4, 37, 130, 4000)] == [
        "a few seconds left", "about 35 s left", "about 2 min left", "about 1 h 07 min left"]
    assert transfer_text(50 * 2**20, 100 * 2**20, 2**19).endswith("512.0 KB/s - about 2 min left")
    assert "left" not in transfer_text(10, None, 5)  # total unknown: no guess


# Proxy

@pytest.fixture
def proxy_setting():
    yield NETWORK
    NETWORK["proxy"] = ""


def test_parse_proxy():
    assert parse_proxy("") == ""
    assert parse_proxy("127.0.0.1:8080") == "http://127.0.0.1:8080"
    assert parse_proxy("socks5://user:pass@host:1080") == "socks5://user:pass@host:1080"
    for text in ("ftp://host:21", "http://host", "socks5://:1080"):
        with pytest.raises(ValueError):
            parse_proxy(text)


def test_ytdlp_uses_the_proxy(proxy_setting, monkeypatch):
    seen = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(core.network, "YoutubeDL", FakeYoutubeDL)
    run_ytdlp(lambda ydl: None, {})
    NETWORK["proxy"] = "socks5://host:1080"
    run_ytdlp(lambda ydl: None, {})
    assert "proxy" not in seen[0] and seen[1]["proxy"] == "socks5://host:1080"


def test_other_requests_go_through_an_http_proxy(proxy_setting):
    asked = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)  # a proxy is asked for the whole address
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"through the proxy")

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        NETWORK["proxy"] = "http://127.0.0.1:%d" % server.server_port
        assert http_get("http://video-site.invalid/thumb.jpg", timeout=5) == b"through the proxy"
    finally:
        server.shutdown()
    assert asked == ["http://video-site.invalid/thumb.jpg"]


# Duplicates

def entry(url, platform="Youtube", fmt="MP4", video_id=""):
    return {"url": url, "platform": platform, "format": fmt, "video_id": video_id, "title": url}


MP4 = {"fmt": "mp4", "quality": "best", "clip": None}


def test_find_downloaded():
    items = [dict(entry("https://youtu.be/a", video_id="a"), choice=MP4), dict(entry("https://vimeo.com/1", "Vimeo"),
                                                                              choice=MP4)]
    assert find_downloaded(items, "https://vimeo.com/1", choice=MP4)["platform"] == "Vimeo"
    # Another link to the same video: found by its ID on the same site.
    assert find_downloaded(items, "https://www.youtube.com/watch?v=a", "Youtube", "a", MP4)["url"] == "https://youtu.be/a"
    assert find_downloaded(items, "https://www.youtube.com/watch?v=b", "Youtube", "b", MP4) is None
    assert find_downloaded(items, "https://example.com/x", "Vimeo", "a", MP4) is None


def test_the_same_video_saved_another_way_is_another_file():
    items = [dict(entry("https://youtu.be/a"), choice=MP4)]
    assert find_downloaded(items, "https://youtu.be/a", choice=dict(MP4, fmt="mp3")) is None
    assert find_downloaded(items, "https://youtu.be/a", choice=dict(MP4, quality="720")) is None
    assert find_downloaded(items, "https://youtu.be/a", choice=dict(MP4, clip=[10.0, 20.0])) is None
    # Saved before the choices were recorded: only the format can tell.
    old = [entry("https://youtu.be/a", fmt="MP3")]
    assert find_downloaded(old, "https://youtu.be/a", choice=dict(MP4, fmt="mp3")) is old[0]
    assert find_downloaded(old, "https://youtu.be/a", choice=MP4) is None


# Wi-Fi only and notifications (App methods, with a stand-in for the rest of App)

class FakeConnectivity:
    def __init__(self, *answers):
        self.answers = list(answers)

    async def get_connectivity(self):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


async def test_wifi_only_waits_for_wifi():
    waited = []
    app = SimpleNamespace(settings={"wifi_only": True}, connectivity=FakeConnectivity(
        [ft.ConnectivityType.MOBILE], [ft.ConnectivityType.MOBILE, ft.ConnectivityType.VPN],
        [ft.ConnectivityType.WIFI]))
    assert await App.wait_for_wifi(app, threading.Event(), lambda: waited.append(1)) is True
    assert waited == [1]  # told once, then waited until Wi-Fi came back


async def test_wifi_only_off_or_cancelled():
    mobile = FakeConnectivity([ft.ConnectivityType.MOBILE])
    off = SimpleNamespace(settings={}, connectivity=mobile)
    assert await App.wait_for_wifi(off, threading.Event(), lambda: None) is True
    cancel = threading.Event()
    on = SimpleNamespace(settings={"wifi_only": True}, connectivity=mobile)
    waiting = asyncio.create_task(App.wait_for_wifi(on, cancel, lambda: None))
    await asyncio.sleep(0.3)
    cancel.set()
    assert await asyncio.wait_for(waiting, 2) is False


async def test_playlist_videos_wait_for_wifi(fake_app, monkeypatch):
    import ui.queue_tab
    from test_queue_and_playlist import FakeDownload, download_list, item_list
    monkeypatch.setattr(ui.queue_tab, "download_media", FakeDownload())
    asked = []

    async def wait_for_wifi(cancelled, on_wait):
        asked.append(1)
        on_wait()
        return True
    fake_app.wait_for_wifi = wait_for_wifi
    items = item_list(fake_app, {"title": "Mix", "uploader": "", "total": 2, "limit": 2, "entries": [
        {"url": "https://www.youtube.com/watch?v=a", "title": "A", "duration": 1, "thumbnail": ""},
        {"url": "https://www.youtube.com/watch?v=b", "title": "B", "duration": 1, "thumbnail": ""}]})
    await download_list(items, fake_app)
    assert asked == [1, 1]  # each item of the list, in the queue


class FakeNotifier:
    def __init__(self):
        self.shown = []
        self.alerts = []

    async def show(self, title, body, id=0, alert=True):
        self.shown.append((title, body, id))
        self.alerts.append(alert)
        return True


async def test_android_notification_when_away():
    notifier = FakeNotifier()
    app = SimpleNamespace(settings={}, desktop=False, notifier=notifier, notification_id=0, away=False, android=True)
    app.user_is_away = lambda: App.user_is_away(app)
    await App.notify_if_away(app, "Download complete", "Song")
    assert notifier.shown == []  # the app is on screen
    App.on_lifecycle_change(app, SimpleNamespace(state=ft.AppLifecycleState.PAUSE))
    await App.notify_if_away(app, "Download complete", "Song")
    await App.notify_if_away(app, "Download complete", "Other")
    assert notifier.shown == [("Download complete", "Song", 1), ("Download complete", "Other", 2)]
    app.settings["alert"] = False  # Settings: no sound or vibration
    await App.notify_if_away(app, "Download complete", "Quiet")
    assert notifier.alerts == [True, True, False]
    app.settings["notify"] = False
    await App.notify_if_away(app, "Download complete", "Muted")
    assert len(notifier.shown) == 3

