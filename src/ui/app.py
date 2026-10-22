"""The app window: what the tabs share (App), and main(), which builds the tabs and the navigation."""

import asyncio
import concurrent.futures
import importlib
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

import flet as ft

from core.cookies import BROWSERS, ENV_COOKIES_BROWSER
from core.chapters import save_folder
from core.editor import MEDIA_EXTENSIONS
from core.backend import AUDIO_FORMATS
from core.filenames import DEFAULT_FILENAME_TEMPLATE, NAME_RULES, render_folder, reserve_file
from core.history import prune_history
from core.jobs import DEFAULT_PARALLEL, DEFAULT_PARALLEL_CONVERSIONS, JobSlots, PARALLEL_CHOICES, SitePause, Slots
from core.link_server import Beacon, LinkServer, find_devices, new_key, offer_link, send_link
from core.links import all_links, first_link
from core.network import NETWORK, RETRY_CHOICES, parse_proxy, parse_speed_limit, speed_limit_bytes
from core.playlist import DEFAULT_PLAYLIST_LIMITS, PLAYLIST_LIMIT_CHOICES
from core.schedule import is_night, night_hours
from core.storage import load_settings, save_settings
from core.streams import CODEC, CODEC_CHOICES
from core.tags import COVER_SIZES, TAG_OPTIONS
from core.system import beep, desktop_notify, open_file
from core.workdirs import WORKDIR_PREFIX, purge_stale_workdirs, workdir_parent
from core.link_server import qr_png
from ui import widgets
from ui.widgets import setting_rows
from ui.download_tab import DownloadTab
from ui.edit_tab import EditTab
from ui.history_tab import HistoryTab
from ui.home_tab import HomeTab
from ui.palette import CommandPalette
from ui.queue_tab import QueueTab
from ui.settings_tab import ACCENTS, CORNERS, THEMES, SettingsTab
from ui.style import (
    APP_SCALES, L, M, MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, RAIL_WIDTH, S, SMALL_SIZE, TEXT_SCALES, TITLE_SIZE, XS,
    caption, column, density_of, row, scale_of,
)
from ui.subscriptions import Subscriptions
from ui.watch import watch_folder
from ui.welcome import Welcome
from ui.i18n import install_language, translate

TITLE_BAR = 36  # height of the computer's title bar
UNDO_SECONDS = 5  # how long "Undo" is offered after something is removed
# The tabs: name -> (icon, icon when chosen, label). Home and Settings are always shown, the others as chosen in
# Settings > Appearance.
TABS = {"home": (ft.Icons.HOME_OUTLINED, ft.Icons.HOME, "Home"),
        "queue": (ft.Icons.QUEUE_OUTLINED, ft.Icons.QUEUE, "Queue"),
        "history": (ft.Icons.VIDEO_LIBRARY_OUTLINED, ft.Icons.VIDEO_LIBRARY, "Library"),
        "subscriptions": (ft.Icons.SUBSCRIPTIONS_OUTLINED, ft.Icons.SUBSCRIPTIONS, "Subscriptions"),
        "settings": (ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS, "Settings")}
ALWAYS_SHOWN = ("home", "settings")
# Flutter's own text sizes (Material 3), made bigger or smaller by the text size of Settings.
TEXT_STYLES = {"body_large": 16, "body_medium": 14, "body_small": 12, "label_large": 14, "label_medium": 12,
               "label_small": 11, "title_large": 22, "title_medium": 16, "title_small": 14}

try:
    import flet_permission_handler as fph
except Exception:
    fph = None
try:  # Android build only: see extensions/flet-share-target
    import flet_share_target
except Exception:
    flet_share_target = None
try:  # Android build only: see extensions/flet-local-notification
    import flet_local_notification
except Exception:
    flet_local_notification = None
try:  # Android build only: see extensions/flet-open-file
    import flet_open_file
except Exception:
    flet_open_file = None
try:  # Windows build only: see extensions/flet-file-drop
    import flet_file_drop
except Exception:
    flet_file_drop = None



