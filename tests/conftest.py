"""Shared setup for the tests.

The app keeps its settings, history and cookies in a data folder: every test run gets an empty
temporary one, so the tests never touch your real files.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Before anything imports core: core.storage reads these once.
os.environ["FLET_APP_STORAGE_DATA"] = tempfile.mkdtemp(prefix="videodl_tests_data_")
os.environ["FLET_APP_STORAGE_TEMP"] = tempfile.mkdtemp(prefix="videodl_tests_temp_")

import pytest  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def probe(path):
    """Codec names of the file's streams, in order (e.g. ["h264", "aac"])."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return [line.strip().strip(",") for line in out.splitlines() if line.strip()]


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    """Small media files made locally with ffmpeg: no network needed."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg is not installed")
    folder = tmp_path_factory.mktemp("media")
    video = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=4"]
    tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=4"]
    ffmpeg(*video, *tone, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac", "-shortest",
           folder / "video.mp4")
    ffmpeg(*video, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", folder / "video_only.mp4")
    ffmpeg(*tone, "-c:a", "aac", folder / "audio_only.m4a")
    # Big enough to be compressed (about 2 MB): a detailed picture at a high quality.
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4", *tone, "-c:v", "libx264", "-crf", "8",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", folder / "big.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=red:size=64x64", "-frames:v", "1", folder / "cover.jpg")
    for ext, codec in (("mp3", "libmp3lame"), ("flac", "flac"), ("opus", "libopus"), ("wav", "pcm_s16le")):
        ffmpeg(*tone, "-c:a", codec, folder / ("tone." + ext))
    (folder / "subs.srt").write_text("1\n00:00:00,500 --> 00:00:02,000\nHello\n", encoding="utf-8")
    return folder


@pytest.fixture
def media(media_dir, tmp_path):
    """Copy one of the media_dir files into this test's own folder (tests may change it)."""
    def copy(name):
        target = tmp_path / name
        shutil.copyfile(media_dir / name, target)
        return target
    return copy


@pytest.fixture
def fake_app(tmp_path):
    """A stand-in for ui.app.App, enough for the tabs' download code (no window, no Flet page)."""
    from core.jobs import JobSlots, SitePause, Slots

    downloads, conversions = Slots(2), Slots(2)
    destination = tmp_path / "saved"
    destination.mkdir()

    async def nothing(*args, **kwargs):
        return None

    app = SimpleNamespace(
        page=SimpleNamespace(update=lambda: None, web=False),
        desktop=True, android=False, needs_bytes=False, settings={},
        remember=lambda key, value: None,
        filename_template=lambda fmt=None: "{title}", embed_tags=lambda: True, cookies_value=lambda: "",
        download_slots=downloads, conversion_slots=conversions,
        new_job_slots=lambda cancelled, rank=None: JobSlots(downloads, conversions, cancelled, rank),
        check_storage_access=nothing, destination=destination, destination_folder=lambda: destination,
        user_is_away=lambda: False, notify_if_away=nothing, save_dialog_lock=asyncio.Lock(), picker=None,
        history=SimpleNamespace(refresh=lambda: None), badges=[],
        card=lambda controls: SimpleNamespace(controls=controls),
        stretch_card=lambda controls, **kwargs: SimpleNamespace(controls=controls, visible=kwargs.get("visible", True)),
        view=lambda title, controls, visible=True: SimpleNamespace(controls=controls, visible=visible),
    )
    app.set_queue_badge = app.badges.append
    app.mobile = False
    app.ask_notification_permission = nothing
    app.download_done = nothing
    app.show_job_progress = lambda job, message, fraction: None

    async def copy_to_backup(path, name, fields):
        return ""
    app.copy_to_backup = copy_to_backup
    app.end_job_progress = lambda job: None
    app.can_open_files = lambda: True
    app.open_saved_file = lambda path: None
    app.sponsorblock_categories = lambda: list(app.settings.get("sponsorblock") or [])
    app.music_tags = app.music_lyrics = lambda: False  # no MusicBrainz or LRCLIB lookups
    app.link_server = None
    app.share_file = app.send_to_phone = nothing
    app.set_taskbar_progress = lambda fraction: None
    app.update_back = lambda: None
    app.open_tool = lambda *args: None  # the page of a tool on the Home tab
    app.offer_restart = lambda ask=True: nothing()
    app.restart = nothing
    app.device_name = lambda: "Test device"
    app.undo = []  # what offer_undo offered: (message, undo)
    app.offer_undo = lambda message, undo: app.undo.append((message, undo))

    def sorted_folder(base, fields):
        from core.filenames import render_folder
        folder = Path(base, *render_folder(app.settings.get("folder_template", ""), fields))
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    app.sorted_folder = sorted_folder

    async def wait_for_wifi(cancelled, on_wait):
        return True
    app.wait_for_wifi = wait_for_wifi

    async def wait_for_schedule(cancelled, on_wait, forced=lambda: False):
        return True
    app.wait_for_schedule = wait_for_schedule
    app.youtube_pause = SitePause()

    async def pause_youtube(message):
        app.youtube_pause.pause(message)
    app.pause_youtube = pause_youtube
    return app


@pytest.fixture(autouse=True)
def empty_data_folder():
    """Each test starts without settings, history, cookies or unfinished downloads."""
    from core.storage import data_dir
    yield
    for path in data_dir().iterdir():
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def pytest_sessionfinish(session, exitstatus):
    for name in ("FLET_APP_STORAGE_DATA", "FLET_APP_STORAGE_TEMP"):
        shutil.rmtree(os.environ[name], ignore_errors=True)


def files_in(folder):
    return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in Path(folder).rglob("*") if p.is_file())
