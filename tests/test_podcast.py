"""Podcasts: reading RSS and Atom feeds, and downloading episodes tagged from the feed (a local web server:
no network)."""

import asyncio
import functools
import http.server
import threading

import pytest
from conftest import needs_ffmpeg

from core.podcast import NotAFeed, fetch_feed, maybe_feed, parse_feed
from test_queue_and_playlist import download_list, item_list

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Morning Talk</title>
  <itunes:author>Radio Example</itunes:author>
  <itunes:image href="{base}/cover.jpg"/>
  <item>
    <title>Episode two</title>
    <pubDate>Tue, 03 Jun 2025 08:00:00 +0000</pubDate>
    <itunes:duration>1:02:03</itunes:duration>
    <itunes:episode>2</itunes:episode>
    <enclosure url="{base}/two.mp3" type="audio/mpeg" length="1000"/>
  </item>
  <item>
    <title>A blog post, no sound file</title>
  </item>
  <item>
    <title>Episode one</title>
    <pubDate>Mon, 02 Jun 2025 08:00:00 +0000</pubDate>
    <itunes:duration>95</itunes:duration>
    <itunes:author>Guest Host</itunes:author>
    <enclosure url="{base}/one.mp3" type="audio/mpeg" length="1000"/>
  </item>
</channel>
</rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Video Show</title>
  <author><name>Someone</name></author>
  <entry>
    <title>Pilot</title>
    <published>2024-12-31T10:00:00Z</published>
    <link rel="alternate" href="https://show.test/pilot"/>
    <link rel="enclosure" type="video/mp4" href="https://show.test/pilot.mp4"/>
  </entry>
</feed>"""


def test_rss_feed():
    feed = parse_feed(RSS.format(base="https://cdn.test").encode())
    assert (feed["title"], feed["uploader"], feed["podcast"]) == ("Morning Talk", "Radio Example", True)
    two, one = feed["entries"]  # the item without a sound file is not an episode
    assert (two["url"], two["duration"], two["date"], two["number"]) == ("https://cdn.test/two.mp3", 3723, "20250603", "2")
    assert two["tags"] == {"title": "Episode two", "artist": "Radio Example", "album": "Morning Talk",
                           "date": "2025-06-03"}
    assert one["tags"]["artist"] == "Guest Host" and one["duration"] == 95
    assert one["cover"] == "https://cdn.test/cover.jpg" and not one["video"]


def test_atom_feed():
    feed = parse_feed(ATOM.encode())
    (pilot,) = feed["entries"]
    assert pilot["video"] and pilot["page"] == "https://show.test/pilot" and pilot["date"] == "20241231"
    assert pilot["tags"] == {"title": "Pilot", "artist": "Someone", "album": "Video Show", "date": "2024-12-31"}


def test_not_a_feed():
    with pytest.raises(NotAFeed):
        parse_feed(b"<!doctype html><html><body>A page</body></html>")
    with pytest.raises(RuntimeError, match="no episodes"):
        parse_feed(b'<rss version="2.0"><channel><title>Blog</title><item><title>Post</title></item></channel></rss>')


@pytest.mark.parametrize("url, feed", [
    ("https://feeds.megaphone.fm/ABC123", True), ("https://anchor.fm/s/1234/podcast/rss", True),
    ("https://example.com/podcast.xml", True), ("https://www.example.com/feed/", True),
    ("https://www.youtube.com/watch?v=abc", False), ("https://vimeo.com/123", False),
])
def test_feed_links(url, feed):
    assert maybe_feed(url) == feed


@pytest.fixture
def feed_server(media_dir, tmp_path):
    """The RSS feed above, with its two MP3 files and its cover art, at http://127.0.0.1:port/."""
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)))
    base = "http://127.0.0.1:%d" % server.server_port
    (tmp_path / "feed.rss").write_text(RSS.format(base=base), encoding="utf-8")
    for name in ("one.mp3", "two.mp3"):
        (tmp_path / name).write_bytes((media_dir / "tone.mp3").read_bytes())
    (tmp_path / "cover.jpg").write_bytes((media_dir / "cover.jpg").read_bytes())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base
    server.shutdown()


@needs_ffmpeg
async def test_download_episodes(fake_app, feed_server):
    from mutagen import File
    feed = await asyncio.to_thread(fetch_feed, feed_server + "/feed.rss", 1)  # the newest only
    assert [e["title"] for e in feed["entries"]] == ["Episode two"] and feed["total"] == 2
    fake_app.filename_template = lambda fmt=None: "{date} {title}"
    items = item_list(fake_app, feed)
    assert items.kind_dd.value == "audio" and items.format_dd.value == "audio"  # saved as it is
    assert items.meta.value == "Radio Example · 2 episodes, showing the first 1 (limit set in Settings)"
    (item,) = items.items
    assert not item["check"].value and items.download_btn.disabled  # nothing checked: episodes are picked
    item["check"].value = True
    items.refresh_count()
    await download_list(items, fake_app)
    (saved,) = fake_app.destination.iterdir()  # one episode: no folder
    assert saved.name == "2025-06-03 Episode two.mp3"
    tags = File(saved).tags
    assert (str(tags["TIT2"]), str(tags["TPE1"]), str(tags["TALB"])) == ("Episode two", "Radio Example", "Morning Talk")
    assert str(tags["TDRC"]) == "2025-06-03" and tags.getall("APIC")  # the podcast's cover art


async def test_feed_link_in_the_download_tab(fake_app, feed_server, monkeypatch, tmp_path):
    import ui.download_tab
    from ui.download_tab import DownloadTab
    fake_app.playlist_limit = lambda site: 100
    tab = DownloadTab(fake_app)
    tab.url_input.value = feed_server + "/feed.rss"  # looks like a feed: read as one first
    await tab.analyze()
    assert tab.items.card.visible and not tab.result_card.visible
    assert [item["entry"]["title"] for item in tab.items.items] == ["Episode two", "Episode one"]
    # A feed whose link says nothing: yt-dlp's generic extractor finds a list, then it is read as a feed.
    (tmp_path / "latest").write_bytes((tmp_path / "feed.rss").read_bytes())
    monkeypatch.setattr(ui.download_tab, "fetch_info",
                        lambda url, *a, **k: {"url": url, "playlist": True, "generic": True})
    tab.items.card.visible = False
    tab.url_input.value = feed_server + "/latest"
    await tab.analyze()
    assert tab.items.card.visible and tab.items.data["podcast"]
