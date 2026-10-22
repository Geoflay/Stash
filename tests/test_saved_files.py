"""What the app keeps between two launches: settings, history, unfinished downloads, cookies."""

import os
import time

import pytest

from core.cookies import check_cookies, describe_cookies, import_cookies, remove_cookies, saved_cookies_path
from core.history import HISTORY_LIMIT, add_history, clear_history, load_history
from core.storage import load_settings, save_settings, write_json
from core.workdirs import (
    STALE_WORKDIR_AGE, WORKDIR_PREFIX, clean_for_resume, load_pending_downloads, purge_stale_workdirs,
    save_pending_downloads, workdir_parent,
)


def test_settings_are_saved():
    assert load_settings() == {}
    save_settings({"quality": "720", "parallel": 3})
    assert load_settings() == {"quality": "720", "parallel": 3}


def test_history_newest_first_without_duplicates():
    add_history({"url": "u1", "format": "MP4", "title": "first"})
    add_history({"url": "u2", "format": "MP3", "title": "second"})
    add_history({"url": "u1", "format": "MP4", "title": "first again"})
    assert [e["title"] for e in load_history()] == ["first again", "second"]
    assert all(e.get("time") for e in load_history())
    clear_history()
    assert load_history() == []


def test_history_is_limited():
    write_json("history.json", [{"url": "u%d" % i, "format": "MP4"} for i in range(HISTORY_LIMIT)])
    add_history({"url": "new", "format": "MP4"})
    items = load_history()
    assert len(items) == HISTORY_LIMIT
    assert items[0]["url"] == "new" and items[-1]["url"] == "u%d" % (HISTORY_LIMIT - 2)


def test_unfinished_downloads():
    good = {"url": "https://youtu.be/x", "fmt": "mp4", "workdir": "/tmp/videodl_1"}
    save_pending_downloads([good, {"url": "missing fields"}, "garbage"])
    assert load_pending_downloads() == [good]


def test_resume_keeps_only_what_yt_dlp_can_continue(tmp_path):
    keep = ["media.mp4.part", "media.mp4.ytdl", "media.f399.mp4", "media.f140-1.m4a"]
    drop = ["media.mp4", "media.temp.mp4", "converted.mkv"]
    for name in keep + drop:
        (tmp_path / name).write_bytes(b"x")
    clean_for_resume(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(keep)


def test_old_work_folders_are_removed_except_resumable_ones():
    parent = workdir_parent()
    old = os.path.join(parent, WORKDIR_PREFIX + "old")
    kept = os.path.join(parent, WORKDIR_PREFIX + "resumable")
    recent = os.path.join(parent, WORKDIR_PREFIX + "recent")
    for folder in (old, kept, recent):
        os.makedirs(folder, exist_ok=True)
    long_ago = time.time() - STALE_WORKDIR_AGE - 60
    for folder in (old, kept):
        os.utime(folder, (long_ago, long_ago))
    purge_stale_workdirs(keep=[kept])
    assert not os.path.exists(old)
    assert os.path.exists(kept) and os.path.exists(recent)


COOKIES = (
    ".instagram.com\tTRUE\t/\tTRUE\t1000000000\tsessionid\texpired\n"  # 2001
    ".tiktok.com\tTRUE\t/\tTRUE\t4102444800\tsessionid\tvalid\n"  # 2100
    ".example.com\tTRUE\t/\tFALSE\t0\tother\tx\n"
)


def test_import_cookies_without_header():
    summary = import_cookies(COOKIES.encode())
    assert summary == "3 cookies · example.com, instagram.com, tiktok.com"
    assert saved_cookies_path() is not None
    assert describe_cookies(saved_cookies_path()) == summary
    remove_cookies()
    assert saved_cookies_path() is None


@pytest.mark.parametrize("content, message", [(b"", "empty"), (b"not a cookie file\n", "No cookies")])
def test_import_cookies_rejects_bad_files(content, message):
    with pytest.raises(ValueError, match=message):
        import_cookies(content)
    assert saved_cookies_path() is None


def test_check_cookies_offline():
    import_cookies(("# Netscape HTTP Cookie File\n" + COOKIES).encode())
    results = dict((text.split(":")[0], (kind, text)) for kind, text in check_cookies("", saved_cookies_path()))
    assert results["YouTube"][0] == "info"  # no YouTube login cookie: nothing to ask YouTube
    assert results["Instagram"][0] == "err" and "expired on 2001" in results["Instagram"][1]
    assert results["TikTok"][0] == "ok" and "2100" in results["TikTok"][1]
    assert results["Twitter"][0] == "info"


def test_check_cookies_without_cookies():
    [(kind, text)] = check_cookies("", None)
    assert kind == "err" and "No cookies to test" in text
