"""The Download tab's list of several videos: a playlist, a channel, an album, a podcast's episodes, or several
links pasted together.

The options at the top are those of every item: format, picture, sound, subtitles, size limit, tags. Each item
can be previewed in its row, and given its own options in the Download tab's editor (the same as for one video:
DownloadTab.edit_item). Every item checked goes to the queue. The items can be sorted, those already in the
Library hidden, and the list followed (subscriptions.py)."""

import asyncio
import uuid

import flet as ft

from core.analyze import fetch_info
from core.backend import AUDIO_FORMATS, BACKEND, VIDEO_FORMATS, format_available
from core.cookies import saved_cookies_path
from core.editor import DEFAULT_SUBTITLE_STYLE, can_burn_subtitles
from core.errors import clean_error
from core.history import load_history
from core.links import supported_site
from core.network import call_with_retries
from core.playlist import ITEM_ORDERS, sort_entries
from core.preview import player_choice, player_resource
from core.streams import estimate_playlist, valid_quality
from core.tags import LABELS
from core.units import format_bytes, format_duration, parse_size_mb
from ui.edit_options import CROP_CHOICES, TURN_CHOICES, choice_dropdown, picture_summary
from ui.player import Preview
from ui.queue_tab import link_params
from ui.style import HEADING_SIZE, RADIUS, S, SMALL_SIZE, THUMB_HEIGHT, THUMB_WIDTH, TOUCH, XS, caption, column, row
from ui.widgets import MultiPicker, OptionGroup, ask, hide_status, set_status, thumb_tile

QUALITIES = [("best", "Best available"), ("1080", "1080p"), ("720", "720p"), ("480", "480p"), ("360", "360p")]
# Subtitles of every item: the languages, each item's own (else YouTube's automatic ones) when it has them.
SUBTITLE_LANGUAGES = [("en", "English"), ("fr", "French"), ("es", "Spanish"), ("de", "German"), ("it", "Italian"),
                      ("pt", "Portuguese"), ("nl", "Dutch"), ("pl", "Polish"), ("ru", "Russian"), ("tr", "Turkish"),
                      ("ar", "Arabic"), ("hi", "Hindi"), ("ja", "Japanese"), ("ko", "Korean"),
                      ("zh-Hans", "Chinese")]
SUBTITLE_MODES = {"embed": "Embedded in the video", "file": "Separate files (.srt)",
                  "txt": "Transcripts (.txt)", "html": "Transcripts (.html)"}
COMMON_TAGS = ("artist", "album", "genre")
FIRST_ITEMS = 10  # "First 10"


async def entry_params(entry, data, options):
    """What the queue gets for an item of the list `data` with the options of every item (ItemList.options):
    also used for the new items of a subscription."""
    podcast = bool(data.get("podcast"))
    platform = "Podcast" if podcast else await asyncio.to_thread(supported_site, entry["url"]) or ""
    quality = options["quality"] if valid_quality(options["quality"]) else "best"
    params = link_params(entry["url"], options["fmt"], quality, platform)
    sub_keys = [] if "burn" in options["edits"] else list(options["sub_keys"])
    params.update(
        title=entry["title"],
        quick=entry["title"] == entry["url"],  # a link pasted: its title once yt-dlp reads it
        thumbnail=entry.get("thumbnail") or "", uploader=data.get("uploader") or "",
        upload_date=entry.get("date") or "", max_mb=options["max_mb"], no_sound=options["no_sound"],
        edits=options["edits"] or None, sub_keys=sub_keys, sub_key=sub_keys[0] if sub_keys else "",
        sub_mode=options["sub_mode"], cover_url=entry.get("cover"),
        tag_edits=dict(entry.get("tags") or {}, **options["tags"]) or None)
    params["choice"].update(max_mb=options["max_mb"], edits=options["edits"] or None)
    return params


