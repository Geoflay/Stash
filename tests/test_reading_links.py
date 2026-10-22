"""Reading what yt-dlp returns: photo posts, previews, playlists, progress, retries."""

import pytest

import core.network as network
from core.analyze import instagram_photo_post, parse_images
from core.download import make_hooks
from core.errors import Cancelled
from core.network import NETWORK, call_with_retries
from core.playlist import _playlist_entries
from core.preview import pick_preview_source
from core.ytdlp_update import ytdlp_is_newer


def test_tiktok_photos():
    item = {"imagePost": {"images": [
        {"imageURL": {"urlList": ["https://a/x.webp", "https://a/x.jpeg?x=1"]}, "imageWidth": 10, "imageHeight": 20},
        {"imageURL": {"urlList": []}}]}}
    assert parse_images(item) == [{"url": "https://a/x.jpeg?x=1", "width": 10, "height": 20}]


def test_instagram_photo_post():
    post = instagram_photo_post({
        "carousel_media": [{"image_versions2": {"candidates": [
            {"url": "u1", "width": 10, "height": 10}, {"url": "u2", "width": 20, "height": 20}]}}],
        "caption": {"text": "x  y " * 30}, "user": {"username": "bob"},
        "music": {"a": [{"progressive_download_url": "https://m/a.m4a"}]}})
    assert post["images"] == [{"url": "u2", "width": 20, "height": 20}]  # largest version
    assert post["audio_url"] == "https://m/a.m4a"
    assert post["uploader"] == "bob"
    assert post["title"].endswith("…")


def test_instagram_video_is_not_a_photo_post():
    assert instagram_photo_post({"video_versions": [1]}) is None


def test_preview_source_prefers_h264_near_360p():
    source = pick_preview_source({"formats": [
        {"url": "https://a", "protocol": "https", "vcodec": "vp9", "height": 360},
        {"url": "https://b", "protocol": "m3u8", "vcodec": "avc1", "height": 1080, "http_headers": {"A": 1},
         "cookies": "c=1"},
        {"url": "https://c", "protocol": "dash", "vcodec": "avc1", "height": 360}]})
    assert source == {"url": "https://b", "headers": {"A": "1", "Cookie": "c=1"}}


def test_playlist_entries_skip_private_videos_and_open_nested_lists():
    out = []
    _playlist_entries([
        {"title": "[Private video]", "id": "a"},
        {"title": "ok", "id": "b", "url": "b", "ie_key": "Youtube"},
        {"title": "no link", "id": "c", "url": "c", "ie_key": "Soundcloud"},  # an ID alone is not a link there
        {"entries": [{"title": "n", "url": "https://n", "thumbnails": [{"url": "t1"}, {"url": "t2"}]}]}], out)
    assert out == [{"url": "https://www.youtube.com/watch?v=b", "title": "ok", "duration": None, "thumbnail": "",
                    "date": ""},
                   {"url": "https://n", "title": "n", "duration": None, "thumbnail": "t2", "date": ""}]


def test_progress_hooks():
    events = []
    hook, pp_hook = make_hooks(lambda *args: events.append(args))
    hook({"status": "downloading", "filename": "a", "total_bytes": 100, "downloaded_bytes": 50, "speed": 10})
    hook({"status": "downloading", "filename": "b", "total_bytes_estimate": 100, "downloaded_bytes": 10})
    hook({"status": "finished", "filename": "a", "total_bytes": 100})
    pp_hook({"status": "started"})
    assert events == [(0.5, "Downloading…", "50 B / 100 B - 10 B/s - a few seconds left"),
                      (0.3, "Downloading…", "60 B / 200 B - 10 B/s - about 15 s left"),
                      (None, "Finalizing…"), (None, "Processing…")]


def test_ytdlp_version_comparison():
    assert ytdlp_is_newer("2026.10.01", "2026.09.27.232945")
    assert not ytdlp_is_newer("2026.09.27", "2026.09.27")


