"""Audio tracks, sorting into folders, SponsorBlock, video codec preference, clean-up (no network)."""

import os
import tempfile
from pathlib import Path

import pytest
from conftest import ffmpeg, files_in, probe

import core.streams
import ui.queue_tab
from core.filenames import file_type, render_folder
from core.history import clear_thumbnails, thumbs_dir
from core.preview import pick_player_source, player_choice
from core.streams import audio_tracks, video_selector, with_audio_language
from core.workdirs import WORKDIR_PREFIX, clean_temporary_files, workdir_parent
from test_queue_and_playlist import PARAMS, FakeDownload
from ui.queue_tab import QueueTab


# Audio tracks (YouTube's dubbed versions)

def track(fid, lang, note, abr=128, preference=-1):
    return {"format_id": fid, "url": "https://cdn.test/" + fid, "protocol": "https", "vcodec": "none",
            "acodec": "mp4a", "language": lang, "format_note": note, "abr": abr, "language_preference": preference}


DUBBED = {"formats": [track("140-0", "fr", "French, medium"), track("140-1", "de", "German, medium"),
                      track("140-2", "en-US", "English (US) original (default), medium", preference=10),
                      track("139-0", "fr", "French, low", abr=48),
                      {"format_id": "v", "url": "https://cdn.test/v", "protocol": "https", "vcodec": "avc1",
                       "acodec": "none", "height": 720}]}


def test_audio_tracks():
    assert audio_tracks(DUBBED) == [{"lang": "en-US", "name": "English (US) original", "default": True},
                                    {"lang": "fr", "name": "French", "default": False},
                                    {"lang": "de", "name": "German", "default": False}]
    assert audio_tracks({"formats": [track("140", "en", "English, medium")]}) == []  # only one: no choice


def test_audio_track_selectors():
    assert with_audio_language("ba[ext=m4a]/ba/b", "fr") == "ba[language=fr][ext=m4a]/ba[language=fr]/ba[ext=m4a]/ba/b"
    assert with_audio_language("bestaudio/best", None) == "bestaudio/best"
    selector = video_selector("720", "mp4", "fr")
    assert selector.startswith("bv*[height<=720][ext=mp4]+ba[language=fr][ext=m4a]/")
    assert selector.endswith("/bv*[height<=720]+ba/b[height<=720]/b")  # the original sound if there is no French


def test_preview_plays_the_chosen_track():
    source = pick_player_source(DUBBED)
    assert player_choice(source, "best", audio_lang="fr")[1] == "https://cdn.test/140-0"  # the best French one
    assert player_choice(source, "best", sound_only=True, audio_lang="de")[0] == "https://cdn.test/140-1"


@pytest.fixture
def codec_preference():
    yield core.streams.CODEC
    core.streams.CODEC["preference"] = "auto"


@pytest.mark.skipif(core.streams.BACKEND != "ffmpeg", reason="the codec preference needs ffmpeg")
def test_codec_preference(codec_preference):
    codec_preference["preference"] = "compatible"
    assert video_selector("1080", "mp4").startswith("bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/")
    assert "avc1" not in video_selector("1080", "webm")  # WebM cannot hold H.264
    codec_preference["preference"] = "av1"
    assert video_selector("best", "mkv").startswith("bv*[vcodec^=av01]+ba/")
    assert "av01" not in video_selector("best", "mov")  # MOV cannot hold AV1
    codec_preference["preference"] = "vp9"
    assert video_selector("720", "webm").startswith("bv*[height<=720][vcodec~='^vp0?9']+ba/")
    codec_preference["preference"] = "hevc"  # TikTok calls it bytevc1
    assert video_selector("best", "mp4").startswith("bv*[vcodec~='^(hvc1|hev1|h265|hevc|bytevc1)']+ba/")
    assert "hev1" not in video_selector("best", "webm")  # WebM cannot hold HEVC
    assert "hev1" in video_selector("best", "mp4", sound=False)


def test_maximum_compatibility_converts(media_dir, tmp_path):
    from core.ffmpeg import ensure_compatible
    vp9 = tmp_path / "vp9.mkv"
    ffmpeg("-i", media_dir / "video.mp4", "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", vp9)
    out = ensure_compatible(vp9, tmp_path, lambda *a: None)
    assert probe(out) == ["h264", "aac"] and out.suffix == ".mkv"
    assert ensure_compatible(media_dir / "video.mp4", tmp_path, lambda *a: None) == media_dir / "video.mp4"


# Sorting into folders

def test_render_folder():
    fields = {"platform": "Youtube", "uploader": "Rick: Astley", "year": "", "type": "Music"}
    assert render_folder("{platform}/{uploader}", fields) == ["Youtube", "Rick Astley"]
    assert render_folder("{type}\\{year}", fields) == ["Music"]  # an empty level is left out
    assert render_folder("", fields) == []
    assert [file_type("mp3"), file_type("gif"), file_type("mp4"), file_type("mp4", True)] == [
        "Music", "Images", "Videos", "Subtitles"]


async def test_queue_sorts_into_folders(fake_app, monkeypatch):
    fake = FakeDownload()
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    fake_app.settings["folder_template"] = "{platform}/{type}"
    fake_app.settings["sponsorblock"] = ["sponsor", "intro"]
    await QueueTab(fake_app).run_download(dict(PARAMS, fmt="mp3", audio_lang="fr"))
    assert files_in(fake_app.destination) == ["Youtube/Music/Title from yt-dlp.mp3"]
    assert fake.calls[0]["audio_lang"] == "fr" and fake.calls[0]["sponsorblock"] == ["sponsor", "intro"]


async def test_no_sponsorblock_with_one_file_per_chapter(fake_app, monkeypatch):
    from test_queue_and_playlist import CHAPTER_PARAMS, fake_split
    fake = FakeDownload()
    monkeypatch.setattr(ui.queue_tab, "download_media", fake)
    monkeypatch.setattr(ui.queue_tab, "split_chapters", fake_split)
    fake_app.settings["sponsorblock"] = ["sponsor"]
    await QueueTab(fake_app).run_download(dict(CHAPTER_PARAMS))
    assert fake.calls[0]["sponsorblock"] is None  # cutting would move the chapters' times


# Clean-up

def test_clean_up(tmp_path):
    parent = Path(workdir_parent() or tempfile.gettempdir())
    old = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=parent))
    (old / "media.part").write_bytes(b"x" * 1000)
    kept = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=parent))
    count, size = clean_temporary_files(keep=[str(kept)])
    assert not old.exists() and kept.exists() and count >= 1 and size >= 1000
    os.rmdir(kept)
    (thumbs_dir() / "a.jpg").write_bytes(b"jpg")
    assert clear_thumbnails() == (1, 3) and not list(thumbs_dir().iterdir())