class ItemList:
    """`tab`: the Download tab, whose editor gives an item its own options."""

    def __init__(self, app, tab):
        self.app = app
        self.tab = tab
        self.page = page = app.page
        settings = app.settings
        self.data = None
        self.items = []  # {"entry", "check", "params" (own options, or None), "snapshot", "info", …}
        self.previewing = None  # the item whose preview plays
        self.preview = Preview(app)
        self.title = ft.Text("", size=HEADING_SIZE, weight=ft.FontWeight.W_600, selectable=True)
        self.meta = caption()

        # Options of every item
        self.format_choices = {kind: [(k, t) for k, t in table.items() if format_available(k)]
                               for kind, table in (("video", VIDEO_FORMATS), ("audio", AUDIO_FORMATS))}
        self.kind_dd = ft.Dropdown(label="Type", expand=True, on_select=self.on_kind,
                                   value="audio" if settings.get("pl_kind") == "audio" else "video",
                                   options=[ft.DropdownOption(key="video", text="Video"),
                                            ft.DropdownOption(key="audio", text="Audio")])
        self.format_dd = ft.Dropdown(label="Format", expand=True, on_select=self.on_format)
        self.quality_dd = ft.Dropdown(
            label="Quality", expand=True, on_select=self.on_quality,
            value=settings.get("pl_quality") if settings.get("pl_quality") in dict(QUALITIES) else "best",
            options=[ft.DropdownOption(key=k, text=t) for k, t in QUALITIES])
        can_edit = BACKEND == "ffmpeg"
        self.crop_dd = choice_dropdown("Crop", CROP_CHOICES, self.on_change)
        self.turn_dd = choice_dropdown("Turn", TURN_CHOICES, self.on_change)
        self.picture_box = row([self.crop_dd, self.turn_dd], visible=can_edit)
        self.no_sound = ft.Switch(label="No sound", value=False, on_change=self.on_change)
        self.loudness = ft.Switch(label="Even out the volume", value=False, on_change=self.on_change, visible=can_edit)
        self.no_silence = ft.Switch(label="Cut out the silences", value=False, on_change=self.on_change,
                                    visible=can_edit)
        self.subs_picker = MultiPicker("Add subtitles", self.on_change)
        self.subs_picker.set([("x:" + lang, name) for lang, name in SUBTITLE_LANGUAGES], self.preferred_subtitles())
        modes = dict(SUBTITLE_MODES, **({"burn": "Drawn onto the picture"} if can_edit and can_burn_subtitles() else {}))
        self.subs_mode_dd = ft.Dropdown(label="Save subtitles as", value="embed" if can_edit else "file",
                                        expand=True, on_select=self.on_change,
                                        options=[ft.DropdownOption(key=k, text=t) for k, t in modes.items()])
        self.subs_box = column([caption("When an item has them (else YouTube's automatic ones)"),
                                self.subs_picker.box, ft.Row([self.subs_mode_dd])], spacing=XS)
        self.size_field = ft.TextField(label="Maximum size of each file (MB)", hint_text="No limit", expand=True,
                                       keyboard_type=ft.KeyboardType.NUMBER, on_change=self.on_change)
        self.size_msg = caption(color=ft.Colors.ERROR, visible=False)
        self.tag_fields = {key: ft.TextField(label=LABELS.get(key, key.capitalize()), expand=True,
                                             on_blur=self.on_change) for key in COMMON_TAGS}
        self.groups = {
            "picture": OptionGroup("Picture", ft.Icons.CROP_ROTATE, [self.picture_box], page),
            "sound": OptionGroup("Sound", ft.Icons.GRAPHIC_EQ, [self.no_sound, self.loudness, self.no_silence], page),
            "subs": OptionGroup("Subtitles", ft.Icons.SUBTITLES_OUTLINED, [self.subs_box], page),
            "size": OptionGroup("Size limit", ft.Icons.COMPRESS, [ft.Row([self.size_field]), self.size_msg], page),
            "tags": OptionGroup("Tags", ft.Icons.LABEL_OUTLINE,
                                [caption("Written into every file (empty: each one's own)"),
                                 row([field for field in self.tag_fields.values()])], page),
        }
        self.subfolder = ft.Switch(label="In a folder named after the list", value=bool(settings.get("pl_subfolder", True)),
                                   on_change=lambda e: app.remember("pl_subfolder", bool(e.control.value)))
        options = ft.Container(
            content=column([caption("For every item (an item's own options win)"),
                            *([row([self.kind_dd, self.format_dd]), ft.Row([self.quality_dd])] if app.needs_bytes
                              else [row([self.kind_dd, self.format_dd, self.quality_dd])]),
                            *[group.box for group in self.groups.values()], self.subfolder], spacing=S),
            padding=S, border_radius=RADIUS, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT))

        self.count = ft.Text("", size=SMALL_SIZE, weight=ft.FontWeight.W_500)
        self.list = column([], spacing=S)
        self.order_dd = ft.Dropdown(label="Order", value="", expand=True, on_select=self.on_order,
                                    options=[ft.DropdownOption(key=k, text=t) for k, t in ITEM_ORDERS.items()])
        self.hide_done = ft.Switch(label="Hide the ones already downloaded", value=bool(settings.get("pl_hide_done")),
                                   on_change=self.on_hide_done)
        self.download_btn = ft.FilledButton("Download the selection", icon=ft.Icons.DOWNLOAD, height=TOUCH,
                                            on_click=self.download)
        self.subscribe_btn = ft.TextButton("Subscribe", icon=ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED,
                                           tooltip="Download its new items on their own from now on",
                                           on_click=self.toggle_subscription)
        self.status = ft.Container(visible=False)
        self.card = app.stretch_card(
            [self.title, self.meta, options,
             row([self.order_dd, self.hide_done]) if not app.needs_bytes
             else column([ft.Row([self.order_dd]), self.hide_done], spacing=XS),
             row([ft.TextButton("Select all", on_click=lambda e: self.select(True)),
                  ft.TextButton("First %d" % FIRST_ITEMS, on_click=lambda e: self.select_first()),
                  ft.TextButton("Select none", on_click=lambda e: self.select(False)), self.count], wrap=True),
             self.list, row([self.download_btn, self.subscribe_btn], wrap=True), self.status],
            visible=False)
        self.show_kind()

    # Options of every item

    def preferred_subtitles(self):
        """The subtitle languages of Settings > Downloads > Languages, as this list's keys."""
        langs = self.app.settings.get("pref_subs")
        return ["x:" + lang for lang in langs] if isinstance(langs, list) else []

    def show_kind(self):
        kind = self.kind_dd.value
        choices = self.format_choices[kind]
        self.format_dd.options = [ft.DropdownOption(key=k, text=t) for k, t in choices]
        remembered = self.app.settings.get("pl_%s_format" % kind)
        self.format_dd.value = remembered if remembered in dict(choices) else choices[0][0]
        self.refresh()

    def on_kind(self, e=None):
        self.app.remember("pl_kind", self.kind_dd.value)
        self.show_kind()
        self.page.update()

    def on_format(self, e=None):
        self.app.remember("pl_%s_format" % self.kind_dd.value, self.format_dd.value)
        self.on_change()

    def on_quality(self, e=None):
        self.app.remember("pl_quality", self.quality_dd.value)
        self.on_change()

    def on_change(self, e=None):
        self.refresh()
        self.page.update()

    def max_mb(self):
        """The size limit typed, in MB (None: none); ValueError if it is not a number."""
        return parse_size_mb(self.size_field.value)

    def options(self):
        """The options of every item: {"kind", "fmt", "quality", "max_mb", "no_sound", "edits", "sub_keys",
        "sub_mode", "tags"}; ValueError with a message if one is wrong."""
        video = self.kind_dd.value == "video"
        edits = {"loudness": self.loudness.visible and self.loudness.value,
                 "no_silence": self.no_silence.visible and self.no_silence.value}
        if video and self.picture_box.visible:
            edits.update(crop=self.crop_dd.value, turn=self.turn_dd.value)
        sub_keys = self.subs_picker.value if video else []
        sub_mode = self.subs_mode_dd.value
        if sub_keys and sub_mode == "burn":
            edits["burn"] = {"key": sub_keys[0], "style": dict(DEFAULT_SUBTITLE_STYLE)}
        return {"kind": self.kind_dd.value, "fmt": self.format_dd.value,
                "quality": self.quality_dd.value if video else "best", "max_mb": self.max_mb(),
                "no_sound": video and bool(self.no_sound.value), "edits": {k: v for k, v in edits.items() if v},
                "sub_keys": sub_keys, "sub_mode": sub_mode,
                "tags": {k: (f.value or "").strip() for k, f in self.tag_fields.items() if (f.value or "").strip()}}

    def refresh(self):
        video = self.kind_dd.value == "video"
        self.quality_dd.visible = video
        self.picture_box.visible = video and BACKEND == "ffmpeg"
        self.no_sound.visible = video and BACKEND == "ffmpeg"
        self.subs_box.visible = video
        try:
            limit = self.max_mb()
            self.size_msg.visible = False
        except ValueError as exc:
            limit = None
            self.size_msg.value, self.size_msg.visible = str(exc), True
        sound = [text for switch, text in ((self.no_sound, "no sound"), (self.loudness, "volume evened out"),
                                           (self.no_silence, "no silences")) if switch.visible and switch.value]
        for key, summary in (("picture", picture_summary(self.crop_dd.value, self.turn_dd.value)),
                             ("sound", ", ".join(sound)),
                             ("subs", ", ".join(self.subs_picker.text(k) for k in self.subs_picker.value)),
                             ("size", "%g MB" % limit if limit else ""),
                             ("tags", ", ".join(f.value.strip() for f in self.tag_fields.values()
                                                if (f.value or "").strip()))):
            self.groups[key].refresh(summary)
        self.refresh_count()

    # The items

    def build(self, data, url):
        """Show the items of `data`: {"title", "uploader", "entries": [{"url", "title", "duration", "thumbnail"…}],
        "total", "limit", "podcast", "noun" (what the items are called)} (see fetch_playlist and fetch_feed)."""
        self.stop_preview()
        self.data, self.url = data, url
        podcast = bool(data.get("podcast"))
        self.title.value = data["title"]
        shown, total, limit = len(data["entries"]), data.get("total"), data.get("limit")
        noun = data.get("noun") or ("episodes" if podcast else "videos")
        if total is not None and total > shown:
            count = "%d %s, showing the first %d (limit set in Settings)" % (total, noun, shown)
        elif total is None and limit and shown >= limit:
            count = "first %d %s (limit set in Settings, there may be more)" % (shown, noun)
        else:
            count = "%d %s" % (shown, noun)
        self.meta.value = " · ".join(x for x in (data.get("uploader"), count) if x)
        if podcast:
            # Episodes are sound files, saved as they are (MP3 most often) unless chosen otherwise.
            self.kind_dd.value = "video" if all(entry.get("video") for entry in data["entries"]) else "audio"
            self.show_kind()
            if self.kind_dd.value == "audio":
                self.format_dd.value = "audio"
        self.subfolder.visible = len(data["entries"]) > 1
        self.subs_picker.set(self.subs_picker.options, self.preferred_subtitles())
        # A podcast's episodes are picked one by one: all of them could be gigabytes. The items already in the
        # Library are marked, and not checked.
        done = {entry.get("url") for entry in load_history()}
        self.items = [self.make_item(entry, not podcast and entry["url"] not in done, entry["url"] in done)
                      for entry in data["entries"]]
        self.order_dd.value = ""
        self.order_dd.visible = len(self.items) > 1
        self.hide_done.visible = bool(done & {entry["url"] for entry in data["entries"]})
        self.show_items()
        self.refresh_subscription()
        hide_status(self.status)
        self.refresh()

    def show_items(self):
        """The items in the order chosen, without those already downloaded when they are hidden."""
        order = sort_entries([item["entry"] for item in self.items], self.order_dd.value)
        place = {id(entry): i for i, entry in enumerate(order)}
        self.items.sort(key=lambda item: place[id(item["entry"])])
        hide = self.hide_done.visible and bool(self.hide_done.value)
        for item in self.items:
            item["box"].visible = not (hide and item["done"])
            if not item["box"].visible:
                item["check"].value = False
        self.list.controls = [item["box"] for item in self.items]

    def on_order(self, e=None):
        self.show_items()
        self.page.update()

    def on_hide_done(self, e=None):
        self.app.remember("pl_hide_done", bool(self.hide_done.value))
        self.show_items()
        self.refresh_count()
        self.page.update()

    def make_item(self, entry, checked, done=False):
        item = {"entry": entry, "params": None, "snapshot": None, "info": None, "done": done}
        item["check"] = ft.Checkbox(value=checked, on_change=lambda e: (self.refresh_count(), self.page.update()))
        item["own"] = caption(color=ft.Colors.PRIMARY, visible=False)
        item["reset"] = ft.IconButton(icon=ft.Icons.RESTART_ALT, tooltip="Back to the options of every item",
                                      visible=False, on_click=lambda e: self.reset(item))
        item["slot"] = column([], spacing=XS, visible=False)  # its preview, when it plays
        item["title"] = ft.Text(entry["title"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                weight=ft.FontWeight.W_500)
        item["thumb"] = ft.Container(thumb_tile(entry.get("thumbnail") or "", THUMB_WIDTH, THUMB_HEIGHT, RADIUS),
                                     tooltip="Tap to play it" if self.preview.available else None,
                                     on_click=(lambda e: self.page.run_task(self.toggle_preview, item))
                                     if self.preview.available else None)
        details = " · ".join(x for x in (
            "Episode %s" % entry["number"] if entry.get("number") else "",
            "%s-%s-%s" % (entry["date"][:4], entry["date"][4:6], entry["date"][6:]) if entry.get("date") else "",
            format_duration(entry.get("duration")) or "") if x)
        item["box"] = column([
            row([item["check"], item["thumb"],
                 ft.Column([item["title"], caption(details, visible=bool(details)),
                            caption("Already downloaded (Library)", color=ft.Colors.GREEN_700, visible=done),
                            item["own"],
                            self.description(entry.get("description"))],
                           spacing=XS, tight=True, expand=True),
                 item["reset"],
                 ft.IconButton(icon=ft.Icons.TUNE, tooltip="Options for this one",
                               on_click=lambda e: self.page.run_task(self.tab.edit_item, item))], spacing=S),
            item["slot"]], spacing=XS)
        return item

    def description(self, text):
        """An episode's description: two lines, all of it once tapped."""
        line = caption(text or "", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

        def toggle(e):
            line.max_lines = None if line.max_lines else 2
            self.page.update()
        return ft.Container(line, on_click=toggle, visible=bool(text), tooltip="Tap to read it all")

    def select(self, value):
        for item in self.items:
            item["check"].value = value and item["box"].visible
        self.refresh_count()
        self.page.update()

    def select_first(self):
        """Only the first items shown, in the order chosen."""
        shown = [item for item in self.items if item["box"].visible]
        for index, item in enumerate(shown):
            item["check"].value = index < FIRST_ITEMS
        self.refresh_count()
        self.page.update()

    def checked(self):
        return [item for item in self.items if item["check"].value]

    def refresh_count(self):
        picked = self.checked()
        self.count.value = "%d of %d selected" % (len(picked), len(self.items))
        general = (self.format_dd.value, self.quality_dd.value if self.kind_dd.value == "video" else "best")
        size, unknown = estimate_playlist(
            (item["entry"].get("duration"),) + ((item["params"]["fmt"], item["params"]["quality"])
                                                 if item["params"] else general) for item in picked)
        if size:
            self.count.value += " · about %s%s" % (format_bytes(size),
                                                   " (+%d of unknown length)" % unknown if unknown else "")
        self.download_btn.disabled = not picked

    def set_own(self, item, params, snapshot):
        """The options chosen for this item in the editor (DownloadTab.edit_item)."""
        item["params"], item["snapshot"] = params, snapshot
        item["own"].value = "Own options: %s" % params["fmt"].upper()
        item["own"].visible = item["reset"].visible = True
        item["check"].value = True
        self.refresh_count()

    def reset(self, item):
        item["params"] = item["snapshot"] = None
        item["own"].visible = item["reset"].visible = False
        self.refresh_count()
        self.page.update()

    async def info_of(self, item):
        """What yt-dlp reads of the item (its formats, subtitles, preview…), read once. A link pasted then
        shows its title and thumbnail."""
        entry = item["entry"]
        if item["info"] is None:
            item["info"] = await asyncio.to_thread(
                call_with_retries, lambda *a: None, fetch_info, entry["url"], self.app.cookies_value(),
                cookie_file=saved_cookies_path())
            if entry["title"] == entry["url"] and item["info"].get("title"):
                entry["title"] = item["title"].value = item["info"]["title"]
                entry["thumbnail"] = item["info"].get("thumbnail") or ""
                item["thumb"].content = thumb_tile(entry["thumbnail"], THUMB_WIDTH, THUMB_HEIGHT, RADIUS)
        return item["info"]

    # Subscription (see subscriptions.py)

    def refresh_subscription(self):
        subs = getattr(self.app, "subscriptions", None)
        followed = subs is not None and self.url is not None and subs.find(self.url) is not None
        self.subscribe_btn.visible = subs is not None and self.url is not None
        self.subscribe_btn.content = "Unsubscribe" if followed else "Subscribe"
        self.subscribe_btn.icon = ft.Icons.NOTIFICATIONS_OFF_OUTLINED if followed \
            else ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED

    async def toggle_subscription(self, e=None):
        subs = self.app.subscriptions
        followed = subs.find(self.url)
        if followed is not None:
            subs.remove(followed)
            self.refresh_subscription()
            self.page.update()
            return
        try:
            options = self.options()
        except ValueError as exc:
            set_status(self.status, "err", str(exc))
            self.page.update()
            return
        existing = ft.Checkbox(label="Also download the %d already there" % len(self.data["entries"]), value=False)
        if not await ask(self.page, "Subscribe to %s" % self.data["title"], column(
                [caption("Its new items are downloaded on their own, with these options."), existing], tight=True), "Subscribe"):
            return
        subs.subscribe(self.url, self.data, options, bool(existing.value))
        self.refresh_subscription()
        set_status(self.status, "ok", "Subscribed: its new items will be downloaded on their own.")
        self.page.update()

    # Preview

    def stop_preview(self):
        if self.previewing is not None:
            self.preview.stop()
            self.previewing["slot"].controls = []
            self.previewing["slot"].visible = False
            self.previewing = None

    async def toggle_preview(self, item):
        """Play the item in its row (one at a time); again: stop it."""
        if self.previewing is item:
            self.stop_preview()
            self.page.update()
            return
        self.stop_preview()
        slot = item["slot"]
        slot.controls, slot.visible = [caption("Loading the preview…")], True
        self.page.update()
        try:
            source = (await self.info_of(item)).get("player")
        except Exception as exc:
            slot.controls = [caption("No preview: %s" % clean_error(exc), color=ft.Colors.ERROR)]
            self.page.update()
            return
        if not source:
            slot.controls = [caption("No preview for this one.")]
            self.page.update()
            return
        sound_only = source["audio_only"] or self.kind_dd.value == "audio"
        url, audio_url, headers = player_choice(source, self.quality_dd.value if self.kind_dd.value == "video"
                                                else "best", sound_only=sound_only)
        self.previewing = item
        slot.controls = [self.preview.box, self.preview.audio_bar,
                         row([ft.Container(expand=True), self.preview.mute_btn, self.preview.volume,
                              ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Close the player",
                                            on_click=lambda e: self.page.run_task(self.toggle_preview, item))]),
                         self.preview.note]
        self.preview.start(player_resource(url, audio_url), headers, sound_only, title=item["entry"]["title"])
        self.page.update()

    # Download

    async def params_of(self, item, options):
        """What the queue gets for this item: its own options, else those of every item."""
        data = self.data
        if item["params"] is not None:
            params = dict(item["params"])
        else:
            params = await entry_params(item["entry"], data, options)
        if len(self.items) > 1 and self.subfolder.value:
            params["folder"] = data["title"]
        params["pace"] = len(self.items) > 1  # the pause between videos of Settings, Wi-Fi only on a phone
        params["podcast"] = bool(data.get("podcast"))
        params["list"] = data["title"] if self.url else ""  # a real list (not links pasted together)
        params["position"] = self.items.index(item)  # its place in the playlist file (.m3u8)
        return params

    async def download(self, e=None):
        picked = self.checked()
        try:
            options = self.options()
        except ValueError as exc:
            set_status(self.status, "err", str(exc))
            self.page.update()
            return
        if not picked:
            set_status(self.status, "err", "Select at least one.")
            self.page.update()
            return
        self.download_btn.disabled = True
        self.page.update()
        try:
            params = [await self.params_of(item, options) for item in picked]
        finally:
            self.download_btn.disabled = False
        # The items of one download of the list: one notification when they are all done (Settings).
        batch = {"id": uuid.uuid4().hex, "title": self.data["title"], "total": len(params)}
        for one in params:
            one["batch"] = batch
        note = "" if self.app.destination_folder() is not None or self.app.desktop else (
            " Without a destination folder (Settings), each file asks where to be saved.")
        set_status(self.status, "info", "%d added to the queue: follow them in the Queue tab.%s" % (len(params), note))
        self.page.update()
        for one in params:
            self.page.run_task(self.app.queue.run_download, one)
