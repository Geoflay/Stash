"""A small editor of subtitle files (.srt, .vtt), from the Edit tab: each line's times and words corrected, lines
removed or added, every time moved at once (subtitles a little early or late), saved as a new .srt next to it."""

import asyncio
from pathlib import Path

import flet as ft

from core.filenames import unique_path
from core.subtitles import cues_to_srt, parse_cues
from core.units import format_moment, parse_time
from ui.style import S, XS, caption, column, row
from ui.widgets import ask, set_status

MOST_LINES = 500  # lines shown in the editor (a film has about 1500: the rest is kept as it is)


def stamp(seconds):
    return format_moment(seconds) or "0:00"


class SubtitleEditor:
    def __init__(self, app, path):
        self.app = app
        self.page = app.page
        self.path = Path(path)
        self.rows = column([], spacing=XS, scroll=ft.ScrollMode.AUTO, height=380)
        self.shift = ft.TextField(label="Move every line by (seconds)", hint_text="-1.5 or 2", expand=True,
                                  keyboard_type=ft.KeyboardType.NUMBER)
        self.message = ft.Container(visible=False)
        self.rest = []  # the lines past MOST_LINES

    def line(self, start, end, body):
        start_field = ft.TextField(value=stamp(start), width=110, dense=True)
        end_field = ft.TextField(value=stamp(end), width=110, dense=True)
        text = ft.TextField(value="\n".join(body), multiline=True, min_lines=1, max_lines=3, dense=True, expand=True)
        box = row([column([start_field, end_field], spacing=XS), text], vertical_alignment=ft.CrossAxisAlignment.START)
        box.controls.append(ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Remove this line",
                                          on_click=lambda e: self.remove(box)))
        box.data = (start_field, end_field, text)
        return box

    def remove(self, box):
        self.rows.controls.remove(box)
        self.page.update()

    def add_line(self, e=None):
        try:
            last = self.cues()[-1][1]
        except (ValueError, IndexError):
            last = 0.0
        self.rows.controls.append(self.line(last + 0.5, last + 2.5, ["…"]))
        self.page.update()

    def cues(self):
        """The lines as typed: [(start, end, [text lines])]; ValueError with a message if a time is wrong."""
        found = []
        for index, box in enumerate(self.rows.controls):
            start_field, end_field, text = box.data
            try:
                start, end = parse_time(start_field.value) or 0.0, parse_time(end_field.value) or 0.0
            except ValueError as exc:
                raise ValueError("Line %d: %s" % (index + 1, exc))
            if end <= start:
                raise ValueError("Line %d ends before it starts." % (index + 1))
            body = [line for line in (text.value or "").split("\n") if line.strip()]
            if body:
                found.append((start, end, body))
        return found

    def move_all(self, e=None):
        try:
            seconds = float((self.shift.value or "").replace(",", "."))
        except ValueError:
            set_status(self.message, "err", "Type a number of seconds, for example -1.5.")
            self.page.update()
            return
        for box in self.rows.controls:
            for field in box.data[:2]:
                try:
                    field.value = stamp(max((parse_time(field.value) or 0.0) + seconds, 0.0))
                except ValueError:
                    continue
        self.rest = [(max(a + seconds, 0.0), max(b + seconds, 0.0), body) for a, b, body in self.rest]
        set_status(self.message, "ok", "Every line moved by %g seconds." % seconds)
        self.page.update()

    async def open(self):
        """Edit the file; returns the new file saved, or None."""
        try:
            text = await asyncio.to_thread(self.path.read_text, encoding="utf-8", errors="replace")
        except OSError as exc:
            self.page.show_dialog(ft.SnackBar(ft.Text("Could not read %s: %s" % (self.path.name, exc))))
            return None
        cues = parse_cues(text)
        self.rows.controls = [self.line(*cue) for cue in cues[:MOST_LINES]]
        self.rest = cues[MOST_LINES:]
        content = column([
            caption("%d lines%s. Times as 1:02.500."
                    % (len(cues), " (the first %d shown)" % MOST_LINES if self.rest else "")),
            self.rows,
            row([self.shift, ft.TextButton("Move them", icon=ft.Icons.SWAP_HORIZ, on_click=self.move_all)]),
            ft.Row([ft.TextButton("Add a line", icon=ft.Icons.ADD, on_click=self.add_line)]),
            self.message], tight=True, width=640, spacing=S)
        while await ask(self.page, self.path.name, content, "Save"):
            try:
                cues = self.cues() + self.rest
            except ValueError as exc:
                set_status(self.message, "err", str(exc))
                continue
            target = unique_path(self.path.parent, "%s (edited).srt" % self.path.stem)
            await asyncio.to_thread(target.write_text, cues_to_srt(sorted(cues)), encoding="utf-8")
            self.page.show_dialog(ft.SnackBar(ft.Text("Saved: %s" % target)))
            return target
        return None
