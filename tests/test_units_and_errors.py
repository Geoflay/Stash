import pytest

from core.errors import clean_error, make_hint
from core.network import is_transient, parse_speed_limit, speed_limit_bytes
from core.units import format_bytes, format_duration, parse_time, transfer_text


@pytest.mark.parametrize("text, seconds", [
    ("", None), ("90", 90.0), ("1:30", 90.0), ("1:02:03", 3723.0), ("1,5", 1.5), (" 2:00 ", 120.0),
])
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["a", "-1", "1:2:3:4", "inf"])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError, match="Invalid time"):
        parse_time(text)


@pytest.mark.parametrize("seconds, text", [
    (None, ""), (0, ""), (5, "0:05"), (65, "1:05"), (3600, "1:00:00"), (3725.6, "1:02:06"),
])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize("size, text", [
    (None, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (1536, "1.5 KB"), (10 ** 6, "976.6 KB"),
    (10 ** 9, "953.7 MB"), (10 ** 13, "9.1 TB"),
])
def test_format_bytes(size, text):
    assert format_bytes(size) == text


def test_transfer_text():
    assert transfer_text(1000, None, 2048) == "1000 B / ? - 2.0 KB/s"
    assert transfer_text(1000, 5000, 2048) == "1000 B / 4.9 KB - 2.0 KB/s - a few seconds left"
    assert transfer_text(5000, 5000, 2048) == "4.9 KB / 4.9 KB - 2.0 KB/s"  # finished: nothing left


@pytest.mark.parametrize("text, limit", [("", None), ("2", 2.0), ("0.5", 0.5), ("1,5", 1.5)])
def test_speed_limit(text, limit):
    assert parse_speed_limit(text) == limit


@pytest.mark.parametrize("text", ["0", "abc", "20000"])
def test_speed_limit_rejects(text):
    with pytest.raises(ValueError):
        parse_speed_limit(text)


def test_speed_limit_bytes():
    assert speed_limit_bytes(None) is None
    assert speed_limit_bytes(2) == 2 * 1024 * 1024


@pytest.mark.parametrize("error, transient", [
    (TimeoutError("x"), True),
    (Exception("HTTP Error 503"), True),
    (Exception("Connection reset by peer"), True),
    (Exception("HTTP Error 403: Forbidden"), False),
    (Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden"), True),
    (Exception("Sign in to confirm you're not a bot"), False),
    (Exception("Private video"), False),
])
def test_is_transient(error, transient):
    assert is_transient(error) is transient


def test_clean_error():
    raw = "\x1b[0;31mERROR:\x1b[0m [youtube] x: Video unavailable; please report this issue on github"
    assert clean_error(raw) == "[youtube] x: Video unavailable"
    assert clean_error("") == "Unknown yt-dlp error"


def test_hints():
    assert "cookies could not be read" in make_hint("cookie database locked", "https://youtube.com", True)
    login = make_hint("Sign in login required", "https://youtube.com", True)
    assert "pick the browser" in login
    assert "pick the browser" not in make_hint("Sign in login required", "https://youtube.com", False)
    assert "TikTok" in make_hint("blah", "https://tiktok.com/x", True)
    assert "Update yt-dlp" in make_hint("Unable to extract", "https://youtube.com", True)
    assert "does not know this site" in make_hint("Unsupported URL: https://example.com/", "https://example.com/", True)
    assert make_hint("other", "https://x.com", True) is None


@pytest.mark.parametrize("url, message, blocked", [
    ("https://www.youtube.com/watch?v=x", "[youtube] x: Sign in to confirm you’re not a bot. Use --cookies", True),
    ("https://youtu.be/x", "Sign in to confirm you're not a bot", True),
    ("https://www.youtube.com/watch?v=x", "The current session has been rate-limited by YouTube for up to an hour", True),
    ("https://www.youtube.com/watch?v=x", "HTTP Error 429: Too Many Requests", True),
    ("https://www.youtube.com/watch?v=x", "Private video. Sign in if you've been granted access", False),
    ("https://www.dailymotion.com/video/x", "HTTP Error 429: Too Many Requests", False),  # YouTube only
])
def test_youtube_block(url, message, blocked):
    from core.errors import is_youtube_block
    assert is_youtube_block(url, message) is blocked


def test_youtube_block_hint():
    from core.errors import YOUTUBE_BLOCK_HINT
    assert make_hint("Sign in to confirm you're not a bot. Use --cookies", "https://youtu.be/x", True) == \
        YOUTUBE_BLOCK_HINT
