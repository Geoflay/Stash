"""The Download page (Home): a link analyzed, its format (or a preset) chosen, then downloaded. The other
options (clip, sound, subtitles, size, tags) are folded in groups, closed at first, and hidden in simple mode
(Settings). The type, format and quality chosen are remembered for each site.

A playlist, a podcast's feed, or several links pasted at once open a list instead (items.py), each item of which
can be given its own options here (edit_item). Words that are not a link are searched on YouTube
(search_card.py). Downloads themselves run in the Queue tab (queue_tab.py).
"""

import asyncio
import inspect

import flet as ft
from yt_dlp.utils import DownloadError

from core.analyze import fetch_info
from core.preview import player_choice, player_resource
from core.backend import (
    AUDIO_FORMATS, BACKEND, CAN_SLIDESHOW, IMAGE_FORMATS, VIDEO_FORMATS, format_available,
)
from core.compress import can_compress
from core.cookies import saved_cookies_path
from core.errors import clean_error, error_report, make_hint
from core.links import (
    all_links, as_link, first_link, is_playlist_url, is_web_link, is_youtube_url, playlist_site, supported_site,
)
from core.network import call_with_retries
from core.playlist import fetch_playlist
from core.podcast import NotAFeed, fetch_feed, maybe_feed
from core.history import find_downloaded, load_history
from core.live import not_started
from core.search import SEARCH_SOURCES, search_youtube
from core.stills import GIF_MAX_SECONDS
from core.streams import (
    best_audio_ext, estimate_choice, frame_rate, has_stream, pick_quality, quality_choices, valid_quality,
    video_heights,
)
from core.download import SPONSORBLOCK_CATEGORIES
from core.editor import DEFAULT_SUBTITLE_STYLE, can_burn_subtitles
from core.sponsorblock import fetch_segments, merge_segments
from core.subtitles import CAN_EMBED_SUBS, preview_subtitles, subtitle_key, subtitle_label
from core.tags import EDITABLE_TAGS, LABELS
from core.units import format_duration, format_moment, parse_size_mb, parse_time
from ui.clip import ClipPicker
from ui.edit_options import CROP_CHOICES, STYLE_CHOICES, TURN_CHOICES, choice_dropdown, picture_summary
from ui.items import ItemList
from ui.search_card import SearchCard
from ui.player import Preview, flet_video
from ui.montage import MontageCard
from ui.presets import PresetBar, saved_presets
from ui.queue_tab import link_params
from ui.style import FAB_CLEARANCE, HEADING_SIZE, M, RADIUS, S, SMALL_SIZE, TOUCH, XS, caption, column, row, section_title
from ui.widgets import MultiPicker, OptionGroup, SpeedPicker, ask, hide_status, play_badge, set_status



def language_of(key):
    """"fr" for a subtitle key ("m:fr", "a:fr-orig") or a language ("fr-FR")."""
    return key.partition(":")[2].split("-")[0] if ":" in key else key.split("-")[0]


RECENT_COUNT = 10  # links and searches kept for the list under the field


def site_choice(settings, platform, key, default=None):
    """A choice remembered for this site ("Youtube", "TikTok"…: yt-dlp's name), else the last one made."""
    sites = settings.get("site_choices")
    site = sites.get(platform) if isinstance(sites, dict) and platform else None
    if isinstance(site, dict) and key in site:
        return site[key]
    return settings.get(key, default)