class TestRetries:
    @pytest.fixture(autouse=True)
    def no_waiting(self, monkeypatch):
        monkeypatch.setattr(network.time, "sleep", lambda seconds: None)
        monkeypatch.setitem(NETWORK, "retries", 3)

    def test_connection_problems_are_retried(self):
        calls, messages = [], []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("timed out")
            return "ok"

        assert call_with_retries(lambda f, m, d=None: messages.append(m), flaky) == "ok"
        assert len(calls) == 3
        assert "attempt 2 of 3" in messages[-1]

    def test_gives_up_after_the_retries(self):
        def broken():
            raise TimeoutError("timed out")

        with pytest.raises(TimeoutError):
            call_with_retries(lambda *a: None, broken)

    def test_permanent_errors_fail_at_once(self):
        calls = []

        def private():
            calls.append(1)
            raise RuntimeError("Private video")

        with pytest.raises(RuntimeError):
            call_with_retries(lambda *a: None, private)
        assert len(calls) == 1

    def test_cancel_is_never_retried(self):
        calls = []

        def cancelled():
            calls.append(1)
            raise Cancelled()

        with pytest.raises(Cancelled):
            call_with_retries(lambda *a: None, cancelled)
        assert len(calls) == 1


# The player of the Download tab (Watch / Listen before downloading)

def fmt(fid, vcodec="none", acodec="none", height=None, protocol="https", abr=None):
    return {"format_id": fid, "url": "https://cdn.test/" + fid, "protocol": protocol, "vcodec": vcodec,
            "acodec": acodec, "height": height, "abr": abr, "http_headers": {"User-Agent": "UA"}}


def test_player_source():
    from core.preview import pick_player_source, player_choice, player_resource
    # One file with picture and sound at 480p, and pictures alone up to 1080p (YouTube's kind).
    source = pick_player_source({"formats": [
        fmt("sd", "avc1", "mp4a", 480), fmt("hd-vp9", "vp9", height=1080), fmt("hd", "avc1", height=1080),
        fmt("hls", "avc1", height=720, protocol="m3u8_native"), fmt("a1", acodec="opus", abr=50),
        fmt("a2", acodec="mp4a", abr=128)]})
    assert not source["audio_only"] and not source["silent"]
    # Best: the 1080p picture (H.264 over https) with the best sound beside it.
    assert player_choice(source, "best") == ("https://cdn.test/hd", "https://cdn.test/a2", {"User-Agent": "UA"})
    assert player_choice(source, "720")[:2] == ("https://cdn.test/hls", "https://cdn.test/a2")
    assert player_choice(source, "480")[:2] == ("https://cdn.test/sd", "")  # picture and sound in one file
    assert player_choice(source, "144")[0] == "https://cdn.test/sd"  # nothing that small: the smallest
    assert player_choice(source, "best", sound_only=True)[:2] == ("https://cdn.test/a2", "")  # an audio format
    # What mpv opens: picture and sound together, and only the part chosen.
    assert player_resource("https://cdn.test/sd") == "https://cdn.test/sd"
    assert player_resource("https://cdn.test/v", "https://cdn.test/a2") == (
        "edl://!no_clip;!no_chapters;%18%https://cdn.test/v;!new_stream;!no_clip;!no_chapters;%19%https://cdn.test/a2")
    assert player_resource("https://cdn.test/v", "https://cdn.test/a2", (5.0, 11.5)) == (
        "edl://%18%https://cdn.test/v,5,6.5;!new_stream;%19%https://cdn.test/a2,5,6.5")
    assert player_resource("https://cdn.test/v", clip=(30.0, None)) == "edl://%18%https://cdn.test/v,30"
    assert pick_player_source({"formats": [fmt("a", acodec="mp3")]})["audio_only"]
    assert pick_player_source({"formats": [fmt("v", "avc1", height=360)]})["silent"]
    assert pick_player_source({"formats": [fmt("dash", "avc1", protocol="http_dash_segments")]}) is None


def test_preview_subtitles(monkeypatch):
    import core.web
    from core.subtitles import preview_subtitles, subtitle_choices
    choices = subtitle_choices({"subtitles": {"en": [{"ext": "json3", "url": "https://s.test/en.json"},
                                                      {"ext": "vtt", "url": "https://s.test/en.vtt"}],
                                              "fr": [{"ext": "json3", "url": "https://s.test/fr.json"}]}})
    assert choices[0]["url"] == "https://s.test/en.vtt" and "url" not in choices[1]
    vtt = "WEBVTT\n\n00:00:06.000 --> 00:00:08.000\nHello\n\n00:00:20.000 --> 00:00:21.000\nLater\n"
    monkeypatch.setattr(core.web, "http_get", lambda url, timeout=60: vtt.encode())
    # Played from 5 s to 15 s: "Hello" shows 1 s after the start, "Later" is outside.
    assert preview_subtitles(choices[0], (5.0, 15.0)) == "1\n00:00:01,000 --> 00:00:03,000\nHello\n"
    assert preview_subtitles(choices[1]) == ""
