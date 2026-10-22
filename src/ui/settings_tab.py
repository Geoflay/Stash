"""The Settings tab, in categories (General, Appearance, Downloads, Network, Storage, Advanced): a new setting
goes in a card of one of them (see CATEGORIES). Every change is saved right away (settings.json). The field at
the top finds a setting by the words of its card."""

import asyncio
import time
from pathlib import Path

import flet as ft
from yt_dlp.version import __version__ as YTDLP_VERSION

from core.app_update import APP_CHECK_INTERVAL, APP_VERSION, is_newer, latest_app_release
from core.cookies import check_cookies, describe_cookies, import_cookies, remove_cookies, saved_cookies_path
from core.backend import BACKEND, CAN_SLIDESHOW, FFMPEG_BIN, FFMPEG_SOURCE, backend_label
from core.errors import Cancelled, clean_error, make_hint
from core.ffmpeg_setup import can_download, download_ffmpeg, ffmpeg_in
from core.download import SPONSORBLOCK_CATEGORIES
from core.filenames import (
    CASES, DATE_FORMATS, DEFAULT_FILENAME_TEMPLATE, FILENAME_FIELDS, FOLDER_FIELDS, NAME_LENGTHS, NAME_RULES, SPACES,
    filename_fields, render_filename, render_folder, unknown_filename_fields,
)
from core.history import HISTORY_KEEP, clear_thumbnails, prune_history, searchable
from core.streams import CODEC, CODEC_CHOICES
from core.tags import COVER_SIZES, TAG_OPTIONS
from core.units import format_bytes
from core.workdirs import clean_temporary_files
from core.jobs import PARALLEL_CHOICES
from core.network import (
    DEFAULT_RESTARTS, NETWORK, RESTART_CHOICES, RETRY_CHOICES, parse_proxy, parse_speed_limit, speed_limit_bytes,
)
from core.playlist import HAS_IMPERSONATION, PLAYLIST_LIMIT_CHOICES, PLAYLIST_PAUSES, PLAYLIST_SITES
from core.schedule import HOURS, night_hours
from core.storage import SETTINGS_FILE_NAME, export_settings, import_settings, save_settings
from core.subscriptions import CHECK_EVERY, DEFAULT_CHECK_EVERY
from core.watch import can_convert, watched_folders
from core.ytdlp_update import (
    YTDLP_CHECK_INTERVAL, YTDLP_UPDATE_ACTIVE, latest_ytdlp_version, remove_ytdlp_update, update_ytdlp,
    ytdlp_is_newer,
)
from ui.progress import ProgressPanel
from ui.i18n import language_options
from ui.items import SUBTITLE_LANGUAGES
from ui.presets import PresetsCard, saved_presets
from ui.style import (
    APP_SCALES, L, M, RADIUS, S, SMALL_SIZE, TEXT_SCALES, XS, caption, column, row, scale_of, section_title,
)
from ui.widgets import MultiPicker, hide_status, set_status, setting_row

# The categories of the tab: key, name, icon, what is in it, and their sub-categories (a card each) in order (one
# missing on this device is left out, like ffmpeg on a phone).
CATEGORIES = (
    ("general", "General", ft.Icons.TUNE, "Folder, file names, notifications",
     ("destination", "files", "background", "phone", "shortcuts")),
    ("appearance", "Appearance", ft.Icons.PALETTE_OUTLINED, "Language, theme, size",
     ("language", "appearance", "simple")),
    ("downloads", "Downloads", ft.Icons.DOWNLOAD_OUTLINED, "Presets, languages, tags, playlists",
     ("presets", "languages", "metadata", "sponsorblock", "music", "playlists", "subscriptions", "schedule")),
    ("network", "Network", ft.Icons.WIFI, "Speed, proxy, cookies", ("network", "cookies")),
    ("storage", "Storage", ft.Icons.FOLDER_OUTLINED, "Library, backup, watched folders",
     ("storage", "backup", "mediaserver", "watch")),
    ("advanced", "Advanced", ft.Icons.BUILD_OUTLINED, "ffmpeg, yt-dlp, updates",
     ("ffmpeg", "ytdlp", "advanced", "about")),
)
# The icon of each sub-category.
GROUP_ICONS = {
    "destination": ft.Icons.FOLDER_OPEN, "files": ft.Icons.DESCRIPTION_OUTLINED,
    "background": ft.Icons.NOTIFICATIONS_OUTLINED, "phone": ft.Icons.SMARTPHONE, "shortcuts": ft.Icons.KEYBOARD,
    "language": ft.Icons.TRANSLATE, "appearance": ft.Icons.PALETTE_OUTLINED, "simple": ft.Icons.TOUCH_APP_OUTLINED,
    "presets": ft.Icons.STAR_OUTLINE, "languages": ft.Icons.SUBTITLES_OUTLINED, "metadata": ft.Icons.LABEL_OUTLINE,
    "sponsorblock": ft.Icons.BLOCK, "music": ft.Icons.MUSIC_NOTE_OUTLINED, "playlists": ft.Icons.PLAYLIST_PLAY,
    "subscriptions": ft.Icons.SUBSCRIPTIONS_OUTLINED, "schedule": ft.Icons.SCHEDULE, "network": ft.Icons.SPEED,
    "cookies": ft.Icons.COOKIE_OUTLINED, "storage": ft.Icons.STORAGE, "backup": ft.Icons.BACKUP_OUTLINED,
    "mediaserver": ft.Icons.TV, "watch": ft.Icons.FOLDER_SPECIAL_OUTLINED, "ffmpeg": ft.Icons.MOVIE_OUTLINED,
    "ytdlp": ft.Icons.SYSTEM_UPDATE_ALT, "advanced": ft.Icons.VIDEO_SETTINGS, "about": ft.Icons.INFO_OUTLINE,
}
THEMES = {"system": ("Same as the system", ft.ThemeMode.SYSTEM), "light": ("Light", ft.ThemeMode.LIGHT),
          "dark": ("Dark", ft.ThemeMode.DARK)}
# Accent colours: name, seed of the light theme, seed of the dark one.
ACCENTS = {"indigo": ("Indigo", "#4f46e5", "#7c75ff"), "blue": ("Blue", "#1e66f5", "#5b8def"),
           "teal": ("Teal", "#00897b", "#4db6ac"), "green": ("Green", "#2e7d32", "#66bb6a"),
           "orange": ("Orange", "#ef6c00", "#ffa726"), "red": ("Red", "#c62828", "#ef5350"),
           "pink": ("Pink", "#c2185b", "#f06292"), "purple": ("Purple", "#6a1b9a", "#ab47bc"),
           "grey": ("Grey", "#546e7a", "#90a4ae")}
START_TABS = {"home": "Home", "queue": "Queue", "history": "Library", "subscriptions": "Subscriptions"}
OPTIONAL_TABS = {"queue": "Queue", "history": "Library", "subscriptions": "Subscriptions"}  # Home and Settings: always
# The roundness of cards, buttons and dialogs: name, radius (None: Flutter's own).
CORNERS = {"square": ("Square", 4), "normal": ("Rounded", None), "round": ("Very round", 24)}
AFTER_DOWNLOAD = {"": "Nothing", "folder": "Open its folder", "file": "Open the file"}
DETAILS_SECONDS = 8  # how long the ⓘ button of a sub-category shows its explanation
TWO_PANES = 700  # phones and tablets at least this wide: the categories and a category side by side


def control_texts(control, out):
    """The words of a card of Settings (title, description, labels, choices), to find it by them."""
    data = getattr(control, "data", None)
    if isinstance(data, dict):
        out.append(data.get("text") or "")
    for attr in ("title", "label", "hint_text", "tooltip", "text"):
        value = getattr(control, attr, None)
        if isinstance(value, str):
            out.append(value)
    description = getattr(control, "description", None)
    if isinstance(description, (str, tuple)):
        out.append(" ".join(filter(None, description)) if isinstance(description, tuple) else description)
    if isinstance(control, ft.Text) and isinstance(control.value, str):
        out.append(control.value)
    for option in getattr(control, "options", None) or []:
        if isinstance(getattr(option, "text", None), str):
            out.append(option.text)
    for child in getattr(control, "controls", None) or []:
        control_texts(child, out)
    content = getattr(control, "content", None)
    if isinstance(content, str):
        out.append(content)
    elif content is not None and not isinstance(content, (int, float, bool)):
        control_texts(content, out)
    return out


def card_title(card):
    data = getattr(card, "data", None)
    return data["title"] if isinstance(data, dict) else getattr(card, "title", "")


def card_summary(card):
    """What a sub-category is about, in a few words."""
    data = getattr(card, "data", None)
    if isinstance(data, dict):
        return data.get("description") or ""
    description = getattr(card, "description", "")
    return description[0] if isinstance(description, tuple) else description or ""


def nav_tile(icon, title, summary, on_click):
    """A row opening a category or a sub-category: its icon, name and what is in it."""
    return ft.Container(
        content=row([ft.Icon(icon, size=24),
                     ft.Column([ft.Text(title, size=15, weight=ft.FontWeight.W_500),
                                caption(summary, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)],
                               spacing=0, tight=True, expand=True),
                     ft.Icon(ft.Icons.CHEVRON_RIGHT, size=20)], spacing=M),
        padding=ft.Padding(left=16, right=12, top=12, bottom=12), border_radius=20, ink=True,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, on_click=on_click)


