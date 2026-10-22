"""Songs: the title of a video read as artist and title, MusicBrainz and LRCLIB answers (stand-ins: no
network), lyrics written into the files, and albums cut into tagged tracks (from the description's times)."""

import pytest
from conftest import ffmpeg, needs_ffmpeg

import core.music as music
from core.chapters import chapters_from_description, split_chapters
from core.tags import tag_file


@pytest.mark.parametrize("info, song", [
    ({"title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)"},
     ("Rick Astley", "Never Gonna Give You Up")),
    ({"title": "Daft Punk - Get Lucky (Official Audio) ft. Pharrell Williams"}, ("Daft Punk", "Get Lucky")),
    ({"title": "Bohemian Rhapsody", "uploader": "Queen - Topic"}, ("Queen", "Bohemian Rhapsody")),
    ({"title": "x", "track": "Blinding Lights", "artist": "The Weeknd"}, ("The Weeknd", "Blinding Lights")),
])
def test_guess_song(info, song):
    assert music.guess_song(info) == song


def recording(title, length, releases, score=100):
    return {"title": title, "score": score, "length": length, "artist-credit": [{"name": "The Weeknd"}],
            "releases": releases}


ALBUM = {"id": "r1", "title": "After Hours", "status": "Official", "date": "2020-03-20",
         "release-group": {"id": "g1", "primary-type": "Album"}}
HITS = {"id": "r2", "title": "Hits 2020", "status": "Official", "date": "2020-01-01",
        "release-group": {"id": "g2", "primary-type": "Album", "secondary-types": ["Compilation"]}}


def test_find_recording(monkeypatch):
    answers = []
    monkeypatch.setattr(music, "MB_INTERVAL", 0)
    monkeypatch.setattr(music, "_get_json", lambda url: {"recordings": answers.pop(0)})
    # The instrumental and a recording of another length are left out; the album wins over the compilation.
    answers[:] = [[recording("Blinding Lights (instrumental)", 200000, [ALBUM]),
                   recording("Blinding Lights", 260000, [ALBUM]),
                   recording("Blinding Lights", 201000, [HITS, ALBUM])]]
    assert music.find_recording("The Weeknd", "Blinding Lights", 200) == {
        "title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours", "date": "2020-03-20",
        "release": "r1", "release_group": "g1"}
    # Only on compilations: the title and artist, not the compilation's name as the album.
    answers[:] = [[], [recording("Blinding Lights", 201000, [HITS])]]
    assert music.find_recording("The Weeknd", "Blinding Lights", 200)["album"] == ""
    answers[:] = [[], [recording("Blinding Lights", 201000, [ALBUM], score=60)]]  # not sure enough
    assert music.find_recording("The Weeknd", "Blinding Lights", 200) is None


def test_find_lyrics(monkeypatch):
    synced = "[00:01.00] one\n[00:02.00] two\n[00:03.00] three\n[00:04.00] four"
    answers = {"get": {"duration": 200, "syncedLyrics": "[00:00.00]probe"},  # a test entry: left out
               "search": [{"duration": 260, "syncedLyrics": synced},  # another length
                          {"duration": 201, "syncedLyrics": synced}]}
    monkeypatch.setattr(music, "_get_json", lambda url: answers["get" if "/get?" in url else "search"])
    assert music.find_lyrics("The Weeknd", "Blinding Lights", duration=200) == synced


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp3", "m4a", "flac", "opus"])
def test_lyrics_are_written(media_dir, tmp_path, ext):
    from mutagen import File
    song = tmp_path / ("song." + ext)
    ffmpeg("-i", media_dir / "tone.mp3", song)
    tag_file(song, {"title": "Song", "lyrics": "[00:01.00] Hello"})
    tags = File(song).tags
    text = (tags.getall("USLT")[0].text if ext == "mp3" else tags["\xa9lyr"][0] if ext == "m4a"
            else tags["lyrics"][0])
    assert text == "[00:01.00] Hello"


def test_chapters_from_the_description():
    description = "Tracklist:\n0:00 Intro\n03:12 Artist B - Second\nSee you\n10:05 Last one"
    assert chapters_from_description(description, 900) == [
        {"title": "Intro", "start": 0.0, "end": 192.0}, {"title": "Artist B - Second", "start": 192.0, "end": 605.0},
        {"title": "Last one", "start": 605.0, "end": 900.0}]
    assert chapters_from_description("Song 0:00\nOther 1:00", 300) == []  # fewer than 3
    assert chapters_from_description("1:00 a\n2:00 b\n3:00 c", 300) == []  # not from the start


@needs_ffmpeg
def test_album_tracks_are_tagged(media_dir, tmp_path):
    from mutagen import File
    chapters = [{"title": "01. Intro", "start": 0.0, "end": 2.0}, {"title": "Guest - Duet", "start": 2.0, "end": 4.0}]
    folder = split_chapters(media_dir / "tone.mp3", chapters, tmp_path, lambda *a: None,
                            tags={"album": "The Album", "artist": "Band"})
    first, second = sorted(folder.iterdir())
    assert (str(File(first).tags["TIT2"]), str(File(first).tags["TPE1"])) == ("Intro", "Band")
    assert (str(File(second).tags["TIT2"]), str(File(second).tags["TPE1"]), str(File(second).tags["TRCK"])) == (
        "Duet", "Guest", "2/2")
    assert str(File(second).tags["TALB"]) == "The Album"


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp3", "m4a", "flac"])
def test_more_tags_are_written(media_dir, tmp_path, ext):
    from mutagen import File
    song = tmp_path / ("song." + ext)
    ffmpeg("-i", media_dir / "tone.mp3", song)
    tag_file(song, {"title": "Song", "album_artist": "Various", "genre": "Jazz", "track": "4", "composer": "Me",
                    "comment": "About it"})
    tags = File(song).tags
    if ext == "mp3":
        values = [str(tags[k]) for k in ("TPE2", "TCON", "TRCK", "TCOM")] + [tags.getall("COMM")[0].text[0]]
    elif ext == "m4a":
        values = [tags["aART"][0], tags["\xa9gen"][0], str(tags["trkn"][0][0]), tags["\xa9wrt"][0], tags["desc"][0]]
    else:
        values = [tags[k][0] for k in ("albumartist", "genre", "tracknumber", "composer", "description")]
    assert values == ["Various", "Jazz", "4", "Me", "About it"]


def test_cover_size_and_square(monkeypatch):
    import io
    from PIL import Image
    from core.tags import TAG_OPTIONS, fit_cover, media_metadata
    picture = io.BytesIO()
    Image.new("RGB", (1600, 900), "red").save(picture, "PNG")
    data = picture.getvalue()
    assert fit_cover(data) == data  # nothing asked: unchanged
    with Image.open(io.BytesIO(fit_cover(data, 500))) as im:
        assert im.size == (500, 281)
    with Image.open(io.BytesIO(fit_cover(data, 300, square=True))) as im:
        assert im.size == (300, 300)
    monkeypatch.setitem(TAG_OPTIONS, "genre", False)
    monkeypatch.setitem(TAG_OPTIONS, "comment", True)
    meta = media_metadata(None, {"title": "T", "genre": "Pop", "description": " Notes "})
    assert "genre" not in meta and meta["comment"] == "Notes"
