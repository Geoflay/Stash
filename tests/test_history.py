"""History: search, filters, the thumbnails kept, and the History tab."""

import os
import time
from datetime import datetime

import flet as ft
import pytest

from core.history import (
    THUMB_DIR, THUMB_GRACE, THUMB_KEEP, add_history, filter_history, history_values, load_history,
    prune_thumbnails,
)
from core.storage import data_dir, write_json
from ui.history_tab import PAGE_SIZE, HistoryTab
from ui.progress import DownloadRow

NOW = datetime(2026, 10, 20, 15, 0)
ITEMS = [
    {"title": "Vidéo de vacances", "name": "Vidéo de vacances.mp4", "platform": "Youtube", "format": "MP4",
     "time": "2026-10-20 09:30"},
    {"title": "Song", "name": "Band - Song.mp3", "platform": "Youtube", "format": "MP3", "time": "2026-10-16 12:00"},
    {"title": "Dance", "name": "Dance.mp4", "platform": "TikTok", "format": "MP4", "time": "2026-09-30 08:00"},
    {"title": "Playlist: Mix (3 videos)", "name": "Mix", "platform": "Playlist", "format": "PLAYLIST",
     "time": "2025-01-01 10:00"},
    {"title": "No date", "platform": "Twitter", "format": "MP4"},
]


def titles(items):
    return [e["title"] for e in items]


def test_no_filter_keeps_everything():
    assert filter_history(ITEMS, now=NOW) == ITEMS


@pytest.mark.parametrize("text, expected", [
    ("VIDEO VACANCES", ["Vidéo de vacances"]),  # accents and case do not matter
    ("vacances vidéo", ["Vidéo de vacances"]),  # every word, in any order
    ("band", ["Song"]),  # the file name counts too
    ("mix", ["Playlist: Mix (3 videos)"]),
    ("nothing like this", []),
])
def test_search(text, expected):
    assert titles(filter_history(ITEMS, text, now=NOW)) == expected


def test_platform_and_format():
    assert titles(filter_history(ITEMS, platform="Youtube", now=NOW)) == ["Vidéo de vacances", "Song"]
    assert titles(filter_history(ITEMS, fmt="MP4", now=NOW)) == ["Vidéo de vacances", "Dance", "No date"]
    assert titles(filter_history(ITEMS, platform="Youtube", fmt="MP4", now=NOW)) == ["Vidéo de vacances"]


@pytest.mark.parametrize("period, expected", [
    ("today", ["Vidéo de vacances"]),
    ("week", ["Vidéo de vacances", "Song"]),
    ("month", ["Vidéo de vacances", "Song", "Dance"]),
    ("year", ["Vidéo de vacances", "Song", "Dance"]),
    ("", titles(ITEMS)),
])
def test_period(period, expected):
    assert titles(filter_history(ITEMS, period=period, now=NOW)) == expected


def test_filter_menus():
    assert history_values(ITEMS, "platform") == ["Playlist", "TikTok", "Twitter", "Youtube"]
    assert history_values(ITEMS, "format") == ["MP3", "MP4", "PLAYLIST"]


def test_only_the_recent_thumbnails_are_kept():
    folder = data_dir() / THUMB_DIR
    folder.mkdir(parents=True, exist_ok=True)
    entries = [{"url": "u%d" % i, "format": "MP4", "thumb": "%020x.jpg" % i} for i in range(THUMB_KEEP + 2)]
    old = time.time() - THUMB_GRACE - 60
    for entry in entries:
        path = folder / entry["thumb"]
        path.write_bytes(b"jpg")
        os.utime(path, (old, old))
    write_json("history.json", entries)
    prune_thumbnails()
    kept = sorted(p.name for p in folder.iterdir())
    assert kept == sorted(e["thumb"] for e in entries[:THUMB_KEEP])


# The tab

@pytest.fixture
def tab(fake_app):
    write_json("history.json", [dict(e, url="https://youtu.be/%d" % i) for i, e in enumerate(ITEMS)])
    return HistoryTab(fake_app)


def test_tab_lists_and_filters(tab):
    assert len(tab.table.body.controls) == len(ITEMS)
    assert tab.count.value == "5 downloads" and not tab.filters.clear_btn.visible
    assert [o.key for o in tab.platform_dd.options] == ["", "Playlist", "TikTok", "Twitter", "Youtube"]
    tab.platform_dd.value = "Youtube"
    tab.filters.changed()
    assert len(tab.table.body.controls) == 2 and tab.filters.clear_btn.visible
    assert tab.count.value == "2 of 5 downloads"
    tab.search.value = "zzz"
    tab.on_filter()
    assert not tab.table.body.controls and tab.no_match.visible
    tab.search.value = ""
    tab.filters.clear()  # the filters' button: all of them back
    assert tab.platform_dd.value == "" and len(tab.table.body.controls) == len(ITEMS)


def test_tab_shows_more_on_demand(fake_app):
    for i in range(PAGE_SIZE + 5):
        add_history({"url": "u%d" % i, "format": "MP4", "title": "Video %d" % i})
    tab = HistoryTab(fake_app)
    assert len(tab.table.body.controls) == PAGE_SIZE and tab.more_btn.visible
    tab.show_more()
    assert len(tab.table.body.controls) == PAGE_SIZE + 5 and not tab.more_btn.visible


def test_tab_keeps_filters_after_a_new_download(tab):
    tab.format_dd.value = "MP3"
    tab.on_filter()
    add_history({"url": "new", "title": "New song", "platform": "Youtube", "format": "MP3"})
    tab.refresh()
    assert tab.format_dd.value == "MP3"
    assert tab.count.value == "2 of 6 downloads"
    assert len(load_history()) == 6


def test_queue_row_shows_the_thumbnail():
    row = DownloadRow(None, "Title", lambda job: None, "https://i.ytimg.com/vi/x/hq.jpg")
    picture = row.box.controls[0].controls[0].content
    assert isinstance(picture, ft.Image) and picture.src == "https://i.ytimg.com/vi/x/hq.jpg"
    no_thumbnail = DownloadRow(None, "Title", lambda job: None).box.controls[0].controls[0].content
    assert isinstance(no_thumbnail, ft.Icon)
