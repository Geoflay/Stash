"""Error reports, the playlist size estimate, album art, photo posts, the backup copy and the file check in the
queue (no network, no window)."""

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import files_in
from PIL import Image
from yt_dlp.utils import DownloadError

import ui.queue_tab
from core.analyze import twitter_photo_post
from core.errors import error_report
from core.streams import estimate_playlist
from core.tags import album_art
from test_queue_and_playlist import PARAMS, FakeDownload, result_text
from ui import widgets
from ui.app import App
from ui.queue_tab import QueueTab


def test_error_report_keeps_the_whole_message():
    long_error = DownloadError("ERROR: [youtube] abc: " + "x" * 500)
    report = error_report(long_error, "https://www.youtube.com/watch?v=abc", "Update yt-dlp.")
    assert "x" * 500 in report and "Link: https://www.youtube.com/watch?v=abc" in report
    assert "Hint shown: Update yt-dlp." in report and "yt-dlp " in report and "Python " in report


def test_copy_the_details_button(monkeypatch):
    copied = []
    monkeypatch.setitem(widgets.clipboard, "copy", copied.append)
    holder = SimpleNamespace(content=None, visible=False)
    widgets.set_status(holder, "err", "It failed", details="full report")
    button = holder.content.controls[-1].controls[-1].controls[0]
    button.update = lambda: None
    button.on_click(SimpleNamespace(control=button))
    assert copied == ["full report"] and button.content == "Copied"
    widgets.set_status(holder, "err", "It failed")  # no details: no button
    assert len(holder.content.controls[-1].controls) == 1


def test_playlist_estimate():
    size, unknown = estimate_playlist([(100, "mp3", "best"), (100, "mp4", "720"), (None, "mp4", "best"),
                                       (100, "mp4", "1440"), (100, "audio", None)])
    assert size == 100 * (32_000 + 300_000 + 560_000 + 17_000) and unknown == 1


def picture(width, height, bars=True):
    """A 16:9 picture: a noisy square in the middle, with plain bars or noise on the sides."""
    im = Image.effect_noise((width, height), 80).convert("RGB")
    if bars:
        side = (width - height) // 2
        im.paste((90, 47, 38), (0, 0, side, height))
        im.paste((90, 47, 38), (width - side, 0, width, height))
    out = io.BytesIO()
    im.save(out, "WEBP")
    return out.getvalue()


def test_album_art_of_a_music_track():
    art = album_art(picture(1280, 720))
    assert Image.open(io.BytesIO(art)).size == (720, 720)
    assert album_art(picture(1280, 720, bars=False)) is None  # a music video: its own picture is kept
    assert album_art(picture(720, 720)) is None


def test_photos_of_a_tweet():
    status = {"full_text": "Look at this https://t.co/abc", "user": {"screen_name": "someone"},
              "extended_entities": {"media": [
                  {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg",
                   "original_info": {"width": 800, "height": 600}},
                  {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.png"}]}}
    post = twitter_photo_post(status)
    assert post["title"] == "Look at this" and post["uploader"] == "someone"
    assert [im["url"] for im in post["images"]] == ["https://pbs.twimg.com/media/A.jpg?name=orig",
                                                     "https://pbs.twimg.com/media/B.png?name=orig"]
    status["extended_entities"]["media"].append({"type": "video"})
    assert twitter_photo_post(status) is None  # yt-dlp downloads the video


async def test_photos_saved_as_separate_files(fake_app, monkeypatch):
    def fake_photos(images, platform, workdir, cookies_browser="", on_progress=None, cookie_file=None):
        folder = Path(workdir, "photos")
        folder.mkdir()
        for i, _ in enumerate(images, 1):
            Path(folder, "%02d.jpg" % i).write_bytes(b"jpg")
        return folder
    monkeypatch.setattr(ui.queue_tab, "download_photos", fake_photos)
    fake_app.settings["folder_template"] = "{type}"
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS, fmt="photos", platform="Twitter", images=[{"url": "a"}, {"url": "b"}]))
    assert files_in(fake_app.destination) == ["Images/My video/01.jpg", "Images/My video/02.jpg"]
    assert result_text(queue.jobs[0]).endswith("(2 files)")


class DamagedDownload(FakeDownload):
    def __call__(self, *args, **kwargs):
        path = super().__call__(*args, **kwargs)
        if kwargs.get("check"):
            kwargs["info_out"]["problems"] = ["It lasts 0:12 instead of 4:12: it may be cut short."]
        return path


async def test_damaged_file_and_backup_copy(fake_app, monkeypatch):
    monkeypatch.setattr(ui.queue_tab, "download_media", DamagedDownload())
    copies = []

    async def copy_to_backup(path, name, fields):
        copies.append((name, fields["type"]))
        return "Backup copy: D:/Cloud/%s" % name
    fake_app.copy_to_backup = copy_to_backup
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    text = result_text(queue.jobs[0])
    assert "Backup copy: D:/Cloud/Title from yt-dlp.mp4" in text and "it may be cut short" in text
    assert copies == [("Title from yt-dlp.mp4", "Videos")]
    fake_app.settings["check_files"] = False  # turned off in Settings
    await queue.run_download(dict(PARAMS))
    assert "cut short" not in result_text(queue.jobs[1])


async def test_error_has_its_report(fake_app, monkeypatch, tmp_path):
    copied = []
    monkeypatch.setitem(widgets.clipboard, "copy", copied.append)
    monkeypatch.setattr(ui.queue_tab, "download_media", FakeDownload(error=DownloadError("ERROR: something broke")))
    queue = QueueTab(fake_app)
    await queue.run_download(dict(PARAMS))
    button = queue.jobs[0].result.content.controls[-1].controls[-1].controls[0]
    button.update = lambda: None
    button.on_click(SimpleNamespace(control=button))
    assert "Error: something broke" in copied[0] and PARAMS["url"] in copied[0]


@pytest.mark.parametrize("is_dir", [False, True])
async def test_copy_to_backup(tmp_path, is_dir):
    source = tmp_path / "work" / "media"
    if is_dir:
        source.mkdir(parents=True)
        (source / "01.jpg").write_bytes(b"jpg")
    else:
        source.parent.mkdir()
        source.write_bytes(b"media")
    backup = tmp_path / "cloud"
    backup.mkdir()
    app = SimpleNamespace(settings={"backup_dir": str(backup), "folder_template": "{type}"}, can_pick_folder=True,
                          android=False, storage_access=None)
    app.backup_folder = lambda: App.backup_folder(app)
    app.sorted_folder = lambda base, fields: App.sorted_folder(app, base, fields)
    line = await App.copy_to_backup(app, source, "Song" if is_dir else "Song.mp3", {"type": "Music"})
    assert line.startswith("Backup copy: ")
    assert files_in(backup) == (["Music/Song/01.jpg"] if is_dir else ["Music/Song.mp3"])
    app.settings["backup_dir"] = str(tmp_path / "unplugged")
    assert "not available" in await App.copy_to_backup(app, source, "Song.mp3", {"type": "Music"})
    app.settings.pop("backup_dir")
    assert await App.copy_to_backup(app, source, "Song.mp3", {"type": "Music"}) == ""
