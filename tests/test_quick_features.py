"""Presets, choices remembered per site, simple mode, several links at once, searching, keyboard shortcuts,
dropped files, links sent from a phone, and the Edit tab's work on several files (no network, no window)."""

import asyncio
from types import SimpleNamespace

import pytest
from conftest import needs_ffmpeg

import ui.download_tab
from core.backend import BACKEND
from core.link_server import LinkServer, qr_png, send_link
from ui.app import App
from ui.download_tab import DownloadTab, site_choice
from test_download_options import URL, VIDEO, choose

TIKTOK = dict(VIDEO, url="https://www.tiktok.com/@a/video/1", platform="TikTok", chapters=[])


@pytest.fixture
def make_tab(fake_app, monkeypatch):
    """A Download tab analyzing what `infos[url]` says; `tab.sent` lists what went to the queue."""
    sent, infos = [], {URL: VIDEO, TIKTOK["url"]: TIKTOK}

    async def run_download(params):
        sent.append(params)
    fake_app.queue = SimpleNamespace(run_download=run_download)
    fake_app.remember = fake_app.settings.__setitem__
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    fake_app.page.show_dialog = fake_app.page.pop_dialog = lambda *a: None
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(infos[url]))

    def make():
        tab = DownloadTab(fake_app)
        tab.sent = sent
        return tab
    return make


async def analyze(tab, url):
    tab.url_input.value = url
    await tab.analyze()


async def test_presets(make_tab):
    tab = make_tab()
    await analyze(tab, URL)
    assert [c.label.value for c in tab.presets.chips.controls] == ["Save as a preset"]  # none at first
    tab.app.settings["presets"] = [{"name": "Music", "kind": "audio", "fmt": "mp3"},
                                   {"name": "WhatsApp", "kind": "video", "fmt": "mp4", "quality": "720", "max_mb": 16}]
    tab.presets.refresh()
    music, whatsapp = tab.presets.chips.controls[0], tab.presets.chips.controls[1]
    music.on_click(None)
    assert (tab.kind_dd.value, tab.format_dd.value) == ("audio", "mp3") and not tab.size_field.value
    whatsapp.on_click(None)
    assert (tab.kind_dd.value, tab.format_dd.value, tab.quality_dd.value) == ("video", "mp4", "best")  # 720p at most
    assert tab.size_field.value == "16"
    assert (tab.groups["size"].summary.value == "16 MB") == (BACKEND == "ffmpeg")  # said while folded
    await tab.download()
    assert tab.sent[-1]["max_mb"] == (16 if BACKEND == "ffmpeg" else None)
    # Saving the options shown as a preset, then removing it.
    tab.app.settings["presets"] = [dict(tab.current_preset(), name="Mine")]
    tab.presets.refresh()
    mine = tab.presets.chips.controls[0]
    assert mine.label.value == "Mine" and tab.presets.saved()[0]["fmt"] == "mp4"
    mine.on_delete(None)
    assert tab.presets.saved() == []


async def test_choices_remembered_per_site(make_tab):
    tab = make_tab()
    await analyze(tab, TIKTOK["url"])
    tab.kind_dd.value = "video"
    tab.on_kind_change(None)
    await analyze(tab, URL)
    tab.kind_dd.value = "audio"
    tab.on_kind_change(None)
    tab.format_dd.value = "m4a"
    tab.on_format_change(None)
    await analyze(tab, TIKTOK["url"])
    assert tab.kind_dd.value == "video"  # TikTok: as last time on TikTok, not as on YouTube just before
    await analyze(tab, URL)
    assert (tab.kind_dd.value, tab.format_dd.value) == ("audio", "m4a")
    assert site_choice(tab.app.settings, "Vimeo", "kind") == "audio"  # a new site: the last choice


async def test_simple_mode(make_tab):
    tab = make_tab()
    tab.app.settings.update(simple_mode=True, max_mb="5")
    await analyze(tab, URL)
    assert not any(group.box.visible for group in tab.groups.values())
    assert not tab.size_box.visible  # hidden options are not used either
    await tab.download()
    assert tab.sent[-1]["max_mb"] is None and tab.sent[-1]["clip"] is None


