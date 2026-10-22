"""Small building blocks shared by the screens."""

import asyncio
import math

import flet as ft

from core.errors import YOUTUBE_BLOCK_HINT
from core.speed import MAX_SPEED, MIN_SPEED, parse_speed
from ui.style import M, RADIUS, S, SMALL_SIZE, XS, caption, column, row


def hint_box(hint):
    return ft.Container(
        content=ft.Text(hint, size=SMALL_SIZE, selectable=True),
        padding=M,
        border_radius=RADIUS,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
    )


# Set by the app: puts a text into the clipboard (the "Copy the details" button of errors).
clipboard = {"copy": None}


def set_status(holder, kind, text, hint=None, details=None):
    """`kind`: "ok", "err", "warn", "info" or "loading". `details`: the full report of an error, copied by a
    button to paste it into a message."""
    color = {"err": ft.Colors.ERROR, "ok": ft.Colors.GREEN_600, "warn": ft.Colors.ORANGE_700}.get(kind)
    body = [ft.Text(text, color=color, selectable=True)]
    if hint:
        body.append(hint_box(hint))
    if details and clipboard["copy"] is not None:
        def copy(e):
            clipboard["copy"](details)
            e.control.content, e.control.icon = "Copied", ft.Icons.CHECK
            e.control.update()
        body.append(ft.Row([ft.TextButton("Copy the details", icon=ft.Icons.CONTENT_COPY, on_click=copy)]))
    row = []
    if kind == "loading":
        row.append(ft.Container(ft.ProgressRing(width=16, height=16, stroke_width=2),
                                margin=ft.Margin(left=0, right=0, top=2, bottom=0)))
    row.append(ft.Column(body, spacing=S, tight=True, expand=True))
    holder.content = ft.Row(row, spacing=S, vertical_alignment=ft.CrossAxisAlignment.START)
    holder.visible = True


