"""The Edit tab: changing a file of the computer (format, picture size, speed, part, subtitles, tags, size
limit), on small files made locally (see media_dir in conftest.py). Needs ffmpeg."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ffmpeg, probe

from core.editor import can_burn_subtitles, edit_media, extract_subtitles, probe_media, track_label
from core.ffmpeg import media_duration
from core.tags import tag_file


def log(fraction, message, detail=None):
    pass


def edit(path, fmt, tmp_path, **options):
    workdir = tmp_path / ("work%d" % len(list(tmp_path.glob("work*"))))
    workdir.mkdir()
    return edit_media(path, fmt, probe_media(path), options, workdir, log)


def tags_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "default=nw=1",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return {k.split(":", 1)[-1].lower(): v for k, _, v in (line.partition("=") for line in out.splitlines())}


def subtitle_text(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def with_subs(media_dir, tmp_path):
    """video.mp4 with a subtitle track, in an MKV file."""
    out = tmp_path / "with_subs.mkv"
    ffmpeg("-i", media_dir / "video.mp4", "-i", media_dir / "subs.srt", "-map", "0", "-map", "1", "-c", "copy",
           "-c:s", "srt", "-metadata", "title=Original", out)
    return out


def test_probe_media(with_subs, media_dir):
    info = probe_media(with_subs)
    assert info["duration"] == pytest.approx(4.0, abs=0.1)
    assert (info["width"], info["height"], info["video"], info["audio"]) == (160, 120, "h264", "aac")
    assert info["subtitles"] == [{"codec": "subrip", "lang": "", "title": ""}] and info["tags"] == {"title": "Original"}
    assert info["audio_tracks"] == [{"codec": "aac", "lang": "", "title": ""}]
    assert probe_media(media_dir / "tone.mp3")["video"] == ""
    with pytest.raises(RuntimeError, match="no video or sound"):
        probe_media(media_dir / "subs.srt")


def test_remove_subtitle_tracks(with_subs, tmp_path):
    out, note = edit(with_subs, "mkv", tmp_path, subs_keep=[])
    assert probe(out) == ["h264", "aac"] and note == ""


@pytest.fixture
def two_of_each(media_dir, tmp_path):
    """video.mp4 with a second sound track and two subtitle tracks, each with its language and title."""
    out = tmp_path / "two_of_each.mkv"
    ffmpeg("-i", media_dir / "video.mp4", "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
           "-i", media_dir / "subs.srt", "-i", media_dir / "subs.srt",
           "-map", "0:v", "-map", "0:a", "-map", "1:a", "-map", "2", "-map", "3", "-c:v", "copy", "-c:a", "aac",
           "-c:s", "srt", "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
           "-metadata:s:a:1", "title=Commentary", "-metadata:s:s:0", "language=eng",
           "-metadata:s:s:1", "language=fre", out)
    return out


def languages(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:stream_tags=language",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return [line.strip().replace(",und", "") for line in out.splitlines() if line.strip()]  # MP4: "und", unknown


def test_keep_some_tracks(two_of_each, tmp_path):
    info = probe_media(two_of_each)
    assert [track_label(t, i) for i, t in enumerate(info["audio_tracks"])] == [
        "1. eng · aac", "2. Commentary · fre · aac"]
    assert [t["lang"] for t in info["subtitles"]] == ["eng", "fre"]
    out, _ = edit(two_of_each, "mkv", tmp_path, audio_keep=[1], subs_keep=[0])
    assert languages(out) == ["video", "audio,fre", "subtitle,eng"]
    out, _ = edit(two_of_each, "mp4", tmp_path, audio_keep=[], subs_keep=[1])  # none: no sound
    assert languages(out) == ["video", "subtitle,fre"]
    out, _ = edit(two_of_each, "mp3", tmp_path, audio_keep=[1])  # an audio file: the first kept
    assert probe(out) == ["mp3"] and abs(media_duration(out) - 4.0) < 0.2


@pytest.mark.parametrize("fmt, codecs", [("mp4", ["h264", "aac", "mov_text"]), ("webm", ["vp9", "opus", "webvtt"])])
def test_other_format_keeps_text_subtitles(with_subs, tmp_path, fmt, codecs):
    out, note = edit(with_subs, fmt, tmp_path)
    assert out.suffix == "." + fmt and probe(out) == codecs and note == ""


def test_part_speed_and_picture_size(media_dir, tmp_path):
    out, _ = edit(media_dir / "big.mp4", "mp4", tmp_path, clip=(1.0, 3.0), speed=2.0, height=240)
    info = probe_media(out)
    assert info["duration"] == pytest.approx(1.0, abs=0.15)
    assert (info["width"], info["height"]) == (426, 240)


def test_added_subtitle_track_follows_the_part_kept(media_dir, tmp_path):
    # subs.srt says "Hello" from 0.5 s to 2 s: keeping from 1 s, it shows from the start to 1 s.
    out, _ = edit(media_dir / "video.mp4", "mkv", tmp_path, clip=(1.0, None),
                  subs_file=media_dir / "subs.srt", subs_mode="track")
    assert probe(out) == ["h264", "aac", "subrip"]
    assert "00:00:00,000 --> 00:00:01,000\nHello" in subtitle_text(out)


@pytest.mark.skipif(not can_burn_subtitles(), reason="this ffmpeg cannot draw subtitles (no libass)")
def test_burned_subtitles_change_the_picture(media_dir, tmp_path):
    plain, _ = edit(media_dir / "big.mp4", "mp4", tmp_path, height=240)
    burned, _ = edit(media_dir / "big.mp4", "mp4", tmp_path, height=240,
                     subs_file=media_dir / "subs.srt", subs_mode="burn")

    def frame(path, seconds):
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
                               "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    assert frame(plain, 1.0) != frame(burned, 1.0)  # "Hello" is on screen
    assert probe(burned) == ["h264", "aac"]  # drawn, not a track


def test_styled_subtitles_cannot_be_moved(media_dir, tmp_path):
    ass = tmp_path / "subs.ass"
    ffmpeg("-i", media_dir / "subs.srt", ass)
    with pytest.raises(RuntimeError, match=r"\.ass"):
        edit(media_dir / "video.mp4", "mp4", tmp_path, clip=(1.0, None), subs_file=ass, subs_mode="burn")


def test_new_tags_keep_the_cover_art(media, media_dir, tmp_path):
    from mutagen import File
    song = media("tone.mp3")
    tag_file(song, {"title": "Old", "artist": "Band", "cover": ((media_dir / "cover.jpg").read_bytes(), "image/jpeg")})
    out, _ = edit(song, "mp3", tmp_path, tags={"title": "New"})
    tags = File(out).tags
    assert str(tags["TIT2"]) == "New" and str(tags["TPE1"]) == "Band" and tags.getall("APIC")


def test_a_tag_can_be_removed(media_dir, tmp_path):
    source = tmp_path / "tagged.mp4"
    ffmpeg("-i", media_dir / "video.mp4", "-c", "copy", "-metadata", "title=T", "-metadata", "artist=A", source)
    out, _ = edit(source, "mp4", tmp_path, tags={"artist": ""})
    assert tags_of(out).get("title") == "T" and "artist" not in tags_of(out)


def test_sound_only_faster(media_dir, tmp_path):
    out, _ = edit(media_dir / "video.mp4", "mp3", tmp_path, speed=2.0)
    assert probe(out) == ["mp3"] and media_duration(out) == pytest.approx(2.0, abs=0.15)


def test_gif_and_size_limit(media_dir, tmp_path):
    gif, _ = edit(media_dir / "big.mp4", "gif", tmp_path)
    assert gif.read_bytes()[:6] == b"GIF89a"
    limit = (media_dir / "big.mp4").stat().st_size // 2
    small, _ = edit(media_dir / "big.mp4", "webm", tmp_path, max_bytes=limit)
    assert small.stat().st_size <= limit and probe(small) == ["vp9", "opus"]


# The tab itself

@pytest.fixture
def edit_tab(fake_app):
    from ui.edit_tab import EditTab
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    return EditTab(fake_app)


async def test_edit_tab(edit_tab, media_dir, tmp_path):
    source = tmp_path / "My clip.mp4"
    shutil.copyfile(media_dir / "big.mp4", source)
    await edit_tab.open_file(source)
    assert edit_tab.options_card.visible and edit_tab.format_dd.value == "mp4"
    assert [o.key for o in edit_tab.quality_dd.options] == ["", "240"]  # only smaller than the 360p original
    with pytest.raises(ValueError, match="Nothing to change"):
        edit_tab.read_options()
    edit_tab.format_dd.value = "mp3"
    edit_tab.refresh()
    assert not edit_tab.quality_dd.visible and not edit_tab.subs_box.visible
    edit_tab.tag_fields["title"].value = "Renamed"
    edit_tab.clip.start.value, edit_tab.clip.end.value = "1", "3"
    assert edit_tab.read_options() == {"clip": (1.0, 3.0), "tags": {"title": "Renamed"}}
    await edit_tab.save()
    saved = tmp_path / "My clip (edited).mp3"
    assert saved.exists() and media_duration(saved) == pytest.approx(2.0, abs=0.15)
    assert edit_tab.folder_btn.visible and edit_tab.play_btn.visible
    await edit_tab.save()  # never overwrites the first copy
    assert (tmp_path / "My clip (edited) (1).mp3").exists()
    await asyncio.sleep(0.5)  # let the preview frames finish


async def test_edit_tab_refuses_a_bad_file(edit_tab, media_dir):
    await edit_tab.open_file(media_dir / "subs.srt")
    assert not edit_tab.options_card.visible and edit_tab.open_status.visible
    assert Path(media_dir / "subs.srt").exists()


def test_remove_the_sound(media_dir, tmp_path):
    silent, _ = edit(media_dir / "video.mp4", "mp4", tmp_path, no_sound=True)
    assert probe(silent) == ["h264"] and silent.stat().st_size < (media_dir / "video.mp4").stat().st_size
    faster, _ = edit(media_dir / "video.mp4", "mkv", tmp_path, no_sound=True, speed=2.0)  # no sound to speed up
    assert probe(faster) == ["h264"] and media_duration(faster) == pytest.approx(2.0, abs=0.2)


async def test_edit_tab_preview(edit_tab, media_dir):
    import ui.player
    if ui.player.flet_video is None:
        pytest.skip("flet-video is not installed")
    await edit_tab.open_file(media_dir / "video.mp4")
    assert edit_tab.thumb_box.visible and edit_tab.play_badge.visible and edit_tab.no_sound.visible
    await edit_tab.toggle_preview()
    player = edit_tab.preview.player
    assert player.playlist[0].resource == str(media_dir / "video.mp4") and not edit_tab.preview.audio_bar.visible
    edit_tab.speed.field.value = "2"
    edit_tab.no_sound.value = True
    edit_tab.refresh()
    assert player.playback_rate == 2 and player.muted  # at once: same player
    assert edit_tab.read_options() == {"speed": 2.0, "no_sound": True}
    edit_tab.format_dd.value = "mp3"
    edit_tab.refresh()
    await asyncio.sleep(1.2)  # sound alone now: the audio bar instead of the picture
    assert edit_tab.preview.player is not player and edit_tab.preview.audio_bar.visible
    assert not edit_tab.no_sound.visible
    await edit_tab.toggle_preview()
    assert edit_tab.preview.player is None and not edit_tab.close_btn.visible and edit_tab.thumb_box.visible
    await asyncio.sleep(0.5)  # let the preview frames finish


async def test_edit_tab_tracks_and_groups(edit_tab, two_of_each):
    await edit_tab.open_file(two_of_each)
    groups = edit_tab.groups
    assert not any(group.opened for group in groups.values())  # only the format is shown at first
    assert not edit_tab.no_sound.visible and edit_tab.audio_tracks_box.visible  # several tracks: one box each
    assert [box.value for box in edit_tab.audio_checks + edit_tab.subs_checks] == [True] * 4
    edit_tab.audio_checks[0].value = False
    edit_tab.subs_checks[1].value = False
    edit_tab.refresh()
    assert groups["sound"].summary.value == "1 of 2 tracks" and groups["subs"].summary.value == "1 of 2 tracks"
    assert edit_tab.read_options() == {"audio_keep": [1], "subs_keep": [0]}
    edit_tab.audio_checks[1].value = False
    edit_tab.refresh()
    assert groups["sound"].summary.value == "no sound" and edit_tab.silent()
    edit_tab.format_dd.value = "mp3"
    edit_tab.refresh()
    assert not edit_tab.subs_box.visible and edit_tab.extract_box.visible  # its subtitles can still be saved
    with pytest.raises(ValueError, match="sound track"):
        edit_tab.read_options()
    edit_tab.format_dd.value = "gif"
    edit_tab.refresh()
    assert groups["clip"].opened  # the passage of a GIF must be chosen
    await asyncio.sleep(0.5)  # let the preview frames finish


def test_subtitles_saved_as_a_file(with_subs, tmp_path):
    out = extract_subtitles(with_subs, 0, "subrip", tmp_path)
    assert out.suffix == ".srt" and "-->" in out.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="pictures"):
        extract_subtitles(with_subs, 0, "dvd_subtitle", tmp_path)


async def test_edit_tab_saves_a_subtitle_track(edit_tab, with_subs, tmp_path):
    source = tmp_path / "film.mkv"
    shutil.copyfile(with_subs, source)
    await edit_tab.open_file(source)
    assert edit_tab.extract_box.visible and len(edit_tab.extract_list.controls) == 1
    await edit_tab.extract(0, edit_tab.info["subtitles"][0])
    saved = [p.name for p in tmp_path.iterdir() if p.suffix == ".srt"]
    assert saved == ["film.srt"] or saved[0].startswith("film.")
    await asyncio.sleep(0.5)  # let the preview frames finish


def size_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return tuple(int(x) for x in out.strip().split(","))


@pytest.mark.parametrize("crop, turn, size", [("9:16", None, (66, 120)), ("1:1", None, (120, 120)),
                                              (None, "right", (120, 160)), ("1:1", "180", (120, 120))])
def test_crop_and_turn(media_dir, tmp_path, crop, turn, size):
    out, _ = edit(media_dir / "video.mp4", "mp4", tmp_path, crop=crop, turn=turn)
    assert size_of(out) == size and probe(out) == ["h264", "aac"]


def test_black_bars_are_found(media_dir, tmp_path):
    boxed = tmp_path / "boxed.mp4"
    ffmpeg("-i", media_dir / "big.mp4", "-vf", "pad=640:480:0:60:black", "-c:a", "copy", boxed)
    out, _ = edit(boxed, "mp4", tmp_path, crop="bars")
    assert size_of(out) == (640, 360)


def test_silences_are_cut_out(tmp_path):
    # One second of tone, two of silence, one of tone: about 2.3 s once the silence is cut (0.15 s kept on
    # each side of it).
    talk = tmp_path / "talk.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-filter_complex",
           "[1]atrim=0:2[s];[0][s][2]concat=n=3:v=0:a=1", talk)
    out, _ = edit(talk, "mp3", tmp_path, no_silence=True)
    assert media_duration(out) == pytest.approx(2.3, abs=0.2)
    video, _ = edit(talk, "mp3", tmp_path, loudness=True)  # the volume evened out, same length
    assert media_duration(video) == pytest.approx(4.0, abs=0.2)


def test_video_with_silences_cut_and_volume(media_dir, tmp_path):
    out, _ = edit(media_dir / "video.mp4", "mkv", tmp_path, loudness=True)  # sound re-encoded, picture copied
    assert probe(out) == ["h264", "aac"] and media_duration(out) == pytest.approx(4.0, abs=0.2)
    quiet = tmp_path / "quiet.mp4"
    ffmpeg("-i", media_dir / "video.mp4", "-af", "volume=enable='between(t,1,3)':volume=0", "-c:v", "copy", quiet)
    out, _ = edit(quiet, "mp4", tmp_path, no_silence=True)
    assert media_duration(out) == pytest.approx(2.3, abs=0.3)


def test_cover_art(media, media_dir, tmp_path):
    from mutagen import File
    song = media("tone.mp3")
    tag_file(song, {"title": "Song", "cover": ((media_dir / "cover.jpg").read_bytes(), "image/jpeg")})
    assert probe_media(song)["cover"]
    kept, _ = edit(song, "m4a", tmp_path)  # converted: the cover art comes along
    assert File(kept).tags["covr"]
    removed, _ = edit(song, "mp3", tmp_path, cover="remove")
    assert not File(removed).tags.getall("APIC")
    png = tmp_path / "new.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:size=32x32", "-frames:v", "1", png)
    changed, _ = edit(song, "mp3", tmp_path, cover=png)  # only the cover: the file is not re-encoded
    # Written again as a plain JPEG, which every player reads.
    assert File(changed).tags.getall("APIC")[0].mime == "image/jpeg" and str(File(changed).tags["TIT2"]) == "Song"
    broken = tmp_path / "broken.jpg"
    broken.write_bytes((media_dir / "cover.jpg").read_bytes()[:40])  # a picture cut short
    with pytest.raises(RuntimeError, match="cannot be used as cover art"):
        edit(song, "mp3", tmp_path, cover=broken)


def test_join_files(media_dir, tmp_path):
    from core.editor import join_media
    infos = [probe_media(media_dir / name) for name in ("video.mp4", "video.mp4")]
    same = join_media([media_dir / "video.mp4"] * 2, "mp4", infos, tmp_path, log)  # copied as they are
    assert media_duration(same) == pytest.approx(8.0, abs=0.3) and probe(same) == ["h264", "aac"]
    work = tmp_path / "mixed"
    work.mkdir()
    paths = [media_dir / "video.mp4", media_dir / "big.mp4", media_dir / "video_only.mp4"]
    mixed = join_media(paths, "mkv", [probe_media(p) for p in paths], work, log)
    assert media_duration(mixed) == pytest.approx(12.0, abs=0.4) and size_of(mixed) == (160, 120)
    sound = join_media([media_dir / "tone.mp3", media_dir / "audio_only.m4a"], "mp3",
                       [probe_media(media_dir / "tone.mp3"), probe_media(media_dir / "audio_only.m4a")], work, log)
    assert probe(sound) == ["mp3"] and media_duration(sound) == pytest.approx(8.0, abs=0.3)
    with pytest.raises(RuntimeError, match="no picture"):
        join_media([media_dir / "tone.mp3"], "mp4", [probe_media(media_dir / "tone.mp3")], work, log)


async def test_edit_tab_picture_sound_and_cover(edit_tab, media, media_dir):
    song = media("tone.mp3")
    tag_file(song, {"cover": ((media_dir / "cover.jpg").read_bytes(), "image/jpeg")})
    await edit_tab.open_file(media_dir / "video.mp4")
    groups = edit_tab.groups
    assert groups["picture"].box.visible and not groups["cover"].box.visible
    edit_tab.crop_dd.value, edit_tab.turn_dd.value = "9:16", "right"
    edit_tab.loudness.value = True
    edit_tab.refresh()
    assert groups["picture"].summary.value == "Vertical 9:16, Turn right"
    assert groups["sound"].summary.value == "volume evened out"
    assert edit_tab.read_options() == {"crop": "9:16", "turn": "right", "loudness": True}
    await edit_tab.open_file(song)
    assert not groups["picture"].box.visible and groups["cover"].box.visible
    assert edit_tab.cover_dd.options[0].text == "Keep the file's own"
    edit_tab.cover_dd.value = "remove"
    edit_tab.refresh()
    assert edit_tab.read_options() == {"cover": "remove"} and groups["cover"].summary.value == "removed"
    await asyncio.sleep(0.5)  # let the preview frames finish
