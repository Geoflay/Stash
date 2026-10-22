"""The Edit tab's changes shared with the Download tab and with "Convert several files": their choices, and the
options of a conversion that work for every file (picture, sound and speed, size limit, tags, cover art)."""

import asyncio
from pathlib import Path

import flet as ft

from core.backend import BACKEND
from core.compress import MB
from core.editor import EDIT_HEIGHTS, cover_picture
from core.tags import LABELS
from core.units import parse_size_mb
from ui.style import XS, caption, column, row
from ui.widgets import OptionGroup, SpeedPicker

CROP_CHOICES = {"": "Unchanged", "9:16": "Vertical 9:16 (TikTok, Shorts, Reels)",
                "9:16blur": "Vertical 9:16, all of it over a blurred copy", "1:1": "Square",
                "16:9": "Wide 16:9", "bars": "Remove the black bars (found automatically)"}
TURN_CHOICES = {"": "Unchanged", "right": "Turn right (90°)", "left": "Turn left (90°)", "180": "Upside down (180°)",
                "mirror": "Mirror (left and right swapped)", "flip": "Flip (top and bottom swapped)"}
STYLE_CHOICES = {
    "size": ("Size", {"small": "Small", "medium": "Medium", "large": "Large"}),
    "color": ("Colour", {"white": "White", "yellow": "Yellow"}),
    "outline": ("Outline", {"outline": "Thin outline", "thick": "Thick outline", "box": "Dark box behind"}),
    "position": ("Position", {"bottom": "Bottom", "top": "Top"}),
}
COMMON_TAGS = ("artist", "album", "album_artist", "genre", "date")  # the title and track are each file's own


async def pick_cover(app):
    """A picture chosen as cover art, once checked: (its path, "") or (None, why it cannot be used; "" when no
    picture was chosen)."""
    files = await app.picker.pick_files(dialog_title="Choose the cover art", file_type=ft.FilePickerFileType.IMAGE)
    if not files or not files[0].path:
        return None, ""
    try:
        await asyncio.to_thread(cover_picture, files[0].path)
    except RuntimeError as exc:
        return None, str(exc)
    return Path(files[0].path), ""


def cover_note(text, cover_file, error):
    """The line under the cover art menu: the picture chosen, or why the one chosen cannot be used."""
    text.value = error or ("New cover art: %s" % Path(cover_file).name if cover_file else "")
    text.color = ft.Colors.ERROR if error else ft.Colors.ON_SURFACE_VARIANT


def choice_dropdown(label, choices, on_change, value=""):
    return ft.Dropdown(label=label, value=value, expand=True, on_select=on_change,
                       options=[ft.DropdownOption(key=k, text=t) for k, t in choices.items()])


def picture_summary(crop, turn):
    """"Vertical 9:16, Turn right": what the crop and turn chosen do."""
    return ", ".join(choices[value].split(" (")[0] for choices, value in ((CROP_CHOICES, crop), (TURN_CHOICES, turn))
                     if value)