class SpeedPicker:
    """Playing speed (Download and Edit tabs): a logarithmic slider (1x in the middle, 0.1x and 10x at the
    ends) and a field for an exact value. `on_change` is called after each change."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.slider = ft.Slider(min=math.log10(MIN_SPEED), max=math.log10(MAX_SPEED), value=0,
                                divisions=400, expand=True, on_change=self.on_slider,
                                on_change_end=self.on_slider_end)
        self.field = ft.TextField(label="Speed", value="1", suffix="×", width=110,
                                  keyboard_type=ft.KeyboardType.NUMBER, on_change=self.on_field)
        self.box = column([row([self.field, self.slider])], spacing=XS, visible=False)

    def value(self, default=None):
        """The speed typed or chosen; `default` when it is not a valid speed (else ValueError)."""
        try:
            return parse_speed(self.field.value)
        except ValueError:
            if default is None:
                raise
            return default

    def set(self, speed):
        self.field.value = "%g" % speed
        self.slider.value = math.log10(speed)

    def on_slider(self, e):
        """While the slider is dragged only the field follows: refreshing the whole tab at each step (size
        estimate, preview, page) made the slider jump back and forth."""
        self.field.value = "%g" % round(10 ** self.slider.value, 2)
        if e is not None:
            self.field.update()

    def on_slider_end(self, e):
        self.field.value = "%g" % round(10 ** self.slider.value, 2)
        self.on_change()

    def on_field(self, e):
        speed = self.value(default=0)
        if speed:
            self.slider.value = math.log10(speed)
        self.on_change()


class OptionGroup:
    """Options folded under a title (Download and Edit tabs), so that only the format is shown at first. The
    title line says what is changed in it (see refresh), and unfolds it when tapped."""

    def __init__(self, title, icon, controls, page=None):
        self.page = page
        self.controls = controls
        self.summary = caption(expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                               text_align=ft.TextAlign.RIGHT)
        self.arrow = ft.Icon(ft.Icons.EXPAND_MORE, size=20, color=ft.Colors.ON_SURFACE_VARIANT)
        self.content = column(controls, visible=False)
        header = ft.Container(
            row([ft.Icon(icon, size=20), ft.Text(title, weight=ft.FontWeight.W_500), self.summary, self.arrow]),
            padding=ft.Padding(left=XS, right=XS, top=S, bottom=S), border_radius=RADIUS, ink=True,
            on_click=self.toggle,
        )
        self.box = column([ft.Divider(height=1), header, self.content], spacing=XS)

    @property
    def opened(self):
        return self.content.visible

    def set_open(self, value):
        self.content.visible = value
        self.arrow.icon = ft.Icons.EXPAND_LESS if value else ft.Icons.EXPAND_MORE

    def toggle(self, e=None):
        self.set_open(not self.content.visible)
        if self.page is not None:
            self.page.update()

    def refresh(self, summary=""):
        """Shown when one of its options applies; `summary`: what is changed in it ("" when nothing)."""
        self.box.visible = any(control.visible for control in self.controls)
        self.summary.value = summary


class MultiPicker:
    """Several choices out of a list (subtitle languages, sound tracks…): a chip for each one chosen (its cross
    removes it) and a menu adding another. `on_change()` is called after each change."""

    def __init__(self, label, on_change):
        self.on_change = on_change
        self.options = []  # [(key, text)]
        self.chosen = []
        self.chips = ft.Row(wrap=True, spacing=S, run_spacing=S, visible=False)
        self.menu = ft.Dropdown(label=label, expand=True, on_select=self.on_add)
        self.box = column([self.chips, ft.Row([self.menu])], spacing=XS)

    @property
    def value(self):
        return list(self.chosen)

    def text(self, key):
        return dict(self.options).get(key, key)

    def set(self, options, chosen=()):
        self.options = list(options)
        keys = [key for key, _ in self.options]
        self.chosen = [key for key in dict.fromkeys(chosen) if key in keys]
        self.refresh()

    def refresh(self):
        self.chips.controls = [ft.Chip(label=ft.Text(self.text(key)), delete_icon_tooltip="Remove",
                                       on_delete=lambda e, key=key: self.remove(key)) for key in self.chosen]
        self.chips.visible = bool(self.chosen)
        self.menu.options = [ft.DropdownOption(key=k, text=t) for k, t in self.options if k not in self.chosen]
        self.menu.value = None
        self.menu.visible = bool(self.menu.options)

    def on_add(self, e=None):
        if self.menu.value and self.menu.value not in self.chosen:
            self.chosen.append(self.menu.value)
        self.refresh()
        self.on_change()

    def remove(self, key):
        if key in self.chosen:
            self.chosen.remove(key)
        self.refresh()
        self.on_change()


def make_opener(action, path):
    """Click handler opening `path` (open_file, show_in_folder…); a file deleted since is just ignored."""
    def opener(e=None):
        try:
            action(path)
        except Exception:
            pass
    return opener


async def ask(page, title, content, confirm, cancel="Cancel", danger=False, submit=None):
    """A question in a dialog: True once `confirm` is pressed (or Enter in the field `submit`), False for `cancel`.
    `danger`: the confirm button in red (what it does cannot be undone)."""
    answer = asyncio.get_running_loop().create_future()

    def close(value):
        if not answer.done():
            answer.set_result(value)
            page.pop_dialog()
    if submit is not None:
        submit.on_submit = lambda e: close(True)
    style = ft.ButtonStyle(bgcolor=ft.Colors.ERROR, color=ft.Colors.ON_ERROR) if danger else None
    page.show_dialog(ft.AlertDialog(
        modal=True, title=ft.Text(title), content=content,
        actions=[ft.TextButton(cancel, on_click=lambda e: close(False)),
                 ft.FilledButton(confirm, style=style, on_click=lambda e: close(True))]))
    return await answer


def empty_state(icon, title, text, on_click):
    """What an empty screen shows: what goes there, and the button that starts it."""
    return ft.Container(
        content=ft.Column([ft.Icon(icon, size=48, color=ft.Colors.PRIMARY),
                           ft.Column([ft.Text(title, size=16, weight=ft.FontWeight.W_600, text_align=ft.TextAlign.CENTER),
                                      caption(text, text_align=ft.TextAlign.CENTER)],
                                     spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER)],
                          spacing=S, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding(left=M, right=M, top=24, bottom=24), alignment=ft.Alignment(0, 0))


COMPACT_DROPDOWN = 190  # width of a menu at the end of a setting's line
COMPACT_FIELD = 120  # and of a number field


def setting_row(title, control, subtitle=None):
    """One setting on one line: its name (and a few words under it), its switch or menu at the end."""
    names = [ft.Text(title)] + ([caption(subtitle)] if subtitle else [])
    return ft.Container(row([ft.Column(names, spacing=0, tight=True, expand=True), control], spacing=M),
                        padding=ft.Padding(left=XS, right=0, top=2, bottom=2))


def setting_rows(controls):
    """Settings' controls made one line each: a switch or a menu with a label becomes its label, then the switch
    or a small menu; a row of them, one line each. The others are kept as they are."""
    rows = []
    for control in controls:
        if isinstance(control, ft.Row) and control.controls and all(one_line(c, test=True) for c in control.controls):
            rows += [one_line(c) for c in control.controls]
        else:
            rows.append(one_line(control))
    return rows


def one_line(control, test=False):
    """A switch, a menu or a number field with a label, on one line (`test`: whether it can be)."""
    fits = isinstance(control, (ft.Switch, ft.Dropdown)) or (
        isinstance(control, ft.TextField) and control.keyboard_type == ft.KeyboardType.NUMBER)
    fits = fits and isinstance(control.label, str) and bool(control.label)
    if test or not fits:
        return fits if test else control
    title, control.label = control.label, None
    if not isinstance(control, ft.Switch):
        control.expand, control.dense = False, True
        control.width = COMPACT_DROPDOWN if isinstance(control, ft.Dropdown) else COMPACT_FIELD
        control.text_size = SMALL_SIZE + 1
    return setting_row(title, control)


def tool_tile(icon, title, text, on_click, **kwargs):
    """A tool to choose (Home): a big tile with its icon, name and what it does."""
    return ft.Container(
        content=ft.Column([ft.Icon(icon, size=30, color=ft.Colors.PRIMARY),
                           ft.Text(title, size=16, weight=ft.FontWeight.W_600), caption(text)], spacing=XS),
        padding=ft.Padding(left=16, right=16, top=16, bottom=16), border_radius=20, ink=True,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH, on_click=on_click, **kwargs)


def play_badge():
    """The round play sign over a picture that plays when tapped."""
    return ft.Container(ft.Icon(ft.Icons.PLAY_CIRCLE_FILLED, size=56, color=ft.Colors.with_opacity(0.85, "white")),
                        alignment=ft.Alignment(0, 0), visible=False)


def hide_status(holder):
    holder.visible = False
    holder.content = None


def thumb_tile(src, width, height, radius, icon=ft.Icons.VIDEO_LIBRARY_OUTLINED):
    return ft.Container(
        content=ft.Image(src=src, fit=ft.BoxFit.COVER, width=width, height=height, error_content=ft.Icon(icon))
        if src else ft.Icon(icon),
        width=width, height=height, border_radius=radius, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, alignment=ft.Alignment(0, 0),
    )


class PauseBanner:
    """Shown while YouTube downloads are paused (see App.youtube_pause), with the Resume button."""

    def __init__(self, app):
        self.app = app
        self.reason = caption(selectable=True)
        self.box = ft.Container(
            content=column([
                row([ft.Icon(ft.Icons.PAUSE_CIRCLE_OUTLINE, color=ft.Colors.ORANGE_700),
                     ft.Text("YouTube downloads are paused", weight=ft.FontWeight.W_600, expand=True)]),
                ft.Text(YOUTUBE_BLOCK_HINT, size=SMALL_SIZE),
                self.reason,
                ft.Row([ft.Button("Resume", icon=ft.Icons.PLAY_ARROW, on_click=self.resume)]),
            ]),
            padding=M, border_radius=RADIUS, border=ft.Border.all(1, ft.Colors.ORANGE_700), visible=False,
        )
        app.youtube_pause.listeners.append(self.refresh)
        self.refresh()

    def refresh(self):
        reason = self.app.youtube_pause.reason
        self.box.visible = bool(reason)
        self.reason.value = "YouTube said: %s" % reason if reason else ""

    def resume(self, e=None):
        self.app.youtube_pause.resume()
        self.app.page.update()
