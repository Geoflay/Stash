"""The Montage tool (Home): passages of several videos (a link, one video at a time, or a file of the device; also
added from a download with "Add to the montage") put one after the other in a single file, made in the queue (see
core/montage.py). Each passage's start and end can be typed, the passages dragged to change their order. They are
kept in the settings until the montage is made or cleared."""

import asyncio

import flet as ft

from core.analyze import fetch_info
from core.backend import AUDIO_FORMATS, VIDEO_FORMATS
from core.cookies import saved_cookies_path
from core.editor import EDIT_AUDIO_FORMATS, MEDIA_EXTENSIONS
from core.errors import clean_error
from core.links import all_links, as_link, is_playlist_url, is_web_link
from core.units import format_duration, parse_time
from ui.i18n import translate
from ui.queue_tab import link_params
from ui.style import S, TOUCH, XS, caption, row
from ui.widgets import hide_status, set_status

MONTAGE_FORMATS = {**VIDEO_FORMATS, **{key: AUDIO_FORMATS[key] for key in EDIT_AUDIO_FORMATS}}
MONTAGE_QUALITIES = {"best": "Best available", "1080": "1080p", "720": "720p", "480": "480p"}


def part_text(part):
    start, end = part["clip"] or (0, None)
    return "%s – %s" % (format_duration(start) or "0:00", format_duration(end) if end is not None else translate("the end"))


