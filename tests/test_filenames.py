import threading

import pytest

from core.filenames import (
    DEFAULT_FILENAME_TEMPLATE, filename_fields, render_filename, reserve_file, safe_filename, unique_path,
    unknown_filename_fields,
)

INFO = {"title": "Never Gonna Give You Up", "uploader": "Rick Astley", "upload_date": "20091025",
        "extractor_key": "Youtube", "id": "dQw4w9WgXcQ", "height": 1080}


@pytest.mark.parametrize("title, name", [
    ("a/b:c*?", "abc.mp4"), ("", "video.mp4"), (" ..", "video.mp4"), ("Héllo", "Héllo.mp4"),
])
def test_safe_filename(title, name):
    assert safe_filename(title, ".MP4") == name


def test_safe_filename_is_shortened():
    assert safe_filename("x" * 200, ".mp4") == "x" * 120 + ".mp4"


def test_default_template_keeps_the_title():
    assert render_filename(DEFAULT_FILENAME_TEMPLATE, filename_fields(INFO, {}), ".mp4") == \
        "Never Gonna Give You Up.mp4"


@pytest.mark.parametrize("template, name", [
    ("{uploader} - {title} ({date})", "Rick Astley - Never Gonna Give You Up (2009-10-25).mp4"),
    ("{title} [{resolution}] {year}", "Never Gonna Give You Up [1080p] 2009.mp4"),
    ("{platform} {id}", "Youtube dQw4w9WgXcQ.mp4"),
    ("{uploader}/{title}", "Rick Astley - Never Gonna Give You Up.mp4"),  # no sub-folders
    ("{title} {nope}", "Never Gonna Give You Up {nope}.mp4"),  # unknown fields stay as text
])
def test_templates(template, name):
    assert render_filename(template, filename_fields(INFO, {}), ".mp4") == name


def test_empty_fields_are_tidied_up():
    fields = filename_fields({"title": "Clip"}, {})
    assert render_filename("{title} ({date}) [{resolution}]", fields, ".mp4") == "Clip.mp4"
    assert render_filename("{uploader} - {title} - {id}", fields, ".mp4") == "Clip.mp4"


def test_slash_in_a_title_is_removed():
    fields = filename_fields({"title": "AC/DC live"}, {})
    assert render_filename("{title}", fields, ".mp3") == "ACDC live.mp3"


def test_fields_fall_back_on_the_analysis():
    fields = filename_fields({}, {"title": "T", "uploader": "U", "upload_date": "20240531", "platform": "TikTok",
                                  "video_id": "123"})
    assert fields == {"title": "T", "uploader": "U", "date": "2024-05-31", "year": "2024", "platform": "TikTok",
                      "id": "123", "resolution": "", "artist": "U", "song": "T"}


def test_song_fields():
    fields = filename_fields({"title": "Rick Astley - Never Gonna Give You Up (Official Video)",
                              "uploader": "Rick Astley"}, {}, video=False)
    assert render_filename("{artist} - {song}", fields, ".mp3") == "Rick Astley - Never Gonna Give You Up.mp3"


def test_no_resolution_for_audio():
    assert filename_fields(INFO, {}, video=False)["resolution"] == ""


def test_unknown_fields_are_listed_once():
    assert unknown_filename_fields("{title} {foo} {foo} {bar} {date}") == ["foo", "bar"]


def test_unique_path(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "a (1).mp4").write_bytes(b"")
    assert unique_path(tmp_path, "a.mp4").name == "a (2).mp4"
    assert unique_path(tmp_path, "b.mp4").name == "b.mp4"


def test_reserve_file_never_gives_the_same_name_twice(tmp_path):
    names, lock = [], threading.Lock()

    def worker():
        path = reserve_file(tmp_path, "Same title.mp4")
        with lock:
            names.append(path.name)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(names)) == 20
    assert len(list(tmp_path.iterdir())) == 20
