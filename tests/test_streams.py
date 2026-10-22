"""Which streams to download, the quality menu and the size estimates."""

import pytest

import core.streams as streams
from core.streams import (
    av_video_selector, best_audio_ext, estimate_choice, has_stream, pick_quality, quality_choices, valid_quality,
    video_heights, video_selector,
)

FORMATS = [
    {"height": 1080, "vcodec": "avc1", "acodec": "none", "size": 50_000_000, "tbr": 4000, "abr": None, "ext": "mp4"},
    {"height": 1080, "vcodec": "vp9", "acodec": "none", "size": 40_000_000, "tbr": 3000, "abr": None, "ext": "webm"},
    {"height": 720, "vcodec": "avc1", "acodec": "none", "size": None, "tbr": 2000, "abr": None, "ext": "mp4"},
    {"height": 360, "vcodec": "avc1", "acodec": "mp4a", "size": 8_000_000, "tbr": 600, "abr": 96, "ext": "mp4"},
    {"height": None, "vcodec": "none", "acodec": "mp4a", "size": 3_000_000, "tbr": 128, "abr": 128, "ext": "m4a"},
    {"height": None, "vcodec": "none", "acodec": "opus", "size": None, "tbr": 160, "abr": 160, "ext": "webm"},
]
MEASURED = {"video": [{"key": "best", "height": 1080, "size": 53_000_000}, {"key": "720", "height": 720, "size": None}],
            "audio": 3_000_000}


@pytest.mark.parametrize("backend, quality, selector", [
    ("ffmpeg", "best", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b/b"),
    ("ffmpeg", "720", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b"),
    ("av", "best", "b[ext=mp4]/b/b"),
    ("av", "720", "b[height<=720][ext=mp4]/b[height<=720]/b"),
    (None, "best", "b[ext=mp4]/b/b"),
])
def test_video_selector(monkeypatch, backend, quality, selector):
    monkeypatch.setattr(streams, "BACKEND", backend)
    assert video_selector(quality) == selector


def test_av_video_selector():
    assert av_video_selector("best", True) == "b[ext=mp4]/bv*[ext=mp4]/bv*/b[ext=mp4]/b/b"


@pytest.mark.parametrize("fmt, quality, merge, text", [
    ("mp4", "best", True, "1080p · ~25.3 MB"),
    ("mp4", "best", False, "360p · ~3.8 MB"),  # without merging, only complete files
    ("mp4", "720", True, "720p · ~25.3 MB"),
    ("mp4", "360", True, "360p · ~3.8 MB"),
    ("mp3", "best", True, "MP3 · ~3.1 MB"),
    ("audio", "best", True, "Audio · ~1.9 MB"),
])
def test_estimate_for_half_of_the_video(fmt, quality, merge, text):
    assert estimate_choice(FORMATS, 200, fmt, quality, merge, 0.5) == text


@pytest.mark.parametrize("fmt, quality, text", [
    ("mp4", "best", "1080p · ~50.5 MB"), ("mp4", "720", "720p"), ("mp4", "480", "360p · ~7.6 MB"),
    ("audio", "best", "Audio · ~2.9 MB"), ("mp3", "best", "MP3 · ~6.1 MB"),
])
def test_estimate_with_measured_sizes(fmt, quality, text):
    assert estimate_choice(FORMATS, 200, fmt, quality, True, 1.0, MEASURED) == text


def test_estimate_without_formats():
    assert estimate_choice([], 200, "mp4", "best", True) == ""


def test_quality_choices():
    assert quality_choices(FORMATS, 200, True) == [
        ("best", "Best (1080p) · ~50.5 MB"), ("720", "720p · ~50.5 MB"), ("360", "360p · ~7.6 MB")]
    assert quality_choices(FORMATS, 200, False) == [("best", "Best (360p) · ~7.6 MB")]
    assert quality_choices(FORMATS, 200, True, MEASURED) == [("best", "Best (1080p) · 50.5 MB"), ("720", "720p")]
    assert quality_choices([], 200, True) == [("best", "Best"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]


@pytest.mark.parametrize("remembered, picked", [
    (None, "best"), ("best", "best"), ("1080", "best"), ("720", "720"), ("480", "360"), ("100", "360"),
    ("2160", "best"), ("abc", "best"),
])
def test_pick_quality(remembered, picked):
    assert pick_quality(remembered, quality_choices(FORMATS, 200, True), 1080) == picked


def test_stream_helpers():
    assert best_audio_ext(FORMATS) == "webm"
    assert has_stream(FORMATS, "video")
    assert not has_stream(FORMATS[:3], "audio")
    assert has_stream([], "audio")  # unknown: allowed
    assert video_heights(FORMATS, True) == [1080, 720, 360]
    assert [valid_quality(q) for q in ("best", "720", "0", "x", None, 720)] == [True, True, False, False, False, False]


def test_without_sound_is_smaller(monkeypatch):
    assert estimate_choice(FORMATS, 200, "mp4", "best", True, 1.0, MEASURED, sound=False) == "1080p · ~47.7 MB"
    assert estimate_choice(FORMATS, 200, "mp4", "best", True, sound=False) == "1080p · ~47.7 MB"  # no m4a added
    monkeypatch.setattr(streams, "BACKEND", "ffmpeg")
    assert video_selector("720", "mp4", sound=False) == "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]/b"