class CommonOptions:
    """The changes made to every file of "Convert several files", in folded groups like the Edit tab's.
    Each one is used where it makes sense: no picture change for a sound file, no cover art for a video."""

    def __init__(self, app):
        self.page = page = app.page
        self.ready = False  # no page update while it is being built
        self.app = app
        self.cover_error = ""
        self.height_dd = ft.Dropdown(label="Picture size", value="", expand=True, on_select=self.refresh,
                                     options=[ft.DropdownOption(key="", text="Unchanged")]
                                     + [ft.DropdownOption(key=str(h), text="%dp at most" % h) for h in EDIT_HEIGHTS])
        self.crop_dd = choice_dropdown("Crop", CROP_CHOICES, self.refresh)
        self.turn_dd = choice_dropdown("Turn", TURN_CHOICES, self.refresh)
        self.speed = SpeedPicker(on_change=self.refresh)
        self.speed.box.visible = True
        self.no_sound = ft.Switch(label="Remove the sound (videos)", value=False, on_change=self.refresh)
        self.loudness = ft.Switch(label="Even out the volume", value=False, on_change=self.refresh)
        self.no_silence = ft.Switch(label="Cut out the silences", value=False, on_change=self.refresh)
        self.size_field = ft.TextField(label="Maximum size of each file (MB)", hint_text="No limit", expand=True,
                                       keyboard_type=ft.KeyboardType.NUMBER, on_change=self.refresh)
        self.tag_fields = {key: ft.TextField(label=LABELS.get(key, key.capitalize()), expand=True,
                                             on_blur=self.refresh) for key in COMMON_TAGS}
        self.cover_file = None
        self.cover_dd = ft.Dropdown(label="Cover art (sound files)", value="keep", expand=True, on_select=self.on_cover,
                                    options=[ft.DropdownOption(key="keep", text="Keep each file's own"),
                                             ft.DropdownOption(key="new", text="A picture of this computer…"),
                                             ft.DropdownOption(key="remove", text="Remove it")])
        self.cover_name = caption()
        self.groups = {
            "picture": OptionGroup("Picture", ft.Icons.CROP_ROTATE,
                                   [column([ft.Row([self.height_dd]), row([self.crop_dd, self.turn_dd])],
                                           spacing=XS)], page),
            "sound": OptionGroup("Sound & speed", ft.Icons.GRAPHIC_EQ,
                                 [self.no_sound, self.loudness, self.no_silence, self.speed.box], page),
            "size": OptionGroup("Size limit", ft.Icons.COMPRESS, [ft.Row([self.size_field])], page),
            "tags": OptionGroup("Tags", ft.Icons.LABEL_OUTLINE,
                                [caption("Written into every file (empty: unchanged)"),
                                 row([self.tag_fields[key] for key in ("artist", "album", "date")]),
                                 row([self.tag_fields["album_artist"], self.tag_fields["genre"]])], page),
            "cover": OptionGroup("Cover art", ft.Icons.IMAGE_OUTLINED,
                                 [ft.Row([self.cover_dd]), self.cover_name], page),
        }
        self.box = column([group.box for group in self.groups.values()], spacing=XS)
        if BACKEND != "ffmpeg":  # without ffmpeg (a phone): what PyAV can do
            self.groups["picture"].controls[0].visible = False
            self.loudness.visible = self.no_silence.visible = self.speed.box.visible = False
        self.refresh()
        self.ready = True

    async def on_cover(self, e=None):
        self.cover_error = ""
        if self.cover_dd.value == "new":
            self.cover_file, self.cover_error = await pick_cover(self.app)
            if self.cover_file is None:
                self.cover_dd.value = "keep"
        self.refresh()

    def max_bytes(self):
        """The size limit in bytes (None: none); ValueError if it is not a number."""
        value = parse_size_mb(self.size_field.value)
        return int(value * MB) if value else None

    def refresh(self, e=None):
        """Each group's title line: what is changed in it."""
        sound = [text for on, text in ((self.no_sound.value, "no sound for videos"),
                                       (self.loudness.value, "volume evened out"),
                                       (self.no_silence.value, "no silences")) if on]
        speed = self.speed.value(default=1.0)
        if speed != 1:
            sound.append("×%g" % speed)
        picture = picture_summary(self.crop_dd.value, self.turn_dd.value)
        if self.height_dd.value:
            picture = ", ".join(x for x in ("%sp at most" % self.height_dd.value, picture) if x)
        size = (self.size_field.value or "").strip()
        tags = ", ".join((field.value or "").strip() for field in self.tag_fields.values() if (field.value or "").strip())
        cover_note(self.cover_name, self.cover_file if self.cover_dd.value == "new" else None, self.cover_error)
        for key, summary in (("picture", picture), ("sound", ", ".join(sound)), ("size", size + " MB" if size else ""),
                             ("tags", tags), ("cover", {"new": "new picture", "remove": "removed"}.get(
                                 self.cover_dd.value, ""))):
            self.groups[key].refresh(summary)
        if self.ready:
            self.page.update()

    def read(self):
        """The options of edit_media for every file; ValueError with a message if one is wrong."""
        options = {"height": int(self.height_dd.value) if self.height_dd.value else None,
                   "crop": self.crop_dd.value, "turn": self.turn_dd.value, "no_sound": self.no_sound.value,
                   "loudness": self.loudness.value, "no_silence": self.no_silence.value,
                   "max_bytes": self.max_bytes()}
        speed = self.speed.value()
        if speed != 1:
            options["speed"] = speed
        tags = {key: (field.value or "").strip() for key, field in self.tag_fields.items() if (field.value or "").strip()}
        if tags:
            options["tags"] = tags
        if self.cover_dd.value == "new" and self.cover_file:
            options["cover"] = self.cover_file
        elif self.cover_dd.value == "remove":
            options["cover"] = "remove"
        return {key: value for key, value in options.items() if value}