class SettingsTab:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.pending_ytdlp = None  # version downloaded during this session, used after a restart
        self.bound = []  # (control, setting, default): shown again as saved by sync()
        cards = {
            "destination": self.destination_card(),
            "files": self.files_card(),
            "background": self.background_card(),
            "cookies": self.cookies_card(),
            "network": self.network_card(),
            "playlists": self.playlists_card(),
            "ytdlp": self.ytdlp_card(),
            "sponsorblock": self.sponsorblock_card(),
            "storage": self.storage_card(),
            "backup": self.backup_card(),
            "advanced": self.advanced_card(),
            "appearance": self.appearance_card(),
            "simple": self.simple_card(),
            "music": self.music_card(),
            "metadata": self.metadata_card(),
            "schedule": self.schedule_card(),
            "mediaserver": self.mediaserver_card(),
            "languages": self.languages_card(),
            "language": self.language_card(),
            "presets": self.presets_card(),
            "subscriptions": self.subscriptions_card(),
            "about": self.about_card(),
        }
        if app.desktop:
            cards["ffmpeg"] = self.ffmpeg_card()
            cards["shortcuts"] = self.shortcuts_card()
            if BACKEND == "ffmpeg":
                cards["watch"] = self.watch_card()
        if app.desktop or app.android:
            cards["phone"] = self.phone_card()
        self.cards = cards
        self.index = {name: searchable(" ".join(control_texts(card, []))) for name, card in cards.items()}
        remembered = app.settings.get("settings_category")
        self.category = remembered if remembered in [c[0] for c in CATEGORIES] else "general"
        self.group = None  # the sub-category open, or None: the list of the category's sub-categories
        self.width = 0
        self.wide = app.desktop  # two panes side by side (see arrange)
        self.on_page = False  # one pane: a category's page is shown instead of the list
        status = self.status_banner()
        self.search = ft.TextField(hint_text="Search the settings", prefix_icon=ft.Icons.SEARCH, expand=True,
                                   on_change=self.on_search, border=ft.OutlineInputBorder(border_radius=28))
        self.results = column([], spacing=S, visible=False)
        # The list of categories (with the search and the status at the bottom), and the page of the one open.
        self.tiles = column([nav_tile(icon, label, summary, lambda e, key=key: self.select(key))
                             for key, label, icon, summary, _ in CATEGORIES], spacing=S)
        self.home = column([ft.Row([self.search]), self.results, self.tiles, ft.Container(height=S), status],
                           spacing=S)
        self.page_title = ft.Text("", size=26, weight=ft.FontWeight.BOLD, expand=True)
        self.back_btn = ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Back", on_click=self.back,
                                      style=ft.ButtonStyle(bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH))
        self.info_btn = ft.IconButton(icon=ft.Icons.INFO_OUTLINE, tooltip="", visible=False,
                                      on_click=self.show_details)
        self.body = column([], spacing=S, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.category_page = column([row([self.back_btn, self.page_title, self.info_btn]), self.body], spacing=M,
                                    visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.arrange(0)
        self.view = app.view("Settings", [ft.Row([self.home, self.category_page], spacing=L,
                                                 vertical_alignment=ft.CrossAxisAlignment.START)], visible=False)

    def bind(self, control, key, default):
        """A control showing a setting the welcome pages can change too (see sync)."""
        self.bound.append((control, key, default))
        return control

    def sync(self):
        """Show the settings again as saved (after the welcome pages)."""
        for control, key, default in self.bound:
            control.value = self.app.settings.get(key, default)
        self.refresh_save_dir()

    # Finding a setting

    def on_search(self, e=None):
        """The sub-categories whose words hold every word typed: tapping one opens it."""
        words = searchable(self.search.value).split()
        found = [(label, name) for key, label, _, _, names in CATEGORIES for name in names
                 if name in self.cards and words and all(word in self.index[name] for word in words)]
        self.results.visible = bool(words)
        self.results.controls = [nav_tile(GROUP_ICONS.get(name, ft.Icons.SETTINGS_OUTLINED),
                                          card_title(self.cards[name]), label,
                                          lambda e, name=name: self.open_found(name)) for label, name in found] \
            or [caption("No setting found.")]
        self.tiles.visible = not words
        self.page.update()

    def open_found(self, name):
        self.search.value = ""
        self.on_search()
        self.open_group(name)

    def status_banner(self):
        """Whether everything needed is there: the yt-dlp version, ffmpeg or PyAV, and what is missing."""
        notes = []
        if BACKEND is None:
            notes.append("Neither ffmpeg nor PyAV is available: no MP3, and quality is limited to "
                         "files that are already complete (often 720p max).")
        elif BACKEND == "av":
            notes.append("Without ffmpeg, WebM and MOV videos are unavailable.")
        if BACKEND == "av" and not CAN_SLIDESHOW:
            notes.append("Photo slideshows are unavailable (Pillow and a video encoder are required).")
        ytdlp_label = YTDLP_VERSION + (" (updated)" if YTDLP_UPDATE_ACTIVE else "")
        self.status_text = "\n".join(["Ready · yt-dlp %s · media: %s" % (ytdlp_label, backend_label())] + notes)
        background, color = ((ft.Colors.ORANGE_100, ft.Colors.ORANGE_900) if notes
                             else (ft.Colors.GREEN_100, ft.Colors.GREEN_900))
        self.status_label = ft.Text(self.status_text, color=color, size=SMALL_SIZE, expand=True)
        # A change applied after a restart, not made yet ("Later" in the question).
        self.restart_btn = ft.TextButton("Restart", icon=ft.Icons.RESTART_ALT, visible=False,
                                         on_click=self.app.restart)
        return ft.Container(content=row([self.status_label, self.restart_btn]), bgcolor=background,
                            border_radius=RADIUS, padding=ft.Padding(left=M, right=M, top=S, bottom=S))

    def set_status_note(self, message):
        """A line under the status (yt-dlp updates)."""
        self.status_label.value = "%s\n%s" % (self.status_text, message)

    def go_home(self, e=None):
        self.on_page, self.group = False, None
        self.arrange()
        self.app.update_back()
        self.page.update()

    def back(self, e=None):
        """From a sub-category to its category, from a category to the list."""
        if self.group is not None:
            self.group = None
            self.arrange()
            self.app.update_back()
            self.page.update()
        else:
            self.go_home()

    def category_of(self, name):
        return next(c for c in CATEGORIES if name in c[4])

    def arrange(self, width=None):
        """The list of categories and the page open: side by side on a computer or a wide screen, else one or the
        other. The page is the list of the category's sub-categories, or the one open."""
        if width is not None:
            self.width = width
        self.wide = self.app.desktop or self.width >= TWO_PANES
        key, label, _, _, names = next(c for c in CATEGORIES if c[0] == self.category)
        if self.group is not None and self.group in self.cards:
            card = self.cards[self.group]
            self.page_title.value = card_title(card)
            details = getattr(card, "data", None) or {}
            self.info_btn.tooltip = details.get("details") if isinstance(details, dict) else None
            self.info_btn.visible = bool(self.info_btn.tooltip)
            self.body.controls = [card]
        else:
            self.page_title.value = label
            self.info_btn.visible = False
            self.body.controls = [nav_tile(GROUP_ICONS.get(name, ft.Icons.SETTINGS_OUTLINED), card_title(
                self.cards[name]), card_summary(self.cards[name]), lambda e, name=name: self.open_group(name))
                for name in names if name in self.cards]
        self.home.visible = self.wide or not self.on_page
        self.category_page.visible = self.wide or self.on_page
        self.home.expand, self.category_page.expand = (2, 3) if self.wide else (1, 1)
        self.back_btn.visible = self.group is not None or not self.wide

    def select(self, key):
        """Open a category: the list of its sub-categories."""
        self.category, self.group, self.on_page = key, None, True
        self.app.remember("settings_category", key)
        self.arrange()
        self.app.update_back()
        self.page.update()

    def open_group(self, name):
        """Open a sub-category (its settings)."""
        self.category, self.group, self.on_page = self.category_of(name)[0], name, True
        self.arrange()
        self.app.update_back()
        self.page.update()

    def show_details(self, e=None):
        self.page.show_dialog(ft.SnackBar(ft.Text(self.info_btn.tooltip or ""), duration=DETAILS_SECONDS * 1000))

    # Appearance

    def appearance_card(self):
        app = self.app

        def remember(key, theme=True):
            def handler(e):
                value = e.control.value
                app.remember(key, bool(value) if isinstance(e.control, ft.Switch) else value)
                if theme:
                    app.apply_theme()
                else:
                    app.apply_nav_labels()
                self.page.update()
            return handler

        def dropdown(label, key, choices, default, theme=True):
            return self.bind(ft.Dropdown(label=label, expand=True, on_select=remember(key, theme),
                                         value=app.settings.get(key) if app.settings.get(key) in choices else default,
                                         options=[ft.DropdownOption(key=k, text=t) for k, t in choices.items()]),
                             key, default)

        return app.section(
            "Theme", "Theme, colours, size",
            row([dropdown("Theme", "theme", {k: t for k, (t, _) in THEMES.items()}, "system"),
                 dropdown("Corners", "corners", {k: name for k, (name, _) in CORNERS.items()}, "normal")]),
            self.accent_picker(),
            self.size_slider("Text size", "text_scale", TEXT_SCALES),
            self.size_slider("App size", "app_scale", APP_SCALES),
            self.tabs_picker(),
            row([dropdown("Tab names", "nav_labels", {"all": "Always shown", "selected": "Only the tab shown"},
                          "all", theme=False)]),
            ft.Row([ft.Dropdown(label="First tab", expand=True, value=app.settings.get("start_tab") if app.settings.get("start_tab") in START_TABS
                                else "home", options=[ft.DropdownOption(key=k, text=t) for k, t in START_TABS.items()],
                                on_select=lambda e: app.remember("start_tab", e.control.value))]),
            ft.Switch(label="Pure black dark theme", value=bool(app.settings.get("pure_black")),
                      on_change=remember("pure_black")),
        )

    def tabs_picker(self):
        """The tabs shown in the navigation, a chip each (Home and Settings are always there)."""
        app = self.app

        def hidden():
            names = app.settings.get("hidden_tabs")
            return [name for name in names if name in OPTIONAL_TABS] if isinstance(names, list) else []

        def toggle(name, e):
            shown = name in hidden()  # hidden until now: shown from now on
            app.remember("hidden_tabs", [n for n in hidden() if n != name] + ([] if shown else [name]))
            e.control.selected = shown
            app.apply_tabs()
            self.page.update()
        chips = [ft.Chip(label=ft.Text(label), selected=name not in hidden(), show_checkmark=True,
                         on_select=lambda e, name=name: toggle(name, e)) for name, label in OPTIONAL_TABS.items()]
        return ft.Container(column([ft.Text("Tabs shown"), caption("Home and Settings are always there."),
                                    ft.Row(chips, wrap=True, spacing=S, run_spacing=S)], spacing=XS),
                            padding=ft.Padding(left=XS, right=0, top=2, bottom=2))

    def accent_picker(self):
        """The accent colours as round swatches: a tap chooses one (the theme changes at once)."""
        app = self.app
        swatches = {}

        def show():
            chosen = app.settings.get("accent") if app.settings.get("accent") in ACCENTS else "indigo"
            for key, swatch in swatches.items():
                swatch.border = ft.Border.all(3, ft.Colors.ON_SURFACE) if key == chosen else None
                swatch.content = ft.Icon(ft.Icons.CHECK, size=18, color=ft.Colors.WHITE) if key == chosen else None

        def choose(key):
            app.remember("accent", key)
            show()
            app.apply_theme()
            self.page.update()
        for key, (name, light, _) in ACCENTS.items():
            swatches[key] = ft.Container(width=34, height=34, border_radius=17, bgcolor=light, tooltip=name,
                                         alignment=ft.Alignment(0, 0), ink=True,
                                         on_click=lambda e, key=key: choose(key))
        show()
        return ft.Container(column([ft.Text("Accent colour"), ft.Row(list(swatches.values()), wrap=True, spacing=10,
                                                                      run_spacing=10)], spacing=XS),
                            padding=ft.Padding(left=XS, right=0, top=2, bottom=2))  # lined up with the others

    def size_slider(self, title, key, limits):
        """A slider for the size of the texts or of the app, from 80 % on (Flutter's sizes change at once, the
        app's own after a restart)."""
        app = self.app
        value = scale_of(app.settings, key, limits)
        percent = ft.Text("%d %%" % round(value * 100), width=round(48 * TEXT_SCALES[1]), text_align=ft.TextAlign.END)

        def on_change(e):
            percent.value = "%d %%" % round(e.control.value * 100)
            self.page.update()

        async def on_change_end(e):
            app.remember(key, round(e.control.value, 2))
            app.apply_theme()
            self.page.update()
            await app.offer_restart()
        slider = ft.Slider(value=value, min=limits[0], max=limits[1], divisions=round((limits[1] - limits[0]) * 20),
                           on_change=on_change, on_change_end=on_change_end, width=200)
        return setting_row(title, row([slider, percent], spacing=0))

    def language_card(self):
        app = self.app

        async def on_select(e):
            app.remember("language", e.control.value)
            await app.offer_restart()
        return app.section(
            "Language", "Language of the app",
            self.bind(ft.Dropdown(label="Language", value=app.settings.get("language") or "system",
                                  options=language_options(), on_select=on_select), "language", "system"),
        )

    def simple_card(self):
        app = self.app

        def on_change(e):
            app.remember("simple_mode", bool(e.control.value))
            if app.download is not None and app.download.result_card.visible:
                app.download.refresh_options()
            self.page.update()

        return app.section(
            "Simple mode",
            "Fewer options after a link is analyzed",
            self.bind(ft.Switch(label="Simple mode", value=bool(app.settings.get("simple_mode")), on_change=on_change),
                      "simple_mode", False),
        )

    # Presets (see presets.py)

    def presets_card(self):
        self.presets = PresetsCard(self.app)
        return self.app.section(
            "Presets",
            ("One-tap choices of the Download tab",
             "Drag them to reorder. Tap one to use it on its own for some sites."),
            *self.presets.controls,
        )

    # Languages

    def languages_card(self):
        app = self.app
        languages = {"": "The video's own"} | dict(SUBTITLE_LANGUAGES)
        subs = MultiPicker("Add a language", lambda: app.remember("pref_subs", [k for k in subs.value]))
        saved = app.settings.get("pref_subs")
        subs.set(SUBTITLE_LANGUAGES, saved if isinstance(saved, list) else [])
        return app.section(
            "Languages",
            "Sound and subtitles chosen first",
            ft.Row([ft.Dropdown(label="Sound track", expand=True, value=app.settings.get("pref_audio") or "",
                                options=[ft.DropdownOption(key=k, text=t) for k, t in languages.items()],
                                on_select=lambda e: app.remember("pref_audio", e.control.value))]),
            caption("Subtitles"),
            subs.box,
        )

    # Subscriptions

    def subscriptions_card(self):
        app = self.app
        hours = app.settings.get("subscription_hours")
        return app.section(
            "Subscriptions",
            "How often they are checked",
            ft.Row([ft.Dropdown(label="Check the subscriptions", expand=True,
                                value=str(hours if hours in CHECK_EVERY else DEFAULT_CHECK_EVERY),
                                options=[ft.DropdownOption(key=str(k), text=t) for k, t in CHECK_EVERY.items()],
                                on_select=lambda e: app.remember("subscription_hours", int(e.control.value)))]),
        )

    # Watched folders (computer)

    def watch_card(self):
        self.watch_list = column([], spacing=S)
        self.watch_note = caption(visible=False)
        self.refresh_watch()
        return self.app.section(
            "Watched folders",
            "Files put there are converted",
            self.watch_list,
            ft.Row([ft.Button("Add a folder…", icon=ft.Icons.CREATE_NEW_FOLDER_OUTLINED,
                              on_click=self.add_watch_dir)]),
            self.watch_note,
        )

    def save_watch(self, folders):
        self.app.remember("watch_folders", folders)
        self.refresh_watch()
        self.page.update()

    def refresh_watch(self):
        """The folders watched, and the presets they can use (also after a preset is saved or removed)."""
        if not hasattr(self, "watch_list"):
            return
        folders = watched_folders(self.app.settings)
        presets = [p for p in saved_presets(self.app) if can_convert(p)]
        names = {p["name"] for p in presets}

        def change(index, key, value):
            folders[index][key] = value
            self.save_watch(folders)

        def remove(index):
            del folders[index]
            self.save_watch(folders)
        self.watch_list.controls = [column([
            row([ft.Icon(ft.Icons.FOLDER_OUTLINED), ft.Text(folder["dir"], expand=True, selectable=True),
                 ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Stop watching it", on_click=lambda e, i=i: remove(i))]),
            ft.Row([ft.Dropdown(label="Convert with the preset", expand=True,
                                value=folder.get("preset") if folder.get("preset") in names else None,
                                helper_text=None if presets else "Save a preset in the Download tab first.",
                                options=[ft.DropdownOption(key=p["name"],
                                                           text="%s (%s)" % (p["name"], p["fmt"].upper()))
                                         for p in presets],
                                on_select=lambda e, i=i: change(i, "preset", e.control.value))]),
            ft.Switch(label="Delete the original once converted", value=bool(folder.get("delete")),
                      on_change=lambda e, i=i: change(i, "delete", bool(e.control.value)))], spacing=XS)
            for i, folder in enumerate(folders)] or [caption("No folder watched.")]

    def set_watch_note(self, text):
        if hasattr(self, "watch_note"):
            self.watch_note.value, self.watch_note.visible = text, bool(text)

    async def add_watch_dir(self, e=None):
        folder = await self.app.picker.get_directory_path(dialog_title="Choose a folder to watch")
        folders = watched_folders(self.app.settings)
        if folder and folder not in [f["dir"] for f in folders]:
            self.save_watch(folders + [{"dir": folder, "preset": None, "delete": False}])

    # The app's own updates

    def about_card(self):
        app = self.app
        self.app_msg = ft.Container(visible=False)
        self.app_page_btn = ft.TextButton("Open the download page", icon=ft.Icons.OPEN_IN_NEW, visible=False)

        async def show_welcome(e):
            app.remember("welcomed", False)
            await app.offer_restart()
        return app.section(
            "App updates",
            ("Stash %s" % APP_VERSION, None),
            ft.Switch(label="Check for a new version at startup", value=bool(app.settings.get("app_check", True)),
                      on_change=lambda e: app.remember("app_check", bool(e.control.value))),
            row([ft.Button("Check now", icon=ft.Icons.REFRESH, on_click=self.check_app),
                 self.app_page_btn], wrap=True),
            self.app_msg,
            ft.Row([ft.TextButton("Show the welcome pages again", icon=ft.Icons.WAVING_HAND_OUTLINED,
                                  on_click=show_welcome)]),
        )

    def show_release(self, release):
        """What the latest release says (None: nothing published yet)."""
        if release is None:
            set_status(self.app_msg, "ok", "No version published yet: this one is the latest.")
        elif is_newer(release["version"]):
            set_status(self.app_msg, "info", "Version %s is available (this one is %s)." % (
                release["version"], APP_VERSION))
            self.app_page_btn.on_click = lambda e: self.page.run_task(self.app.open_link, release["page"])
            self.app_page_btn.visible = True
            self.set_status_note("Version %s of the app is available: Settings > Advanced." % release["version"])
        else:
            set_status(self.app_msg, "ok", "The app is up to date (%s)." % APP_VERSION)

    async def check_app(self, e=None):
        set_status(self.app_msg, "loading", "Looking for a new version…")
        self.page.update()
        try:
            self.show_release(await asyncio.to_thread(latest_app_release))
        except Exception as exc:
            set_status(self.app_msg, "err", "Could not check: %s" % clean_error(exc))
        self.page.update()

    async def startup_app_check(self):
        settings = self.app.settings
        if not settings.get("app_check", True):
            return
        checked_at = settings.get("app_checked_at")
        if isinstance(checked_at, (int, float)) and time.time() - checked_at < APP_CHECK_INTERVAL:
            return
        try:
            release = await asyncio.to_thread(latest_app_release)
        except Exception:
            return
        self.app.remember("app_checked_at", time.time())
        if release is not None and is_newer(release["version"]):
            self.show_release(release)
            self.page.update()

    # Schedule

    def schedule_card(self):
        app = self.app
        start, end = night_hours(app.settings)

        def hour_dropdown(label, key, value):
            def on_select(e):
                app.remember(key, int(e.control.value))
                app.apply_night_speed()
            return ft.Dropdown(label=label, value=str(value), expand=True, on_select=on_select,
                               options=[ft.DropdownOption(key=str(h), text="%d:00" % h) for h in HOURS])

        def on_night_speed(e):
            app.remember("night_speed", bool(e.control.value))
            self.night_limit.disabled = not e.control.value
            app.apply_night_speed()
            self.page.update()

        def on_night_limit(e):
            try:
                parse_speed_limit(e.control.value)
            except ValueError as exc:
                self.night_msg.value, self.night_msg.visible = str(exc), True
                self.page.update()
                return
            self.night_msg.visible = False
            app.remember("night_speed_limit", (e.control.value or "").strip())
            app.apply_night_speed()
            self.page.update()

        self.night_limit = ft.TextField(label="Speed limit at night (MB/s)", hint_text="Unlimited", expand=True,
                                        value=app.settings.get("night_speed_limit", ""),
                                        keyboard_type=ft.KeyboardType.NUMBER, on_change=on_night_limit,
                                        disabled=not app.settings.get("night_speed"))
        self.night_msg = ft.Text("", size=SMALL_SIZE, color=ft.Colors.ERROR, visible=False)
        controls = [
            row([hour_dropdown("Night from", "night_start", start), hour_dropdown("to", "night_end", end)]),
            ft.Switch(label="Download only at night",
                      value=bool(app.settings.get("night_downloads")),
                      on_change=lambda e: app.remember("night_downloads", bool(e.control.value))),
            ft.Switch(label="Another speed limit at night",
                      value=bool(app.settings.get("night_speed")), on_change=on_night_speed),
            ft.Row([self.night_limit]), self.night_msg,
        ]
        if app.android:
            controls.append(ft.Switch(
                label="Download only while the phone is charging, on Wi-Fi",
                value=bool(app.settings.get("charging_only")),
                on_change=lambda e: app.remember("charging_only", bool(e.control.value))))
        return app.section(
            "Schedule",
            ("Download at night", "“Start now” on a waiting download starts it at once."),
            *controls,
        )

    # Plex and Jellyfin

    def mediaserver_card(self):
        app = self.app
        return app.section(
            "Plex and Jellyfin",
            "Videos arranged as TV shows",
            ft.Switch(label="Lay out videos for Plex or Jellyfin", value=bool(app.settings.get("media_server")),
                      on_change=lambda e: app.remember("media_server", bool(e.control.value))),
        )

    # Metadata

    def metadata_card(self):
        app = self.app

        def remember(key, value):
            TAG_OPTIONS[key] = value
            app.remember("tag_" + key, value)

        switches = [ft.Switch(label=label, value=TAG_OPTIONS[key],
                              on_change=lambda e, key=key: remember(key, bool(e.control.value)))
                    for key, label in (("genre", "Genre"), ("album_artist", "Album artist"),
                                       ("track", "Track number"), ("composer", "Composer"),
                                       ("comment", "Description, as a comment"))]
        return app.section(
            "Metadata",
            "Extra tags in the files",
            *switches,
            row([ft.Dropdown(label="Cover art size", expand=True, value=str(TAG_OPTIONS["cover_size"]),
                             options=[ft.DropdownOption(key=str(n), text="%d pixels at most" % n if n else
                                                        "As the site gives it") for n in COVER_SIZES],
                             on_select=lambda e: remember("cover_size", int(e.control.value)))]),
            ft.Switch(label="Square cover art", value=TAG_OPTIONS["square_cover"],
                      on_change=lambda e: remember("square_cover", bool(e.control.value))),
        )

    # Music

    def music_card(self):
        app = self.app
        return app.section(
            "Music",
            ("Real tags and lyrics for songs",
             "Only sure matches are used: same title, same length."),
            ft.Switch(label="Real title, artist, album and cover art (MusicBrainz)",
                      value=bool(app.settings.get("music_tags", True)),
                      on_change=lambda e: app.remember("music_tags", bool(e.control.value))),
            ft.Switch(label="Lyrics (LRCLIB)",
                      value=bool(app.settings.get("lyrics", True)),
                      on_change=lambda e: app.remember("lyrics", bool(e.control.value))),
        )

    # Keyboard shortcuts (computer)

    def shortcuts_card(self):
        app = self.app
        return app.section(
            "Keyboard shortcuts",
            "Ctrl+V, Enter, Escape",
            ft.Switch(label="Keyboard shortcuts", value=bool(app.settings.get("keyboard_shortcuts")),
                      on_change=lambda e: app.remember("keyboard_shortcuts", bool(e.control.value))),
        )

    # Links sent from a phone

    def phone_card(self):
        """The other devices of the Wi-Fi: links received from them (they find this one), the name they see; on a
        computer, the page a phone's browser opens (QR code) and files sent to a phone."""
        app = self.app
        self.qr_image = ft.Image(src="", width=220, height=220, visible=False)
        self.qr_address = ft.Text("", selectable=True, size=SMALL_SIZE, visible=False)
        self.phone_msg = ft.Container(visible=False)
        switch = ft.Switch(label="Receive links from other devices", value=bool(app.settings.get("phone_links")),
                           on_change=self.toggle_phone)

        def on_name(e):
            app.remember("device_name", (e.control.value or "").strip())
        name = ft.TextField(label="Name of this device", value=app.settings.get("device_name") or "",
                            hint_text=app.device_name(), expand=True, on_blur=on_name, on_submit=on_name)
        self.show_phone_address()
        if app.android:
            def on_address(e):
                app.remember("computer_address", (e.control.value or "").strip())
            return app.section(
                "Other devices", ("Links between your devices", "Same Wi-Fi only."),
                switch, ft.Row([name]), self.phone_msg,
                ft.Row([ft.TextField(label="Computer address (if it is not found)",
                                     hint_text="http://192.168.1.20:8790/…",
                                     value=app.settings.get("computer_address", ""), expand=True,
                                     keyboard_type=ft.KeyboardType.URL, on_blur=on_address,
                                     on_submit=on_address)]),
            )

        async def send(folder):
            if folder:
                path = await app.picker.get_directory_path(dialog_title="Choose the folder to send")
            else:
                files = await app.picker.pick_files(dialog_title="Choose the file to send", allow_multiple=False)
                path = files[0].path if files and files[0].path else None
            if path:
                await app.send_to_phone(path)
        return app.section(
            "Other devices",
            ("Links between your devices, files to a phone", "Same Wi-Fi only."),
            switch, ft.Row([name]), self.qr_image, self.qr_address, self.phone_msg,
            section_title("Send to a phone"),
            row([ft.TextButton("Send a file…", icon=ft.Icons.UPLOAD_FILE, on_click=lambda e: send(False)),
                 ft.TextButton("Send a folder (.zip)…", icon=ft.Icons.DRIVE_FOLDER_UPLOAD_OUTLINED,
                               on_click=lambda e: send(True))], wrap=True),
        )

    def show_phone_address(self):
        server = self.app.link_server
        address = server.address if server is not None and self.app.desktop else ""
        if address:
            from core.link_server import qr_png
            self.qr_image.src = qr_png(address)
        self.qr_image.visible = self.qr_address.visible = bool(address)
        self.qr_address.value = address

    def toggle_phone(self, e):
        app = self.app
        app.remember("phone_links", bool(e.control.value))
        hide_status(self.phone_msg)
        if e.control.value:
            try:
                app.start_link_server()
            except OSError as exc:
                set_status(self.phone_msg, "err", "Could not start: %s" % exc)
        else:
            app.stop_link_server()
        self.show_phone_address()
        self.page.update()

    # ffmpeg (computer only)

    def ffmpeg_card(self):
        app = self.app
        found = {"path": "Using ffmpeg from the PATH: %s",
                 "folder": "Using ffmpeg from the folder chosen here: %s",
                 "downloaded": "Using the ffmpeg downloaded by the app: %s"}
        self.ffmpeg_text = ft.Text(found[FFMPEG_SOURCE] % FFMPEG_BIN if FFMPEG_BIN else
                                   "ffmpeg was not found: MP3, WebM, the best qualities, GIFs, the speed and "
                                   "most of Edit are not available.", size=SMALL_SIZE,
                                   selectable=True,
                                   color=None if FFMPEG_BIN else ft.Colors.ERROR)
        self.ffmpeg_msg = ft.Container(visible=False)
        self.ffmpeg_progress = ProgressPanel(self.page)
        self.ffmpeg_download_btn = ft.Button("Download ffmpeg", icon=ft.Icons.DOWNLOAD, visible=can_download(),
                                             on_click=self.download_ffmpeg)
        self.ffmpeg_forget_btn = ft.TextButton("Forget this folder", icon=ft.Icons.CLOSE,
                                               visible=bool(app.settings.get("ffmpeg_dir")), on_click=self.forget_ffmpeg_dir)
        return app.section(
            "ffmpeg",
            "Converts, merges and cuts",
            self.ffmpeg_text,
            row([ft.TextButton("Choose its folder…", icon=ft.Icons.FOLDER_OPEN, on_click=self.choose_ffmpeg_dir),
                 self.ffmpeg_download_btn, self.ffmpeg_forget_btn], wrap=True),
            self.ffmpeg_progress.box,
            self.ffmpeg_msg,
        )

    async def choose_ffmpeg_dir(self, e=None):
        folder = await self.app.picker.get_directory_path(dialog_title="Choose the folder where ffmpeg is")
        if not folder:
            return
        program = ffmpeg_in(folder)
        if program is None:
            set_status(self.ffmpeg_msg, "err", "There is no ffmpeg program in this folder (nor in its \"bin\" folder).")
        else:
            self.app.remember("ffmpeg_dir", folder)
            self.ffmpeg_forget_btn.visible = True
            set_status(self.ffmpeg_msg, "ok", "Found: %s." % program)
            self.page.update()
            await self.app.offer_restart()
        self.page.update()

    async def forget_ffmpeg_dir(self, e=None):
        self.app.settings.pop("ffmpeg_dir", None)
        save_settings(self.app.settings)
        self.ffmpeg_forget_btn.visible = False
        self.page.update()
        await self.app.offer_restart()  # ffmpeg is then looked for in the app's own copy, then in the PATH

    async def download_ffmpeg(self, e=None):
        panel = self.ffmpeg_progress
        self.ffmpeg_download_btn.disabled = True
        hide_status(self.ffmpeg_msg)
        panel.start()
        self.page.update()
        try:
            program = await asyncio.to_thread(download_ffmpeg, panel.reporter())
            # The chosen folder would come first: the copy just downloaded is the one wanted.
            self.app.settings.pop("ffmpeg_dir", None)
            save_settings(self.app.settings)
            self.ffmpeg_forget_btn.visible = False
            set_status(self.ffmpeg_msg, "ok", "ffmpeg downloaded: %s." % program)
            self.page.update()
            await self.app.offer_restart()
        except Cancelled:
            set_status(self.ffmpeg_msg, "info", "Cancelled.")
        except Exception as exc:
            set_status(self.ffmpeg_msg, "err", "The download failed: %s" % exc)
        finally:
            panel.box.visible = False
            self.ffmpeg_download_btn.disabled = False
            self.page.update()

    # Destination folder

    def destination_card(self):
        app = self.app
        self.save_dir_text = ft.Text("", size=SMALL_SIZE, selectable=True)
        choose_btn = ft.Button("Choose folder…", icon=ft.Icons.FOLDER_OPEN)
        self.save_dir_reset_btn = ft.TextButton("Ask every time", icon=ft.Icons.CLOSE, visible=False)
        self.save_dir_msg = ft.Text("", size=SMALL_SIZE, color=ft.Colors.ERROR, visible=False)
        self.save_dir_settings_btn = ft.TextButton("Open app settings", icon=ft.Icons.SETTINGS, visible=False)
        choose_btn.on_click = self.choose_save_dir
        self.save_dir_reset_btn.on_click = self.reset_save_dir
        self.save_dir_settings_btn.on_click = self.open_permission_settings
        self.refresh_save_dir()
        if not app.can_pick_folder:
            return app.section("Destination folder", "Where downloads are saved")
        self.backup_text = ft.Text("", size=SMALL_SIZE, selectable=True)
        self.backup_reset_btn = ft.TextButton("No backup copy", icon=ft.Icons.CLOSE, on_click=self.reset_backup_dir)
        self.refresh_backup_dir()
        return app.section(
            "Destination folder",
            "Where downloads are saved",
            self.save_dir_text,
            self.save_dir_msg,
            row([choose_btn, self.save_dir_reset_btn, self.save_dir_settings_btn], wrap=True),
            section_title("Backup copy"),
            caption("Every file is also copied there (a cloud folder, a NAS…)."),
            self.backup_text,
            row([ft.Button("Choose folder…", icon=ft.Icons.CLOUD_UPLOAD_OUTLINED, on_click=self.choose_backup_dir),
                 self.backup_reset_btn], wrap=True),
        )

    def refresh_backup_dir(self):
        folder = self.app.settings.get("backup_dir")
        self.backup_text.value = ("Copying to: %s" % folder) if folder else "No backup copy."
        if folder and self.app.backup_folder() is None and (self.app.desktop or self.app.storage_access):
            self.backup_text.value += " (not available now: the files are not copied)"
        self.backup_reset_btn.visible = bool(folder)

    async def choose_backup_dir(self, e=None):
        app = self.app
        if app.android and not await app.request_storage_access():
            return
        try:
            folder = await app.picker.get_directory_path(dialog_title="Choose the backup folder")
        except Exception:
            folder = None
        if folder:
            app.remember("backup_dir", folder)
        self.refresh_backup_dir()
        self.page.update()

    def reset_backup_dir(self, e=None):
        self.app.settings.pop("backup_dir", None)
        save_settings(self.app.settings)
        self.refresh_backup_dir()
        self.page.update()

    def refresh_save_dir(self):
        app = self.app
        saved_dir = app.settings.get("save_dir")
        if saved_dir:
            self.save_dir_text.value = "Saving to: %s" % saved_dir
        else:
            self.save_dir_text.value = "No folder: asked for each file."
        self.save_dir_reset_btn.visible = bool(saved_dir)
        self.save_dir_settings_btn.visible = bool(app.android and saved_dir and app.storage_access is False)
        if saved_dir and app.android and app.storage_access is False:
            self.save_dir_msg.value = "“All files access” is not granted: the app asks for each file."
            self.save_dir_msg.visible = True
        elif saved_dir and app.destination_folder() is None and (app.desktop or app.storage_access):
            self.save_dir_msg.value = "This folder cannot be written to: the app asks for each file."
            self.save_dir_msg.visible = True
        else:
            self.save_dir_msg.visible = False

    async def choose_save_dir(self, e=None):
        app = self.app
        if app.android:
            granted = await app.request_storage_access()
            if not granted:
                self.refresh_save_dir()
                self.save_dir_msg.value = "Turn on “All files access” for the app, then choose again."
                self.save_dir_msg.visible = True
                self.save_dir_settings_btn.visible = True
                self.page.update()
                return
        try:
            folder = await app.picker.get_directory_path(dialog_title="Choose destination folder")
        except Exception as exc:
            self.refresh_save_dir()
            self.save_dir_msg.value = "Could not open the folder picker: %s" % exc
            self.save_dir_msg.visible = True
            self.page.update()
            return
        if folder:
            app.remember("save_dir", folder)
        self.refresh_save_dir()
        self.page.update()

    async def open_permission_settings(self, e=None):
        if self.app.permissions is not None:
            try:
                await self.app.permissions.open_app_settings()
            except Exception:
                pass

    def reset_save_dir(self, e=None):
        self.app.settings.pop("save_dir", None)
        save_settings(self.app.settings)
        self.refresh_save_dir()
        self.page.update()

    # Files

    def files_card(self):
        app = self.app
        self.template_field = ft.TextField(
            label="File name",
            value=app.filename_template(),
            hint_text=DEFAULT_FILENAME_TEMPLATE,
            expand=True,
        )
        self.template_preview = ft.Text("", size=SMALL_SIZE, selectable=True)
        self.template_field.on_change = self.on_template_change
        self.music_field = ft.TextField(label="Music file name", value=app.settings.get("music_template", ""),
                                        hint_text="Same as videos ({artist} - {song}…)", expand=True,
                                        on_change=self.on_template_change)
        self.music_preview = ft.Text("", size=SMALL_SIZE, selectable=True)
        self.refresh_template_preview()
        after = {k: t for k, t in AFTER_DOWNLOAD.items() if k != "folder" or app.desktop}
        after_dd = ft.Dropdown(label="When a download is saved", expand=True, visible=app.can_open_files(),
                               value=app.settings.get("after_download") if app.settings.get("after_download") in after
                               else "", options=[ft.DropdownOption(key=k, text=t) for k, t in after.items()],
                               on_select=lambda e: app.remember("after_download", e.control.value))
        tags_switch = ft.Switch(
            label="Add tags, cover art and chapters to the files",
            value=app.embed_tags(),
            on_change=lambda e: app.remember("embed_tags", bool(e.control.value)),
        )
        self.folder_field = ft.TextField(
            label="Sort into folders", hint_text="None ({platform}/{uploader}…)",
            value=app.settings.get("folder_template", ""), expand=True,
        )
        self.folder_preview = ft.Text("", size=SMALL_SIZE, selectable=True)
        self.folder_field.on_change = self.on_folder_change
        self.refresh_folder_preview()
        check_switch = ft.Switch(
            label="Check each file once downloaded",
            value=bool(app.settings.get("check_files", True)),
            on_change=lambda e: app.remember("check_files", bool(e.control.value)),
        )
        duplicates_switch = ft.Switch(
            label="Warn before downloading the same file again",
            value=bool(app.settings.get("warn_duplicates", True)),
            on_change=lambda e: app.remember("warn_duplicates", bool(e.control.value)),
        )
        return app.section(
            "Files",
            ("Names, folders, tags", "Fields: %s." % ", ".join("{%s}" % name for name in FILENAME_FIELDS)),
            ft.Row([self.template_field]),
            self.template_preview,
            ft.Row([self.music_field]),
            self.music_preview,
            ft.Row([after_dd]),
            ft.Row([self.folder_field]),
            self.folder_preview,
            tags_switch,
            duplicates_switch,
            check_switch,
            *self.name_rules_controls(),
        )

    TEMPLATE_EXAMPLE = {"title": "Never Gonna Give You Up", "uploader": "Rick Astley", "upload_date": "20091025",
                        "platform": "Youtube", "video_id": "dQw4w9WgXcQ"}

    def refresh_folder_preview(self):
        parts = render_folder(self.folder_field.value.strip(), dict(
            filename_fields({}, self.TEMPLATE_EXAMPLE), type="Videos"))
        unknown = unknown_filename_fields(self.folder_field.value.replace("{type}", ""))
        if unknown:
            self.folder_preview.value = "Unknown field: %s. Fields: %s." % (
                ", ".join("{%s}" % name for name in unknown), ", ".join("{%s}" % name for name in FOLDER_FIELDS))
        else:
            self.folder_preview.value = "Example: %s/" % "/".join(parts) if parts else ""

    def on_folder_change(self, e):
        self.app.remember("folder_template", self.folder_field.value.strip())
        self.refresh_folder_preview()
        self.page.update()

    def refresh_template_preview(self):
        template = self.template_field.value.strip() or DEFAULT_FILENAME_TEMPLATE
        unknown = unknown_filename_fields(template)
        example = render_filename(template, dict(filename_fields({"height": 1080}, self.TEMPLATE_EXAMPLE)), ".mp4")
        self.show_example(self.template_preview, example, unknown)
        music = self.music_field.value.strip()
        song = dict(self.TEMPLATE_EXAMPLE, title="Rick Astley - Never Gonna Give You Up (Official Video)")
        self.show_example(self.music_preview, render_filename(music or template, filename_fields(song, {}, False),
                                                              ".mp3"), unknown_filename_fields(music))

    def name_rules_controls(self):
        """How the names made from the templates are cleaned up (core.filenames.NAME_RULES)."""
        def change(key, value):
            NAME_RULES[key] = value
            self.app.remember("name_rules", dict(NAME_RULES))
            self.refresh_template_preview()
            self.page.update()

        def switch(label, key):
            return ft.Switch(label=label, value=bool(NAME_RULES[key]),
                             on_change=lambda e: change(key, bool(e.control.value)))

        def dropdown(label, key, choices):
            return ft.Dropdown(label=label, expand=True, value=str(NAME_RULES[key]),
                               options=[ft.DropdownOption(key=str(k), text=t) for k, t in choices.items()],
                               on_select=lambda e: change(key, int(e.control.value) if key == "max_length"
                                                          else e.control.value))
        remove = ft.TextField(label="Words to remove", value=NAME_RULES["remove"],
                              hint_text="Official, HD…", expand=True,
                              on_blur=lambda e: change("remove", (e.control.value or "").strip()))
        return [section_title("Clean-up of the names"),
                switch("Remove the emojis", "emoji"),
                switch("Remove the accents and other letters (é → e)", "ascii"),
                switch("Remove “(Official Video)”, “[Lyrics]”, “(4K)”…", "noise"),
                ft.Row([remove]),
                row([dropdown("Spaces", "spaces", SPACES), dropdown("Capitals", "case", CASES)]),
                row([dropdown("Date", "date", DATE_FORMATS),
                     dropdown("Longest name", "max_length", {n: "%d characters" % n for n in NAME_LENGTHS})])]

    def show_example(self, text, example, unknown):
        text.value = "Example: %s" % example
        if unknown:
            text.value += "\nUnknown field%s, kept as text: %s" % (
                "s" if len(unknown) > 1 else "", ", ".join("{%s}" % name for name in unknown))
        text.color = ft.Colors.ERROR if unknown else ft.Colors.ON_SURFACE_VARIANT

    def on_template_change(self, e):
        self.app.remember("filename_template", self.template_field.value.strip() or DEFAULT_FILENAME_TEMPLATE)
        self.app.remember("music_template", self.music_field.value.strip())
        self.refresh_template_preview()
        self.page.update()

    # Background downloads

    def background_card(self):
        app = self.app
        if app.android:
            return app.section(
                "Notifications",
                "When a download finishes",
                self.bind(ft.Switch(label="Notify when a download finishes",
                                    value=bool(app.settings.get("notify", True)),
                                    on_change=lambda e: app.remember("notify", bool(e.control.value))),
                          "notify", True),
                self.alert_switch("Sound or vibration when a download finishes"),
                self.lists_switch(),
            )
        if not app.desktop:
            return app.section(
                "Background downloads",
                "Notifications, clipboard",
            )
        notify_switch = self.bind(ft.Switch(
            label="Notify when a download finishes",
            value=bool(app.settings.get("notify", True)),
            on_change=lambda e: app.remember("notify", bool(e.control.value)),
        ), "notify", True)
        queue_switch = ft.Switch(value=bool(app.settings.get("clipboard_queue", False)),
                                 disabled=not app.settings.get("watch_clipboard", False),
                                 on_change=lambda e: app.remember("clipboard_queue", bool(e.control.value)))

        def on_watch(e):
            app.remember("watch_clipboard", bool(e.control.value))
            queue_switch.disabled = not e.control.value
            self.page.update()

        clipboard_switch = column([
            setting_row("Analyze the links I copy",
                        self.bind(ft.Switch(value=bool(app.settings.get("watch_clipboard", False)), on_change=on_watch),
                                  "watch_clipboard", False)),
            setting_row("…and download them at once", queue_switch),
        ], spacing=XS)
        return app.section(
            "Background downloads",
            "Notifications, clipboard",
            notify_switch,
            self.alert_switch("Play a sound when a download finishes"),
            self.lists_switch(),
            clipboard_switch,
        )

    def alert_switch(self, label):
        app = self.app
        return self.bind(ft.Switch(label=label, value=bool(app.settings.get("alert", True)),
                                   on_change=lambda e: app.remember("alert", bool(e.control.value))), "alert", True)

    def lists_switch(self):
        app = self.app
        return self.bind(ft.Switch(label="Lists: one notification once the whole list is done",
                                   value=bool(app.settings.get("notify_lists_once", True)),
                                   on_change=lambda e: app.remember("notify_lists_once", bool(e.control.value))),
                         "notify_lists_once", True)

    # SponsorBlock

    def sponsorblock_card(self):
        app = self.app
        chosen = set(app.sponsorblock_categories())

        def toggle(key):
            def on_change(e):
                categories = [k for k in SPONSORBLOCK_CATEGORIES
                              if (k == key and e.control.value) or (k != key and k in app.sponsorblock_categories())]
                app.remember("sponsorblock", categories)
            return on_change

        boxes = [ft.Switch(label=label, value=key in chosen, on_change=toggle(key))
                 for key, label in SPONSORBLOCK_CATEGORIES.items()]
        return app.section(
            "SponsorBlock (YouTube)",
            "Parts cut out of YouTube videos",
            *boxes,
        )

    # Storage

    def storage_card(self):
        app = self.app
        self.clean_btn = ft.Button("Clean up", icon=ft.Icons.CLEANING_SERVICES_OUTLINED, on_click=self.clean_up)
        self.clean_msg = ft.Container(visible=False)
        days = app.settings.get("history_days")

        def on_keep(e):
            app.remember("history_days", int(e.control.value))
            if prune_history(int(e.control.value)):
                app.history.refresh()
        return app.section(
            "Storage",
            "Library, temporary files",
            ft.Row([ft.Dropdown(label="Keep the Library's downloads", expand=True,
                                value=str(days if days in HISTORY_KEEP else 0), on_select=on_keep,
                                options=[ft.DropdownOption(key=str(k), text=t) for k, t in HISTORY_KEEP.items()])]),
            ft.Row([self.clean_btn]),
            self.clean_msg,
        )

    async def clean_up(self, e=None):
        app = self.app
        if app.busy():
            set_status(self.clean_msg, "err", "Wait until the downloads and edits are finished.")
            self.page.update()
            return
        keep = list(app.queue.leftover_workdirs())
        for picker in [app.download.clip] + ([app.edit.clip] if app.edit is not None else []):
            if picker.preview_state["dir"]:
                keep.append(picker.preview_state["dir"])
        self.clean_btn.disabled = True
        self.page.update()
        try:
            thumbs, thumb_bytes = await asyncio.to_thread(clear_thumbnails)
            folders, folder_bytes = await asyncio.to_thread(clean_temporary_files, keep)
            set_status(self.clean_msg, "ok", "Freed %s: %d thumbnail%s and %d temporary folder%s." % (
                format_bytes(thumb_bytes + folder_bytes), thumbs, "" if thumbs == 1 else "s",
                folders, "" if folders == 1 else "s"))
            app.history.refresh()
        except OSError as exc:
            set_status(self.clean_msg, "err", "Could not clean up: %s" % exc)
        finally:
            self.clean_btn.disabled = False
            self.page.update()

    # Backup

    def backup_card(self):
        self.backup_msg = ft.Container(visible=False)
        return self.app.section(
            "Back up the settings",
            "Copy them to another device",
            row([ft.Button("Export…", icon=ft.Icons.SAVE_ALT, on_click=self.export_settings),
                 ft.TextButton("Import…", icon=ft.Icons.UPLOAD_FILE, on_click=self.import_settings)], wrap=True),
            self.backup_msg,
        )

    async def export_settings(self, e=None):
        app = self.app
        data = export_settings(app.settings)
        try:
            if app.needs_bytes:
                saved = await app.picker.save_file(dialog_title="Export the settings", file_name=SETTINGS_FILE_NAME,
                                                   src_bytes=data)
                done = saved is not None or bool(self.page.web)
            else:
                saved = await app.picker.save_file(dialog_title="Export the settings", file_name=SETTINGS_FILE_NAME)
                done = bool(saved)
                if done:
                    await asyncio.to_thread(Path(saved).write_bytes, data)
            if done:
                set_status(self.backup_msg, "ok", "Settings exported%s." % (": %s" % saved if saved else ""))
        except Exception as exc:
            set_status(self.backup_msg, "err", "Could not export the settings: %s" % exc)
        self.page.update()

    async def import_settings(self, e=None):
        app = self.app
        try:
            files = await app.picker.pick_files(dialog_title="Choose the exported settings file",
                                                allow_multiple=False, with_data=self.page.web)
            if not files:
                return
            f = files[0]
            content = await asyncio.to_thread(Path(f.path).read_bytes) if f.path else bytes(f.bytes or b"")
            count = import_settings(content, app.settings)
            save_settings(app.settings)
            set_status(self.backup_msg, "ok", "Imported %d setting%s." % (count, "" if count == 1 else "s"))
            self.page.update()
            await app.offer_restart()
        except ValueError as exc:
            set_status(self.backup_msg, "err", str(exc))
        except Exception as exc:
            set_status(self.backup_msg, "err", "Could not read the file: %s" % exc)
        self.page.update()

    # Advanced

    def advanced_card(self):
        app = self.app
        codec_dd = ft.Dropdown(
            label="Video codec", value=CODEC["preference"], expand=True,
            options=[ft.DropdownOption(key=k, text=t) for k, t in CODEC_CHOICES.items()],
        )

        def on_codec(e):
            CODEC["preference"] = codec_dd.value
            app.remember("codec", codec_dd.value)

        codec_dd.on_select = on_codec
        return app.section(
            "Video codec",
            ("Leave on Automatic unless a video does not play",
             "Maximum compatibility: H.264 + AAC, for old devices. HEVC, AV1, VP9: smaller files that some "
             "devices cannot play."),
            ft.Row([codec_dd]),
        )

    # Cookies

    def cookies_card(self):
        app = self.app
        browser_row = []
        if app.desktop:
            cookies_dd = ft.Dropdown(
                label="Browser cookies (desktop)",
                value=app.cookies_browser(),
                options=[ft.DropdownOption(key="none", text="None")]
                + [ft.DropdownOption(key=k, text=k.capitalize()) for k in app.cookies_browsers()],
                expand=True,
            )
            cookies_dd.on_select = lambda e: app.remember("cookies_browser", cookies_dd.value)
            browser_row = [ft.Row([self.bind(cookies_dd, "cookies_browser", app.cookies_browser())])]
        self.cookies_info = ft.Text("", size=SMALL_SIZE, selectable=True)
        self.cookies_msg = ft.Container(visible=False)
        import_btn = ft.Button("Import cookies.txt", icon=ft.Icons.UPLOAD_FILE)
        self.cookies_remove_btn = ft.TextButton("Remove", icon=ft.Icons.DELETE_OUTLINE, visible=False)
        self.cookies_test_btn = ft.TextButton("Test cookies", icon=ft.Icons.FACT_CHECK_OUTLINED)
        import_btn.on_click = self.pick_cookies
        self.cookies_remove_btn.on_click = self.clear_cookies
        self.cookies_test_btn.on_click = self.test_cookies
        self.refresh_cookies()
        return app.section(
            "Cookies (optional)",
            "For videos that need a login",
            self.cookies_info,
            row([import_btn, self.cookies_test_btn, self.cookies_remove_btn], wrap=True),
            self.cookies_msg,
            *browser_row,
        )

    def refresh_cookies(self):
        path = saved_cookies_path()
        if path is None:
            self.cookies_info.value = "No cookies file imported."
            self.cookies_info.color = ft.Colors.ON_SURFACE_VARIANT
            self.cookies_remove_btn.visible = False
            return
        try:
            self.cookies_info.value = "Cookies file saved in the app: " + describe_cookies(path)
            self.cookies_info.color = None
        except Exception as exc:
            self.cookies_info.value = "The saved cookies file is unreadable: %s" % clean_error(exc)
            self.cookies_info.color = ft.Colors.ERROR
        self.cookies_remove_btn.visible = True

    async def pick_cookies(self, e=None):
        try:
            files = await self.app.picker.pick_files(
                dialog_title="Choose your cookies.txt file", allow_multiple=False, with_data=self.page.web
            )
            if not files:
                return
            f = files[0]
            content = Path(f.path).read_bytes() if f.path else bytes(f.bytes or b"")
            summary = await asyncio.to_thread(import_cookies, content)
            self.refresh_cookies()
            set_status(self.cookies_msg, "ok", "Imported: %s. The original file can be deleted." % summary)
        except ValueError as exc:
            set_status(self.cookies_msg, "err", str(exc))
        except Exception as exc:
            set_status(self.cookies_msg, "err", "Could not read the file: %s" % exc)
        self.page.update()

    def clear_cookies(self, e=None):
        remove_cookies()
        self.refresh_cookies()
        hide_status(self.cookies_msg)
        self.page.update()

    async def test_cookies(self, e=None):
        self.cookies_test_btn.disabled = True
        set_status(self.cookies_msg, "loading", "Testing the cookies…")
        self.page.update()
        try:
            results = await asyncio.to_thread(check_cookies, self.app.cookies_value(), saved_cookies_path())
            colors = {"ok": ft.Colors.GREEN_600, "err": ft.Colors.ERROR}
            self.cookies_msg.content = ft.Column(
                [ft.Text(text, size=SMALL_SIZE, color=colors.get(kind), selectable=True) for kind, text in results],
                spacing=XS, tight=True)
            self.cookies_msg.visible = True
        except Exception as exc:
            message = clean_error(exc)
            set_status(self.cookies_msg, "err", "The test failed: %s" % message,
                       make_hint(message, "https://www.youtube.com/", self.app.desktop))
        finally:
            self.cookies_test_btn.disabled = False
            self.page.update()

    # Network

    def network_card(self):
        app = self.app
        self.retries_dd = ft.Dropdown(
            label="Automatic retries",
            value=str(NETWORK["retries"]),
            options=[ft.DropdownOption(key=str(n), text="Off" if n == 0 else "%d %s" % (n, "retry" if n == 1 else "retries"))
                     for n in RETRY_CHOICES],
            expand=True,
        )
        restarts = app.settings.get("restarts")
        restarts_dd = ft.Dropdown(
            label="Queue: start again after a connection loss",
            value=str(restarts if restarts in RESTART_CHOICES else DEFAULT_RESTARTS),
            options=[ft.DropdownOption(key=str(n), text="Never" if n == 0 else "Up to %d time%s" % (n, "s" if n > 1 else ""))
                     for n in RESTART_CHOICES],
            expand=True,
            on_select=lambda e: app.remember("restarts", int(e.control.value)),
        )
        self.speed_field = ft.TextField(
            label="Speed limit (MB/s)",
            hint_text="Unlimited",
            value="%g" % app.settings["speed_limit"] if isinstance(app.settings.get("speed_limit"), (int, float)) else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            expand=True,
        )
        self.speed_msg = ft.Text("", size=SMALL_SIZE, color=ft.Colors.ERROR, visible=False)
        self.proxy_field = ft.TextField(
            label="Proxy", hint_text="None (for example socks5://host:1080)", value=app.settings.get("proxy", ""),
            autocorrect=False, enable_suggestions=False, expand=True,
        )
        self.proxy_msg = ft.Text("", size=SMALL_SIZE, color=ft.Colors.ERROR, visible=False)
        self.proxy_field.on_change = self.on_proxy_change
        extra = []
        if app.mobile:
            extra.append(ft.Switch(
                label="Playlists only on Wi-Fi",
                value=bool(app.settings.get("wifi_only", False)),
                on_change=lambda e: app.remember("wifi_only", bool(e.control.value)),
            ))
        parallel_dd = self.limit_dropdown("Parallel downloads (max)", app.download_slots, "parallel")
        conversions_dd = self.limit_dropdown("Parallel conversions (max)", app.conversion_slots, "parallel_conversions")
        self.retries_dd.on_select = self.on_retries_change
        self.speed_field.on_change = self.on_speed_change
        return app.section(
            "Network",
            "Speed, retries, proxy",
            row([parallel_dd, conversions_dd]),
            row([self.retries_dd, restarts_dd]) if app.desktop else ft.Row([self.retries_dd]),
            *([] if app.desktop else [ft.Row([restarts_dd])]),
            ft.Row([self.speed_field]),
            self.speed_msg,
            ft.Row([self.proxy_field]),
            self.proxy_msg,
            *extra,
        )

    def limit_dropdown(self, label, limiter, setting_key):
        def on_select(e):
            self.app.remember(setting_key, int(e.control.value))
            limiter.set_limit(int(e.control.value))
            self.app.queue.refresh()
            self.page.update()

        return ft.Dropdown(
            label=label,
            value=str(limiter.limit),
            options=[ft.DropdownOption(key=str(n), text="1 (one at a time)" if n == 1 else str(n))
                     for n in PARALLEL_CHOICES],
            expand=True,
            on_select=on_select,
        )

    def on_retries_change(self, e):
        NETWORK["retries"] = int(self.retries_dd.value)
        self.app.remember("retries", NETWORK["retries"])

    def on_proxy_change(self, e):
        try:
            proxy = parse_proxy(self.proxy_field.value)
        except ValueError as exc:
            self.proxy_msg.value = str(exc)
            self.proxy_msg.visible = True
            self.page.update()
            return
        self.proxy_msg.visible = False
        NETWORK["proxy"] = proxy
        self.app.remember("proxy", proxy)
        self.page.update()

    def on_speed_change(self, e):
        try:
            limit = parse_speed_limit(self.speed_field.value)
        except ValueError as exc:
            self.speed_msg.value = str(exc)
            self.speed_msg.visible = True
            self.page.update()
            return
        self.speed_msg.visible = False
        NETWORK["ratelimit"] = speed_limit_bytes(limit)
        if limit:
            self.app.remember("speed_limit", limit)
        else:
            self.app.settings.pop("speed_limit", None)
            save_settings(self.app.settings)
        self.page.update()

    # Playlists

    def playlists_card(self):
        sites = [site for site in PLAYLIST_SITES if site != "tiktok" or HAS_IMPERSONATION]
        app = self.app
        return self.app.section(
            "Playlists",
            "How many videos, pause between them",
            ft.Switch(label="Playlist file (.m3u8) for lists", value=bool(app.settings.get("m3u", True)),
                      on_change=lambda e: app.remember("m3u", bool(e.control.value))),
            *[ft.Row([self.playlist_limit_dropdown(site)]) for site in sites],
            ft.Row([ft.Dropdown(
                label="Pause between videos", expand=True,
                value=str(self.app.settings.get("playlist_pause") or 0),
                options=[ft.DropdownOption(key=str(n), text="%d seconds" % n if n else "None")
                         for n in PLAYLIST_PAUSES],
                on_select=lambda e: self.app.remember("playlist_pause", int(e.control.value)))]),
        )

    def playlist_limit_dropdown(self, site):
        settings = self.app.settings

        def on_select(e):
            limits = settings.get("playlist_limits")
            if not isinstance(limits, dict):
                limits = settings["playlist_limits"] = {}
            limits[site] = int(e.control.value)
            save_settings(settings)

        return ft.Dropdown(
            label=PLAYLIST_SITES[site],
            value=str(self.app.playlist_limit(site)),
            options=[ft.DropdownOption(key=str(n), text="%d videos" % n if n else "No limit (can be slow)")
                     for n in PLAYLIST_LIMIT_CHOICES],
            expand=True,
            on_select=on_select,
        )

    # yt-dlp

    def ytdlp_card(self):
        app = self.app
        yt_info = ft.Text("Running yt-dlp %s (%s)." % (YTDLP_VERSION, "updated" if YTDLP_UPDATE_ACTIVE else "bundled"),
                          size=SMALL_SIZE)
        self.yt_msg = ft.Container(visible=False)
        self.yt_update_btn = ft.Button("Update yt-dlp", icon=ft.Icons.REFRESH)
        check_switch = ft.Switch(
            label="Check for updates at startup",
            value=bool(app.settings.get("ytdlp_check", True)),
            on_change=lambda e: app.remember("ytdlp_check", bool(e.control.value)),
        )
        auto_switch = ft.Switch(
            label="Download updates automatically",
            value=bool(app.settings.get("ytdlp_auto_install", False)),
            on_change=lambda e: app.remember("ytdlp_auto_install", bool(e.control.value)),
        )
        self.yt_reset_btn = ft.TextButton("Use bundled version", icon=ft.Icons.UNDO, visible=YTDLP_UPDATE_ACTIVE)
        self.yt_update_btn.on_click = self.update_ytdlp_click
        self.yt_reset_btn.on_click = self.reset_ytdlp_click
        return app.section(
            "yt-dlp",
            ("Keeps downloads working", "An update is used after a restart."),
            yt_info,
            check_switch,
            auto_switch,
            row([self.yt_update_btn, self.yt_reset_btn], wrap=True),
            self.yt_msg,
        )

    async def update_ytdlp_click(self, e=None):
        self.yt_update_btn.disabled = True
        set_status(self.yt_msg, "loading", "Downloading the latest yt-dlp…")
        self.page.update()
        try:
            known = self.pending_ytdlp or YTDLP_VERSION
            version, installed = await asyncio.to_thread(update_ytdlp, known)
            if installed:
                self.pending_ytdlp = version
                self.yt_reset_btn.visible = True
                set_status(self.yt_msg, "ok", "yt-dlp %s downloaded." % version)
                self.page.update()
                await self.app.offer_restart(ask=e is not None)  # not asked when downloaded on its own
            else:
                set_status(self.yt_msg, "ok", "yt-dlp is already up to date (%s)." % known)
        except Exception as exc:
            set_status(self.yt_msg, "err", "Update failed: %s" % clean_error(exc))
        finally:
            self.yt_update_btn.disabled = False
            self.page.update()

    async def startup_ytdlp_check(self):
        settings = self.app.settings
        if not settings.get("ytdlp_check", True):
            return
        current = self.pending_ytdlp or YTDLP_VERSION
        latest = settings.get("ytdlp_latest")
        checked_at = settings.get("ytdlp_checked_at")
        due = not isinstance(checked_at, (int, float)) or time.time() - checked_at >= YTDLP_CHECK_INTERVAL
        if due:
            try:
                latest = await asyncio.to_thread(latest_ytdlp_version)
            except Exception:
                return
            settings["ytdlp_latest"] = latest
            settings["ytdlp_checked_at"] = time.time()
            save_settings(settings)
        if not ytdlp_is_newer(latest, current):
            return
        if settings.get("ytdlp_auto_install", False):
            await self.update_ytdlp_click()
            return
        message = "yt-dlp %s is available (running %s). Update it in Settings." % (latest, current)
        self.set_status_note(message)
        set_status(self.yt_msg, "info", message)
        self.page.update()

    async def reset_ytdlp_click(self, e=None):
        remove_ytdlp_update()
        self.pending_ytdlp = None
        self.yt_reset_btn.visible = False
        self.page.update()
        await self.app.offer_restart()
