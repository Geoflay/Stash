import pytest

from core.links import (
    first_link, is_instagram_url, is_playlist_url, is_twitter_url, is_web_link, playlist_site, supported_site,
)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=x", "http://m.tiktok.com/@a/video/123", "https://vimeo.com/76979871",
    "https://example.com/page-with-a-video",  # yt-dlp's generic extractor may still find one
])
def test_web_links_are_accepted(url):
    assert is_web_link(url)


@pytest.mark.parametrize("url", ["ftp://youtube.com/x", "nope", "https://", "http://[::1", ""])
def test_other_text_is_refused(url):
    assert not is_web_link(url)


@pytest.mark.parametrize("url, site", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Youtube"),
    ("https://youtu.be/dQw4w9WgXcQ", "Youtube"),
    ("https://www.tiktok.com/@a/video/7000000000000000000", "TikTok"),
    ("https://x.com/a/status/1234567890", "Twitter"),
    ("https://www.instagram.com/p/abcdefghijk/", "Instagram"),
    ("https://vimeo.com/76979871", "Vimeo"),
    ("https://soundcloud.com/forss/flickermood", "Soundcloud"),
    ("https://www.dailymotion.com/video/x5kesuj", "Dailymotion"),
    ("https://www.twitch.tv/videos/123456789", "TwitchVod"),
    ("https://example.com/page", None),  # no extractor made for it: only the generic one would try
    ("not a link", None),
])
def test_supported_site(url, site):
    assert supported_site(url) == site


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/playlist?list=PL1", True),
    ("https://www.youtube.com/@chan", True),
    ("https://www.youtube.com/channel/UC1", True),
    ("https://www.tiktok.com/@user", True),
    ("https://www.tiktok.com/@user/", True),
    ("https://www.youtube.com/playlist", False),  # no list id
    ("https://www.youtube.com/watch?v=x", False),
    ("https://www.tiktok.com/@user/video/1", False),
    ("https://x.com/a/status/1", False),
])
def test_playlist_links(url, expected):
    assert is_playlist_url(url) is expected


def test_playlist_site():
    assert playlist_site("https://www.youtube.com/@chan") == "youtube"
    assert playlist_site("https://www.tiktok.com/@user") == "tiktok"
    assert playlist_site("https://soundcloud.com/forss/sets/ecclesia") == "other"


def test_platform_detection():
    assert is_twitter_url("https://x.com/a/status/1")
    assert is_twitter_url("https://twitter.com/a")
    assert not is_twitter_url("https://www.youtube.com/watch?v=x")
    assert is_instagram_url("https://www.instagram.com/p/x")
    assert not is_instagram_url("https://x.com/a")


def test_first_link_in_pasted_text():
    assert first_link("look at this: https://youtu.be/abc and that") == "https://youtu.be/abc"
    assert first_link("no link here") is None
