"""Playlists on any site (SoundCloud sets, Bandcamp albums…), told apart from single videos with fake
yt-dlp results (no network)."""

import pytest

import core.network
import ui.download_tab
from core.analyze import fetch_info
from core.playlist import DEFAULT_PLAYLIST_LIMITS, fetch_playlist, lists_several_items
from ui.download_tab import DownloadTab

SET_URL = "https://soundcloud.com/forss/sets/ecclesia"
TRACK_URL = "https://soundcloud.com/forss/introitus"
# What yt-dlp returns for a SoundCloud set with extract_flat="in_playlist".
SOUNDCLOUD_SET = {"_type": "playlist", "title": "Ecclesia", "uploader": "Forss", "playlist_count": 3, "entries": [
    {"_type": "url_transparent", "ie_key": "Soundcloud", "id": "1", "url": TRACK_URL},
    {"_type": "url_transparent", "ie_key": "Soundcloud", "id": "2", "url": "https://soundcloud.com/forss/voca"},
    {"_type": "url_transparent", "ie_key": "Soundcloud", "id": "3", "url": "https://soundcloud.com/forss/ite"}]}
TRACK = {"id": "1", "title": "Introitus", "uploader": "Forss", "duration": 60, "extractor_key": "Soundcloud",
         "webpage_url": TRACK_URL, "formats": [{"format_id": "mp3", "url": "https://x/a.mp3", "vcodec": "none",
                                                "acodec": "mp3", "ext": "mp3"}]}


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL: returns canned results by link and remembers the options."""

    results, calls = {}, []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False, ie_key=None):
        FakeYoutubeDL.calls.append((url, self.opts))
        return FakeYoutubeDL.results[url]


@pytest.fixture
def ytdlp(monkeypatch):
    def install(results):
        FakeYoutubeDL.results, FakeYoutubeDL.calls = results, []
        monkeypatch.setattr(core.network, "YoutubeDL", FakeYoutubeDL)
        return FakeYoutubeDL.calls
    return install


def test_set_is_a_playlist(ytdlp):
    calls = ytdlp({SET_URL: SOUNDCLOUD_SET})
    assert fetch_info(SET_URL) == {"url": SET_URL, "playlist": True, "generic": False}
    opts = calls[0][1]
    assert opts["extract_flat"] == "in_playlist" and opts["playlist_items"] == "1-2"  # items are not read


def test_single_track_is_not_a_playlist(ytdlp):
    ytdlp({TRACK_URL: TRACK})
    data = fetch_info(TRACK_URL)
    assert "playlist" not in data
    assert data["title"] == "Introitus" and data["platform"] == "Soundcloud"


def test_list_of_one_item_opens_that_item(ytdlp):
    one = dict(SOUNDCLOUD_SET, entries=SOUNDCLOUD_SET["entries"][:1])
    ytdlp({SET_URL: one, TRACK_URL: TRACK})
    data = fetch_info(SET_URL)
    assert "playlist" not in data and data["title"] == "Introitus"


def test_post_with_several_videos_is_not_a_playlist():
    post = "https://x.com/a/status/1"  # its videos all share the post's link
    assert not lists_several_items({"_type": "playlist", "entries": [
        {"id": "1", "title": "a", "webpage_url": post}, {"id": "2", "title": "b", "webpage_url": post}]})
    assert not lists_several_items(TRACK)


def test_playlist_entries_of_other_sites(ytdlp):
    album = {"_type": "playlist", "title": "Entropy EP", "uploader": "JST", "entries": [
        {"_type": "url", "ie_key": "Bandcamp", "id": "1", "title": "Entropy",
         "url": "https://jstrecords.bandcamp.com/track/entropy"},
        {"_type": "url", "ie_key": "Bandcamp", "id": "2", "title": "No link", "url": "2"}]}
    ytdlp({"https://jstrecords.bandcamp.com/album/entropy-ep": album})
    data = fetch_playlist("https://jstrecords.bandcamp.com/album/entropy-ep")
    # The entry without a link is left out, not turned into a YouTube link.
    assert [e["url"] for e in data["entries"]] == ["https://jstrecords.bandcamp.com/track/entropy"]


async def test_download_tab_opens_the_playlist_card(fake_app, monkeypatch):
    limits = []
    fake_app.playlist_limit = lambda site: limits.append(site) or DEFAULT_PLAYLIST_LIMITS[site]
    fake_app.cookies_value = lambda: ""
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: {"url": url, "playlist": True})
    monkeypatch.setattr(ui.download_tab, "fetch_playlist", lambda url, *a, limit=0, **k: {
        "title": "Ecclesia", "uploader": "Forss", "total": 3, "limit": limit,
        "entries": [{"url": TRACK_URL, "title": "Introitus", "duration": 60, "thumbnail": ""}]})
    tab = DownloadTab(fake_app)
    tab.url_input.value = SET_URL
    await tab.analyze()
    assert tab.items.card.visible and not tab.result_card.visible
    assert limits == ["other"] and tab.state["url"] is None