async def test_several_links_go_to_the_queue(make_tab, monkeypatch):
    import ui.items
    monkeypatch.setattr(ui.items, "supported_site", lambda url: "Youtube" if "youtube" in url else None)
    tab = make_tab()
    await analyze(tab, "%s\nhttps://example.com/b.mp4 %s" % (URL, URL))
    items = tab.items
    assert items.card.visible and not tab.result_card.visible
    assert [item["entry"]["url"] for item in items.items] == [URL, "https://example.com/b.mp4"]
    assert items.title.value == "2 links"
    items.kind_dd.value = "audio"
    items.on_kind(None)
    await items.download()
    await asyncio.sleep(0.05)
    assert [(p["url"], p["fmt"], p["platform"], p["quick"]) for p in tab.sent] == [
        (URL, "mp3", "Youtube", True), ("https://example.com/b.mp4", "mp3", "", True)]


async def test_an_item_of_the_list_gets_its_own_options(make_tab, monkeypatch):
    import ui.items
    monkeypatch.setattr(ui.items, "fetch_info", lambda url, *a, **k: dict(VIDEO, url=url))
    monkeypatch.setattr(ui.items, "supported_site", lambda url: "Youtube")
    tab = make_tab()
    await analyze(tab, "%s https://www.youtube.com/watch?v=def" % URL)
    items = tab.items
    items.size_field.value = "8"
    first = items.items[0]
    await tab.edit_item(first)  # the editor of one video, with the options of every item
    assert tab.result_card.visible and not items.card.visible and tab.item_bar.visible
    assert first["title"].value == VIDEO["title"]  # a link pasted: its title, once read
    assert tab.size_field.value == "8" and tab.download_btn.content == "Use for this one"
    choose(tab, "audio", "mp3")
    tab.clip.start.value, tab.clip.end.value = "0:10", "0:20"
    tab.refresh_options()
    await tab.download()  # kept for this item, not downloaded
    assert tab.sent == [] and items.card.visible and first["own"].value == "Own options: MP3"
    await tab.edit_item(first)  # shown again as it was left
    assert tab.format_dd.value == "mp3" and tab.clip.start.value == "0:10"
    tab.back_to_list()
    await items.download()
    await asyncio.sleep(0.05)
    assert [(p["fmt"], p["clip"]) for p in tab.sent] == [("mp3", [10.0, 20.0]), ("mp4", None)]
    assert tab.sent[1]["max_mb"] == 8.0


async def test_search(make_tab, monkeypatch):
    found = [{"url": URL, "title": "Song", "uploader": "Channel", "duration": 120, "thumbnail": ""}]
    monkeypatch.setattr(ui.download_tab, "search_youtube", lambda query, **k: found if query == "a song" else [])
    tab = make_tab()
    await analyze(tab, "a song")
    assert tab.search_card.card.visible and tab.search_card.title.value.endswith("“a song”")
    tab.search_card.list.controls[0].on_click(None)  # tapping a result analyzes it
    await asyncio.sleep(0.5)
    assert tab.result_card.visible and tab.url_input.value == URL
    await analyze(tab, "www.youtube.com/watch?v=abc")  # a link typed without https://
    assert tab.result_card.visible


async def test_keyboard_shortcuts(make_tab):
    tab = make_tab()
    await analyze(tab, URL)
    app = SimpleNamespace(settings={"keyboard_shortcuts": True}, download=tab, edit=None, page=tab.page,
                          nav=SimpleNamespace(selected_index=0), view_names=["home"],
                          home=SimpleNamespace(current="download"))
    key = SimpleNamespace(key="Enter", ctrl=False, alt=False, meta=False, shift=False)
    tab.url_input.on_focus(None)
    await App.on_key(app, key)
    assert tab.sent == []  # Enter in the link field analyzes, it does not download
    await tab.url_input.on_blur(None)
    await App.on_key(app, key)
    assert len(tab.sent) == 1
    app.settings["keyboard_shortcuts"] = False  # off by default
    await App.on_key(app, key)
    assert len(tab.sent) == 1