class MontageCard:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        saved = app.settings.get("montage")
        self.parts = [p for p in saved if isinstance(p, dict) and (p.get("url") or p.get("path"))] \
            if isinstance(saved, list) else []
        self.link_field = ft.TextField(hint_text="A video link (one video, not a playlist)", expand=True,
                                       keyboard_type=ft.KeyboardType.URL, on_submit=self.add_link)
        self.add_status = ft.Container(visible=False)
        self.list = ft.ReorderableListView([], on_reorder=self.on_reorder)
        self.empty = caption("No passage yet: add a link or a file.")
        self.format_dd = ft.Dropdown(label="Format", value="mp4", expand=True,
                                     options=[ft.DropdownOption(key=k, text=t) for k, t in MONTAGE_FORMATS.items()])
        self.quality_dd = ft.Dropdown(label="Quality", value="720", expand=True,
                                      options=[ft.DropdownOption(key=k, text=t) for k, t in MONTAGE_QUALITIES.items()])
        self.make_btn = ft.FilledButton("Make the montage", icon=ft.Icons.MOVIE_CREATION_OUTLINED, height=TOUCH,
                                        on_click=self.make)
        self.status = ft.Container(visible=False)
        file_btn = ft.TextButton("Add a file…", icon=ft.Icons.VIDEO_FILE_OUTLINED, on_click=self.add_files,
                                 visible=not self.page.web)
        self.card = app.stretch_card([
            caption("The passages one after the other, in one file: drag them to change their order."),
            row([self.link_field, ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=self.add_link)]),
            ft.Row([file_btn]), self.add_status,
            self.empty, self.list,
            row([self.format_dd, self.quality_dd]),
            ft.Row([self.make_btn]),
            self.status])
        self.refresh()

    def save(self):
        self.app.remember("montage", self.parts)
        self.refresh()
        self.page.update()

    def refresh(self):
        self.list.controls = [self.make_row(index, part) for index, part in enumerate(self.parts)]
        self.empty.visible = not self.parts
        self.make_btn.disabled = not self.parts

    def make_row(self, index, part):
        start, end = part["clip"] or (None, None)
        start_field = ft.TextField(label="From", hint_text="0:00", width=96, dense=True,
                                   value=format_duration(start) if start else "")
        end_field = ft.TextField(label="To", hint_text="The end", width=96, dense=True,
                                 value=format_duration(end) if end is not None else "")
        start_field.on_blur = end_field.on_blur = lambda e: self.set_clip(part, start_field, end_field)
        start_field.on_submit = end_field.on_submit = start_field.on_blur
        icon = ft.Icons.VIDEO_FILE_OUTLINED if part.get("path") else ft.Icons.LINK
        return ft.Container(row([
            ft.Text("%d." % (index + 1), width=28),
            ft.Icon(icon, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Column([ft.Text(part["title"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                               tooltip=part.get("path") or part.get("url")),
                       caption(" · ".join(x for x in (part_text(part), format_duration(part.get("duration")))
                                          if x))],
                      spacing=0, tight=True, expand=True),
            start_field, end_field,
            ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Leave it out", on_click=lambda e, i=index: self.remove(i))],
            spacing=XS), padding=ft.Padding(left=0, right=S if self.app.mobile else 40, top=XS, bottom=XS))

    def set_clip(self, part, start_field, end_field):
        """The passage typed: from `start_field` to `end_field` (empty: the start, the end)."""
        try:
            start, end = parse_time(start_field.value) or 0.0, parse_time(end_field.value)
            if end is not None and end <= start:
                raise ValueError("A passage must end after it starts.")
        except ValueError as exc:
            set_status(self.status, "err", str(exc))
            self.page.update()
            return
        clip = [start, end] if start or end is not None else None
        if clip != part["clip"]:
            part["clip"] = clip
            hide_status(self.status)
            self.save()

    # Adding passages

    def add(self, url, title, clips, video):
        """Passages of a video (`clips`: [(start, end)], or [] for all of it)."""
        for clip in clips or [None]:
            self.parts.append({"url": url, "title": title, "clip": list(clip) if clip else None})
        if not video and self.format_dd.value in VIDEO_FORMATS:
            self.format_dd.value = "mp3"  # sound only: the montage too
        self.save()

    async def add_link(self, e=None):
        """One video's link: added at once, its title read meanwhile."""
        text = (self.link_field.value or "").strip()
        links = all_links(text)
        url = links[0] if links else as_link(text)
        if len(links) > 1 or not url or not is_web_link(url):
            set_status(self.add_status, "err", "One video link at a time." if len(links) > 1 else
                       "Paste the link of a video.")
            self.page.update()
            return
        if is_playlist_url(url):
            set_status(self.add_status, "err", "Only one video: a playlist cannot be added.")
            self.page.update()
            return
        part = {"url": url, "title": url, "clip": None}
        self.parts.append(part)
        self.link_field.value = ""
        set_status(self.add_status, "loading", "Reading its title…")
        self.save()
        try:
            data = await asyncio.to_thread(fetch_info, url, self.app.cookies_value(), cookie_file=saved_cookies_path())
        except Exception as exc:
            set_status(self.add_status, "warn", "Its title could not be read (%s): it is kept." % clean_error(exc))
            self.page.update()
            return
        if data.get("playlist"):
            if part in self.parts:
                self.parts.remove(part)
            set_status(self.add_status, "err", "Only one video: a playlist cannot be added.")
        else:
            part.update(title=data.get("title") or url, duration=data.get("duration"))
            hide_status(self.add_status)
        self.save()

    async def add_files(self, e=None):
        files = await self.app.picker.pick_files(dialog_title="Choose video or audio files", allow_multiple=True,
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=MEDIA_EXTENSIONS)
        for file in files or []:
            if file.path:
                self.parts.append({"path": file.path, "title": file.name, "clip": None})
        hide_status(self.add_status)
        self.save()

    def on_reorder(self, e):
        if 0 <= e.old_index < len(self.parts) and 0 <= e.new_index < len(self.parts):
            self.parts.insert(e.new_index, self.parts.pop(e.old_index))
            self.save()

    def remove(self, index):
        part = self.parts.pop(index)
        self.save()

        def undo():
            self.parts.insert(index, part)
            self.save()
        self.app.offer_undo("Passage left out of the montage.", undo)

    def clear(self):
        """The page's Clear button."""
        self.parts = []
        hide_status(self.status)
        hide_status(self.add_status)
        self.save()

    async def make(self, e=None):
        if not self.parts:
            return
        fmt, quality = self.format_dd.value, self.quality_dd.value
        first = next((part["url"] for part in self.parts if part.get("url")), "")
        params = link_params(first, fmt, quality, "Montage")
        params.update(title="Montage (%d passages)" % len(self.parts), montage=list(self.parts),
                      choice={"montage": list(self.parts), "fmt": fmt, "quality": quality})
        set_status(self.status, "info", "Added to the queue: follow it in the Queue tab.")
        self.page.update()
        await self.app.queue.run_download(params)
