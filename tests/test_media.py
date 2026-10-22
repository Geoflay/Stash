"""Converting, cutting, merging and tagging, on small files made locally (see media_dir in conftest.py).

Needs ffmpeg on the PATH; the PyAV tests also need the `av` package.
"""

import functools
import http.server
import os
import re
import subprocess
import threading

import pytest
from conftest import ffmpeg, probe

import core.pyav as pyav
from core.backend import _pick_video_encoder, av, format_available
from core.chapters import chapter_list, split_chapters, zip_folder
from core.compress import fit_in_size
from core.download import download_media
from core.ffmpeg import convert_container, media_duration
from core.preview import _preview_av, _preview_ffmpeg
from core.pyav import av_merge, av_make_video, av_to_audio, av_trim, has_audio_stream, probe_duration
from core.speed import change_speed
from core.stills import grab_frame, make_gif
from core.subtitles import embed_subtitles
from core.tags import attach_cover, main_tags, tag_file, tag_mp3

needs_av = pytest.mark.skipif(av is None, reason="PyAV is not installed")
META = {"title": "Song", "artist": "Band", "album": "Album", "date": "2024-05-31", "source": "https://example.com/v"}


def progress_log():
    events = []

    def notify(fraction, message, detail=None):
        events.append((fraction, message))
    notify.events = events
    return notify


@pytest.fixture
def cover(media_dir):
    return ((media_dir / "cover.jpg").read_bytes(), "image/jpeg")


# Tags

def test_tag_mp3_without_mutagen(tmp_path):
    path = tmp_path / "a.mp3"
    path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x02xx" + b"FRAMEDATA")
    tag_mp3(path, {"title": "T", "artist": "A", "cover": (b"img", "image/jpeg")})
    assert path.read_bytes().hex() == (
        "494433030000000000395449543200000005000001fffe54005450453100000005000001fffe410041504943000000110000"
        "00696d6167652f6a706567000300696d674652414d4544415441")


@pytest.mark.parametrize("name", ["tone.mp3", "tone.wav"])
def test_id3_tags(media, cover, name):
    from mutagen import File
    path = media(name)
    tag_file(path, dict(META, cover=cover))
    tags = File(path).tags
    assert str(tags["TIT2"]) == "Song" and str(tags["TPE1"]) == "Band" and str(tags["TALB"]) == "Album"
    assert tags["WOAS"].url == META["source"]
    assert tags.getall("APIC")[0].data == cover[0]


@pytest.mark.parametrize("name", ["tone.flac", "tone.opus"])
def test_vorbis_tags(media, cover, name):
    from mutagen import File
    path = media(name)
    tag_file(path, dict(META, cover=cover))
    audio = File(path)
    assert audio["title"] == ["Song"] and audio["artist"] == ["Band"] and audio["date"] == ["2024-05-31"]
    assert audio["comment"] == [META["source"]]
    if name.endswith(".flac"):
        assert audio.pictures[0].data == cover[0]
    else:
        assert audio["metadata_block_picture"]


def test_mp4_tags(media, cover):
    from mutagen.mp4 import MP4
    path = media("video.mp4")
    tag_file(path, dict(META, cover=cover))
    tags = MP4(path).tags
    assert tags["\xa9nam"] == ["Song"] and tags["\xa9ART"] == ["Band"]
    assert bytes(tags["covr"][0]) == cover[0]
    assert probe(path)[:2] == ["h264", "aac"]  # still playable


def test_main_tags():
    assert main_tags({"title": "Song (Official Video)", "track": "Song", "artists": ["A", "B"], "uploader": "Channel",
                      "album": "Album", "genres": ["Pop", "Rock"], "track_number": 3, "album_artist": "A"}) == {
        "title": "Song", "artist": "A, B", "album": "Album", "album_artist": "A", "genre": "Pop, Rock", "track": "3"}
    assert main_tags({"title": "Vlog", "uploader": "Channel"}) == {"title": "Vlog", "artist": "Channel", "album": "",
                                                                   "album_artist": "", "genre": "", "track": ""}