async def test_dropped_files(tmp_path):
    opened, analyzed, added = [], [], []
    link_file = tmp_path / "video.url"
    link_file.write_text("[InternetShortcut]\nURL=%s\n" % URL)
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp3").write_bytes(b"")

    async def analyze_link(text):
        analyzed.append(text)
    app = SimpleNamespace(open_tool=opened.append, show_view=lambda i: None, page=SimpleNamespace(update=lambda: None),
                          edit=SimpleNamespace(files=SimpleNamespace(add=lambda paths: added.append(len(paths)))),
                          download=SimpleNamespace(analyze_link=analyze_link))
    await App.open_dropped(app, [str(tmp_path / "a.mp4")])
    await App.open_dropped(app, [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp3")])
    await App.open_dropped(app, [str(link_file)])
    assert opened == ["file", "file"] and added == [1, 2] and analyzed == [URL]  # in Edit file(s)


async def test_links_from_a_phone(fake_app):
    received = []
    server = LinkServer("secret", received.append)
    address = server.start(port=8790)
    try:
        local = address.replace(address.split("/")[2].split(":")[0], "127.0.0.1")
        assert await asyncio.to_thread(send_link, local, "look: %s" % URL) == 1
        assert received == [URL]
        with pytest.raises(OSError):  # without the key, nothing is accepted
            await asyncio.to_thread(send_link, local.rsplit("/", 1)[0] + "/wrong", URL)
    finally:
        server.stop()
    assert qr_png(address)[:8] == b"\x89PNG\r\n\x1a\n"
    # A link received is analyzed in the Download tab, like a pasted one.
    analyzed = []

    async def analyze_link(link):
        analyzed.append(link)
    fake_app.download = SimpleNamespace(analyze_link=analyze_link)
    fake_app.show_view = lambda index: None
    fake_app.page.show_dialog = lambda *a: None
    await App.receive_link(fake_app, URL)
    assert analyzed == [URL]


def edit_files(fake_app):
    """Edit file(s): the list of files of the editor of one file."""
    from ui.edit_tab import EditTab
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    return EditTab(fake_app).files


@needs_ffmpeg
async def test_join_and_edit_several_files(fake_app, media_dir, tmp_path):
    from ui.edit_more import JoinCard
    fake_app.notify_if_away = fake_app.check_storage_access
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    for name in ("video.mp4", "tone.mp3", "audio_only.m4a"):
        (tmp_path / name).write_bytes((media_dir / name).read_bytes())
    join = JoinCard(fake_app)
    join.set_files([tmp_path / "video.mp4", tmp_path / "video.mp4"])  # a file can be joined twice
    assert join.format_dd.value == "mp4" and not join.go_btn.disabled and len(join.table.body.controls) == 2
    await join.run()
    assert (tmp_path / "video (joined).mp4").exists()
    files = edit_files(fake_app)
    files.set_files([tmp_path / "tone.mp3", tmp_path / "audio_only.m4a"])
    assert files.defaults.visible  # several files: the changes of every file
    files.format_dd.value = "mp3"
    await files.run()
    text = files.result.content.controls[-1].controls[0].value
    assert "1 of 2 files saved" in text and "Nothing to change: tone.mp3" in text
    assert (tmp_path / "audio_only (edited).mp3").exists()
    files.changes.loudness.value = True  # the volume evened out: the MP3 is edited too
    await files.run()
    assert (tmp_path / "tone (edited).mp3").exists()
    await asyncio.sleep(0.5)  # let the pictures of the rows finish


@needs_ffmpeg
async def test_a_file_of_several_has_its_own_changes(fake_app, media_dir, tmp_path):
    for name in ("video.mp4", "tone.mp3"):
        (tmp_path / name).write_bytes((media_dir / name).read_bytes())
    files = edit_files(fake_app)
    editor = files.editor
    files.add([tmp_path / "video.mp4"])
    await asyncio.sleep(1)  # a single file: its editor opens at once
    assert editor.single_row.visible and not files.card.visible and editor.source.name == "video.mp4"
    assert not editor.list_bar.visible and editor.save_btn.content == "Save the edited copy"
    editor.format_dd.value = "mkv"
    editor.keep_changes()  # more files added: these changes stay its own
    files.add([tmp_path / "tone.mp3"])
    files.show_list()
    assert files.files[0]["own"]["fmt"] == "mkv" and files.files[1]["own"] is None
    files.changes.loudness.value = True
    await files.open_record(files.files[1])  # the changes of every file, on this one
    assert editor.loudness.value and editor.list_bar.visible and editor.save_btn.content == "Use for this file"
    editor.format_dd.value = "m4a"
    await editor.save()  # kept for this file, back to the list
    assert files.card.visible and files.files[1]["own"]["fmt"] == "m4a"
    await files.open_record(files.files[0])
    assert editor.format_dd.value == "mkv"  # as chosen for it
    files.reset(files.files[0])
    assert files.files[0]["own"] is None
    await asyncio.sleep(0.5)


SUBTITLED = dict(VIDEO, subtitles=[{"lang": "en", "name": "English", "auto": False},
                                   {"lang": "fr", "name": "French", "auto": False}])


async def test_several_subtitles_and_edit_changes(make_tab, monkeypatch):
    import ui.download_tab
    monkeypatch.setattr(ui.download_tab, "fetch_info", lambda url, *a, **k: dict(SUBTITLED))
    tab = make_tab()
    await analyze(tab, URL)
    picker = tab.subs_picker
    for key in ("m:fr", "m:en"):
        picker.menu.value = key
        picker.on_add()
    assert picker.value == ["m:fr", "m:en"] and tab.groups["subs"].summary.value == "French (fr), English (en)"
    assert tab.app.settings["subs_choices"] == ["m:fr", "m:en"]  # remembered for the next video
    tab.subs_mode_dd.value = "embed"
    await tab.download()
    sent = tab.sent[-1]
    assert sent["sub_keys"] == ["m:fr", "m:en"] and sent["sub_key"] == "m:fr" and sent["edits"] is None
    if BACKEND != "ffmpeg":
        return
    # The Edit tab's changes, made once downloaded.
    tab.crop_dd.value, tab.turn_dd.value = "9:16", "left"
    tab.loudness.value = True
    tab.subs_mode_dd.value = "burn" if any(o.key == "burn" for o in tab.subs_mode_dd.options) else "embed"
    tab.refresh_options()
    assert tab.groups["picture"].summary.value == "Vertical 9:16, Turn left"
    await tab.download()
    sent = tab.sent[-1]
    assert {k: sent["edits"][k] for k in ("crop", "turn", "loudness")} == {"crop": "9:16", "turn": "left",
                                                                          "loudness": True}
    if tab.subs_mode_dd.value == "burn":  # drawn: only the first language, with its style
        assert sent["edits"]["burn"]["key"] == "m:fr" and sent["sub_keys"] == []
        assert sent["edits"]["burn"]["style"]["position"] == "bottom"
    tab.no_silence.value = True  # cut silences: subtitles would not match
    tab.refresh_options()
    assert not tab.subs_box.visible


async def test_files_filters_sort_and_options(fake_app, tmp_path):
    paths = [tmp_path / name for name in ("a.mp4", "b.mkv", "c.mp3", "d.flac")]
    for size, path in enumerate(paths):
        path.write_bytes(b"x" * size)
    files = edit_files(fake_app)
    files.set_files(paths)
    assert [o.key for o in files.ext_dd.options] == ["", "flac", "mkv", "mp3", "mp4"]
    for kind, ext, names in (("video", "", ["a.mp4", "b.mkv"]), ("audio", "", ["c.mp3", "d.flac"]),
                             ("", "mkv", ["b.mkv"])):
        files.kind_dd.value, files.ext_dd.value = kind, ext
        files.filters.changed()
        assert [r["path"].name for r in files.shown()] == names and files.filters.clear_btn.visible
    files.filters.clear()
    assert len(files.shown()) == 4 and not files.filters.clear_btn.visible
    size = next(c for c in files.table.columns if c.key == "size")
    files.table.sort_by(size)
    files.table.sort_by(size)  # biggest first
    assert [r["path"].name for r in files.files] == ["d.flac", "c.mp3", "b.mkv", "a.mp4"]
    changes = files.changes
    changes.height_dd.value, changes.crop_dd.value = "720", "1:1"
    changes.no_silence.value = True
    changes.size_field.value = "5"
    changes.tag_fields["album"].value = "Holidays"
    changes.speed.set(1.5)
    assert changes.read() == {"height": 720, "crop": "1:1", "no_silence": True, "max_bytes": 5 * 1024 * 1024,
                              "speed": 1.5, "tags": {"album": "Holidays"}}
    changes.refresh()
    assert changes.groups["picture"].summary.value == "720p at most, Square"
    await asyncio.sleep(0.5)  # let the files be read