class App:
    """What the tabs share: the page and platform, the settings, the file picker, the download slots…

    The tabs reach each other through it too (app.download, app.queue, app.history, app.settings_tab).
    """


    def __init__(self, page):
        self.page = page
        self.needs_bytes = bool(page.web) or page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
        self.desktop = not self.needs_bytes
        self.android = (not page.web) and page.platform == ft.PagePlatform.ANDROID
        self.mobile = (not page.web) and page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
        self.page_pad = S if self.needs_bytes else L
        self.card_pad = M
        self.heading_size = TITLE_SIZE

        # Flet services register themselves with the page when created: adding them to page.services too
        # puts them in the tree twice, and the client can then lose the picker ("Timeout waiting for invoke
        # method listener for FilePicker…": the save dialog never opens). Flet drops services nothing
        # references any more, so these attributes must stay alive.
        self.picker = ft.FilePicker()
        self.clipboard = ft.Clipboard()  # one for the session, instead of a new service on every paste
        widgets.clipboard["copy"] = lambda text: page.run_task(self.clipboard.set, text)
        self.permissions = None
        if self.android and fph is not None:
            self.permissions = fph.PermissionHandler()
        # Android: links shared to the app with another app's Share button.
        self.share_target = None
        if self.android and flet_share_target is not None:
            self.share_target = flet_share_target.ShareTarget(on_receive=self.on_shared_text)
        # Mobile: Wi-Fi or mobile data, for "Wi-Fi only" (Settings); charging, for "only while charging".
        self.connectivity = ft.Connectivity() if self.mobile else None
        self.battery = ft.Battery() if self.android else None
        # Android: a notification when a download finishes while the app is in the background.
        self.notifier = None
        if self.android and flet_local_notification is not None:
            self.notifier = flet_local_notification.LocalNotification(on_cancel=self.on_notification_cancel)
        self.notification_id = 0
        self.job_notices = {}  # queue download -> its progress notification (see show_job_progress)
        # Android: the downloaded files open with the phone's apps; a short vibration when one is done.
        self.file_opener = flet_open_file.OpenFile() if (self.android and flet_open_file is not None) else None
        # Phones: a downloaded file sent to another app (WhatsApp, Drive…) with the system's Share menu.
        self.share = ft.Share() if self.mobile else None
        self.url_launcher = ft.UrlLauncher()
        self.haptics = ft.HapticFeedback() if self.mobile else None
        self.notification_permission_asked = False
        self.can_pick_folder = self.desktop or self.permissions is not None
        self.storage_access = None  # Android: whether "All files access" is granted, once checked

        self.settings = load_settings()
        prune_history(self.settings.get("history_days") or 0)  # Settings > Storage
        if self.settings.get("retries") in RETRY_CHOICES:
            NETWORK["retries"] = self.settings["retries"]
        try:
            NETWORK["ratelimit"] = speed_limit_bytes(parse_speed_limit(str(self.settings.get("speed_limit") or "")))
        except ValueError:
            NETWORK["ratelimit"] = None
        try:
            NETWORK["proxy"] = parse_proxy(str(self.settings.get("proxy") or ""))
        except ValueError:
            NETWORK["proxy"] = ""
        self.apply_night_speed()
        rules = self.settings.get("name_rules")  # Settings > General > Files
        if isinstance(rules, dict):
            NAME_RULES.update({key: value for key, value in rules.items()
                               if key in NAME_RULES and isinstance(value, type(NAME_RULES[key]))})
        for key, value in TAG_OPTIONS.items():  # Settings > Downloads > Metadata
            saved = self.settings.get("tag_" + key)
            if isinstance(saved, type(value)) and (key != "cover_size" or saved in COVER_SIZES):
                TAG_OPTIONS[key] = saved
        if self.settings.get("codec") == "efficient":  # "AV1 or VP9", before they were separate choices
            self.settings["codec"] = "av1"
        if self.settings.get("codec") in CODEC_CHOICES:
            CODEC["preference"] = self.settings["codec"]

        # Shared by single videos and playlist videos: how many download at once, and how many convert at once.
        self.download_slots = Slots(self.saved_limit("parallel", DEFAULT_PARALLEL))
        self.conversion_slots = Slots(self.saved_limit("parallel_conversions", DEFAULT_PARALLEL_CONVERSIONS))
        # Downloads run in asyncio's default thread pool (cpu count + 4 threads): make room for the most
        # parallel downloads and conversions, plus analysis, previews and file copies at the same time.
        asyncio.get_running_loop().set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=2 * max(PARALLEL_CHOICES) + 16))
        # One save dialog at a time: parallel downloads can finish together.
        self.save_dialog_lock = asyncio.Lock()
        # YouTube downloads wait here while YouTube takes the app for a bot (until Resume is pressed).
        self.youtube_pause = SitePause()

        # Window state (desktop). Downloads run in a worker thread, so they keep going while the
        # window is minimized; this only tracks whether the user is looking at the app.
        self.away = False
        if self.desktop:
            page.window.on_event = self.on_window_event
        elif self.android:
            page.on_app_lifecycle_state_change = self.on_lifecycle_change

        # Set by main() once everything is built.
        self.download = self.edit = self.queue = self.history = self.settings_tab = None
        self.home = None  # the Home tab, where the tools are used (each on its page)
        self.tabs = []  # every tab: (name, its view)
        self.views = []
        self.view_names = []  # "home", "queue"…: the tabs shown, in the navigation order
        self.scroller = None  # computer: the column the tabs scroll in
        self.nav = None
        self.tasks = []  # background tasks, kept so they are not garbage-collected mid-run
        self.link_server = None  # receives the links sent from a phone or another device (Settings > General)
        self.beacon = None  # answers the devices looking for this one on the Wi-Fi
        self.subscriptions = None
        self.palette = None  # computer: Ctrl+K

    # Settings

    def remember(self, key, value):
        self.settings[key] = value
        save_settings(self.settings)

    def saved_limit(self, key, default):
        return self.settings[key] if self.settings.get(key) in PARALLEL_CHOICES else default

    def filename_template(self, fmt=None):
        """The file name template of Settings; songs (a sound format) have their own when one is set."""
        music = self.settings.get("music_template")
        if fmt in AUDIO_FORMATS and isinstance(music, str) and music.strip():
            return music
        template = self.settings.get("filename_template")
        return template if isinstance(template, str) and template.strip() else DEFAULT_FILENAME_TEMPLATE

    def embed_tags(self):
        return bool(self.settings.get("embed_tags", True))

    def music_tags(self):
        """Songs saved as sound files get their real tags and cover art from MusicBrainz (Settings)."""
        return self.embed_tags() and bool(self.settings.get("music_tags", True))

    def music_lyrics(self):
        return self.embed_tags() and bool(self.settings.get("lyrics", True))

    def playlist_limit(self, site):
        limits = self.settings.get("playlist_limits")
        limit = limits.get(site) if isinstance(limits, dict) else None
        return limit if limit in PLAYLIST_LIMIT_CHOICES else DEFAULT_PLAYLIST_LIMITS[site]

    def cookies_browsers(self):
        """Browsers the cookies can be read from (desktop only)."""
        keys = list(BROWSERS)
        if ENV_COOKIES_BROWSER and ENV_COOKIES_BROWSER not in keys:
            keys.append(ENV_COOKIES_BROWSER)
        return keys

    def cookies_browser(self):
        """The choice of the "Browser cookies" menu in Settings: a browser or "none"."""
        remembered = self.settings.get("cookies_browser")
        if remembered in self.cookies_browsers() + ["none"]:
            return remembered
        return ENV_COOKIES_BROWSER or "none"

    def cookies_value(self):
        """The browser to read cookies from, for yt-dlp ("" for none)."""
        if not self.desktop or self.cookies_browser() in (None, "", "none"):
            return ""
        return self.cookies_browser()

    # Downloads

    def new_job_slots(self, cancelled, rank=None):
        return JobSlots(self.download_slots, self.conversion_slots, cancelled, rank)

    def on_window_event(self, e):
        if e.type in (ft.WindowEventType.MINIMIZE, ft.WindowEventType.HIDE, ft.WindowEventType.BLUR):
            self.away = True
        elif e.type in (ft.WindowEventType.RESTORE, ft.WindowEventType.SHOW, ft.WindowEventType.FOCUS):
            self.away = False

    def on_lifecycle_change(self, e):
        """Android: whether the app is on screen or in the background."""
        if e.state in (ft.AppLifecycleState.PAUSE, ft.AppLifecycleState.HIDE):
            self.away = True
        elif e.state in (ft.AppLifecycleState.RESUME, ft.AppLifecycleState.SHOW):
            self.away = False

    def user_is_away(self):
        if self.android:
            return self.away
        if not self.desktop:
            return False
        return self.away or bool(self.page.window.minimized)

    async def notify_if_away(self, title, message):
        if not self.settings.get("notify", True) or not self.user_is_away():
            return
        title, message = translate(title), translate(message)  # not given to a control: translated here
        if self.desktop:
            await asyncio.to_thread(desktop_notify, title, message)
        elif self.notifier is not None:
            self.notification_id += 1
            try:
                await self.notifier.show(title, message, self.notification_id, alert=self.settings.get("alert", True))
            except Exception:
                pass

    def show_job_progress(self, job, message, fraction):
        """Android: a queue download's progress in a notification with a Cancel button, updated at most once a
        second (and at once when the message changes)."""
        if self.notifier is None or not self.settings.get("notify", True):
            return
        notice = self.job_notices.get(job)
        if notice is None:
            self.notification_id += 1
            notice = self.job_notices[job] = {"id": self.notification_id, "message": None, "time": 0.0}
        now = time.monotonic()
        if message == notice["message"] and now - notice["time"] < 1:
            return
        notice["message"], notice["time"] = message, now
        percent = -1 if fraction is None else int(fraction * 100)
        body = translate(message if fraction is None else "%s %d %%" % (message, percent))
        self.page.run_task(self.call_notifier, "progress", notice["id"], job.title.value, body, percent)

    def end_job_progress(self, job):
        notice = self.job_notices.pop(job, None)
        if notice is not None:
            self.page.run_task(self.call_notifier, "cancel", notice["id"])

    async def call_notifier(self, method, *args):
        try:
            await getattr(self.notifier, method)(*args)
        except Exception:
            pass

    def on_notification_cancel(self, e):
        """The Cancel button of a download's notification."""
        for job, notice in list(self.job_notices.items()):
            if notice["id"] == e.id and job.running:
                job.cancel()

    async def download_done(self):
        """A download (or a playlist) is finished: the sound or vibration set in Settings. On Android, when the
        app is in the background, the notification makes them instead."""
        if not self.settings.get("alert", True):
            return
        if self.desktop:
            await asyncio.to_thread(beep)
        elif self.haptics is not None and not self.user_is_away():
            try:
                await self.haptics.vibrate()
            except Exception:
                pass

    def can_open_files(self):
        return self.desktop or self.file_opener is not None

    async def share_file(self, path):
        """Phone: send a downloaded file to another app with the system's Share menu."""
        if self.share is None:
            return
        try:
            await self.share.share_files([ft.ShareFile.from_path(str(path))])
        except Exception as exc:
            self.page.show_dialog(ft.SnackBar(ft.Text("Could not share the file: %s" % exc)))

    async def send_to_phone(self, path):
        """Computer: a QR code that downloads `path` (a file, or a folder as a .zip) on a phone on the same Wi-Fi
        (the link server of Settings, started for this if needed)."""
        page = self.page
        started = self.link_server is None
        try:
            self.start_link_server()
        except OSError as exc:
            page.show_dialog(ft.SnackBar(ft.Text("Could not start sending: %s" % exc)))
            return
        if Path(path).is_dir():  # a folder goes as a .zip (deleted at the next start, with the work folders)
            page.show_dialog(ft.SnackBar(ft.Text("Making the .zip of %s…" % Path(path).name)))
            page.update()
            folder = tempfile.mkdtemp(prefix=WORKDIR_PREFIX, dir=workdir_parent())
            path = await asyncio.to_thread(shutil.make_archive, str(Path(folder, Path(path).name)), "zip", str(path))
        address = self.link_server.share_file(path)

        def close(e=None):
            page.pop_dialog()
            if started and not self.settings.get("phone_links"):
                self.stop_link_server()
        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Send to a phone"),
            content=column([caption("Scan it with the phone (same Wi-Fi)."),
                            ft.Container(ft.Image(src=qr_png(address), width=220, height=220),
                                         alignment=ft.Alignment(0, 0)),
                            ft.Text(Path(path).name, weight=ft.FontWeight.W_500),
                            caption(address, selectable=True)], tight=True, spacing=S),
            actions=[ft.TextButton("Done", on_click=close)]))

    def offer_undo(self, message, undo):
        """A message with an Undo button for a few seconds, after something was removed."""
        def on_undo(e):
            undo()
            self.page.update()
        # Flutter keeps a message with a button until it is closed, unless told otherwise (persist).
        self.page.show_dialog(ft.SnackBar(ft.Text(message), duration=UNDO_SECONDS * 1000, persist=False,
                                          action=ft.SnackBarAction(label="Undo", on_click=on_undo)))

    def set_taskbar_progress(self, fraction):
        """Computer: the progress of the downloads on the app's icon in the taskbar (None: none)."""
        if self.desktop and self.page.window.progress_bar != fraction:
            self.page.window.progress_bar = fraction

    async def open_link(self, url):
        try:
            await self.url_launcher.launch_url(url)
        except Exception:
            pass

    def open_saved_file(self, path):
        """Open a downloaded file with the app that plays it (computer, or Android through flet-open-file)."""
        if self.desktop:
            open_file(path)
        elif self.file_opener is not None:
            self.page.run_task(self.file_opener.open, str(path))

    def sorted_folder(self, base, fields):
        """The folder a file goes to in `base`, sorted into sub-folders by the template of Settings."""
        folder = Path(base, *render_folder(self.settings.get("folder_template", ""), fields))
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def backup_folder(self):
        """The backup folder of Settings (a folder synced to a cloud, a NAS share…), when it can be written."""
        if not self.can_pick_folder or (self.android and not self.storage_access):
            return None
        folder = self.settings.get("backup_dir")
        return Path(folder) if folder and os.path.isdir(folder) and os.access(folder, os.W_OK) else None

    async def copy_to_backup(self, path, name, fields):
        """Copy the finished file (or folder of files) `path` as `name` into the backup folder, sorted like the
        destination folder. Returns a line for the result ("" without a backup folder)."""
        if not self.settings.get("backup_dir"):
            return ""
        if self.android:
            await self.check_storage_access()
        base = self.backup_folder()
        if base is None:
            return "Not copied to the backup folder: it is not available."

        def copy():
            folder = self.sorted_folder(base, fields)
            if Path(path).is_dir():
                return save_folder(path, folder, name)
            target = reserve_file(folder, name)
            shutil.copyfile(str(path), str(target))
            return target
        try:
            return "Backup copy: %s" % await asyncio.to_thread(copy)
        except OSError as exc:
            return "The backup copy failed: %s" % exc

    def sponsorblock_categories(self):
        categories = self.settings.get("sponsorblock")
        return list(categories) if isinstance(categories, list) else []

    def busy(self):
        """Whether something is being downloaded or converted (the clean-up must wait)."""
        edit = self.edit is not None and (self.edit.save_btn.disabled or any(
            card.running for card in (self.edit.files, self.edit.join, self.edit.music)))
        return any(job.running for job in self.queue.jobs) or edit

    # Restarting: for the settings read once at the start (language, sizes, ffmpeg, yt-dlp, imported settings)

    async def offer_restart(self, ask=True):
        """Such a setting was changed: restart now, or later with the button of Settings' status."""
        self.settings_tab.restart_btn.visible = True
        if not ask:
            return
        if self.busy():
            self.page.show_dialog(ft.SnackBar(ft.Text("Restart once the downloads and edits are finished.")))
        elif await widgets.ask(self.page, "Restart the app?", ft.Text("The change is applied after a restart."),
                               "Restart", cancel="Later"):
            await self.restart()
            return
        self.page.update()

    async def restart(self, e=None):
        """The app built again from its files, as if it had been closed and opened (the window stays)."""
        if self.busy():
            self.page.show_dialog(ft.SnackBar(ft.Text("Restart once the downloads and edits are finished.")))
            self.page.update()
            return
        page = self.page
        for task in self.tasks:
            task.cancel()
        self.stop_link_server()
        page.controls.clear()
        page.navigation_bar = page.floating_action_button = page.on_resize = page.on_keyboard_event = None
        page.update()
        # Every module of the app (and yt-dlp, maybe updated) read again; the language's stays (it is installed).
        sys.path[:] = [p for p in sys.path if not Path(p).name.startswith("yt-dlp-")]
        for name in [n for n in sys.modules if n.split(".")[0] in ("core", "ui", "yt_dlp")]:
            if name not in ("core", "ui", "ui.i18n", "ui.i18n_fr"):
                del sys.modules[name]
        await importlib.import_module("ui.app").main(page)

    async def ask_notification_permission(self):
        """Android 13+: notifications need a permission, asked once (at the first download)."""
        if self.notifier is None or self.permissions is None or self.notification_permission_asked:
            return
        self.notification_permission_asked = True
        if not self.settings.get("notify", True):
            return
        try:
            if await self.permissions.get_status(fph.Permission.NOTIFICATION) != fph.PermissionStatus.GRANTED:
                await self.permissions.request(fph.Permission.NOTIFICATION)
        except Exception:
            pass

    async def wait_for_wifi(self, cancelled, on_wait):
        """Wi-Fi only (Settings, mobile): wait while there is no Wi-Fi. False if cancelled meanwhile."""
        warned = False
        while self.connectivity is not None and self.settings.get("wifi_only"):
            try:
                kinds = await self.connectivity.get_connectivity()
            except Exception:
                return True  # unknown: do not block the downloads
            if ft.ConnectivityType.WIFI in kinds or ft.ConnectivityType.ETHERNET in kinds:
                return True
            if not warned:
                on_wait()
                warned = True
            for _ in range(12):  # look again in 3 seconds
                if cancelled.is_set():
                    return False
                await asyncio.sleep(0.25)
        return True

    def apply_night_speed(self):
        """The night hours and the night speed limit of Settings, for the downloads starting from now."""
        NETWORK["night"] = night_hours(self.settings)
        NETWORK["night_speed"] = bool(self.settings.get("night_speed"))
        try:
            NETWORK["night_ratelimit"] = speed_limit_bytes(
                parse_speed_limit(str(self.settings.get("night_speed_limit") or "")))
        except ValueError:
            NETWORK["night_ratelimit"] = None

    async def charging_on_wifi(self):
        try:
            state = await self.battery.get_battery_state()
            kinds = await self.connectivity.get_connectivity()
        except Exception:
            return True  # unknown: do not block the downloads
        return state in (ft.BatteryState.CHARGING, ft.BatteryState.FULL) and ft.ConnectivityType.WIFI in kinds

    async def schedule_wait_reason(self):
        """Why a download added now must wait (Settings > Downloads > Schedule), or "" to start it."""
        if self.settings.get("night_downloads") and not is_night(night_hours(self.settings)):
            return ("Waiting for the night (%d:00) to start, as set in Settings."
                    % night_hours(self.settings)[0])
        if self.battery is not None and self.settings.get("charging_only") and not await self.charging_on_wifi():
            return "Waiting for the phone to be charging, on Wi-Fi, as set in Settings."
        return ""

    async def wait_for_schedule(self, cancelled, on_wait, forced=lambda: False):
        """Wait for the night hours, or for the phone to charge on Wi-Fi, when chosen in Settings; `forced()`
        True ends the wait ("Start now"). False if cancelled meanwhile."""
        shown = ""
        while not forced():
            reason = await self.schedule_wait_reason()
            if not reason:
                return True
            if reason != shown:
                on_wait(reason)
                shown = reason
            for _ in range(20):  # look again in 5 seconds
                if cancelled.is_set():
                    return False
                if forced():
                    return True
                await asyncio.sleep(0.25)
        return True

    async def pause_youtube(self, message):
        """YouTube blocked a download: hold every YouTube download until the user presses Resume."""
        if self.youtube_pause.pause(message):
            self.page.update()
            await self.notify_if_away("YouTube downloads paused", "YouTube is blocking downloads: open the app "
                                      "and press Resume later.")

    async def on_shared_text(self, e):
        await self.open_shared_text(e.text)

    async def open_shared_text(self, text):
        """A link shared to the app: analyzed here, or sent to another device of the Wi-Fi (the user picks, when
        one is found)."""
        if flet_share_target is not None and text == flet_share_target.PASTE_REQUEST:
            await self.paste_and_download()
            return
        link = first_link(text or "")
        if not link:
            return
        devices = await self.devices_around()
        if devices:
            device = await self.choose_device(link, devices, here="On this phone")
            if isinstance(device, dict) and await self.send_link_to(device, link):
                return
        self.show_view(0)
        await self.download.analyze_link(link)

    async def paste_and_download(self):
        """The "Paste & download" tile or widget (Android): the link copied is analyzed and downloaded with the
        options shown (several links: all of them to the queue)."""
        self.show_view(0)
        self.page.update()
        links = []
        for _ in range(10):  # the clipboard can only be read once the app is on screen
            try:
                links = all_links(await self.clipboard.get())
            except Exception:
                links = []
            if links:
                break
            await asyncio.sleep(0.3)
        if not links:
            await self.download.paste_link()  # says that no link was found
            return
        await self.download.analyze_link(" ".join(links))
        if self.download.items.card.visible:
            await self.download.items.download()
        elif self.download.result_card.visible:
            await self.download.download()

    # The other devices of the Wi-Fi running the app (Settings > General > Other devices)

    def device_id(self):
        if not self.settings.get("device_id"):
            self.remember("device_id", new_key())
        return self.settings["device_id"]

    def device_name(self):
        """What the other devices call this one (Settings), else the computer's name."""
        name = (self.settings.get("device_name") or "").strip()
        if name:
            return name
        host = socket.gethostname()
        return host if host and host != "localhost" else ("Android phone" if self.android else "Phone")

    async def devices_around(self):
        """The devices found on the Wi-Fi, and the computer whose address was typed in Settings."""
        devices = await asyncio.to_thread(find_devices, self.device_id())
        address = (self.settings.get("computer_address") or "").strip()
        if address and not any(address.startswith(device["address"]) for device in devices):
            devices.append({"name": address.split("/")[2], "address": address, "keyed": True})
        return devices

    async def choose_device(self, link, devices=None, here=None):
        """The device to send `link` to, tapped in a list (looked for while it shows when `devices` is None):
        that device, "here" (the `here` button), or None."""
        page = self.page
        answer = asyncio.get_running_loop().create_future()

        def close(value):
            if not answer.done():
                answer.set_result(value)
                page.pop_dialog()
        listing = column([row([ft.ProgressRing(width=18, height=18, stroke_width=2),
                               ft.Text("Looking for devices…")])], spacing=0)
        actions = [ft.FilledButton(here, on_click=lambda e: close("here"))] if here else \
            [ft.TextButton("Cancel", on_click=lambda e: close(None))]
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Send to a device"), actions=actions, on_dismiss=lambda e: close(None),
            content=column([caption(link, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS), listing], tight=True,
                           width=420)))
        page.update()
        if devices is None:
            devices = await self.devices_around()

        def tile(device):
            return ft.ListTile(leading=ft.Icon(ft.Icons.DEVICES), title=ft.Text(device["name"]),
                               subtitle=caption(device["address"].split("/")[2]),
                               on_click=lambda e: close(device))
        listing.controls = [tile(device) for device in devices] or [caption(
            "No device found. On the other one, open the app with “Receive links” on (same Wi-Fi).")]
        page.update()
        return await answer

    async def send_to_device(self, link):
        """Send `link` to a device of the Wi-Fi chosen in a list."""
        device = await self.choose_device(link)
        if isinstance(device, dict):
            await self.send_link_to(device, link)

    async def send_link_to(self, device, link):
        """True once `device` took the link (it asks its user, unless it trusts this device)."""
        try:
            if device.get("keyed"):  # the address of Settings: its key is in it
                await asyncio.to_thread(send_link, device["address"], link)
            else:
                await asyncio.to_thread(offer_link, device, link, self.device_name(), self.device_id())
        except Exception as exc:
            self.page.show_dialog(ft.SnackBar(ft.Text("%s did not answer (%s)." % (device["name"], exc))))
            self.page.update()
            return False
        self.page.show_dialog(ft.SnackBar(ft.Text("Sent to %s." % device["name"])))
        self.page.update()
        return True

    def start_link_server(self):
        """Start receiving links (from a phone's browser, or the app on other devices, which find this one on
        the Wi-Fi); returns the address to open on the phone."""
        if self.link_server is None:
            key = self.settings.get("phone_key") or new_key()
            self.remember("phone_key", key)
            loop = asyncio.get_running_loop()
            self.link_server = LinkServer(
                key, lambda link: loop.call_soon_threadsafe(self.page.run_task, self.receive_link, link),
                on_offer=lambda *offer: loop.call_soon_threadsafe(self.page.run_task, self.receive_offer, *offer))
            self.link_server.start()
            self.beacon = Beacon(self.device_name(), self.device_id(), self.link_server.server.server_port)
            try:
                self.beacon.start()
            except OSError:  # another app holds the port: this device is not found, the rest works
                self.beacon = None
        return self.link_server.address

    def stop_link_server(self):
        if self.link_server is not None:
            self.link_server.stop()
            self.link_server = None
        if self.beacon is not None:
            self.beacon.stop()
            self.beacon = None

    async def receive_offer(self, link, name, device_id):
        """A link sent by the app on another device: taken at once from a trusted device, else asked."""
        trusted = self.settings.get("trusted_devices")
        trusted = trusted if isinstance(trusted, dict) else {}
        if device_id not in trusted:
            always = ft.Checkbox(label="Always accept links from this device", value=False)
            if not await widgets.ask(self.page, "Link from %s" % name, column(
                    [ft.Text(link, selectable=True), always], tight=True), "Download", cancel="Ignore"):
                return
            if always.value and device_id:
                self.remember("trusted_devices", dict(trusted, **{device_id: name}))
        await self.receive_link(link, name)

    async def receive_link(self, link, sender=None):
        """A link sent from a phone or another device: analyzed on the Download page, like a pasted one."""
        self.page.show_dialog(ft.SnackBar(ft.Text("Link received from %s." % sender if sender
                                                  else "Link received from a phone.")))
        await self.notify_if_away("Link received", link)
        await self.download.analyze_link(link)

    # Keyboard shortcuts and dropped files (computer)

    async def on_key(self, e):
        """Ctrl+K opens the command palette (computer). Ctrl+V pastes a link and analyzes it, Enter downloads,
        Escape closes the preview (when turned on in Settings)."""
        if getattr(self, "palette", None) is not None and (e.ctrl or e.meta) and e.key.upper() == "K":
            self.palette.open()
            return
        if not self.settings.get("keyboard_shortcuts") or self.nav is None:
            return
        name = self.view_names[self.nav.selected_index or 0]
        part = self.home.current if name == "home" else None  # the tool whose page is shown
        if e.key == "Escape":
            if part == "download":
                self.download.close_player()
            elif part == "file" and self.edit is not None:
                self.edit.close_preview()
            self.page.update()
        elif name != "home":
            return
        elif (e.ctrl or e.meta) and e.key.upper() == "V" and self.download.typing in (None, self.download.url_input):
            await self.download.paste_link()
        elif e.key == "Enter" and not (e.ctrl or e.alt or e.meta or e.shift) and part == "download":
            await self.download.shortcut_download()

    async def open_dropped(self, paths):
        """Files dropped onto the window: video or sound files are added to Edit file(s) (a single one opens
        there); a link file (.url, .webloc, .txt) is downloaded like a pasted link."""
        media = [Path(p) for p in paths if Path(p).suffix.lower().lstrip(".") in MEDIA_EXTENSIONS]
        if media and self.edit is not None:
            self.open_tool("file")
            self.edit.files.add(media)
            self.page.update()
            return
        links = []
        for path in paths:
            try:
                links += all_links(await asyncio.to_thread(Path(path).read_text, encoding="utf-8", errors="replace"))
            except OSError:
                continue
        if links:
            self.show_view(0)
            await self.download.analyze_link(" ".join(dict.fromkeys(links)))

    async def on_drop(self, e):
        await self.open_dropped(e.files)

    async def check_storage_access(self):
        if self.permissions is None:
            return False
        granted = False
        for permission in (fph.Permission.MANAGE_EXTERNAL_STORAGE, fph.Permission.STORAGE):
            try:
                if await self.permissions.get_status(permission) == fph.PermissionStatus.GRANTED:
                    granted = True
                    break
            except Exception:
                continue
        self.storage_access = granted
        return granted

    async def request_storage_access(self):
        if await self.check_storage_access():
            return True
        for permission in (fph.Permission.MANAGE_EXTERNAL_STORAGE, fph.Permission.STORAGE):
            try:
                await self.permissions.request(permission)
            except Exception:
                continue
            if await self.check_storage_access():
                return True
        return False

    def destination_folder(self):
        """The folder set in Settings, when files can be saved there without a dialog."""
        if not self.can_pick_folder:
            return None
        if self.android and not self.storage_access:
            return None
        saved_dir = self.settings.get("save_dir")
        if not saved_dir:
            return None
        folder = Path(saved_dir)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return folder if os.access(folder, os.W_OK) else None

    # Layout

    def card(self, controls):
        return ft.Card(elevation=0, margin=0, content=ft.Container(
            padding=self.card_pad,
            content=column(controls),
        ))

    def corner_radius(self, default=None):
        """The roundness of the corners chosen in Settings > Appearance (None: Flutter's own)."""
        return CORNERS.get(self.settings.get("corners"), CORNERS["normal"])[1] or default

    def section(self, title, description, *controls):
        """A sub-category of Settings: its card of settings, each on one line (see widgets.setting_rows).
        `description`: a few words (shown in the list of sub-categories), or (those words, an explanation shown by
        the ⓘ button of its page)."""
        details = None
        if isinstance(description, tuple):
            description, details = description
        content = ft.Container(padding=self.card_pad, content=column(setting_rows(controls), spacing=XS,
                                                                       horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        radius = self.corner_radius(20)
        return ft.Card(elevation=0, margin=0, content=content,
                       data={"title": title, "description": description, "details": details,
                             "text": " ".join(words for text in (title, description, details or "")
                                              for words in {text, translate(text)})},  # found in either language
                       shape=ft.RoundedRectangleBorder(radius=radius) if radius else None)

    def stretch_card(self, controls, **kwargs):
        return ft.Card(elevation=0, margin=0, content=ft.Container(padding=self.card_pad, content=column(
            controls, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)), **kwargs)

    def view(self, title, controls, visible=True):
        """One tab's content: its cards (`title`: what the tab is, for the reader of the code)."""
        return column(
            controls,
            spacing=M,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=visible,
        )

    # Navigation

    def show_view(self, index):
        shown = self.views[index] if index < len(self.views) else None
        for _, view in self.tabs:
            view.visible = view is shown
        if self.nav is not None:
            self.nav.selected_index = index
        name = self.view_names[index] if index < len(self.view_names) else ""
        self.download.show_fab(name == "home" and self.home.current == "download")
        if name == "history":
            self.history.refresh()
        # Home's field and tiles sit in the middle of the window; everything else starts at the top.
        middle = ft.MainAxisAlignment.CENTER if name == "home" and self.home.current is None \
            else ft.MainAxisAlignment.START
        if self.scroller is not None:
            self.scroller.alignment = middle
        elif self.needs_bytes:
            self.page.vertical_alignment = middle
        self.update_back()

    def open_tool(self, key):
        """The Home tab, at the page of the tool `key` ("download", "file", "join"…; None: its field and tiles)."""
        self.home.show(key)
        self.show_view(self.view_names.index("home"))

    def apply_tabs(self):
        """The tabs shown in the navigation (Settings > Appearance): Home and Settings always."""
        hidden = self.settings.get("hidden_tabs")
        hidden = set(hidden) if isinstance(hidden, list) else set()
        current = self.view_names[self.nav.selected_index or 0] if self.view_names else None
        shown = [(name, view) for name, view in self.tabs if name in ALWAYS_SHOWN or name not in hidden]
        self.view_names = [name for name, _ in shown]
        self.views = [view for _, view in shown]
        if isinstance(self.nav, ft.NavigationBar):
            self.nav.destinations = [ft.NavigationBarDestination(icon=TABS[name][1], label=TABS[name][2])
                                     for name in self.view_names]
        else:
            self.nav.destinations = [ft.NavigationRailDestination(icon=TABS[name][0], selected_icon=TABS[name][1],
                                                                  label=TABS[name][2]) for name in self.view_names]
        self.queue.refresh()  # the Queue tab's badge
        self.show_view(self.view_names.index(current) if current in self.view_names else 0)

    # The phone's back gesture or button

    def back_action(self):
        """What the phone's back gesture does now: back to the list of Settings, to the list being edited, or to
        the first tab; None on the first tab (the app is left)."""
        settings = self.settings_tab
        if settings is not None and settings.view.visible and settings.on_page and not settings.wide:
            return settings.go_home
        home = self.home
        if home.view.visible and home.current == "download" and self.download.editing is not None:
            return self.download.back_to_list
        if home.view.visible and home.current == "file" and self.edit.single_row.visible \
                and len(self.edit.files.files) > 1:
            return self.edit.files.back_to_list
        if home.view.visible and home.current is not None:
            return home.back
        if self.nav is not None and (self.nav.selected_index or 0) != 0:
            def first_tab():
                self.show_view(0)
                self.page.update()
            return first_tab
        return None

    def update_back(self):
        """Phones: the back gesture stays in the app while it has somewhere to go back to (see on_back)."""
        if self.mobile and self.page.views:
            self.page.views[0].can_pop = self.back_action() is None

    async def on_back(self, e):
        action = self.back_action()
        if action is not None:
            action()
        self.update_back()
        self.page.update()
        try:
            await e.control.confirm_pop(False)
        except Exception:
            pass

    # Appearance (Settings > Appearance)

    def apply_theme(self):
        """The theme, accent colour, size of the app, pure black and corners chosen in Settings."""
        light, dark = ACCENTS.get(self.settings.get("accent"), ACCENTS["indigo"])[1:]
        scale, app_scale = scale_of(self.settings, "text_scale", TEXT_SCALES), scale_of(self.settings, "app_scale",
                                                                                        APP_SCALES)
        density = {"compact": ft.VisualDensity.COMPACT, "standard": ft.VisualDensity.STANDARD,
                   "comfortable": ft.VisualDensity.COMFORTABLE}[density_of(app_scale)]
        # The sizes: Flutter's own now (texts, controls, icons), the app's own (titles, spacing) after a restart.
        text = ft.TextTheme(**{name: ft.TextStyle(size=round(size * scale)) for name, size in TEXT_STYLES.items()}) \
            if scale != 1 else None
        radius = self.corner_radius()
        shape = ft.RoundedRectangleBorder(radius=radius) if radius is not None else None
        button = ft.ButtonStyle(shape=shape) if shape else None
        common = dict(visual_density=density, text_theme=text, card_theme=ft.CardTheme(shape=shape) if shape else None,
                      icon_theme=ft.IconTheme(size=round(24 * app_scale)) if app_scale != 1 else None,
                      button_theme=ft.ButtonTheme(style=button) if button else None,
                      filled_button_theme=ft.FilledButtonTheme(style=button) if button else None,
                      text_button_theme=ft.TextButtonTheme(style=button) if button else None,
                      chip_theme=ft.ChipTheme(shape=shape) if shape else None,
                      dialog_theme=ft.DialogTheme(shape=shape) if shape else None)
        self.page.theme = ft.Theme(color_scheme_seed=light, **common)
        self.page.dark_theme = ft.Theme(color_scheme_seed=dark, **common,
                                        scaffold_bgcolor=ft.Colors.BLACK if self.settings.get("pure_black") else None)
        self.page.theme_mode = THEMES.get(self.settings.get("theme"), THEMES["system"])[1]

    def apply_nav_labels(self):
        """The navigation's labels: under every icon, or only under the tab shown."""
        selected = self.settings.get("nav_labels") == "selected"
        if isinstance(self.nav, ft.NavigationBar):
            self.nav.label_behavior = (ft.NavigationBarLabelBehavior.ONLY_SHOW_SELECTED if selected
                                       else ft.NavigationBarLabelBehavior.ALWAYS_SHOW)
        elif self.nav is not None:
            self.nav.label_type = ft.NavigationRailLabelType.SELECTED if selected else ft.NavigationRailLabelType.ALL

    async def on_nav(self, e):
        await self.leave_view()
        self.show_view(e.control.selected_index)
        self.page.update()

    async def leave_view(self):
        """The tab on screen is about to be hidden: its preview remembers where it was (see Preview.remember)."""
        if self.home.view.visible:
            await self.home.remember_preview()

    def set_queue_badge(self, count):
        """Number of unfinished downloads, on the Queue tab's icon (a destination's own badge is not drawn)."""
        if self.nav is None or "queue" not in self.view_names:
            return
        badge = ft.Badge(label=str(count)) if count else None
        destination = self.nav.destinations[self.view_names.index("queue")]
        destination.icon = ft.Icon(TABS["queue"][1 if self.needs_bytes else 0], badge=badge)
        destination.selected_icon = ft.Icon(ft.Icons.QUEUE, badge=badge)


def title_bar(page, on_search=None):
    """Computer: the window's title bar, drawn by the app in its own colours (moved by dragging, maximized by a
    double click), with the command palette's button (`on_search`) and its minimize, maximize and close buttons."""
    def minimize(e):
        page.window.minimized = True
        page.update()

    def maximize(e):
        page.window.maximized = not page.window.maximized
        page.update()

    async def close(e):
        await page.window.close()

    def button(icon, tooltip, on_click, hover=None):
        return ft.IconButton(icon=icon, icon_size=16, tooltip=tooltip, on_click=on_click, width=46, height=TITLE_BAR,
                             style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=0), overlay_color=hover))
    return ft.Container(
        content=ft.Row([
            # Flutter's own double click of the drag area is lost on Windows (dragging takes the second click):
            # this one maximizes the window, like the system's title bar.
            ft.GestureDetector(content=ft.WindowDragArea(ft.Container(
                content=row([ft.Image(src="icon.png", width=18, height=18),
                             ft.Text(page.title, size=SMALL_SIZE, weight=ft.FontWeight.W_500)]),
                padding=ft.Padding(left=M, right=0, top=0, bottom=0), height=TITLE_BAR), maximizable=False),
                on_double_tap=maximize, expand=True),
            *([button(ft.Icons.SEARCH, "Search everything (Ctrl+K)", on_search)] if on_search else []),
            button(ft.Icons.HORIZONTAL_RULE, "Minimize", minimize),
            button(ft.Icons.CROP_SQUARE, "Maximize", maximize),
            button(ft.Icons.CLOSE, "Close", close, ft.Colors.RED_400),
        ], spacing=0),
        height=TITLE_BAR, bgcolor=ft.Colors.SURFACE_CONTAINER,
    )


