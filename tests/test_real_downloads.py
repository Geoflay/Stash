"""Real downloads from the sites: slow, and they fail when a site blocks or changes.

Skipped by default; run them with:  python -m pytest -m network
"""

import pytest
from conftest import probe

import core.backend
import core.download
import core.preview
import core.slideshow
import core.streams
import core.subtitles
from core.analyze import fetch_info
from core.download import download_media
from core.errors import clean_error, make_hint
from core.playlist import fetch_playlist
from core.pyav import probe_duration

pytestmark = pytest.mark.network

YOUTUBE = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s
DAILYMOTION = "https://www.dailymotion.com/video/x5kesuj"
BANDCAMP = "https://youtube-dl.bandcamp.com/track/youtube-dl-test-song"
SOUNDCLOUD = "https://soundcloud.com/forss/flickermood"
SOUNDCLOUD_SET = "https://soundcloud.com/forss/sets/ecclesia"
BANDCAMP_ALBUM = "https://jstrecords.bandcamp.com/album/entropy-ep"


@pytest.fixture(params=["ffmpeg", "av"])
def backend(request, monkeypatch):
    """Run the test with ffmpeg, then as on Android (PyAV only)."""
    if request.param == "av" and core.backend.av is None:
        pytest.skip("PyAV is not installed")
    for module in (core.backend, core.download, core.preview, core.slideshow, core.streams, core.subtitles):
        monkeypatch.setattr(module, "BACKEND", request.param)
    return request.param


def test_analyze_youtube():
    data = fetch_info(YOUTUBE)
    assert data["title"] == "Me at the zoo"
    assert data["duration"] == 19
    assert data["formats"] and data["preview"]


def test_youtube_channel_as_playlist():
    data = fetch_playlist("https://www.youtube.com/@jawed", limit=5)
    assert any(entry["title"] == "Me at the zoo" for entry in data["entries"])


def test_youtube_clip(backend, tmp_path):
    info = {}
    path = download_media(YOUTUBE, "mp4", "best", str(tmp_path), clip=(2.0, 8.0), info_out=info)
    video, audio = probe(path)[:2]
    assert video in ("h264", "av1", "vp9") and audio == "aac"  # PyAV copies the stream YouTube sends (often AV1)
    if backend == "ffmpeg":
        assert probe_duration(path) == pytest.approx(6.0, abs=1.0)
    else:  # PyAV starts the cut at the keyframe before 2 s (the app warns about it)
        assert 5.5 <= probe_duration(path) <= 8.5
    assert info["title"] == "Me at the zoo"


@pytest.mark.parametrize("fmt, codec", [("mp3", "mp3"), ("m4a", "aac"), ("flac", "flac")])
def test_audio_with_cover(backend, tmp_path, fmt, codec):
    path = download_media(BANDCAMP, fmt, "best", str(tmp_path))
    assert probe(path) == [codec, "mjpeg"]  # the cover art is the second stream


def test_mkv_with_cover(tmp_path):
    path = download_media(DAILYMOTION, "mkv", "best", str(tmp_path), clip=(0.0, 5.0))
    assert path.suffix == ".mkv"
    assert probe(path) == ["h264", "aac", "mjpeg"]


def test_other_site_soundcloud(tmp_path):
    data = fetch_info(SOUNDCLOUD)
    assert data["platform"] == "Soundcloud" and data["title"] == "Flickermood"
    path = download_media(SOUNDCLOUD, "mp3", "best", str(tmp_path))
    assert probe(path) == ["mp3", "mjpeg"]


@pytest.mark.parametrize("single, album, site", [(SOUNDCLOUD, SOUNDCLOUD_SET, "soundcloud.com/"),
                                                (BANDCAMP, BANDCAMP_ALBUM, ".bandcamp.com/track/")])
def test_other_site_playlist(tmp_path, single, album, site):
    assert "playlist" not in fetch_info(single)  # a single track still opens the video card
    assert fetch_info(album)["playlist"]
    data = fetch_playlist(album, limit=2)
    assert len(data["entries"]) == 2 and data["total"] >= 2
    assert all(site in entry["url"] for entry in data["entries"])
    path = download_media(data["entries"][0]["url"], "mp3", "best", str(tmp_path))
    assert probe(path)[0] == "mp3"


def test_page_without_video_gets_a_hint():
    with pytest.raises(Exception) as error:
        fetch_info("https://example.com/")
    message = clean_error(error.value)
    assert "Unsupported URL" in message
    assert "does not know this site" in make_hint(message, "https://example.com/", True)
