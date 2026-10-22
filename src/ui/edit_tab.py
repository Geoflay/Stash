"""The editor of one file of "Edit file(s)" (Home): a video or audio file saved as a changed copy next to it:
another format, a smaller picture, another speed (or for a passage only), a part only, the sound and subtitle
tracks kept (or saved as files of their own), subtitles added, a size limit, new tags, a crop, a turn, a text or
a logo on the picture, the volume evened out, the sound faded in and out, the silences cut out, new cover art. The
options are folded in groups, like for a download. A tap on the file's picture plays it with the part, the speed
and the sound chosen (its picture can be saved), and one picture of the result can be seen before saving.

With several files (the list of edit_more.EditFilesCard), the changes chosen here are kept for this file only.
The tools on several files (joining them, tagging songs) are in edit_more.py too."""

import asyncio
from pathlib import Path

import flet as ft

from core.backend import AUDIO_FORMATS, BACKEND, VIDEO_FORMATS, format_available
from core.compress import MB, can_compress
from core.editor import (
    DEFAULT_SUBTITLE_STYLE, EDIT_AUDIO_FORMATS, EDIT_HEIGHTS, SUBTITLE_EXTENSIONS, SUBTITLE_FILES,
    can_burn_subtitles, edit_media, extract_subtitles, preview_picture, probe_media, save_frame, track_label,
)
from core.errors import Cancelled
from core.preview import player_resource
from core.tags import LABELS
from core.system import show_in_folder
from core.units import format_bytes, format_duration, format_moment, parse_size_mb, parse_time
from core.workdirs import work_folder
from ui.clip import ClipPicker
from ui.edit_more import EditFilesCard, JoinCard, MusicCard, save_next_to
from ui.subtitle_editor import SubtitleEditor
from ui.edit_options import (
    CROP_CHOICES, STYLE_CHOICES, TURN_CHOICES, choice_dropdown, cover_note, pick_cover, picture_summary,
)
from ui.player import Preview
from ui.progress import ProgressPanel
from ui.style import M, RADIUS, TOUCH, XS, caption, column, row, section_title
from ui.widgets import OptionGroup, SpeedPicker, hide_status, make_opener, play_badge, set_status

EDIT_TAGS = ("title", "artist", "album", "date", "album_artist", "genre", "track")
FULL = BACKEND == "ffmpeg"  # without ffmpeg (a phone), only what PyAV can do: cut, convert, tags, size
EDITABLE_SUBTITLES = (".srt", ".vtt")  # the subtitle files the editor corrects
FADES = {"": "None", "1": "1 second", "2": "2 seconds", "3": "3 seconds", "5": "5 seconds", "10": "10 seconds"}
OVERLAYS = {"": "Nothing", "text": "A text", "picture": "A logo (a picture)"}
PLACES = {"bottom-right": "Bottom right", "bottom-left": "Bottom left", "top-right": "Top right",
          "top-left": "Top left", "center": "Middle"}


