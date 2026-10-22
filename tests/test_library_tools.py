"""The Library's tools (how long it remembers, files saved twice, groups, grid, menus, several selected, Undo),
the queue's (drag, Undo, one notification per list) and subscriptions (no network, no window)."""

import asyncio
import os
import time
from datetime import datetime

import pytest

import ui.subscriptions
from core.history import (
    delete_file, duplicates, filter_history, load_history, prune_history, save_history,
)
from core.playlist import add_to_m3u, sort_entries
from core.storage import write_json
from core.subscriptions import changes, is_due, new_subscription
import test_queue_and_playlist
from test_queue_and_playlist import PARAMS, PLAYLIST, download_list, item_list
from ui.history_tab import HistoryTab, entry_key
from ui.queue_tab import QueueTab
from ui.subscriptions import Subscriptions

NOW = datetime(2026, 10, 20, 12, 0)
fake_download = test_queue_and_playlist.fake_download  # the fixture


def test_library_forgets_old_downloads():
    save_history([{"title": "new", "time": "2026-10-19 10:00"}, {"title": "old", "time": "2025-01-01 10:00"},
                  {"title": "no date"}])
    assert prune_history(0, NOW) == 0  # always kept
    assert prune_history(90, NOW) == 1 and [e["title"] for e in load_history()] == ["new", "no date"]


def test_files_saved_twice(tmp_path):
    a, b, c = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp3"
    for f in (a, b, c):
        f.write_bytes(b"x" * 10)
    items = [{"title": "A", "platform": "Youtube", "video_id": "1", "path": str(a)},
             {"title": "B", "platform": "Youtube", "video_id": "1", "path": str(b)},
             {"title": "C", "platform": "Youtube", "video_id": "2", "path": str(c)},
             {"title": "D", "platform": "Youtube", "video_id": "2", "path": str(tmp_path / "gone.mp3")}]
    assert [e["title"] for e in duplicates(items)] == ["A", "B"]  # a file deleted since is not a copy
    assert [e["title"] for e in filter_history(items, state="duplicates")] == ["A", "B"]
    assert delete_file(items[1]) == 10 and not b.exists()


def test_list_order_and_playlist_file(tmp_path):
    entries = [{"title": "a", "duration": 60, "date": "20240102"}, {"title": "b", "duration": None, "date": ""},
               {"title": "c", "duration": 300, "date": "20240305"}]
    assert [e["title"] for e in sort_entries(entries, "longest")] == ["c", "a", "b"]
    assert [e["title"] for e in sort_entries(entries, "oldest")] == ["a", "c", "b"]
    assert sort_entries(entries, "") == entries
    playlist = tmp_path / "Mix.m3u8"
    for position, name in ((2, "third.mp3"), (0, "first.mp3"), (1, "second.mp3")):  # saved in any order
        (tmp_path / name).write_bytes(b"")
        add_to_m3u(playlist, tmp_path / name, position, name[:-4], 61)
    lines = playlist.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.endswith(".mp3")] == ["first.mp3", "second.mp3", "third.mp3"]
    assert lines[0] == "#EXTM3U" and "#EXTINF:61,first" in lines


# The Library tab