async def show_welcome(app):
    """The first start: the welcome pages, alone, until they are finished or skipped."""
    page = app.page
    done = asyncio.get_running_loop().create_future()
    welcome = Welcome(app, lambda: done.done() or done.set_result(True))
    if app.needs_bytes:
        page.add(ft.SafeArea(content=welcome.view))
    else:
        page.add(ft.SafeArea(expand=True, content=ft.Column(
            [title_bar(page), ft.Container(welcome.view, expand=True, alignment=ft.Alignment(0, 0))],
            spacing=0, expand=True)))
    page.update()
    await done
    page.controls.clear()


async def main(page: ft.Page):
    page.title = "Stash"
    app = App(page)
    install_language(app.settings.get("language"))  # before any text is given to a control
    app.apply_theme()
    page.padding = app.page_pad if app.needs_bytes else 0
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    if app.needs_bytes:
        page.scroll = ft.ScrollMode.AUTO
        page.vertical_alignment = ft.MainAxisAlignment.CENTER  # a short page sits in the middle
    elif not page.window.min_width:  # the first start (a restart keeps the window as it is)
        page.window.width = 1200
        page.window.height = 800
        page.window.min_width = MIN_WINDOW_WIDTH
        page.window.min_height = MIN_WINDOW_HEIGHT
        page.window.title_bar_hidden = True  # the app draws its own (see title_bar)
    if not app.settings.get("welcomed"):
        await show_welcome(app)  # the tabs are then built in the language chosen there
        install_language(app.settings.get("language"))

    app.download = DownloadTab(app)
    app.edit = EditTab(app) if not page.web else None  # it edits files of this device
    app.home = HomeTab(app)
    app.queue = QueueTab(app)
    app.history = HistoryTab(app)
    if not page.web and app.settings.get("phone_links"):  # before Settings, which shows its QR code
        try:
            app.start_link_server()
        except OSError:
            pass
    app.settings_tab = SettingsTab(app)
    app.subscriptions = Subscriptions(app)
    app.tabs = [("home", app.home.view), ("queue", app.queue.view), ("history", app.history.view),
                ("subscriptions", app.subscriptions.view), ("settings", app.settings_tab.view)]
    # Its width is set by on_resize(), called once the page is built.
    holder = ft.Container(content=ft.Column([view for _, view in app.tabs], spacing=0,
                                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
    if app.needs_bytes:
        page.floating_action_button = app.download.fab

    if app.needs_bytes:
        app.nav = ft.NavigationBar(selected_index=0, on_change=app.on_nav)
        page.navigation_bar = app.nav
    else:
        app.nav = ft.NavigationRail(selected_index=0, label_type=ft.NavigationRailLabelType.ALL, min_width=RAIL_WIDTH,
                                    on_change=app.on_nav)
    app.apply_nav_labels()
    app.apply_tabs()  # the tabs shown, and the Queue tab's badge
    start = app.settings.get("start_tab")
    app.show_view(app.view_names.index(start) if start in app.view_names else 0)

    def on_resize(e=None):
        width = page.width or 640
        if app.needs_bytes:
            if width < 700:
                holder.width = max(280, width - 2 * app.page_pad)
            else:  # a tablet, or a phone held sideways: two panes side by side, like on a computer
                holder.width = width - 2 * app.page_pad if width < 1100 else width * 0.85
        app.settings_tab.arrange(holder.width if app.needs_bytes else width)
        app.update_back()  # two panes side by side: nothing to go back to in Settings
        page.update()

    page.on_resize = on_resize
    page.on_keyboard_event = app.on_key
    if app.mobile and page.views:
        page.views[0].on_confirm_pop = app.on_back
    if app.needs_bytes:
        page.add(ft.SafeArea(content=holder))
    else:
        app.scroller = ft.Column([ft.Container(content=holder, padding=L)], expand=True, scroll=ft.ScrollMode.AUTO,
                                 horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        app.show_view(app.nav.selected_index or 0)  # its alignment
        window = ft.Row(
            [
                app.nav,
                ft.VerticalDivider(width=1),
                app.scroller,
            ],
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        app.palette = CommandPalette(app)
        window = ft.Column([title_bar(page, lambda e: app.palette.open()), window], spacing=0, expand=True)
        if flet_file_drop is not None:
            window = flet_file_drop.FileDrop(content=window, expand=True, on_drop=app.on_drop)
        page.add(ft.SafeArea(expand=True, content=window))
    on_resize()
    app.tasks = [asyncio.create_task(app.settings_tab.startup_ytdlp_check()),
                 asyncio.create_task(app.settings_tab.startup_app_check()),
                 asyncio.create_task(asyncio.to_thread(purge_stale_workdirs, app.queue.leftover_workdirs())),
                 asyncio.create_task(app.subscriptions.keep_checking())]
    if app.desktop and app.edit is not None:
        app.tasks.append(asyncio.create_task(watch_folder(app)))

    if app.desktop:
        await app.download.paste_at_startup()
        app.tasks.append(asyncio.create_task(app.download.watch_clipboard()))
    if app.share_target is not None:  # the app was opened by sharing a link to it
        try:
            await app.open_shared_text(await app.share_target.take_shared_text())
        except Exception:
            pass