def test_mkv_cover_is_an_attachment(media, cover, tmp_path):
    mkv = convert_container(media("video.mp4"), "mkv", tmp_path, progress_log())
    out = attach_cover(mkv, cover, tmp_path)
    assert out.name == "with_cover.mkv"
    assert probe(out) == ["h264", "aac", "mjpeg"]


# ffmpeg

@pytest.mark.parametrize("fmt, codecs", [
    ("mkv", ["h264", "aac"]),  # streams copied
    ("mov", ["h264", "aac"]),
    ("webm", ["vp9", "opus"]),  # re-encoded: H.264 does not fit in WebM
])
def test_convert_container(media, tmp_path, fmt, codecs):
    notify = progress_log()
    out = convert_container(media("video.mp4"), fmt, tmp_path, notify)
    assert out.suffix == "." + fmt
    assert probe(out) == codecs
    assert notify.events[0][1].startswith("Converting to")


@pytest.mark.parametrize("ext, codec", [(".mp4", "mov_text"), (".mkv", "subrip")])
def test_embed_subtitles(media, media_dir, tmp_path, ext, codec):
    video = media("video.mp4")
    if ext != ".mp4":
        video = convert_container(video, ext[1:], tmp_path, progress_log())
    out = embed_subtitles(video, [("en", media_dir / "subs.srt")], tmp_path)
    assert probe(out) == ["h264", "aac", codec]
    both = embed_subtitles(video, [("en", media_dir / "subs.srt"), ("fr", media_dir / "subs.srt")], tmp_path)
    assert probe(both) == ["h264", "aac", codec, codec]  # one track per language


def test_embed_subtitles_refuses_webm_srt(media, media_dir, tmp_path):
    with pytest.raises(RuntimeError):
        embed_subtitles(media("tone.opus"), [("en", media_dir / "subs.srt")], tmp_path)


def test_media_duration(media_dir):
    assert media_duration(media_dir / "video.mp4") == pytest.approx(4.0, abs=0.1)
    assert media_duration(media_dir / "subs.srt") is None


# Size limit (for example 10 MB for Discord)

def test_small_file_is_kept(media_dir, tmp_path):
    path = media_dir / "big.mp4"
    assert fit_in_size(path, path.stat().st_size, tmp_path, progress_log()) == path


@pytest.mark.parametrize("source, codecs", [("big.mp4", ["h264", "aac"]), ("big.webm", ["vp9", "opus"]),
                                            ("tone.mp3", ["mp3"])])
def test_fit_in_size(media_dir, tmp_path, source, codecs):
    if source == "big.webm":
        path = convert_container(media_dir / "big.mp4", "webm", tmp_path, progress_log())
    else:
        path = media_dir / source
    limit = path.stat().st_size * 2 // 3
    notify = progress_log()
    out = fit_in_size(path, limit, tmp_path, notify)
    assert out.suffix == path.suffix and out.stat().st_size <= limit
    assert probe(out) == codecs
    assert any(fraction and message.startswith("Compressing") for fraction, message in notify.events)


@pytest.mark.parametrize("encoder, codecs", [("libx264", ["h264", "aac"]), ("mpeg4", ["mpeg4", "aac"])])
def test_fit_in_size_without_ffmpeg(media_dir, tmp_path, monkeypatch, encoder, codecs):
    """Android has no ffmpeg: PyAV compresses, with the best encoder it has (MPEG-4 is in every PyAV)."""
    import core.compress
    import core.pyav
    monkeypatch.setattr(core.compress, "BACKEND", "av")
    monkeypatch.setattr(core.pyav, "COMPRESS_ENCODERS", (encoder,))
    core.pyav.compress_encoder.cache_clear()
    try:
        assert core.compress.can_compress("mp4") and not core.compress.can_compress("webm")
        path = media_dir / "big.mp4"
        limit = path.stat().st_size * 2 // 3
        notify = progress_log()
        out = fit_in_size(path, limit, tmp_path, notify)
        assert out.stat().st_size <= limit and probe(out) == codecs
        assert any(fraction and message.startswith("Compressing") for fraction, message in notify.events)
        song = fit_in_size(media_dir / "tone.mp3", (media_dir / "tone.mp3").stat().st_size * 2 // 3, tmp_path,
                           progress_log())
        assert probe(song) == ["mp3"]
    finally:
        core.pyav.compress_encoder.cache_clear()