class DownloadTab:
    def __init__(self, app):
        self.app = app
        self.page = page = app.page
        settings = app.settings
        needs_bytes = app.needs_bytes
        self.state = {"url": None, "title": None, "platform": "", "slideshow": False, "images": [], "audio_url": "",
                      "selected": 0, "formats": [], "duration": None, "subs": [], "measured": {}, "thumbnail": "", "player": None,
                      "uploader": "", "upload_date": "", "video_id": "", "tags": {}, "chapters": [], "audio_tracks": [], "segments": [],
                      "live": False}

        self.url_input = ft.TextField(
            hint_text="A link (or several), or words to search on YouTube",
            keyboard_type=ft.KeyboardType.URL,
            autocorrect=False,
            enable_suggestions=False,
            text_size=HEADING_SIZE,
            expand=True,
        )
        paste_btn = ft.TextButton("Paste", icon=ft.Icons.CONTENT_PASTE, expand=needs_bytes, height=TOUCH)
        self.analyze_btn = ft.FilledButton("Analyze", icon=ft.Icons.SEARCH, expand=needs_bytes, height=TOUCH)
        self.analyze_status = ft.Container(visible=False)  # a link being analyzed (the Download page)
        self.search_status = ft.Container(visible=False)  # words searched, the clipboard (Home)

        self.thumb = ft.Image(src="", fit=ft.BoxFit.COVER)
        # A tap on the thumbnail plays the video (or the sound) before downloading it.
        self.play_badge = play_badge()
        self.thumb_box = ft.Container(
            content=ft.Stack([self.thumb, self.play_badge], fit=ft.StackFit.EXPAND),
            aspect_ratio=16 / 9,
            border_radius=RADIUS,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            on_click=self.toggle_player,
        )
        self.video_title = ft.Text("", size=HEADING_SIZE, weight=ft.FontWeight.W_600, selectable=True)
        self.video_meta = caption()
        self.estimate_text = ft.Text("", size=SMALL_SIZE, weight=ft.FontWeight.W_500)

        self.kind_dd = ft.Dropdown(label="Type", value="video", expand=True, options=[
            ft.DropdownOption(key="video", text="Video"), ft.DropdownOption(key="audio", text="Audio"),
            ft.DropdownOption(key="gif", text="GIF"), ft.DropdownOption(key="image", text="Image")])
        self.format_dd = ft.Dropdown(label="Format", value="mp4", options=[], expand=True)
        # Format choices of the analyzed link, per type: [(key, label, enabled)].
        self.format_menu = {"video": [], "audio": [], "gif": [], "image": []}
        self.set_formats(False)

        self.duration_dd = ft.Dropdown(
            label="Seconds per photo",
            value=settings.get("photo_seconds") if settings.get("photo_seconds") in ("auto", "2", "3", "5") else "auto",
            options=[
                ft.DropdownOption(key="auto", text="Match audio length"),
                ft.DropdownOption(key="2", text="2 seconds"),
                ft.DropdownOption(key="3", text="3 seconds"),
                ft.DropdownOption(key="5", text="5 seconds"),
            ],
            expand=True,
        )
        self.duration_row = ft.Row([self.duration_dd], visible=False)
        self.slideshow_note = caption(visible=False)

        self.clip = ClipPicker(page, on_change=self.refresh_options, several=True)

        # Several languages: each one a track in the video, or a file next to it.
        self.subs_picker = MultiPicker("Add subtitles", self.on_subs_change)
        self.subs_only_btn = ft.IconButton(
            icon=ft.Icons.FILE_DOWNLOAD_OUTLINED,
            tooltip="Download only the subtitles, without the video (saved as chosen below)",
            visible=False,
        )
        burn = BACKEND == "ffmpeg" and can_burn_subtitles()
        self.subs_mode_dd = ft.Dropdown(
            label="Save subtitles as",
            value=(settings.get("subs_mode") if settings.get("subs_mode") in ("file", "txt", "html")
                   or (settings.get("subs_mode") == "burn" and burn) else "embed" if CAN_EMBED_SUBS else "file"),
            options=[
                ft.DropdownOption(key="embed", text="Embedded in the video" if CAN_EMBED_SUBS
                                  else "Embedded in the video (needs ffmpeg)", disabled=not CAN_EMBED_SUBS),
                ft.DropdownOption(key="file", text="Separate file (.srt)"),
                ft.DropdownOption(key="txt", text="Transcript: plain text (.txt)"),
                ft.DropdownOption(key="html", text="Transcript: readable page (.html)"),
            ] + ([ft.DropdownOption(key="burn", text="Drawn onto the picture (always shown)")] if burn else []),
            expand=True,
        )
        self.subs_mode_row = ft.Row([self.subs_mode_dd, self.subs_only_btn], visible=False)
        self.style_dds = {key: ft.Dropdown(label=label, value=DEFAULT_SUBTITLE_STYLE[key], expand=True,
                                           options=[ft.DropdownOption(key=k, text=t) for k, t in choices.items()])
                          for key, (label, choices) in STYLE_CHOICES.items()}
        self.style_box = column([row([self.style_dds["size"], self.style_dds["color"]]),
                                 row([self.style_dds["outline"], self.style_dds["position"]])],
                                spacing=XS, visible=False)
        self.subs_note = caption(visible=False)
        self.subs_box = column([self.subs_picker.box, self.subs_mode_row, self.style_box, self.subs_note],
                               spacing=XS, visible=False)

        self.photos_row = ft.Row(wrap=True, spacing=S, run_spacing=S)
        self.photos_box = column([caption("Photo to show (tap to choose)"), self.photos_row], spacing=XS,
                                 visible=False)
        self.photo_boxes = []

        self.quality_dd = ft.Dropdown(label="Quality", options=[], expand=True)
        self.quality_row = ft.Row([self.quality_dd], expand=not needs_bytes)
        self.set_qualities([], None)
        if needs_bytes:
            format_rows = [row([self.kind_dd, self.format_dd]), self.quality_row]
        else:
            format_rows = [row([self.kind_dd, self.format_dd, self.quality_row])]

        self.speed = SpeedPicker(on_change=lambda: self.on_option_change(None))
        # Several sound tracks (YouTube's dubbed ones) in one video: the first one plays by default.
        self.audio_picker = MultiPicker("Add a sound track", lambda: self.on_option_change(None))
        self.audio_caption = caption()
        self.audio_track_row = column([self.audio_caption, self.audio_picker.box], spacing=XS, visible=False)
        self.loudness = ft.Switch(label="Even out the volume", value=False, on_change=self.on_option_change)
        self.no_silence = ft.Switch(label="Cut out the silences", value=False, on_change=self.on_option_change)
        self.crop_dd = choice_dropdown("Crop", CROP_CHOICES, self.on_option_change)
        self.turn_dd = choice_dropdown("Turn", TURN_CHOICES, self.on_option_change)
        self.picture_box = row([self.crop_dd, self.turn_dd], visible=False)
        self.no_sound_switch = ft.Switch(label="No sound", value=False, visible=False,
                                         on_change=self.on_option_change)
        self.sponsor_switch = ft.Switch(value=True, visible=False, on_change=self.on_option_change)
        self.speed_row = self.speed.box
        self.bounce_switch = ft.Switch(label="Back and forth (a loop without a jump)", value=False, visible=False,
                                       on_change=self.on_option_change)

        self.chapters_switch = ft.Switch(label="One file per chapter", value=False)
        self.chapters_summary = ""
        self.chapters_note = caption()
        # The chapters to keep: the unchecked ones are cut out of the file (or not saved, one file per chapter).
        self.chapter_checks = []
        self.chapters_btn = ft.TextButton("Choose the chapters", icon=ft.Icons.CHECKLIST,
                                          on_click=self.toggle_chapter_list)
        self.chapters_all_btn = ft.TextButton("Select none", on_click=self.toggle_all_chapters)
        self.chapter_list = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, visible=False)
        self.chapters_box = column([self.chapters_switch, self.chapters_btn, self.chapter_list, self.chapters_note],
                                   spacing=XS, visible=False)

        self.size_field = ft.TextField(label="Maximum size (MB)", hint_text="No limit", value=settings.get("max_mb", ""),
                                       keyboard_type=ft.KeyboardType.NUMBER, expand=True)
        self.size_box = column([ft.Row([self.size_field])], visible=False)

        self.tag_fields = {key: ft.TextField(label=LABELS.get(key, key.capitalize()), expand=True,
                                             on_blur=self.on_option_change) for key in EDITABLE_TAGS}
        self.tags_box = column(
            [caption("Tags written in the file (you can correct them)"),
             ft.Row([self.tag_fields["title"]]),
             row([self.tag_fields["artist"], self.tag_fields["album"]]),
             row([self.tag_fields["album_artist"], self.tag_fields["genre"], self.tag_fields["track"]])],
            visible=False,
        )

        self.download_btn = ft.FilledButton("Download", icon=ft.Icons.DOWNLOAD, height=TOUCH, visible=not needs_bytes)
        self.download_status = ft.Container(visible=False)
        self.fab = ft.FloatingActionButton(icon=ft.Icons.DOWNLOAD, content="Download", visible=False,
                                           on_click=self.download)

        # Watching or listening before downloading, to check it is the right one.
        self.preview = Preview(app)
        self.can_play = False  # the thumbnail plays the link when tapped
        self.close_btn = ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Close the player", visible=False,
                                       on_click=self.toggle_player)
        # The picture shown in the player, saved at the video's full quality (a PNG, through the queue).
        self.frame_btn = ft.IconButton(icon=ft.Icons.PHOTO_CAMERA_OUTLINED, tooltip="Save this picture (full quality)",
                                       visible=False, on_click=self.save_frame)
        self.player_key = None  # what the player plays (streams, part, subtitles): a change reloads it
        self.reload_token = 0
        # The parts SponsorBlock will cut, along the video (a tap plays one in the player).
        self.segments_bar = ft.Row(spacing=0, height=10)
        self.segments_note = caption()
        self.segments_box = column([ft.Container(self.segments_bar, border_radius=RADIUS,
                                                 clip_behavior=ft.ClipBehavior.ANTI_ALIAS),
                                    self.segments_note], spacing=XS, visible=False)
        # Giving an item of a list its own options (see edit_item): back to the list from here.
        self.editing = None
        self.item_bar = row([ft.TextButton("Back to the list", icon=ft.Icons.ARROW_BACK, on_click=self.back_to_list),
                             caption("Options of this one only", expand=True)], visible=False)
        # While watching: the clip starts or ends at the moment shown (or the image is taken there).
        self.start_here_btn = ft.TextButton("Start here", icon=ft.Icons.FIRST_PAGE,
                                            on_click=lambda e: self.page.run_task(self.mark_moment, "start"))
        self.end_here_btn = ft.TextButton("End here", icon=ft.Icons.LAST_PAGE,
                                          on_click=lambda e: self.page.run_task(self.mark_moment, "end"))
        self.mark_row = ft.Row([self.start_here_btn, self.end_here_btn], wrap=True, spacing=0, visible=False)
        summary_controls = [self.item_bar, self.thumb_box, self.preview.box, self.preview.audio_bar,
                            row([ft.Container(expand=True), self.frame_btn, self.preview.mute_btn,
                                 self.preview.volume, self.close_btn]),
                            self.mark_row, self.preview.note, self.segments_box,
                            self.video_title, self.video_meta, self.estimate_text, self.slideshow_note]
        # Folded, so that the tab stays "paste a link, download": the title line says what is changed.
        self.groups = {
            "cut": OptionGroup("Clip & chapters", ft.Icons.CONTENT_CUT,
                               [self.clip.box, self.bounce_switch, self.chapters_box, self.sponsor_switch], page),
            "picture": OptionGroup("Picture", ft.Icons.CROP_ROTATE, [self.picture_box], page),
            "sound": OptionGroup("Sound & speed", ft.Icons.GRAPHIC_EQ,
                                 [self.audio_track_row, self.no_sound_switch, self.loudness, self.no_silence,
                                  self.speed_row], page),
            "subs": OptionGroup("Subtitles", ft.Icons.SUBTITLES_OUTLINED, [self.subs_box], page),
            "size": OptionGroup("Size limit", ft.Icons.COMPRESS, [self.size_box], page),
            "tags": OptionGroup("Tags", ft.Icons.LABEL_OUTLINE, [self.tags_box], page),
        }
        self.clip_mode = "clip"  # a GIF or an image opens "Clip & chapters": its passage must be chosen
        self.montage_btn = ft.TextButton("Add to the montage", icon=ft.Icons.MOVIE_FILTER_OUTLINED,
                                         tooltip="Its clip (or all of it) joins the passages of other videos in a "
                                                 "single file", on_click=self.add_to_montage)
        self.presets = PresetBar(self)
        option_controls = [self.presets.box] + format_rows + [self.duration_row, self.photos_box] + [
            group.box for group in self.groups.values()]
        # The link sent to another device of the Wi-Fi running the app (it downloads it there).
        self.send_btn = ft.IconButton(icon=ft.Icons.DEVICES, tooltip="Send to a device", visible=not page.web,
                                      on_click=lambda e: page.run_task(app.send_to_device, self.state["url"]))
        action_controls = [row([self.download_btn, self.montage_btn, self.send_btn], wrap=True), self.download_status]
        if needs_bytes:
            # Side by side once there is room (a tablet, a phone held sideways), else one under the other.
            self.result_card = ft.ResponsiveRow(
                [app.stretch_card(summary_controls + action_controls, col={"xs": 12, "sm": 5}),
                 app.stretch_card(option_controls, col={"xs": 12, "sm": 7})],
                spacing=M, run_spacing=M, visible=False)
        else:
            self.result_card = row(
                [
                    app.stretch_card(summary_controls + action_controls, expand=2),
                    app.stretch_card([section_title("Format")] + option_controls, expand=3),
                ],
                spacing=M,
                vertical_alignment=ft.CrossAxisAlignment.START,
                visible=False,
            )

        self.items = ItemList(app, self)
        self.search_card = SearchCard(app, self.analyze_link, self.search)
        self.montage = MontageCard(app)
        # The links and searches typed lately, over the page under the field while it is being typed in (the
        # Home tab places it: the page does not move).
        self.recent_list = column([], spacing=0)
        self.recent_box = ft.Container(self.recent_list, visible=False, padding=XS, border_radius=RADIUS * 2,
                                       bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                                       shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.3, "black")))
        self.recent_token = 0

        self.kind_dd.on_select = self.on_kind_change
        self.format_dd.on_select = self.on_format_change
        self.quality_dd.on_select = self.on_quality_change
        self.subs_mode_dd.on_select = self.remember_and_refresh("subs_mode", self.subs_mode_dd)
        self.duration_dd.on_select = lambda e: app.remember("photo_seconds", self.duration_dd.value)
        self.size_field.on_change = self.remember_and_refresh("max_mb", self.size_field)
        self.chapters_switch.on_change = self.on_option_change
        self.analyze_btn.on_click = self.analyze
        self.url_input.on_submit = self.analyze
        self.download_btn.on_click = self.download
        self.subs_only_btn.on_click = self.download_subs_only
        paste_btn.on_click = self.paste_link

        if needs_bytes:
            link_rows = [ft.Row([self.url_input]), row([paste_btn, self.analyze_btn])]
        else:
            link_rows = [row([self.url_input, paste_btn, self.analyze_btn])]
        # The field is on the Home tab; what it finds is shown on the Download page (this view).
        self.link_card = app.card(link_rows + [self.search_status])
        set_status(self.analyze_status, "info", "Search or paste a link on Home.")
        self.view = app.view("Download", [
            self.analyze_status,
            self.result_card,
            self.items.card,
            ft.Container(height=FAB_CLEARANCE, visible=needs_bytes),
        ])

        # The text field being typed in (keyboard shortcuts must not take its Enter or Ctrl+V), or None.
        self.typing = None
        self.url_input.on_focus = lambda e: self.show_recent()
        self.url_input.on_change = lambda e: self.show_recent()
        self.url_input.on_blur = self.hide_recent
        for field in [self.url_input, self.size_field, self.clip.start, self.clip.end, self.speed.field,
                      *self.tag_fields.values()]:
            self.track_typing(field)

    def track_typing(self, field):
        blur, focus = field.on_blur, field.on_focus

        def on_focus(e):
            self.typing = field
            if focus is not None:
                focus(e)

        async def on_blur(e):
            if self.typing is field:
                self.typing = None
            if blur is not None and inspect.isawaitable(result := blur(e)):
                await result
        field.on_focus, field.on_blur = on_focus, on_blur

    # The links and searches typed lately

    def recent(self):
        saved = self.app.settings.get("recent")
        return [text for text in saved if isinstance(text, str)] if isinstance(saved, list) else []

    def remember_recent(self, text):
        self.app.remember("recent", ([text] + [t for t in self.recent() if t != text])[:RECENT_COUNT])

    def show_recent(self):
        """Under the field: the latest ones holding what is typed (all of them while it is empty)."""
        typed = (self.url_input.value or "").strip().casefold()
        found = [text for text in self.recent() if typed in text.casefold() and text.casefold() != typed]
        self.recent_list.controls = [
            ft.Container(row([ft.Icon(ft.Icons.LINK if is_web_link(first_link(text) or as_link(text) or "")
                                      else ft.Icons.SEARCH,
                                      size=18, color=ft.Colors.ON_SURFACE_VARIANT),
                              ft.Text(text, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)]),
                         padding=ft.Padding(left=S, right=S, top=6, bottom=6), border_radius=RADIUS, ink=True,
                         on_click=lambda e, text=text: self.page.run_task(self.analyze_link, text))
            for text in found[:6]]
        if found:
            self.recent_list.controls.append(ft.Row([ft.TextButton("Forget them", icon=ft.Icons.HISTORY_TOGGLE_OFF,
                                                                  on_click=self.forget_recent)],
                                                   alignment=ft.MainAxisAlignment.END))
        self.recent_box.visible = bool(found)
        self.page.update()

    async def hide_recent(self, e=None):
        self.recent_token += 1
        token = self.recent_token
        await asyncio.sleep(0.3)  # a tap on one of them comes after the field loses the focus
        if token == self.recent_token and self.recent_box.visible:
            self.recent_box.visible = False
            self.page.update()

    def forget_recent(self, e=None):
        self.app.remember("recent", [])
        self.recent_box.visible = False
        self.page.update()

    # Formats

    def format_setting_key(self, kind):
        """Settings entry remembering the last format of this type (slideshows have their own video formats)."""
        if kind in ("audio", "gif", "image"):
            return kind + "_format"
        return "slide_format" if self.state["slideshow"] else "video_format"

    def remembered(self, key, default=None):
        return site_choice(self.app.settings, self.state["platform"], key, default)

    def remember_choice(self, key, value):
        """Remembered for every link, and for this site (it wins for the next link of the same site)."""
        self.app.remember(key, value)
        platform = self.state["platform"]
        if platform:
            sites = self.app.settings.get("site_choices")
            sites = sites if isinstance(sites, dict) else {}
            sites.setdefault(platform, {})[key] = value
            self.app.remember("site_choices", sites)

    def show_kind(self, kind):
        choices = self.format_menu[kind]
        self.kind_dd.value = kind
        self.format_dd.options = [ft.DropdownOption(key=k, text=t, disabled=not ok) for k, t, ok in choices]
        enabled = [k for k, _, ok in choices if ok]
        remembered = self.remembered(self.format_setting_key(kind))
        self.format_dd.value = remembered if remembered in enabled else (enabled[0] if enabled else None)

    def set_formats(self, slideshow, has_images=False, formats=None, thumbnail=False):
        formats = formats or []
        has_audio_track = has_stream(formats, "audio")
        audio_ext = best_audio_ext(formats)
        audio_choices = []
        for key, label in AUDIO_FORMATS.items():
            if key == "audio" and audio_ext:
                label = "%s (%s)" % (label, audio_ext.upper())
            usable = format_available(key)
            audio_choices.append((key, label if usable else label + " (needs ffmpeg)", usable and has_audio_track))
        if slideshow:
            video_choices = [
                ("cover", "Chosen photo + audio (MP4)", CAN_SLIDESHOW and has_images),
                ("slideshow", "Slideshow: all photos + audio (MP4)", CAN_SLIDESHOW and has_images),
            ]
        else:
            has_video_track = has_stream(formats, "video")
            video_choices = [(key, label if format_available(key) else label + " (needs ffmpeg)",
                              format_available(key) and has_video_track) for key, label in VIDEO_FORMATS.items()]
        # Photo posts have no video to take a GIF or a picture from: their photos are saved as they are.
        still_choices = [(key, label if format_available(key) else label + " (needs ffmpeg)",
                          format_available(key) and has_stream(formats, "video") and not slideshow)
                         for key, label in IMAGE_FORMATS.items()]
        self.format_menu["gif"] = [c for c in still_choices if c[0] == "gif"]
        self.format_menu["image"] = ([("photos", "All photos, as separate files", has_images)] if slideshow
                                     else [("thumbnail", "Thumbnail, full size", thumbnail)]
                                     + [c for c in still_choices if c[0] != "gif"])
        self.format_menu["video"], self.format_menu["audio"] = video_choices, audio_choices
        usable_kinds = [kind for kind in ("video", "audio", "gif", "image")
                        if any(ok for _, _, ok in self.format_menu[kind])]
        self.kind_dd.options = [ft.DropdownOption(key=option.key, text=option.text,
                                                  disabled=option.key not in usable_kinds)
                                for option in self.kind_dd.options]
        remembered = self.remembered("slide_kind" if slideshow else "kind", "video")
        self.show_kind(remembered if remembered in usable_kinds else (usable_kinds[0] if usable_kinds else "video"))

    def set_qualities(self, formats, duration, measured=None):
        choices = quality_choices(formats, duration, BACKEND is not None, measured)
        self.quality_labels = choices
        self.quality_still = None  # the labels are set by show_quality_sizes
        self.show_quality_sizes(True)
        heights = video_heights(formats, BACKEND is not None)
        top = heights[0] if heights else None
        if measured and measured.get("video") and measured["video"][0].get("height"):
            top = measured["video"][0]["height"]
        self.quality_dd.value = pick_quality(self.remembered("quality"), choices, top)
        self.quality_top = top

    def show_quality_sizes(self, sizes):
        """The quality menu with the size of the video at each quality, or without for an image (its size is not
        the video's)."""
        if self.quality_still == (not sizes):
            return
        self.quality_still = not sizes
        self.quality_dd.options = [ft.DropdownOption(key=k, text=t if sizes else t.split(" · ")[0])
                                   for k, t in self.quality_labels]

    def set_subtitles(self, choices):
        self.state["subs"] = choices
        remembered = self.app.settings.get("subs_choices")
        if not isinstance(remembered, list):  # one language, before several could be chosen
            remembered = [self.app.settings["subs_choice"]] if self.app.settings.get("subs_choice") else []
        keys = [subtitle_key(c) for c in choices]
        preferred = self.app.settings.get("pref_subs")
        if isinstance(preferred, list) and preferred:  # the languages of Settings, the video's own first
            remembered = [key for key in (next((k for k in keys if language_of(k) == lang and k.startswith("m:")),
                                               None) or next((k for k in keys if language_of(k) == lang), None)
                                          for lang in preferred) if key]
        self.subs_picker.set([(subtitle_key(c), subtitle_label(c)) for c in choices], remembered)

    def on_subs_change(self):
        self.app.remember("subs_choices", self.subs_picker.value)
        self.refresh_options()
        self.page.update()

    def chosen_subtitles(self):
        """The subtitle languages chosen (keys), when subtitles apply to this download."""
        return self.subs_picker.value if self.subs_box.visible else []

    def select_photo(self, i):
        self.state["selected"] = i
        for j, box in enumerate(self.photo_boxes):
            box.border = ft.Border.all(3, ft.Colors.PRIMARY if j == i else ft.Colors.TRANSPARENT)

    def build_photos(self, images):
        self.photo_boxes.clear()
        for i, im in enumerate(images):
            self.photo_boxes.append(ft.Container(
                content=ft.Image(src=im["url"], fit=ft.BoxFit.COVER, width=72, height=96,
                                 error_content=ft.Icon(ft.Icons.IMAGE)),
                width=72, height=96, border_radius=RADIUS, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                on_click=lambda e, i=i: (self.select_photo(i), self.page.update()),
            ))
        self.photos_row.controls = list(self.photo_boxes)
        self.select_photo(0)

    def refresh_options(self):
        """Show the options that apply to the chosen format, and the size estimate."""
        state = self.state
        fmt = self.format_dd.value
        slide = state["slideshow"]
        is_video = fmt in VIDEO_FORMATS
        is_media = is_video or fmt in AUDIO_FORMATS  # not a photo post video
        is_still = fmt in IMAGE_FORMATS and fmt != "gif"
        # Simple mode (Settings): the format and the presets only; the passage of a GIF or an image still shows.
        simple = bool(self.app.settings.get("simple_mode"))
        self.quality_row.visible = (not slide) and (is_video or is_still)
        self.show_quality_sizes(not is_still)
        live = bool(state.get("live"))
        self.format_dd.visible = self.kind_dd.value != "gif" and not live  # a GIF has a single format
        self.kind_dd.visible = not live  # a live is recorded as a video
        self.duration_row.visible = slide and fmt == "slideshow"
        self.photos_box.visible = slide and fmt == "cover" and bool(self.photo_boxes)
        mode = "gif" if fmt == "gif" else "still" if is_still else "clip"
        self.clip.show(is_media or fmt in IMAGE_FORMATS, is_video, mode)
        if simple and mode == "clip":
            self.clip.box.visible = False
        if mode != self.clip_mode:
            self.clip_mode = mode
            self.groups["cut"].set_open(mode != "clip")
        can_edit = (not slide) and is_media and BACKEND == "ffmpeg"
        self.speed_row.visible = can_edit and not simple
        normal_speed = not self.speed_row.visible or self.speed.value(default=1.0) == 1.0
        self.chapters_box.visible = can_edit and len(state["chapters"]) > 1 and not simple
        self.chapters_switch.label = ("One file per track (an album: numbered and tagged)" if fmt in AUDIO_FORMATS
                                      else "One file per chapter")
        kept = self.kept_chapters() if self.chapters_box.visible else state["chapters"]
        split = self.chapters_box.visible and bool(self.chapters_switch.value)
        skipping = len(kept) < len(state["chapters"])
        if split or skipping:
            self.clip.box.visible = False  # each chapter is already a clip, and a clip would move their times
        try:
            several = len(self.clip.all_clips()) > 1
        except ValueError:
            several = self.clip.several_clips()
        self.chapters_btn.content = "Choose the chapters (%d of %d)" % (len(kept), len(state["chapters"]))
        self.chapters_all_btn.content = "Select all" if skipping else "Select none"
        self.chapters_note.value = ("Each checked chapter is saved as its own file." if split
                                    else "The unchecked chapters are cut out of the file." if skipping
                                    else self.chapters_summary)
        self.size_box.visible = (not slide) and (can_compress(fmt) or fmt == "gif") and not split and not several \
            and not simple
        self.tags_box.visible = (not slide) and is_media and self.app.embed_tags() and not simple
        self.no_sound_switch.visible = can_edit and is_video and not simple
        no_sound = self.no_sound_switch.visible and bool(self.no_sound_switch.value)
        self.audio_track_row.visible = (not slide) and is_media and bool(state["audio_tracks"]) and not no_sound \
            and not simple
        self.audio_caption.value = ("Sound tracks (the first one plays by default)" if is_video
                                    else "Sound track (a sound file keeps the first one)")
        # The Edit tab's changes, made once downloaded.
        self.picture_box.visible = can_edit and is_video
        self.loudness.visible = self.no_silence.visible = can_edit and not no_sound
        no_silence = self.no_silence.visible and bool(self.no_silence.value)
        try:
            clipped = self.clip.box.visible and self.clip.read() is not None
        except ValueError:
            clipped = False
        sponsor = self.app.sponsorblock_categories()
        self.sponsor_switch.label = "SponsorBlock"
        self.sponsor_switch.visible = bool(sponsor) and can_edit and not clipped and not split and not several \
            and is_youtube_url(state["url"] or "")
        self.refresh_segments(sponsor)
        # Subtitles would no longer match the sound once sped up, or with parts cut out.
        self.subs_box.visible = bool(state["subs"]) and (not slide) and is_video and normal_speed and not split \
            and not skipping and not several and not simple and not no_silence
        chosen_subs = self.chosen_subtitles()
        self.subs_mode_row.visible = bool(chosen_subs)
        self.subs_only_btn.visible = self.subs_mode_row.visible and self.editing is None  # once a language is chosen
        self.style_box.visible = self.subs_mode_row.visible and self.subs_mode_dd.value == "burn"
        notes_for_subs = []
        if self.style_box.visible and len(chosen_subs) > 1:
            notes_for_subs.append("Only the first language is drawn onto the picture.")
        if self.subs_mode_row.visible and self.subs_mode_dd.value == "file":
            notes_for_subs.append("Saved next to the video (video.en.srt).")
        if self.subs_mode_row.visible and self.subs_mode_dd.value in ("txt", "html"):
            notes_for_subs.append("The words of the video as text.")
        auto = [c for c in state["subs"] if subtitle_key(c) in chosen_subs and c["auto"]]
        if auto:
            notes_for_subs.append("Translated by YouTube from automatic subtitles: expect mistakes."
                                  if any(c.get("translated") for c in auto)
                                  else "Automatic subtitles: expect mistakes.")
        self.subs_note.value = " ".join(notes_for_subs)
        self.subs_note.visible = bool(notes_for_subs)
        estimate = ""
        if is_media:
            estimate = estimate_choice(state["formats"], state["duration"], fmt,
                                       self.quality_dd.value, BACKEND is not None, self.clip.scale(),
                                       state["measured"], sound=not no_sound)
        try:
            limit = self.read_max_mb() if self.size_box.visible else None
        except ValueError:
            limit = None
        if estimate and limit:
            estimate += " (re-encoded if bigger than %g MB)" % limit
        self.estimate_text.value = ("Estimated download: " + estimate) if estimate else ""
        self.estimate_text.visible = bool(estimate)
        self.bounce_switch.visible = fmt == "gif"
        self.refresh_groups(split, kept, limit, no_sound)
        if live:  # recorded as it comes: none of the options applies
            for group in self.groups.values():
                group.box.visible = False
            self.estimate_text.visible = False
        self.montage_btn.visible = is_media and not live and not slide and self.editing is None
        self.mark_row.visible = self.preview.player is not None and self.clip.box.visible
        self.start_here_btn.content = "Image here" if self.clip_mode == "still" else "Start here"
        self.end_here_btn.visible = self.clip_mode != "still"
        self.sync_player()

    def refresh_groups(self, split, kept, limit, no_sound):
        """Each group's title line: what is changed in it, so that nothing chosen stays hidden."""
        cut = []
        try:
            clips = self.clip.all_clips() if self.clip.box.visible else []
        except ValueError:
            clips = []
        if len(clips) > 1:
            cut.append("%d clips" % len(clips))
        elif clips:
            start, end = clips[0]
            cut.append("at %s" % (format_moment(start) or "0:00") if self.clip_mode == "still"
                       else "from %s" % (format_duration(start) or "0:00")
                       + (" to %s" % format_duration(end) if end is not None else ""))
        if split:
            cut.append("one file per chapter")
        elif len(kept) < len(self.state["chapters"]):
            cut.append("%d of %d chapters" % (len(kept), len(self.state["chapters"])))
        sound = []
        if no_sound:
            sound.append("no sound")
        elif self.chosen_audio_tracks():
            sound.append(", ".join(self.audio_picker.text(lang) for lang in self.chosen_audio_tracks()))
        for switch, text in ((self.loudness, "volume evened out"), (self.no_silence, "no silences")):
            if switch.visible and switch.value:
                sound.append(text)
        speed = self.preview_speed()
        if speed != 1:
            sound.append("×%g" % speed)
        subs = ", ".join(self.subs_picker.text(key) for key in self.chosen_subtitles())
        picture = picture_summary(self.crop_dd.value, self.turn_dd.value) if self.picture_box.visible else ""
        tags = self.tags_box.visible and any((field.value or "").strip() != self.state["tags"].get(key, "")
                                             for key, field in self.tag_fields.items())
        for key, summary in (("cut", ", ".join(cut)), ("sound", ", ".join(sound)), ("subs", subs),
                             ("size", "%g MB" % limit if limit else ""), ("tags", "changed" if tags else ""),
                             ("picture", picture)):
            self.groups[key].refresh(summary)
        if self.app.settings.get("simple_mode"):  # SponsorBlock still applies, as set in Settings
            for key, group in self.groups.items():
                group.box.visible = group.box.visible and key == "cut" and self.clip_mode != "clip"

    def on_option_change(self, e):
        self.refresh_options()
        self.page.update()

    # Chapters

    def build_chapters(self, chapters):
        self.chapter_checks = [
            ft.Checkbox(label="%s · %s" % (format_duration(c["start"]) or "0:00", c["title"]), value=True,
                        on_change=self.on_option_change)
            for c in chapters]
        self.chapter_list.controls = [self.chapters_all_btn] + self.chapter_checks
        self.chapter_list.height = 260 if len(chapters) > 6 else None
        self.chapter_list.visible = False

    def kept_chapters(self):
        return [c for c, box in zip(self.state["chapters"], self.chapter_checks) if box.value]

    def toggle_chapter_list(self, e=None):
        self.chapter_list.visible = not self.chapter_list.visible
        self.page.update()

    def toggle_all_chapters(self, e=None):
        value = len(self.kept_chapters()) < len(self.state["chapters"])
        for box in self.chapter_checks:
            box.value = value
        self.on_option_change(e)

    # SponsorBlock

    async def load_segments(self, video_id):
        """The parts marked on SponsorBlock, fetched once per video (all the categories: the ones shown follow
        Settings)."""
        try:
            segments = await asyncio.to_thread(fetch_segments, video_id, list(SPONSORBLOCK_CATEGORIES))
        except Exception:
            return
        if self.state["video_id"] != video_id:
            return
        self.state["segments"] = segments
        self.refresh_options()
        self.page.update()

    def refresh_segments(self, categories):
        segments = merge_segments(self.state["segments"], categories)
        duration = self.state["duration"]
        self.segments_box.visible = self.sponsor_switch.visible and bool(self.sponsor_switch.value) \
            and bool(segments) and bool(duration)
        if not self.segments_box.visible:
            return
        parts, position, cut = [], 0.0, 0.0
        for start, end, names in segments:
            start, end = max(start, position), min(end, duration)
            if end <= start:
                continue
            if start > position:
                parts.append(self.segment_part(start - position))
            parts.append(self.segment_part(end - start, (start, end, names)))
            position, cut = end, cut + end - start
        if duration > position:
            parts.append(self.segment_part(duration - position))
        self.segments_bar.controls = parts
        count = len(segments)
        self.segments_note.value = "SponsorBlock will cut %d part%s (%s in all), in orange%s." % (
            count, "s" if count > 1 else "", format_duration(cut) or "0:00",
            ": tap one to watch it" if self.can_play else "")

    def segment_part(self, length, segment=None):
        if segment is None:
            return ft.Container(expand=max(1, round(length * 10)), height=10,
                                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST)
        start, end, names = segment
        return ft.Container(
            expand=max(1, round(length * 10)), height=10, bgcolor=ft.Colors.ORANGE,
            tooltip="%s: %s – %s" % (", ".join(SPONSORBLOCK_CATEGORIES.get(n, n) for n in names),
                                     format_duration(start) or "0:00", format_duration(end)),
            on_click=(lambda e: self.page.run_task(self.watch_segment, start)) if self.can_play else None,
        )

    async def watch_segment(self, seconds):
        """Play a part SponsorBlock will cut (the player opens if needed)."""
        if self.preview.player is None:
            await self.open_player()
            self.page.update()
            await asyncio.sleep(1.5)  # the video must be loaded before it can be moved
        try:
            await self.preview.seek(seconds)
        except Exception:
            pass

    def read_max_mb(self):
        """The size limit typed, in MB (None = no limit); ValueError if it is not a number."""
        return parse_size_mb(self.size_field.value)

    def on_kind_change(self, e):
        self.remember_choice("slide_kind" if self.state["slideshow"] else "kind", self.kind_dd.value)
        self.show_kind(self.kind_dd.value)
        self.refresh_options()
        self.page.update()

    def on_format_change(self, e):
        self.remember_choice(self.format_setting_key(self.kind_dd.value), self.format_dd.value)
        self.refresh_options()
        self.page.update()

    def on_quality_change(self, e):
        self.remember_choice("quality", self.quality_dd.value)
        self.refresh_options()
        self.page.update()

    # Presets (see presets.py)

    def apply_preset(self, preset):
        """Set the type, format, quality, size limit and sound of `preset`; returns why it cannot be used
        for this link, or ""."""
        kind, fmt = preset.get("kind"), preset.get("fmt")
        usable = {k for k, _, ok in self.format_menu.get(kind, []) if ok}
        if fmt not in usable:
            return "This preset cannot be used for this link (%s is not available)." % (fmt or "its format")
        self.remember_choice("slide_kind" if self.state["slideshow"] else "kind", kind)
        self.show_kind(kind)
        self.format_dd.value = fmt
        self.remember_choice(self.format_setting_key(kind), fmt)
        if preset.get("quality") and self.quality_dd.options:
            choices = [(o.key, o.text) for o in self.quality_dd.options]
            self.quality_dd.value = pick_quality(preset["quality"], choices, getattr(self, "quality_top", None))
            self.remember_choice("quality", preset["quality"])
        self.size_field.value = "%g" % preset["max_mb"] if preset.get("max_mb") else ""
        self.app.remember("max_mb", self.size_field.value)
        self.no_sound_switch.value = bool(preset.get("no_sound"))
        self.loudness.value, self.no_silence.value = bool(preset.get("loudness")), bool(preset.get("no_silence"))
        self.crop_dd.value, self.turn_dd.value = preset.get("crop") or "", preset.get("turn") or ""
        self.refresh_options()
        return ""

    def use_site_preset(self):
        """The preset chosen for this site in Settings, used at once."""
        platform = self.state["platform"].lower()
        preset = next((p for p in saved_presets(self.app) if any(
            platform.startswith(site.lower().replace(" ", "")) for site in p.get("sites") or [] if site)), None)
        if preset is not None and not self.apply_preset(preset):
            set_status(self.download_status, "info", "Preset “%s” used for %s (Settings > Downloads > Presets)."
                       % (preset["name"], self.state["platform"]))

    def current_preset(self):
        """The options shown, as a preset (without its name)."""
        preset = {"kind": self.kind_dd.value, "fmt": self.format_dd.value}
        if self.quality_row.visible and self.quality_dd.value:
            preset["quality"] = self.quality_dd.value
        try:
            limit = self.read_max_mb() if self.size_box.visible else None
        except ValueError:
            limit = None
        if limit:
            preset["max_mb"] = limit
        if self.no_sound_switch.visible and self.no_sound_switch.value:
            preset["no_sound"] = True
        for switch, key in ((self.loudness, "loudness"), (self.no_silence, "no_silence")):
            if switch.visible and switch.value:
                preset[key] = True
        if self.picture_box.visible:
            preset.update({key: dd.value for key, dd in (("crop", self.crop_dd), ("turn", self.turn_dd)) if dd.value})
        return preset

    def remember_and_refresh(self, key, control):
        def handler(e):
            self.app.remember(key, control.value)
            self.refresh_options()
            self.page.update()
        return handler

    def show_fab(self, on_this_tab):
        """Mobile: the floating Download button, while an analyzed video is shown on this tab."""
        self.fab.visible = self.app.needs_bytes and on_this_tab and self.result_card.visible

    # Player: the video as it will be downloaded (quality, part, speed, subtitles), without converting
    # anything: the player picks the stream, mpv cuts the part, plays faster or slower and shows the
    # subtitles fetched from the site.

    @property
    def player(self):
        return self.preview.player

    async def toggle_player(self, e=None):
        if not self.can_play:
            return
        if self.preview.player is None:
            await self.open_player()
        else:
            self.close_player()
        self.page.update()

    def preview_settings(self):
        """What the player should play now: (url, sound url, headers, part, subtitle choice or None)."""
        fmt = self.format_dd.value
        quality = self.quality_dd.value if self.quality_row.visible else "best"
        tracks = self.chosen_audio_tracks()
        url, audio_url, headers = player_choice(self.state["player"], quality, sound_only=fmt in AUDIO_FORMATS,
                                                audio_lang=tracks[0] if tracks else None)
        try:
            clip = self.clip.read() if self.clip.box.visible else None
        except ValueError:
            clip = None
        sub = None
        chosen = self.chosen_subtitles()
        if chosen and self.subs_mode_dd.value in ("file", "embed", "burn"):  # the first one is shown
            sub = next((c for c in self.state["subs"] if subtitle_key(c) == chosen[0]), None)
        return url, audio_url, headers, clip, sub

    def chosen_audio_tracks(self):
        """The languages of the sound tracks chosen (the first plays by default), or None for the video's own."""
        if not self.audio_track_row.visible:
            return None
        default = next((t["lang"] for t in self.state["audio_tracks"] if t["default"]), None)
        value = self.audio_picker.value
        return value if value and value != [default] else None

    def preview_speed(self):
        return self.speed.value(default=1.0) if self.speed_row.visible else 1.0

    def sound_only_preview(self):
        source = self.state["player"]
        return source["audio_only"] or (self.format_dd.value in AUDIO_FORMATS and bool(source["audio_url"]))

    async def open_player(self):
        """Start (or restart, after a change of the options) the player."""
        url, audio_url, headers, clip, sub = self.preview_settings()
        self.player_key = (url, audio_url, clip, subtitle_key(sub) if sub else "")
        subtitles = None
        if sub:
            try:
                text = await asyncio.to_thread(preview_subtitles, sub, clip)
            except Exception:
                text = ""
            if text.strip():
                subtitles = flet_video.VideoSubtitleTrack(src=text, title=sub["name"], language=sub["lang"])
        sound_only = self.sound_only_preview()
        self.preview.set_silent(self.no_sound_switch.visible and bool(self.no_sound_switch.value))
        self.preview.start(player_resource(url, audio_url, clip), headers, sound_only, self.preview_speed(),
                           subtitles, self.state["title"])
        # Sound alone: the audio bar under the thumbnail; a video takes the thumbnail's place.
        self.thumb_box.visible = sound_only and bool(self.state["thumbnail"])
        self.close_btn.visible = True
        self.frame_btn.visible = not sound_only and BACKEND == "ffmpeg" and self.editing is None
        self.mark_row.visible = self.clip.box.visible
        silent = self.state["player"]["silent"] and not sound_only
        self.preview.show_note("No sound in this preview: the download will have it." if silent else "")

    def sync_player(self):
        """The options changed: the speed and the sound change at once; another stream, part or subtitles
        restart the player once the options stop changing (a slider being dragged)."""
        if self.preview.player is None:
            return
        self.preview.set_speed(self.preview_speed())
        self.preview.set_silent(self.no_sound_switch.visible and bool(self.no_sound_switch.value))
        url, audio_url, _, clip, sub = self.preview_settings()
        if (url, audio_url, clip, subtitle_key(sub) if sub else "") != self.player_key:
            self.reload_token += 1
            self.page.run_task(self.reload_player, self.reload_token)

    async def reload_player(self, token):
        await asyncio.sleep(0.8)
        if token != self.reload_token or self.preview.player is None:
            return
        await self.open_player()
        self.page.update()

    def close_player(self):
        """Stop and remove the player (the thumbnail comes back)."""
        if self.preview.player is None:
            return
        self.preview.stop()
        self.player_key = None
        self.frame_btn.visible = self.mark_row.visible = self.close_btn.visible = False
        self.thumb_box.visible = bool(self.state["thumbnail"]) or self.can_play

    async def player_moment(self):
        """The moment of the video the player shows, in seconds of the whole video (None: none)."""
        if self.preview.player is None:
            return None
        try:
            seconds = await self.preview.player.get_current_position()
        except Exception:
            return None
        seconds = seconds.in_milliseconds / 1000 if isinstance(seconds, ft.Duration) else 0.0
        try:
            clip = self.clip.read() if self.clip.box.visible else None
        except ValueError:
            clip = None
        return seconds + (clip[0] if clip else 0.0)  # the player plays the part chosen

    async def mark_moment(self, which):
        """The clip starts (or ends) at the moment the player shows; an image is taken there."""
        seconds = await self.player_moment()
        if seconds is None:
            return
        field = self.clip.start if which == "start" else self.clip.end
        field.value = format_moment(seconds)
        if which == "start" and self.clip_mode != "still":
            try:
                end = parse_time(self.clip.end.value)
            except ValueError:
                end = None
            if end is not None and end <= seconds:
                self.clip.end.value = ""  # an end before the new start: until the end
        self.clip.sync_slider_from_fields()
        self.clip.update_captions()
        self.refresh_options()
        self.page.update()
        await self.clip.refresh_previews()

    async def save_frame(self, e=None):
        """The picture the player shows, at the video's full quality: a PNG image of that moment, through the
        queue like any download."""
        seconds = await self.player_moment()
        if seconds is None:
            return
        state = self.state
        params = link_params(state["url"], "png", "best", state["platform"])
        params.update(title="%s (%s)" % (state["title"], (format_duration(seconds) or "0:00").replace(":", ".")),
                      thumbnail=state["thumbnail"], uploader=state["uploader"], upload_date=state["upload_date"],
                      video_id=state["video_id"], clip=[seconds, None])
        params["choice"].update(clip=[seconds, None])
        set_status(self.download_status, "info", "Picture at %s added to the queue." % (format_duration(seconds) or "0:00"))
        self.page.update()
        await self.app.queue.run_download(params)

    # Analyze

    async def analyze(self, e=None):
        app, page = self.app, self.page
        text = (self.url_input.value or "").strip()
        if not text:
            return
        self.recent_box.visible = self.search_card.card.visible = False
        hide_status(self.search_status)
        self.remember_recent(text)
        links = all_links(text)
        url = links[0] if links else as_link(text)
        if len(links) <= 1 and (url is None or not is_web_link(url)):
            await self.search(text)  # shown on Home: what the Download page holds is kept
            return
        # A link: shown on the Download page, instead of what was there.
        self.result_card.visible = False
        self.set_editing(None)
        self.close_player()
        self.items.stop_preview()
        self.items.card.visible = False
        hide_status(self.download_status)
        app.open_tool("download")
        if len(links) > 1:
            self.items.build({"title": "%d links" % len(links), "uploader": "", "noun": "links", "total": None,
                              "limit": 0, "entries": [{"url": link, "title": link, "duration": None,
                                                       "thumbnail": ""} for link in links]}, None)
            self.items.card.visible = True
            hide_status(self.analyze_status)
            page.update()
            return

        self.analyze_btn.disabled = True
        set_status(self.analyze_status, "loading", "Analyzing link…")
        page.update()
        try:
            analyze_loop = asyncio.get_running_loop()

            def set_status_and_update(message):
                set_status(self.analyze_status, "loading", message)
                page.update()

            def analyze_notify(fraction, message, detail=None):
                analyze_loop.call_soon_threadsafe(set_status_and_update, message)

            if is_playlist_url(url):
                await self.show_playlist(url, analyze_notify)
                return
            if maybe_feed(url) and await self.show_feed(url):
                return
            data = await asyncio.to_thread(
                call_with_retries, analyze_notify, fetch_info, url, app.cookies_value(),
                cookie_file=saved_cookies_path(),
            )
            if data.get("playlist"):  # an album, a set… on a site whose links do not say it
                if not (data.get("generic") and await self.show_feed(url)):
                    await self.show_playlist(url, analyze_notify)
                return
            self.show_video(data)
            hide_status(self.analyze_status)
        except DownloadError as exc:
            message = clean_error(exc)
            if not_started(message):  # a live not started yet, a channel not live now
                self.offer_live_wait(url, message)
                return
            hint = make_hint(message, url, app.desktop)
            set_status(self.analyze_status, "err", message, hint, error_report(exc, url, hint))
        except Exception as exc:
            set_status(self.analyze_status, "err", "Error: %s" % exc, details=error_report(exc, url))
        finally:
            self.analyze_btn.disabled = False
            page.update()

    def offer_live_wait(self, url, message):
        """A live that has not started: recorded once it starts, if wanted."""
        async def wait(e):
            params = link_params(url, "mp4", "best")
            params.update(live="wait", quick=True)
            set_status(self.analyze_status, "info", "Waiting in the Queue tab: recorded once it starts.")
            self.page.update()
            await self.app.queue.run_download(params)
        set_status(self.analyze_status, "info", "Not live now: %s" % message)
        self.analyze_status.content.controls[-1].controls.append(ft.Row([ft.FilledButton(
            "Record it when it starts", icon=ft.Icons.FIBER_MANUAL_RECORD, on_click=wait)]))

    def show_video(self, data):
        """One analyzed video (what fetch_info returned) in the editor: its details, formats and options."""
        app, page, state = self.app, self.page, self.state
        state["live"] = bool(data.get("live"))
        state["url"], state["title"], state["platform"] = data["url"], data["title"], data["platform"]
        state["uploader"], state["upload_date"] = data["uploader"], data["upload_date"]
        state["video_id"] = data["video_id"]
        state["slideshow"], state["images"] = data["slideshow"], data["images"]
        state["audio_url"] = data.get("audio_url") or ""
        state["formats"], state["duration"] = data["formats"], data["duration"]
        state["measured"] = data.get("measured") or {}
        self.set_subtitles(data.get("subtitles") or [])
        state["tags"] = data.get("tags") or {}
        state["chapters"] = data.get("chapters") or []
        self.build_chapters(state["chapters"])
        state["segments"] = []
        self.sponsor_switch.value = True
        state["audio_tracks"] = data.get("audio_tracks") or []
        default_track = next((t["lang"] for t in state["audio_tracks"] if t["default"]),
                             state["audio_tracks"][0]["lang"] if state["audio_tracks"] else None)
        preferred = app.settings.get("pref_audio")  # Settings > Downloads > Languages
        default_track = next((t["lang"] for t in state["audio_tracks"] if preferred
                              and language_of(t["lang"]) == preferred), default_track)
        self.audio_picker.set([(t["lang"], t["name"]) for t in state["audio_tracks"]],
                              [default_track] if default_track else [])
        self.chapters_switch.value = False
        self.no_sound_switch.value = self.loudness.value = self.no_silence.value = False
        self.crop_dd.value = self.turn_dd.value = ""
        self.speed.set(1)
        titles = [c["title"] for c in state["chapters"][:3]]
        self.chapters_summary = "%d chapters: %s%s" % (len(state["chapters"]), ", ".join(titles),
                                                       "…" if len(state["chapters"]) > 3 else "")
        for key, field in self.tag_fields.items():
            field.value = state["tags"].get(key, "")
        self.clip.reset(data["duration"], data.get("preview"), frame_rate(data["formats"]))
        self.video_title.value = data["title"]
        self.video_meta.value = " · ".join(
            x for x in (
                data["platform"], data["uploader"], format_duration(data["duration"]),
                ("Slideshow of %d photos" % len(data["images"])) if data["images"] else "",
            ) if x
        )
        self.set_formats(data["slideshow"], bool(data["images"]), data["formats"], bool(data["thumbnail"]))
        self.set_qualities(data["formats"], data["duration"], state["measured"])
        self.build_photos(data["images"])
        self.slideshow_note.visible = data["slideshow"]
        if data["images_error"]:
            self.slideshow_note.value = ("The photos cannot be read (%s): only the sound can be downloaded." % data["images_error"])
        elif data["slideshow"] and not CAN_SLIDESHOW:
            self.slideshow_note.value = ("Making a video of the photos needs ffmpeg: only the sound can be downloaded.")
        elif data["slideshow"] and data["platform"] == "Instagram" and not data.get("audio_url"):
            self.slideshow_note.value = ("No music found: import your cookies (Settings) to get it.")
        elif data["slideshow"] and data["platform"] not in ("TikTok", "Instagram"):
            self.slideshow_note.value = ("Photo post without sound.")
        elif data["slideshow"]:
            self.slideshow_note.value = ("Photo post: one photo or all of them, with the sound.")
        self.refresh_options()
        state["thumbnail"] = data["thumbnail"] or ""
        self.thumb.src = data["thumbnail"] or ""
        self.thumb_box.visible = bool(data["thumbnail"])
        state["player"] = data.get("player")
        audio_only = bool(state["player"] and state["player"]["audio_only"])
        self.can_play = self.preview.available and bool(state["player"])
        self.thumb_box.tooltip = ("Tap to listen" if audio_only else "Tap to watch") if self.can_play else None
        self.play_badge.visible = self.can_play
        self.thumb_box.visible = self.thumb_box.visible or self.can_play
        self.result_card.visible = True
        self.show_fab(True)
        if state["live"]:
            self.slideshow_note.value, self.slideshow_note.visible = (
                "Live now: recorded until it ends or you stop it.", True)
            self.download_btn.content = self.fab.content = "Record the live"
        else:
            self.use_site_preset()
        if app.sponsorblock_categories() and is_youtube_url(state["url"]) and state["video_id"]:
            page.run_task(self.load_segments, state["video_id"])

    # An item of a list (items.py), given its own options here

    def set_editing(self, item):
        """Editing an item of the list (or None: a video of its own): the Download button keeps the options
        for that item instead."""
        self.editing = item
        self.item_bar.visible = item is not None
        label = "Use for this one" if item is not None else "Download"
        self.download_btn.content = self.fab.content = label
        self.download_btn.icon = self.fab.icon = ft.Icons.CHECK if item is not None else ft.Icons.DOWNLOAD
        self.app.update_back()

    async def edit_item(self, item):
        """Open an item of the list in the editor: its options as last chosen for it, else those of every item."""
        self.items.stop_preview()
        set_status(self.analyze_status, "loading", "Analyzing %s…" % item["entry"]["title"])
        self.page.update()
        try:
            data = await self.items.info_of(item)
        except Exception as exc:
            set_status(self.analyze_status, "err", "Error: %s" % clean_error(exc),
                       details=error_report(exc, item["entry"]["url"]))
            self.page.update()
            return
        hide_status(self.analyze_status)
        hide_status(self.download_status)
        self.close_player()
        self.items.card.visible = False
        self.show_video(data)
        self.set_editing(item)
        if item["snapshot"] is not None:
            self.restore(item["snapshot"])
        else:
            self.apply_general(self.items.options())
        self.refresh_options()
        self.page.update()

    def back_to_list(self, e=None):
        self.close_player()
        self.result_card.visible = False
        self.set_editing(None)
        self.items.card.visible = True
        self.show_fab(True)
        self.page.update()

    def apply_general(self, options):
        """The options of every item of the list, on the item opened."""
        self.apply_preset({key: options[key] for key in ("kind", "fmt", "quality", "max_mb", "no_sound")})
        edits = options["edits"]
        self.crop_dd.value, self.turn_dd.value = edits.get("crop", ""), edits.get("turn", "")
        self.loudness.value, self.no_silence.value = bool(edits.get("loudness")), bool(edits.get("no_silence"))
        # A language of every item: this one's own subtitles in it, else YouTube's automatic ones.
        keys = [key for key, _ in self.subs_picker.options]
        chosen = []
        for wanted in options["sub_keys"]:
            lang = wanted.partition(":")[2]
            found = next((k for k in ("m:" + lang, "a:" + lang, "a:%s-orig" % lang) if k in keys), None)
            if found:
                chosen.append(found)
        self.subs_picker.set(self.subs_picker.options, chosen)
        self.subs_mode_dd.value = "burn" if "burn" in edits else options["sub_mode"]
        for key, value in options["tags"].items():
            if key in self.tag_fields:
                self.tag_fields[key].value = value

    def value_controls(self):
        """The controls whose values make an item's options (see snapshot)."""
        return [self.kind_dd, self.format_dd, self.quality_dd, self.size_field, self.no_sound_switch, self.loudness,
                self.no_silence, self.crop_dd, self.turn_dd, self.sponsor_switch, self.chapters_switch,
                self.subs_mode_dd, self.duration_dd, self.speed.field, self.clip.start, self.clip.end,
                *self.style_dds.values(), *self.tag_fields.values()]

    def snapshot(self):
        """Everything chosen for the item opened, to show it again the same way (see restore)."""
        return {"values": [control.value for control in self.value_controls()], "subs": self.subs_picker.value,
                "audio": self.audio_picker.value, "chapters": [box.value for box in self.chapter_checks],
                "clips": list(self.clip.clips)}

    def restore(self, snapshot):
        self.show_kind(snapshot["values"][0])
        for control, value in zip(self.value_controls(), snapshot["values"]):
            control.value = value
        self.speed.set(self.speed.value(default=1.0))
        self.subs_picker.set(self.subs_picker.options, snapshot["subs"])
        self.audio_picker.set(self.audio_picker.options, snapshot["audio"])
        for box, value in zip(self.chapter_checks, snapshot["chapters"]):
            box.value = value
        self.clip.clips = list(snapshot["clips"])
        self.clip.sync_slider_from_fields()

    async def search(self, query, source=None, kind=None, start=1):
        """Words instead of a link: the first videos YouTube (or SoundCloud) finds, tapping one analyzes it;
        `start`: the next ones, under those shown."""
        card = self.search_card
        source = source or card.source_dd.value
        kind = card.kind() if kind is None else kind
        self.analyze_btn.disabled = card.more_btn.disabled = True
        set_status(self.search_status, "loading", "Searching %s…" % SEARCH_SOURCES[source])
        self.page.update()
        try:
            results = await asyncio.to_thread(search_youtube, query, cookies_browser=self.app.cookies_value(),
                                              cookie_file=saved_cookies_path(), source=source, kind=kind,
                                              start=start)
            card.build(query, results, more=start > 1)
            card.card.visible = True
            hide_status(self.search_status)
        except Exception as exc:
            message = clean_error(exc)
            set_status(self.search_status, "err", "The search failed: %s" % message,
                       details=error_report(exc, "ytsearch:" + query))
        finally:
            self.analyze_btn.disabled = card.more_btn.disabled = False
            self.page.update()

    async def show_playlist(self, url, notify):
        app = self.app
        playlist = await asyncio.to_thread(
            call_with_retries, notify, fetch_playlist, url, app.cookies_value(),
            cookie_file=saved_cookies_path(), limit=app.playlist_limit(playlist_site(url)),
        )
        self.items.build(playlist, url)
        self.state["url"] = None
        self.items.card.visible = True
        hide_status(self.analyze_status)

    async def show_feed(self, url):
        """A podcast's feed: its episodes in the playlist card, tagged from the feed. False when the link is
        not a feed (it is then read by yt-dlp)."""
        try:
            feed = await asyncio.to_thread(fetch_feed, url, self.app.playlist_limit("podcast"))
        except NotAFeed:
            return False
        self.items.build(feed, url)
        self.state["url"] = None
        self.items.card.visible = True
        hide_status(self.analyze_status)
        return True

    async def analyze_link(self, url):
        self.url_input.value = url
        self.page.update()
        await self.analyze()

    async def paste_link(self, e=None):
        try:
            links = all_links(await self.app.clipboard.get())
        except Exception:
            links = []
        if not links:
            set_status(self.search_status, "err", "No link found in the clipboard.")
            self.page.update()
            return
        await self.analyze_link(" ".join(links))  # several: they go to the queue together

    async def shortcut_download(self):
        """Enter (keyboard shortcuts, Settings): download the video shown, unless a field is being typed in."""
        if self.typing is None and self.result_card.visible and not self.analyze_btn.disabled:
            await self.download()

    async def watch_clipboard(self):
        """Desktop, when turned on in Settings: a link copied anywhere is analyzed right away, and downloaded with
        the options shown (the formats chosen last time) when "added to the queue at once" is on too."""
        seen = None  # clipboard text already handled (or there before the watch started)
        while True:
            await asyncio.sleep(1)
            if not self.app.settings.get("watch_clipboard"):
                seen = None
                continue
            try:
                text = await self.app.clipboard.get()
            except Exception:
                continue
            if seen is None or text == seen:
                seen = text
                continue
            if self.analyze_btn.disabled:  # an analysis is running: try again in a moment
                continue
            seen = text
            link = first_link(text)
            if link and link != (self.url_input.value or "").strip() and await asyncio.to_thread(supported_site, link):
                await self.analyze_link(link)
                if self.app.settings.get("clipboard_queue") and self.result_card.visible:
                    self.page.run_task(self.download)

    async def paste_at_startup(self):
        """Desktop: fill in the link already in the clipboard, if it is one the app can download."""
        try:
            link = first_link(await self.app.clipboard.get())
            if link and await asyncio.to_thread(supported_site, link):
                self.url_input.value = link
                self.page.update()
        except Exception:
            pass

    # Download

    async def download(self, e=None, subs_only=False):
        state = self.state
        if not state["url"]:
            return
        if state.get("live") and self.editing is None:
            params = link_params(state["url"], "mp4", self.quality_dd.value if valid_quality(self.quality_dd.value)
                                 else "best", state["platform"])
            params.update(title=state["title"], thumbnail=state.get("thumbnail") or "", uploader=state["uploader"],
                          live="now")
            set_status(self.download_status, "info", "Recording: follow it (and stop it) in the Queue tab.")
            await self.app.queue.run_download(params)
            return
        url, title, platform = state["url"], state["title"], state["platform"]
        thumbnail_url = state.get("thumbnail") or ""
        fmt, quality = self.format_dd.value or "mp4", self.quality_dd.value
        if not valid_quality(quality):
            quality = "best"
        slideshow_mode = state["slideshow"] and fmt in ("cover", "slideshow")
        sub_keys = self.chosen_subtitles() if (fmt in VIDEO_FORMATS and not slideshow_mode) else []
        if subs_only and not sub_keys:
            set_status(self.download_status, "err", "Choose a subtitle language first.")
            self.page.update()
            return
        sub_mode = self.subs_mode_dd.value
        # The Edit tab's changes, made once downloaded (subtitles drawn onto the picture are one of them).
        edits = {}
        if self.picture_box.visible:
            edits.update(crop=self.crop_dd.value, turn=self.turn_dd.value)
        edits.update(loudness=self.loudness.visible and bool(self.loudness.value),
                     no_silence=self.no_silence.visible and bool(self.no_silence.value))
        if sub_keys and sub_mode == "burn":
            if subs_only:
                sub_mode = "file"  # only the subtitles: there is no picture to draw them onto
            else:
                edits["burn"] = {"key": sub_keys[0], "style": {k: dd.value for k, dd in self.style_dds.items()}}
                sub_keys = []
        if fmt == "gif" and self.bounce_switch.value:
            edits["bounce"] = True
        edits = {key: value for key, value in edits.items() if value}
        sub_key = sub_keys[0] if sub_keys else ""
        seconds = None if self.duration_dd.value in (None, "auto") else float(self.duration_dd.value)
        images, selected = list(state["images"]), state["selected"]
        post = ({"audio_url": state["audio_url"], "platform": platform}
                if (state["slideshow"] and platform in ("Instagram", "Twitter")) else None)
        try:
            clips = self.clip.all_clips() if self.clip.box.visible else []
            if len(clips) > 1:
                clip = None
            else:  # no clip, or the one kept is also the one in the fields
                clip, clips = (clips[0] if clips else None), None
            max_mb = self.read_max_mb() if self.size_box.visible else None
            speed = self.speed.value() if self.speed_row.visible else 1.0
            if fmt == "gif":
                start, end = clip or (0.0, None)
                length = (end or state["duration"] or 0) - start
                if not 0 < length <= GIF_MAX_SECONDS:
                    raise ValueError("A GIF can last %d seconds at most: choose the passage with Start and End."
                                     % GIF_MAX_SECONDS)
            kept = self.kept_chapters() if self.chapters_box.visible else state["chapters"]
            if self.chapters_box.visible and not kept:
                raise ValueError("Check at least one chapter.")
            if clips and any(end is None for _, end in clips) and not state["duration"]:
                raise ValueError("Give each clip an end: the length of this video is not known.")
        except ValueError as exc:
            set_status(self.download_status, "err", str(exc))
            self.page.update()
            return
        speed = None if speed == 1.0 else speed
        split = self.chapters_box.visible and self.chapters_switch.value
        chapters = kept if split else None
        left_out = [[c["start"], c["end"]] for c in state["chapters"] if c not in kept]
        remove_ranges = left_out if left_out and not split else None
        if clips:
            # Several clips: cut from a single download, like chapters.
            def clock(seconds):
                return (format_duration(seconds) or "0:00").replace(":", ".")
            chapters = [{"title": "From %s to %s" % (clock(start), clock(end or state["duration"])),
                         "start": start, "end": end or float(state["duration"])} for start, end in clips]
        sponsorblock = (self.app.sponsorblock_categories()
                        if self.sponsor_switch.visible and self.sponsor_switch.value else None)
        # Only the tags the user changed: the others stay as the site gives them.
        tag_edits = {key: (field.value or "").strip() for key, field in self.tag_fields.items()
                     if self.tags_box.visible and (field.value or "").strip() != state["tags"].get(key, "")}

        # What makes the file: the same video saved another way is another file (no warning).
        choice = {"fmt": fmt, "quality": quality if self.quality_row.visible else None,
                  "clip": list(clip) if clip else None, "speed": speed, "chapters": bool(chapters),
                  "max_mb": max_mb, "subs": [sub_keys, sub_mode] if sub_keys else None, "subs_only": subs_only,
                  "photo": [selected, seconds] if slideshow_mode else None}
        audio_lang = self.chosen_audio_tracks()
        if audio_lang:
            choice["audio"] = audio_lang
        if edits:
            choice["edits"] = edits
        no_sound = self.no_sound_switch.visible and bool(self.no_sound_switch.value)
        if no_sound:
            choice["no_sound"] = True
        for key, value in (("clips", [list(c) for c in clips or []]), ("left_out", left_out),
                           ("sponsorblock", sponsorblock)):
            if value:
                choice[key] = value
        params = {
            "url": url, "title": title, "platform": platform, "thumbnail": thumbnail_url,
            "uploader": state["uploader"], "upload_date": state["upload_date"], "video_id": state["video_id"],
            "fmt": fmt, "quality": quality, "sub_key": sub_key, "sub_keys": sub_keys, "sub_mode": sub_mode,
            "seconds": seconds, "images": images, "selected": selected, "post": post,
            "clip": list(clip) if clip else None, "subs_only": subs_only,
            "max_mb": max_mb, "tag_edits": tag_edits or None, "speed": speed, "chapters": chapters,
            "choice": choice, "audio_lang": audio_lang, "sponsorblock": sponsorblock, "remove_ranges": remove_ranges,
            "no_sound": no_sound, "edits": edits or None,
        }
        if self.editing is not None:  # an item of a list: these options are kept for "Download the selection"
            self.items.set_own(self.editing, params, self.snapshot())
            self.back_to_list()
            return
        if self.app.settings.get("warn_duplicates", True):
            previous = find_downloaded(load_history(), url, platform, state["video_id"], choice)
            if previous is not None and not await self.confirm_download_again(previous):
                return

        set_status(self.download_status, "info", "Added to the queue: follow it in the Queue tab.")
        await self.app.queue.run_download(params)

    async def confirm_download_again(self, entry):
        """This video is in the history: ask before downloading it again. True to download."""
        when = entry.get("time") or "earlier"
        saved_as = entry.get("name") or entry.get("format") or ""
        return await ask(self.page, "Already downloaded",
                         ft.Text("Already downloaded on %s%s. Download it again?"
                                 % (when, " (%s)" % saved_as if saved_as else "")), "Download again")

    def add_to_montage(self, e=None):
        """Its clips (or all of it) join the montage (montage.py)."""
        try:
            clips = self.clip.all_clips() if self.clip.box.visible else []
        except ValueError as exc:
            set_status(self.download_status, "err", str(exc))
            self.page.update()
            return
        self.montage.add(self.state["url"], self.state["title"], clips, self.format_dd.value in VIDEO_FORMATS)
        set_status(self.download_status, "ok", "Added to the montage (%d passage%s): Montage on Home."
                   % (len(self.montage.parts), "" if len(self.montage.parts) == 1 else "s"))
        self.page.update()

    def clear(self):
        """The page's Clear button: no video or list shown any more."""
        self.close_player()
        self.items.stop_preview()
        self.set_editing(None)
        self.result_card.visible = self.items.card.visible = False
        self.state["url"] = None
        hide_status(self.download_status)
        set_status(self.analyze_status, "info", "Search or paste a link on Home.")
        self.show_fab(False)

    async def download_subs_only(self, e=None):
        await self.download(e, subs_only=True)