@pytest.fixture
def library(fake_app, tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x" * 100)
    write_json("history.json", [
        {"title": "Song", "format": "MP3", "path": str(song), "url": "https://a", "time": "2026-10-20 10:00",
         "list": "Mix"},
        {"title": "Clip", "format": "MP4", "url": "https://b", "time": "2026-10-18 10:00"}])
    fake_app.remember = fake_app.settings.__setitem__
    fake_app.page.show_dialog = fake_app.page.pop_dialog = lambda *a: None
    return HistoryTab(fake_app)


def test_library_menu(library, fake_app):
    labels = [label for _, label, _ in library.actions(library.items[0])]
    assert labels[labels.index("Open"):] == ["Open", "Show in its folder", "Send to a phone", "Download again (other options)",
                      "Copy the link", "Delete the file", "Forget it (the file is kept)"]
    assert [label for _, label, _ in library.actions(library.items[1])] == [
        "Download again (other options)", "Copy the link", "Forget it (the file is kept)"]
    library.forget([library.items[1]])  # forgotten, with Undo
    assert [e["title"] for e in load_history()] == ["Song"]
    message, undo = fake_app.undo[-1]
    undo()
    assert [e["title"] for e in load_history()] == ["Song", "Clip"] and len(library.items) == 2


def test_library_sorted_by_a_column_and_selection(library, fake_app):
    assert library.table.sort_key == "time" and not library.table.ascending  # newest first
    title = next(c for c in library.table.columns if c.key == "title")
    library.table.sort_by(title)
    assert [e["title"] for e in library.found] == ["Clip", "Song"]
    assert fake_app.settings["library_sort"] == {"key": "title", "ascending": True}  # remembered
    library.table.sort_by(title)  # again: the other way
    assert [e["title"] for e in library.found] == ["Song", "Clip"]
    library.toggle_select()
    library.selected.add(entry_key(library.items[0]))
    library.show_rows()
    assert library.selection_bar.visible and library.selection_text.value == "1 selected"
    library.forget_selected()
    assert [e["title"] for e in load_history()] == ["Clip"]


def test_library_clear_can_be_undone(library, fake_app):
    library.on_clear()
    assert load_history() == []
    fake_app.undo[-1][1]()
    assert len(load_history()) == 2


# The queue

async def test_clear_finished_can_be_undone(fake_app, fake_download):
    fake_download()
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    queue.clear_finished()
    assert queue.jobs == []
    fake_app.undo[-1][1]()
    assert len(queue.jobs) == 1


async def test_one_notification_for_a_list(fake_app, fake_download):
    fake_download()
    notes = []

    async def notify(title, message):
        notes.append(title)
    fake_app.notify_if_away = notify
    items = item_list(fake_app)
    await download_list(items, fake_app)
    await asyncio.sleep(0.1)
    assert notes == ["List downloaded: My playlist"]  # not one per video


# Subscriptions

def test_subscription_changes():
    entries = [{"url": "u1"}, {"url": "u2"}]
    sub = new_subscription("list", "List", {}, False, entries)
    assert sub["seen"] == ["u1", "u2"] and not is_due(sub, 6) and is_due(sub, 6, time.time() + 6 * 3600)
    now = [{"url": "u3"}, {"url": "u2"}]
    assert changes(sub, now, complete=True) == ([{"url": "u3"}], ["u1"])
    assert changes(sub, now, complete=False) == ([{"url": "u3"}], [])  # the list may be longer than read
    assert new_subscription("list", "List", {}, False, entries, download_existing=True)["seen"] == []


async def test_subscription_downloads_the_new_ones(fake_app, fake_download, monkeypatch, tmp_path):
    fake = fake_download()
    items = item_list(fake_app)
    fake_app.history = HistoryTab(fake_app)
    fake_app.playlist_limit = lambda site: 100
    fake_app.offer_undo = lambda message, undo: None
    subs = Subscriptions(fake_app)
    fake_app.subscriptions = subs
    subs.subscribe("https://www.youtube.com/playlist?list=x", PLAYLIST, items.options(), False)
    assert not subs.empty.visible and len(subs.items) == 1
    newer = dict(PLAYLIST, total=3, entries=[{"url": "https://www.youtube.com/watch?v=new", "title": "New one",
                                              "duration": 60, "thumbnail": ""}] + PLAYLIST["entries"][:2])
    monkeypatch.setattr(ui.subscriptions, "fetch_playlist", lambda *a, **k: newer)
    sub = subs.items[0]
    sub["remove_gone"] = True
    gone = tmp_path / "gone.mp4"
    gone.write_bytes(b"x")
    save_history([{"title": "Video 2", "url": "https://www.youtube.com/watch?v=2", "path": str(gone),
                   "subscription": sub["url"]}])
    await subs.check(sub)
    for _ in range(100):
        if fake.calls and not any(job.running for job in fake_app.queue.jobs):
            break
        await asyncio.sleep(0.05)
    assert [call["url"] for call in fake.calls] == ["https://www.youtube.com/watch?v=new"]
    assert not gone.exists() and sub["note"] == "1 new, 1 deleted"  # Video 2 left the list
    assert os.path.isdir(fake_app.destination / "My playlist")  # in a folder named after the list
    await subs.check(sub)  # nothing new since
    assert len(fake.calls) == 1