def test_cover_art_room_is_kept(media_dir, tmp_path):
    path = media_dir / "big.mp4"
    size = path.stat().st_size
    out = fit_in_size(path, size, tmp_path, progress_log(), reserve=size // 2)
    assert out.stat().st_size <= size // 2


def test_too_long_for_the_size_limit(media_dir, tmp_path):
    with pytest.raises(RuntimeError, match="too long"):
        fit_in_size(media_dir / "big.mp4", 20_000, tmp_path, progress_log())


# GIFs and still images

def test_gif(media_dir, tmp_path):
    out = make_gif(media_dir / "big.mp4", tmp_path / "a.gif", tmp_path, progress_log())
    assert out.read_bytes()[:6] == b"GIF89a"
    assert probe(out) == ["gif"]
    limit = out.stat().st_size // 3
    small = make_gif(media_dir / "big.mp4", tmp_path / "b.gif", tmp_path, progress_log(), max_bytes=limit)
    assert small.stat().st_size <= limit  # smaller and choppier until it fits


@pytest.mark.parametrize("ext, magic", [(".jpg", b"\xff\xd8"), (".png", b"\x89PNG")])
def test_grab_frame(media_dir, tmp_path, ext, magic):
    out = grab_frame(media_dir / "big.mp4", tmp_path / ("frame" + ext), tmp_path)
    assert out.read_bytes()[:len(magic)] == magic


# Speed and chapters

@pytest.mark.parametrize("name, video, speed, codecs, length", [
    ("video.mp4", True, 2.0, ["h264", "aac"], 2.0),
    ("video.mp4", True, 0.5, ["h264", "aac"], 8.0),
    ("tone.mp3", False, 1.5, ["mp3"], 2.67),
    ("tone.opus", False, 2.0, ["opus"], 2.0),
])
def test_change_speed(media_dir, tmp_path, name, video, speed, codecs, length):
    notify = progress_log()
    out = change_speed(media_dir / name, speed, tmp_path, notify, video)
    assert out.suffix == (media_dir / name).suffix and probe(out) == codecs
    assert media_duration(out) == pytest.approx(length, abs=0.15)
    assert any(fraction for fraction, _ in notify.events)


def test_chapter_list():
    info = {"chapters": [{"start_time": 0, "end_time": 2, "title": "Intro"},
                         {"start_time": 2, "end_time": 2, "title": "Empty"},
                         {"start_time": 2, "end_time": 4, "title": " "}]}
    assert chapter_list(info) == [{"title": "Intro", "start": 0.0, "end": 2.0},
                                  {"title": "Chapter 3", "start": 2.0, "end": 4.0}]
    assert chapter_list({}) == []


CHAPTERS = [{"title": "Intro", "start": 0.0, "end": 2.0}, {"title": "Song: part 2", "start": 2.0, "end": 4.0}]


@pytest.mark.parametrize("name", ["tone.mp3", "video.mp4"])
def test_split_chapters(media, tmp_path, name):
    source = media(name)
    folder = split_chapters(source, CHAPTERS, tmp_path, progress_log(), tags={"album": "Live"})
    parts = sorted(folder.iterdir())
    assert [p.name for p in parts] == ["01 - Intro" + source.suffix, "02 - Song part 2" + source.suffix]
    for part in parts:
        assert media_duration(part) == pytest.approx(2.0, abs=0.15)
    tags = file_tags(parts[1])
    assert tags["title"] == "Song: part 2" and tags["album"] == "Live" and tags["track"].startswith("2")
    archive = zip_folder(folder, tmp_path / "parts.zip", "My album")
    import zipfile
    assert zipfile.ZipFile(archive).namelist() == ["My album/" + p.name for p in parts]


def test_split_after_a_speed_change(media, tmp_path):
    fast = change_speed(media("tone.mp3"), 2.0, tmp_path, progress_log(), video=False)  # 2 s long now
    folder = split_chapters(fast, CHAPTERS, tmp_path, progress_log(), speed=2.0)
    assert [media_duration(p) for p in sorted(folder.iterdir())] == [pytest.approx(1.0, abs=0.15)] * 2


# download_media from a local web server: what yt-dlp does with a site, without the network

def file_tags(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "default=nw=1",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return {key.split(":", 1)[-1].lower(): value for key, _, value in
            (line.partition("=") for line in out.splitlines())}


@pytest.mark.parametrize("fmt, options, codecs", [
    ("mp4", {"max_bytes": 1_000_000, "tag_edits": {"title": "Fixed title", "album": "My album"}}, ["h264", "aac"]),
    ("mkv", {"tag_edits": {"artist": "Fixed artist"}}, ["h264", "aac"]),
    ("gif", {"clip": (1.0, 3.0)}, ["gif"]),
    ("jpg", {"clip": (2.0, None)}, ["mjpeg"]),
])
def test_download_media(served, tmp_path, fmt, options, codecs):
    path = download_media(served + "big.mp4", fmt, "best", tmp_path, **options)
    assert path.suffix == "." + fmt and probe(path) == codecs
    if "max_bytes" in options:
        assert path.stat().st_size <= options["max_bytes"]
    tags = file_tags(path)
    for key, value in options.get("tag_edits", {}).items():
        assert tags[key] == value
    if fmt == "gif":
        assert media_duration(path) == pytest.approx(2.0, abs=0.2)


def test_chapters_left_out_are_cut(served, media_dir, tmp_path):
    full = media_duration(media_dir / "big.mp4")
    path = download_media(served + "big.mp4", "mp3", "best", tmp_path, remove_ranges=[(1.0, 3.0)])
    assert media_duration(path) == pytest.approx(full - 2.0, abs=0.3)


# PyAV (used when ffmpeg is missing, e.g. on Android)

@needs_av
def test_probe(media_dir):
    assert probe_duration(media_dir / "video.mp4") == pytest.approx(4.0, abs=0.1)
    assert has_audio_stream(media_dir / "video.mp4")
    assert not has_audio_stream(media_dir / "video_only.mp4")


@needs_av
def test_av_merge(media_dir, tmp_path):
    out = tmp_path / "merged.mp4"
    notify = progress_log()
    av_merge(media_dir / "video_only.mp4", media_dir / "audio_only.m4a", out, notify)
    assert probe(out) == ["h264", "aac"]
    assert notify.events and notify.events[-1][0] == pytest.approx(1.0, abs=0.1)


@needs_av
@pytest.mark.parametrize("clip, length", [((1.0, 3.0), 2.0), ((2.0, None), 2.0)])
def test_av_trim(media_dir, tmp_path, clip, length):
    out = av_trim(media_dir / "video.mp4", tmp_path / "clip.mp4", clip, progress_log())
    assert probe(out) == ["h264", "aac"]
    assert probe_duration(out) == pytest.approx(length, abs=0.3)  # keyframe every second


@needs_av
def test_av_trim_empty_range(media_dir, tmp_path):
    # Audio only: a video cut starts at the keyframe before `start`, so it is never empty.
    with pytest.raises(RuntimeError, match="empty"):
        av_trim(media_dir / "audio_only.m4a", tmp_path / "clip.m4a", (10.0, 12.0), progress_log())
    assert not (tmp_path / "clip.m4a").exists()


@needs_av
@pytest.mark.parametrize("fmt, codec", [("mp3", "mp3"), ("m4a", "aac"), ("opus", "opus"), ("flac", "flac"),
                                        ("wav", "pcm_s16le")])
def test_av_to_audio(media_dir, tmp_path, fmt, codec):
    if not format_available(fmt) and av is not None:
        from core.backend import AV_AUDIO_CODECS, _encoder_available
        if not _encoder_available(AV_AUDIO_CODECS[fmt][0]):
            pytest.skip("no %s encoder in this PyAV build" % fmt)
    out = tmp_path / ("audio." + fmt)
    av_to_audio(media_dir / "video.mp4", out, fmt, progress_log(), clip=(1.0, 3.0))
    assert probe(out) == [codec]
    assert probe_duration(out) == pytest.approx(2.0, abs=0.2)


@needs_av
def test_av_slideshow(media_dir, tmp_path, monkeypatch):
    encoder = _pick_video_encoder()
    if encoder is None:
        pytest.skip("no video encoder in this PyAV build")
    pytest.importorskip("PIL")
    monkeypatch.setattr(pyav, "VIDEO_ENCODER", encoder)
    photo = media_dir / "cover.jpg"
    out = av_make_video([photo, photo], "slideshow", media_dir / "audio_only.m4a", 1.5, None, 320, 240,
                        tmp_path / "slides.mp4", progress_log())
    assert probe(out)[1] == "aac"
    assert probe_duration(out) >= 2.9


# Previews (a local web server stands in for the video site)

class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves parts of a file, like video sites do: ffmpeg needs it to jump to a clip."""

    def send_head(self):
        found = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not found or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start, end = int(found.group(1)), int(found.group(2) or size - 1)
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return f

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except ConnectionError:  # ffmpeg stops reading once it has what it needs
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def served(media_dir):
    handler = functools.partial(RangeHandler, directory=str(media_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % server.server_port
    server.shutdown()


@pytest.mark.parametrize("make", [
    _preview_ffmpeg, pytest.param(_preview_av, marks=needs_av)])
def test_preview_frame(served, tmp_path, make):
    out = tmp_path / "frame.jpg"
    make({"url": served + "video.mp4", "headers": {"X-Test": "1"}}, 2.0, out, 120)
    assert out.read_bytes()[:2] == b"\xff\xd8"  # a JPEG


# Checking the finished file

def test_check_a_complete_file(media_dir):
    from core.verify import check_media
    assert check_media(media_dir / "video.mp4", expected=4.0, picture=True) == []
    assert check_media(media_dir / "audio_only.m4a", expected=4.0) == []
    assert check_media(media_dir / "video_only.mp4", picture=True) == ["It has no sound."]
    assert check_media(media_dir / "video.mp4", expected=60.0) == ["It lasts 0:04 instead of 1:00: it may be cut short."]


def test_check_a_truncated_file(media_dir, tmp_path):
    from core.verify import check_media
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-c:a", "libmp3lame", tmp_path / "long.mp3")
    data = (tmp_path / "long.mp3").read_bytes()
    cut = tmp_path / "cut.mp3"
    cut.write_bytes(data[:len(data) // 2])  # the connection dropped halfway
    assert check_media(cut, expected=20.0) and not check_media(tmp_path / "long.mp3", expected=20.0)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes((media_dir / "video.mp4").read_bytes()[:3000])
    assert check_media(broken, expected=4.0, picture=True) == ["The file cannot be read: it may be damaged."]


def test_download_reports_the_check(served, tmp_path):
    info = {}
    download_media(served + "video.mp4", "mp3", "best", tmp_path, info_out=info, check=True)
    assert info["problems"] == []


def test_photos_of_a_post(served, media_dir, tmp_path):
    from core.slideshow import download_photos
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=32x32", "-frames:v", "1", media_dir / "second.png")
    images = [{"url": served + "cover.jpg"}, {"url": served + "second.png"}]
    folder = download_photos(images, "Twitter", tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == ["01.jpg", "02.png"]


def test_download_without_sound(served, tmp_path):
    path = download_media(served + "video.mp4", "mp4", "best", tmp_path, sound=False)
    assert probe(path) == ["h264"]