class EditTab:
    def __init__(self, app):
        self.app = app
        self.page = page = app.page
        self.source = None  # the opened file
        self.info = {}  # what probe_media read from it
        self.subs_file = None  # the subtitle file to add
        self.editing = None  # the file of the list (edit_more.EditFilesCard) opened here

        self.open_btn = ft.TextButton("Add files…", icon=ft.Icons.ADD,
                                      on_click=lambda e: page.run_task(self.files.add_files))
        self.list_bar = row([ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Back to the list",
                                           on_click=lambda e: self.files.back_to_list()),
                             caption("Changes of this file only", expand=True)], visible=False)
        self.file_text = ft.Text("", weight=ft.FontWeight.W_600, selectable=True)
        self.file_details = caption()
        self.open_status = ft.Container(visible=False)
        self.preview = Preview(app)
        # The file's picture (a third in, a song's cover art): a tap plays it here.
        self.thumb = ft.Image(src="", fit=ft.BoxFit.CONTAIN)
        self.play_badge = play_badge()
        self.thumb_box = ft.Container(ft.Stack([self.thumb, self.play_badge], fit=ft.StackFit.EXPAND), aspect_ratio=16 / 9, border_radius=RADIUS, visible=False,
                                      clip_behavior=ft.ClipBehavior.ANTI_ALIAS, alignment=ft.Alignment(0, 0),
                                      bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, tooltip="Play it",
                                      on_click=self.toggle_preview)
        self.close_btn = ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Close the player", visible=False,
                                       on_click=self.toggle_preview)
        self.frame_btn = ft.IconButton(icon=ft.Icons.PHOTO_CAMERA_OUTLINED, tooltip="Save this picture (PNG, full size)",
                                       visible=False, on_click=self.save_frame)
        self.preview_key = None  # what the preview plays (part, picture or sound): a change reloads it
        self.reload_token = 0

        self.format_dd = ft.Dropdown(label="Format", expand=True, on_select=self.on_change)
        self.quality_dd = ft.Dropdown(label="Picture size", expand=True, on_select=self.on_change)
        self.speed = SpeedPicker(on_change=self.refresh)
        self.no_sound = ft.Switch(label="Remove the sound (a smaller file)", value=False, on_change=self.on_change)
        self.loudness = ft.Switch(label="Even out the volume (quiet and loud parts at the same level)", value=False,
                                  on_change=self.on_change)
        self.no_silence = ft.Switch(label="Cut out the silences (podcasts, lessons)", value=False,
                                    on_change=self.on_change)
        self.crop_dd = choice_dropdown("Crop", CROP_CHOICES, self.on_change)
        self.turn_dd = choice_dropdown("Turn", TURN_CHOICES, self.on_change)
        # A text or a logo on the picture, and one picture of the result before saving.
        self.overlay_dd = choice_dropdown("Put on the picture", OVERLAYS, self.on_overlay)
        self.place_dd = choice_dropdown("Where", PLACES, self.on_change, "bottom-right")
        self.overlay_text = ft.TextField(label="Text", expand=True, on_blur=self.on_change, on_submit=self.on_change)
        self.logo_file = None
        self.logo_name = caption()
        self.overlay_box = column([row([self.overlay_dd, self.place_dd]), ft.Row([self.overlay_text]), self.logo_name],
                                  spacing=XS)
        self.picture_img = ft.Image(src="", height=200, fit=ft.BoxFit.CONTAIN, border_radius=RADIUS, visible=False)
        self.picture_btn = ft.TextButton("See the result", icon=ft.Icons.IMAGE_SEARCH, on_click=self.toggle_picture)
        self.picture_note = caption(visible=False)
        self.picture_token = 0
        self.picture_box = column([row([self.crop_dd, self.turn_dd]), self.overlay_box,
                                   ft.Row([self.picture_btn]), self.picture_img, self.picture_note], spacing=XS)
        # A sound file's cover art: its own kept (also when converted), a new picture, or none.
        self.cover_file, self.cover_error = None, ""
        self.cover_dd = ft.Dropdown(label="Cover art", value="keep", expand=True, on_select=self.on_cover)
        self.cover_name = caption()
        self.cover_box = column([ft.Row([self.cover_dd]), self.cover_name], spacing=XS)
        # Files with several sound tracks (languages, commentary): one box per track, all checked at first.
        self.audio_checks = []
        self.audio_caption = caption()
        self.audio_tracks_box = column([self.audio_caption, ft.Column(spacing=0)], spacing=XS)
        # The sound faded in at the start and out at the end; a passage only at another speed.
        self.fade_in = choice_dropdown("Fade in", FADES, self.on_change)
        self.fade_out = choice_dropdown("Fade out", FADES, self.on_change)
        self.fades_row = row([self.fade_in, self.fade_out])
        self.passage_switch = ft.Switch(label="Another speed for a passage only", value=False, on_change=self.on_change)
        self.passage_start = ft.TextField(label="From", hint_text="1:30", expand=True, on_blur=self.on_change)
        self.passage_end = ft.TextField(label="To", hint_text="The end", expand=True, on_blur=self.on_change)
        self.passage_speed = ft.TextField(label="Speed", value="2", suffix="×", width=110,
                                          keyboard_type=ft.KeyboardType.NUMBER, on_blur=self.on_change)
        self.passage_fields = row([self.passage_start, self.passage_end, self.passage_speed], visible=False)
        self.passage_box = column([self.passage_switch, self.passage_fields], spacing=XS)
        self.clip = ClipPicker(page, on_change=self.refresh)
        self.clip_mode = "clip"
        self.bounce = ft.Switch(label="Back and forth (a loop without a jump)", value=False, visible=False,
                                on_change=self.on_change)
        self.size_field = ft.TextField(label="Maximum size (MB)", hint_text="No limit", expand=True,
                                       keyboard_type=ft.KeyboardType.NUMBER, on_change=self.on_change)
        self.size_row = ft.Row([self.size_field])

        self.subs_checks = []
        self.subs_tracks_box = column([caption("Subtitle tracks to keep"), ft.Column(spacing=0)], spacing=XS)
        self.subs_btn = ft.TextButton("Add a subtitle file (.srt, .vtt, .ass)", icon=ft.Icons.SUBTITLES_OUTLINED,
                                      on_click=self.choose_subtitles)
        self.subs_name = ft.Text("", expand=True)
        self.subs_clear = ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Do not add subtitles", visible=False,
                                        on_click=self.clear_subtitles)
        self.subs_edit = ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Correct this subtitle file",
                                       visible=False, on_click=self.edit_subtitles)
        burn = can_burn_subtitles()
        self.subs_mode = ft.Dropdown(
            label="Add them", expand=True, value="burn" if burn else "track", on_select=self.on_change,
            options=[ft.DropdownOption(key="burn", text="Drawn onto the picture (always shown)" if burn
                                       else "Drawn onto the picture (this ffmpeg cannot)", disabled=not burn),
                     ft.DropdownOption(key="track", text="As a track (players can turn it off)")])
        self.subs_mode_row = ft.Row([self.subs_mode], visible=False)
        # How the subtitles drawn onto the picture look.
        self.style_dds = {key: ft.Dropdown(label=label, value=DEFAULT_SUBTITLE_STYLE[key], expand=True,
                                           options=[ft.DropdownOption(key=k, text=t) for k, t in choices.items()])
                          for key, (label, choices) in STYLE_CHOICES.items()}
        self.style_box = column([row([self.style_dds["size"], self.style_dds["color"]]),
                                 row([self.style_dds["outline"], self.style_dds["position"]])],
                                spacing=XS, visible=False)
        self.extract_list = ft.Column(spacing=0)
        self.extract_box = column([caption("Save a subtitle track as a file of its own"), self.extract_list],
                                  spacing=XS)
        self.subs_box = column(
            [self.subs_tracks_box,
             row([self.subs_btn, self.subs_name, self.subs_edit, self.subs_clear]),
             self.subs_mode_row,
             self.style_box,
             caption("Subtitles drawn into the picture cannot be removed.")],
            spacing=XS,
        )

        self.tag_fields = {key: ft.TextField(label=LABELS.get(key, key.capitalize()), expand=True,
                                             on_blur=self.on_change) for key in EDIT_TAGS}
        self.tags_box = column(
            [ft.Row([self.tag_fields["title"]]),
             row([self.tag_fields["artist"], self.tag_fields["album"], self.tag_fields["date"]]),
             row([self.tag_fields["album_artist"], self.tag_fields["genre"], self.tag_fields["track"]])],
        )

        self.save_btn = ft.FilledButton("Save the edited copy", icon=ft.Icons.SAVE, on_click=self.save, height=TOUCH)
        self.progress = ProgressPanel(page)
        self.result = ft.Container(visible=False)
        self.folder_btn = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Open folder", visible=False)
        self.play_btn = ft.IconButton(icon=ft.Icons.PLAY_CIRCLE_OUTLINE, tooltip="Open file", visible=False)

        self.groups = {
            "clip": OptionGroup("Clip", ft.Icons.CONTENT_CUT, [self.clip.box, self.bounce], page),
            "picture": OptionGroup("Picture", ft.Icons.CROP_ROTATE, [self.picture_box], page),
            "sound": OptionGroup("Sound & speed", ft.Icons.GRAPHIC_EQ,
                                 [self.audio_tracks_box, self.no_sound, self.loudness, self.no_silence,
                                  self.fades_row, self.speed.box, self.passage_box], page),
            "cover": OptionGroup("Cover art", ft.Icons.IMAGE_OUTLINED, [self.cover_box], page),
            "subs": OptionGroup("Subtitles", ft.Icons.SUBTITLES_OUTLINED, [self.subs_box, self.extract_box], page),
            "size": OptionGroup("Size limit", ft.Icons.COMPRESS, [self.size_row], page),
            "tags": OptionGroup("Tags", ft.Icons.LABEL_OUTLINE, [self.tags_box], page),
        }
        self.options_card = app.stretch_card(
            [section_title("Changes"), row([self.format_dd, self.quality_dd])]
            + [group.box for group in self.groups.values()], visible=False, expand=3)
        self.action_box = column([row([self.save_btn, self.folder_btn, self.play_btn]), self.progress.box,
                                  self.result], visible=False)

        self.join = JoinCard(app)
        self.music = MusicCard(app)
        file_card = app.stretch_card([row([ft.Container(self.list_bar, expand=True), self.open_btn]),
                                      self.file_text, self.file_details, self.open_status,
                                      self.thumb_box, self.preview.box, self.preview.audio_bar,
                                      row([ft.Container(expand=True), self.frame_btn, self.preview.mute_btn,
                                           self.preview.volume, self.close_btn]),
                                      self.preview.note, self.action_box], expand=2)
        if app.needs_bytes:  # a phone: one card under the other, side by side once there is room
            file_card.col, self.options_card.col = {"xs": 12, "md": 5}, {"xs": 12, "md": 7}
            self.single_row = ft.ResponsiveRow([file_card, self.options_card], spacing=M, run_spacing=M)
        else:
            self.single_row = row([file_card, self.options_card], spacing=M,
                                  vertical_alignment=ft.CrossAxisAlignment.START)
        self.single_row.visible = False
        # Edit file(s): the list of files, or this editor of one of them.
        self.files = EditFilesCard(app, self)
        self.view = column([self.files.card, self.single_row], spacing=M)

    # Opening (from the list of files)

    async def open_file(self, path, record=None):
        """Read `path` and show its changes as at first; False when it cannot be opened."""
        path = Path(path)
        self.close_preview()
        self.editing = record
        several = len(self.files.files) > 1
        self.list_bar.visible = several
        self.save_btn.content, self.save_btn.icon = ("Use for this file", ft.Icons.CHECK) if several \
            else ("Save the edited copy", ft.Icons.SAVE)
        self.file_text.value, self.file_details.value = path.name, ""
        self.thumb_box.visible = False
        self.options_card.visible = self.action_box.visible = False
        set_status(self.open_status, "loading", "Reading the file…")
        self.page.update()
        try:
            info = await asyncio.to_thread(probe_media, path)
        except Exception as exc:
            set_status(self.open_status, "err", "This file cannot be opened: %s" % exc)
            self.source = None
            self.page.update()
            return False
        hide_status(self.open_status)
        self.source, self.info = path, info
        self.file_text.value = path.name
        self.file_details.value = " · ".join(x for x in (
            format_bytes(path.stat().st_size), format_duration(info["duration"]),
            "%dx%d" % (info["width"], info["height"]) if info["height"] else "",
            " + ".join(c for c in (info["video"], info["audio"]) if c),
            "%d subtitle track%s" % (len(info["subtitles"]), "s" if len(info["subtitles"]) > 1 else "")
            if info["subtitles"] else "",
        ) if x)
        self.set_formats()
        self.speed.set(1)
        self.no_sound.value = False
        self.size_field.value = ""
        self.audio_checks = [ft.Checkbox(label=track_label(t, i), value=True, on_change=self.on_change)
                             for i, t in enumerate(info["audio_tracks"])]
        self.audio_tracks_box.controls[1].controls = self.audio_checks
        self.subs_checks = [ft.Checkbox(label=track_label(t, i), value=True, on_change=self.on_change)
                            for i, t in enumerate(info["subtitles"])]
        self.subs_tracks_box.controls[1].controls = self.subs_checks
        self.extract_list.controls = [
            row([ft.Text(track_label(t, i), expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                 ft.IconButton(icon=ft.Icons.SAVE_ALT, tooltip="Save it next to the file",
                               disabled=t["codec"] not in SUBTITLE_FILES,
                               on_click=lambda e, i=i, t=t: self.page.run_task(self.extract, i, t))])
            for i, t in enumerate(info["subtitles"])]
        self.subs_file = None
        self.loudness.value = self.no_silence.value = self.passage_switch.value = False
        self.crop_dd.value = self.turn_dd.value = self.fade_in.value = self.fade_out.value = self.overlay_dd.value = ""
        self.overlay_text.value, self.logo_file = "", None
        self.picture_img.visible = self.picture_note.visible = False
        self.cover_file, self.cover_dd.value, self.cover_error = None, "keep", ""
        self.cover_dd.options = [
            ft.DropdownOption(key="keep", text="Keep the file's own" if info["cover"] else "None"),
            ft.DropdownOption(key="new", text="A picture of this computer…"),
            ft.DropdownOption(key="remove", text="Remove it", disabled=not info["cover"])]
        for key, field in self.tag_fields.items():
            field.value = info["tags"].get(key, "")
        # Preview frames are read from the file itself.
        self.clip.reset(info["duration"], {"url": str(path), "headers": {}} if info["video"] else None, info["fps"])
        hide_status(self.result)
        self.folder_btn.visible = self.play_btn.visible = False
        self.options_card.visible = self.action_box.visible = True
        self.thumb.src = ""
        picture = bool(info["video"] or info["cover"]) and FULL
        self.thumb_box.visible = picture or self.preview.available
        self.thumb_box.tooltip = "Play it" if self.preview.available else None
        self.play_badge.visible = self.preview.available
        if picture:
            self.page.run_task(self.load_thumb, path, info)
        self.refresh()
        return True

    async def load_thumb(self, path, info):
        """The file's picture: a third in (a song: its cover art)."""
        try:
            async with work_folder() as workdir:
                data = await asyncio.to_thread(preview_picture, path, info, {},
                                               (info["duration"] or 0) / 3 if info["video"] else 0, workdir)
        except Exception:
            data = None
        if path == self.source:
            self.thumb.src = data or ""
            self.thumb_box.visible = (bool(data) or self.preview.available) and self.preview.player is None
            self.page.update()

    def close(self):
        """Back to the list (or nothing left): the player stops."""
        self.close_preview()
        self.editing = self.source = None

    def keep_changes(self):
        """The changes of the file shown, kept for it when the list comes back (more files are added)."""
        if self.editing is None or self.source is None:
            return
        try:
            options = self.read_options()
        except ValueError:
            return
        self.files.keep_own(self.editing, self.format_dd.value, options, self.snapshot())

    # A file's own changes, kept while the others are edited (see edit_more.EditFilesCard)

    def value_controls(self):
        return [self.format_dd, self.quality_dd, self.speed.field, self.no_sound, self.loudness, self.no_silence,
                self.crop_dd, self.turn_dd, self.overlay_dd, self.place_dd, self.overlay_text, self.fade_in,
                self.fade_out, self.passage_switch, self.passage_start, self.passage_end, self.passage_speed,
                self.bounce, self.size_field, self.subs_mode, self.cover_dd, self.clip.start, self.clip.end,
                *self.style_dds.values(), *self.tag_fields.values(), *self.audio_checks, *self.subs_checks]

    def snapshot(self):
        return {"values": [control.value for control in self.value_controls()], "subs_file": self.subs_file,
                "logo_file": self.logo_file, "cover_file": self.cover_file}

    def restore(self, snapshot):
        for control, value in zip(self.value_controls(), snapshot["values"]):
            control.value = value
        self.speed.set(self.speed.value(default=1.0))
        self.subs_file, self.logo_file, self.cover_file = (snapshot["subs_file"], snapshot["logo_file"],
                                                           snapshot["cover_file"])
        self.clip.sync_slider_from_fields()
        self.clip.update_captions()

    def apply_defaults(self, fmt, changes):
        """The changes of every file (edit_more's CommonOptions, and its format), on the file opened."""
        keys = [option.key for option in self.format_dd.options]
        if fmt in keys:
            self.format_dd.value = fmt
        heights = [option.key for option in self.quality_dd.options]
        if changes.height_dd.value and changes.height_dd.value in heights:
            self.quality_dd.value = changes.height_dd.value
        self.crop_dd.value, self.turn_dd.value = changes.crop_dd.value, changes.turn_dd.value
        self.speed.set(changes.speed.value(default=1.0))
        self.no_sound.value = bool(changes.no_sound.value)
        self.loudness.value, self.no_silence.value = bool(changes.loudness.value), bool(changes.no_silence.value)
        self.size_field.value = changes.size_field.value or ""
        for key, field in changes.tag_fields.items():
            if (field.value or "").strip() and key in self.tag_fields:
                self.tag_fields[key].value = field.value.strip()
        if changes.cover_dd.value == "remove" and self.info.get("cover"):
            self.cover_dd.value = "remove"
        elif changes.cover_dd.value == "new" and changes.cover_file:
            self.cover_dd.value, self.cover_file = "new", changes.cover_file

    def set_formats(self):
        info = self.info
        choices = []
        if info["video"]:
            choices += [(key, label) for key, label in VIDEO_FORMATS.items() if format_available(key)]
            choices += [("gif", "GIF (animated)")] if FULL else []
        choices += [(key, AUDIO_FORMATS[key]) for key in EDIT_AUDIO_FORMATS if format_available(key)]
        self.format_dd.options = [ft.DropdownOption(key=k, text=t) for k, t in choices]
        same = self.source.suffix.lower().lstrip(".")
        keys = [k for k, _ in choices]
        self.format_dd.value = same if same in keys else ("mp4" if info["video"] else "mp3")
        height = info["height"] or 0
        self.quality_dd.options = [ft.DropdownOption(key="", text="Unchanged (%dp)" % height if height else "Unchanged")] + [
            ft.DropdownOption(key=str(h), text="%dp" % h) for h in EDIT_HEIGHTS if h < height]
        self.quality_dd.value = ""

    # Options

    def on_change(self, e=None):
        self.refresh()

    def refresh(self):
        fmt = self.format_dd.value
        video_out = fmt in VIDEO_FORMATS
        gif = fmt == "gif"
        self.quality_dd.visible = video_out and FULL
        self.speed.box.visible = not gif and FULL
        self.no_sound.visible = video_out and (len(self.audio_checks) == 1 or not FULL)
        self.audio_tracks_box.visible = not gif and len(self.audio_checks) > 1 and FULL
        self.audio_caption.value = ("Sound tracks to keep (uncheck them all for a video without sound)" if video_out
                                    else "Sound track to keep: an audio file holds one, the first one checked")
        mode = "gif" if gif else "clip"
        self.clip.show(True, video_out, mode)
        if mode != self.clip_mode:
            self.clip_mode = mode
            self.groups["clip"].set_open(gif)  # the passage of a GIF must be chosen
        self.size_row.visible = can_compress(fmt) or gif
        self.subs_box.visible = (video_out or gif) and FULL
        self.extract_box.visible = bool(self.extract_list.controls) and FULL
        self.subs_tracks_box.visible = bool(self.subs_checks) and video_out
        self.subs_name.value = self.subs_file.name if self.subs_file else ""
        self.subs_clear.visible = self.subs_mode_row.visible = bool(self.subs_file)
        self.subs_edit.visible = bool(self.subs_file) and self.subs_file.suffix.lower() in EDITABLE_SUBTITLES
        self.bounce.visible = gif
        if gif and self.subs_mode.value == "track":
            self.subs_mode.value = "burn"  # a GIF has no tracks
        self.style_box.visible = bool(self.subs_file) and self.subs_mode.value == "burn"
        self.tags_box.visible = not gif
        self.picture_box.visible = (video_out or gif) and FULL
        self.overlay_box.visible = video_out
        self.overlay_text.visible = self.overlay_dd.value == "text"
        self.place_dd.visible = bool(self.overlay_dd.value)
        self.logo_name.value = "Logo: %s" % self.logo_file.name if self.overlay_dd.value == "picture" and self.logo_file \
            else ""
        self.picture_btn.content = "Hide the result" if self.picture_img.visible else "See the result"
        has_sound = bool(self.info.get("audio")) and not gif and not self.silent()
        self.loudness.visible = self.no_silence.visible = self.fades_row.visible = has_sound and FULL
        self.passage_box.visible = not gif and FULL
        self.passage_fields.visible = bool(self.passage_switch.value)
        if self.picture_img.visible:
            self.picture_token += 1
            self.page.run_task(self.show_picture, self.picture_token, 0.8)
        self.cover_box.visible = fmt in AUDIO_FORMATS
        cover_note(self.cover_name, self.cover_file if self.cover_dd.value == "new" else None, self.cover_error)
        self.refresh_groups()
        self.sync_preview()
        self.page.update()

    def kept_tracks(self, checks):
        return [i for i, box in enumerate(checks) if box.value]

    def silent(self):
        """Whether the copy will have no sound (the preview is muted then)."""
        if self.no_sound.visible:
            return bool(self.no_sound.value)
        return self.audio_tracks_box.visible and self.format_dd.value in VIDEO_FORMATS \
            and not self.kept_tracks(self.audio_checks)

    def refresh_groups(self):
        """Each group's title line: what is changed in it."""
        try:
            clip = self.clip.read() if self.clip.box.visible else None
        except ValueError:
            clip = None
        sound = []
        if self.silent():
            sound.append("no sound")
        elif self.audio_tracks_box.visible and len(self.kept_tracks(self.audio_checks)) < len(self.audio_checks):
            sound.append("%d of %d tracks" % (len(self.kept_tracks(self.audio_checks)), len(self.audio_checks)))
        speed = self.preview_speed()
        if speed != 1:
            sound.append("×%g" % speed)
        if self.passage_box.visible and self.passage_switch.value:
            sound.append("a passage ×%s" % (self.passage_speed.value or "?"))
        for dd, text in ((self.fade_in, "fade in"), (self.fade_out, "fade out")):
            if self.fades_row.visible and dd.value:
                sound.append(text)
        subs = []
        if self.subs_tracks_box.visible and len(self.kept_tracks(self.subs_checks)) < len(self.subs_checks):
            subs.append("%d of %d tracks" % (len(self.kept_tracks(self.subs_checks)), len(self.subs_checks)))
        if self.subs_box.visible and self.subs_file:
            subs.append("adding %s" % self.subs_file.name)
        if self.loudness.visible and self.loudness.value:
            sound.append("volume evened out")
        if self.no_silence.visible and self.no_silence.value:
            sound.append("no silences")
        cover = {"new": "new picture", "remove": "removed"}.get(self.cover_dd.value, "")
        size = (self.size_field.value or "").strip() if self.size_row.visible else ""
        tags = self.tags_box.visible and any((field.value or "").strip() != self.info["tags"].get(key, "")
                                             for key, field in self.tag_fields.items())
        for key, summary in (
                ("clip", "from %s" % (format_duration(clip[0]) or "0:00")
                 + (" to %s" % format_duration(clip[1]) if clip[1] is not None else "") if clip else ""),
                ("sound", ", ".join(sound)), ("subs", ", ".join(subs)), ("size", size + " MB" if size else ""),
                ("picture", ", ".join(x for x in (picture_summary(self.crop_dd.value, self.turn_dd.value),
                                                  {"text": "a text", "picture": "a logo"}.get(self.overlay_dd.value, "")
                                                  if self.overlay_box.visible else "") if x)), ("cover", cover),
                ("tags", "changed" if tags else "")):
            self.groups[key].refresh(summary)

    # Preview: the file with the part, the speed and the sound chosen (nothing is converted)

    def preview_settings(self):
        """(what the player opens, sound alone)."""
        try:
            clip = self.clip.read() if self.clip.box.visible else None
        except ValueError:
            clip = None
        sound_only = not self.info.get("video") or self.format_dd.value in AUDIO_FORMATS
        return player_resource(str(self.source), "", clip), sound_only

    def preview_speed(self):
        return self.speed.value(default=1.0) if self.speed.box.visible else 1.0

    async def toggle_preview(self, e=None):
        if not self.preview.available:
            return
        if self.preview.player is None:
            self.open_preview()
        else:
            self.close_preview()
        self.page.update()

    def open_preview(self):
        resource, sound_only = self.preview_settings()
        self.preview_key = (resource, sound_only)
        self.preview.set_silent(self.silent())
        self.preview.start(resource, sound_only=sound_only, speed=self.preview_speed(), title=self.source.name)
        self.thumb_box.visible = sound_only and bool(self.thumb.src)  # a video takes the picture's place
        self.close_btn.visible = True
        self.frame_btn.visible = not sound_only and FULL

    def close_preview(self):
        self.preview.stop()
        self.preview_key = None
        self.frame_btn.visible = self.close_btn.visible = False
        self.thumb_box.visible = (bool(self.thumb.src) or self.preview.available) and self.source is not None

    def sync_preview(self):
        """The options changed: the speed and the sound change at once; another part restarts the player
        once the options stop changing."""
        if self.preview.player is None:
            return
        self.preview.set_speed(self.preview_speed())
        self.preview.set_silent(self.silent())
        if self.preview_settings() != self.preview_key:
            self.reload_token += 1
            self.page.run_task(self.reload_preview, self.reload_token)

    async def reload_preview(self, token):
        await asyncio.sleep(0.8)
        if token != self.reload_token or self.preview.player is None:
            return
        self.open_preview()
        self.page.update()

    async def save_frame(self, e=None):
        """The picture the player shows, saved next to the file at its full size."""
        if self.preview.player is None:
            return
        try:
            position = await self.preview.player.get_current_position()
        except Exception:
            return
        try:
            clip = self.clip.read() if self.clip.box.visible else None
        except ValueError:
            clip = None
        seconds = (clip[0] if clip else 0.0) + (position.in_milliseconds / 1000
                                                if isinstance(position, ft.Duration) else 0.0)
        name = "%s (%s).png" % (self.source.stem, (format_moment(seconds) or "0:00").replace(":", "."))
        try:
            async with work_folder() as workdir:
                out = await asyncio.to_thread(save_frame, self.source, seconds, workdir / "frame.png", workdir)
                target = await save_next_to(self.source, name, out, self.app)
            set_status(self.result, "ok", "Picture saved: %s" % target)
            self.show_saved(target)
        except Exception as exc:
            set_status(self.result, "err", "The picture could not be saved: %s" % exc)
        self.page.update()

    def show_saved(self, target):
        """The buttons opening the file saved, and its folder (computer)."""
        if target is None:
            return
        self.folder_btn.on_click = make_opener(show_in_folder, str(target))
        self.play_btn.on_click = make_opener(self.app.open_saved_file, str(target))
        self.folder_btn.visible, self.play_btn.visible = self.app.desktop, self.app.can_open_files()

    # The picture of the result (crop, turn, text or logo), before saving

    async def on_overlay(self, e=None):
        if self.overlay_dd.value == "picture" and self.logo_file is None:
            files = await self.app.picker.pick_files(dialog_title="Choose the logo",
                                                     file_type=ft.FilePickerFileType.IMAGE)
            self.logo_file = Path(files[0].path) if files and files[0].path else None
            if self.logo_file is None:
                self.overlay_dd.value = ""
        self.refresh()

    def picture_options(self):
        options = {"crop": self.crop_dd.value, "turn": self.turn_dd.value}
        if self.quality_dd.visible and self.quality_dd.value:
            options["height"] = int(self.quality_dd.value)
        overlay = self.overlay()
        if overlay:
            options["overlay"], options["overlay_place"] = overlay, self.place_dd.value
        return {key: value for key, value in options.items() if value}

    def overlay(self):
        """The text or logo chosen ({"text"} or {"picture"}), or None."""
        if not self.overlay_box.visible:
            return None
        if self.overlay_dd.value == "text" and (self.overlay_text.value or "").strip():
            return {"text": self.overlay_text.value.strip()}
        if self.overlay_dd.value == "picture" and self.logo_file:
            return {"picture": str(self.logo_file)}
        return None

    async def toggle_picture(self, e=None):
        self.picture_img.visible = not self.picture_img.visible
        self.picture_note.visible = False
        self.refresh()

    async def show_picture(self, token, wait=0.0):
        """One picture of the result, at the start of the part kept (a third of the file without one)."""
        await asyncio.sleep(wait)
        if token != self.picture_token or not self.picture_img.visible or self.source is None:
            return
        try:
            clip = self.clip.read() if self.clip.box.visible else None
        except ValueError:
            clip = None
        seconds = clip[0] if clip else (self.info.get("duration") or 0) / 3
        try:
            async with work_folder() as workdir:
                data = await asyncio.to_thread(preview_picture, self.source, self.info, self.picture_options(),
                                               seconds, workdir)
            if token == self.picture_token:
                self.picture_img.src = data
                self.picture_note.visible = False
        except Exception as exc:
            self.picture_note.value, self.picture_note.visible = "No picture: %s" % exc, True
        self.page.update()

    async def on_cover(self, e=None):
        self.cover_error = ""
        if self.cover_dd.value == "new":
            self.cover_file, self.cover_error = await pick_cover(self.app)
            if self.cover_file is None:
                self.cover_dd.value = "keep"
        self.refresh()

    async def choose_subtitles(self, e=None):
        files = await self.app.picker.pick_files(dialog_title="Choose a subtitle file",
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=SUBTITLE_EXTENSIONS)
        if files and files[0].path:
            self.subs_file = Path(files[0].path)
            self.refresh()

    async def edit_subtitles(self, e=None):
        """Correct the subtitle file added: the corrected copy is the one added."""
        edited = await SubtitleEditor(self.app, self.subs_file).open()
        if edited is not None:
            self.subs_file = edited
            self.refresh()

    async def extract(self, index, track):
        """A subtitle track of the file, saved next to it as a file of its own (name.lang.srt)."""
        try:
            async with work_folder() as workdir:
                out = await asyncio.to_thread(extract_subtitles, self.source, index, track["codec"], workdir)
                name = "%s%s%s" % (self.source.stem, ".%s" % track["lang"] if track["lang"] else "", out.suffix)
                target = await save_next_to(self.source, name, out, self.app)
            set_status(self.result, "ok", "Subtitles saved: %s" % (target or out.name))
            self.show_saved(target)
        except Exception as exc:
            set_status(self.result, "err", "The subtitles could not be saved: %s" % exc)
        self.page.update()

    async def open_subtitle_file(self, e=None):
        """Correct a subtitle file of this computer (without a video)."""
        files = await self.app.picker.pick_files(dialog_title="Choose a subtitle file",
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=list(ext.lstrip(".") for ext in EDITABLE_SUBTITLES))
        if files and files[0].path:
            await SubtitleEditor(self.app, files[0].path).open()

    def clear_subtitles(self, e=None):
        self.subs_file = None
        self.refresh()

    def read_options(self):
        """The changes chosen, for edit_media; ValueError with a message if something is wrong or nothing
        would change."""
        fmt = self.format_dd.value
        options = {}
        if self.quality_dd.visible and self.quality_dd.value:
            options["height"] = int(self.quality_dd.value)
        if self.speed.box.visible:
            speed = self.speed.value()
            if speed != 1:
                options["speed"] = speed
        if self.clip.box.visible:
            options["clip"] = self.clip.read()
        max_mb = parse_size_mb(self.size_field.value) if self.size_row.visible else None
        if max_mb:
            options["max_bytes"] = int(max_mb * MB)
        if self.no_sound.visible and self.no_sound.value:
            options["no_sound"] = True
        if self.bounce.visible and self.bounce.value:
            options["bounce"] = True
        if self.fades_row.visible:
            options["fade_in"] = float(self.fade_in.value) if self.fade_in.value else None
            options["fade_out"] = float(self.fade_out.value) if self.fade_out.value else None
        if self.passage_box.visible and self.passage_switch.value:
            start, end = parse_time(self.passage_start.value) or 0.0, parse_time(self.passage_end.value)
            try:
                speed = float((self.passage_speed.value or "").replace(",", "."))
            except ValueError:
                speed = 0
            if not 0.1 <= speed <= 10:
                raise ValueError("The speed of the passage must be between 0.1 and 10, for example 2.")
            if end is not None and end <= start:
                raise ValueError("The passage must end after it starts.")
            options["passage"] = (start, end, speed)
        overlay = self.overlay()
        if overlay:
            options["overlay"], options["overlay_place"] = overlay, self.place_dd.value
        tracks = {}  # the tracks kept, only when some are left out ([] keeps none)
        if self.audio_tracks_box.visible:
            kept = self.kept_tracks(self.audio_checks)
            if not kept and fmt not in VIDEO_FORMATS:
                raise ValueError("Check the sound track to keep: an audio file needs one.")
            if len(kept) < len(self.audio_checks):
                tracks["audio_keep"] = kept
        if self.subs_tracks_box.visible and len(self.kept_tracks(self.subs_checks)) < len(self.subs_checks):
            tracks["subs_keep"] = self.kept_tracks(self.subs_checks)
        if self.subs_box.visible and self.subs_file:
            options["subs_file"], options["subs_mode"] = self.subs_file, self.subs_mode.value
            if self.style_box.visible:
                options["subs_style"] = {key: dd.value for key, dd in self.style_dds.items()}
        if self.picture_box.visible:
            options["crop"], options["turn"] = self.crop_dd.value, self.turn_dd.value
        if self.loudness.visible and self.loudness.value:
            options["loudness"] = True
        if self.no_silence.visible and self.no_silence.value:
            if options.get("subs_file"):
                raise ValueError("Subtitles cannot be added while the silences are cut out.")
            options["no_silence"] = True
        if self.cover_box.visible and self.cover_dd.value != "keep":
            options["cover"] = self.cover_file if self.cover_dd.value == "new" else "remove"
        if self.tags_box.visible:
            tags = {key: (field.value or "").strip() for key, field in self.tag_fields.items()
                    if (field.value or "").strip() != self.info["tags"].get(key, "")}
            if tags:
                options["tags"] = tags
        options = {key: value for key, value in options.items() if value}
        options.update(tracks)
        if not options and self.source.suffix.lower() == "." + fmt:
            raise ValueError("Nothing to change.")
        return options

    # Saving

    async def save(self, e=None):
        app, page = self.app, self.page
        try:
            options = self.read_options()
        except ValueError as exc:
            set_status(self.result, "err", str(exc))
            page.update()
            return
        if self.editing is not None and len(self.files.files) > 1:  # one of several: saved with the others
            self.files.keep_own(self.editing, self.format_dd.value, options, self.snapshot())
            self.files.back_to_list()
            return
        fmt, source, info = self.format_dd.value, self.source, self.info
        self.save_btn.disabled = True
        self.folder_btn.visible = self.play_btn.visible = False
        hide_status(self.result)
        self.progress.start()
        self.progress.show("Waiting for a free conversion slot…")
        page.update()
        slot = False
        try:
            async with work_folder() as workdir:
                # Editing is converting: it shares the conversion limit with the downloads.
                slot = await app.conversion_slots.acquire(self.progress.cancelled)
                if not slot:
                    raise Cancelled()
                out, note = await asyncio.to_thread(edit_media, source, fmt, info, options, workdir,
                                                    self.progress.reporter())
                app.conversion_slots.release()
                slot = False
                target = await save_next_to(source, "%s (edited)%s" % (source.stem, out.suffix), out, self.app)
                size = out.stat().st_size
            set_status(self.result, "ok", "Saved: %s (%s)%s" % (target or out.name, format_bytes(size),
                                                                 "\n" + note if note else ""))
            self.show_saved(target)
            await app.notify_if_away("Edited copy saved", str(target))
        except Cancelled:
            set_status(self.result, "info", "Cancelled.")
        except Exception as exc:
            set_status(self.result, "err", "Error: %s" % exc)
        finally:
            if slot:
                app.conversion_slots.release()
            self.progress.box.visible = False
            self.save_btn.disabled = False
            page.update()
