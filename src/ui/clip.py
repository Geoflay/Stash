"""Extracting a clip: start and end fields, a range slider, and a preview frame of each end.

The same controls choose the passage of a GIF, or the one moment taken as a still image (to the frame: buttons
step one frame or one second, the field takes milliseconds). In the Download tab, several clips can be kept: each
one becomes its own file."""

import asyncio
import os
import tempfile
from pathlib import Path

import flet as ft

from core.backend import BACKEND, CAN_CLIP
from core.preview import make_preview
from core.units import format_duration, format_moment, parse_time
from core.workdirs import workdir_parent
from ui.style import RADIUS, S, XS, caption, row


# Heading of the controls for each use (see ClipPicker.show).
TITLES = {"clip": "Extract a clip (optional)", "gif": "Passage turned into a GIF (30 seconds at most)",
          "still": "Moment of the image"}


def preview_placeholder(text):
    return caption(text, text_align=ft.TextAlign.CENTER)


def make_preview_box():
    return ft.Container(
        content=preview_placeholder("Preview"),
        aspect_ratio=16 / 9, border_radius=RADIUS, expand=True,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        alignment=ft.Alignment(0, 0),
    )


class ClipPicker:
    """The "Extract a clip" part of the Download tab. `on_change` is called when the clip changes. `several`:
    the "Add another clip" button, which keeps the clip and empties the fields for the next one."""

    def __init__(self, page, on_change, several=False):
        self.page = page
        self.on_change = on_change
        self.several = several
        self.clips = []  # the clips kept with "Add another clip", as (start, end)
        self.mode = "clip"  # or "gif", or "still": a single moment (no end)
        self.title = caption(TITLES["clip"])
        self.duration = None  # of the analyzed video
        self.source = None  # where preview frames are read from (see pick_preview_source)
        self.start = ft.TextField(label="Start", hint_text="0:00", expand=True)
        self.end = ft.TextField(label="End", hint_text="1:30", expand=True)
        self.note = caption(visible=False)
        self.slider = ft.RangeSlider(min=0, max=1, start_value=0, end_value=1, visible=False)
        self.moment = ft.Slider(min=0, max=1, value=0, visible=False)  # "still" mode: replaces the range slider
        self.fps = 30.0  # frames per second of the video, for the frame steps
        # "still" mode: one frame or one second before or after the moment.
        self.steps = ft.Row([ft.IconButton(icon=icon, tooltip=tip, on_click=lambda e, step=step: self.page.run_task(
                                 self.step_moment, step))
                             for icon, tip, step in ((ft.Icons.FAST_REWIND, "1 second before", -1.0),
                                                     (ft.Icons.SKIP_PREVIOUS, "1 frame before", -1),
                                                     (ft.Icons.SKIP_NEXT, "1 frame after", 1),
                                                     (ft.Icons.FAST_FORWARD, "1 second after", 1.0))],
                            alignment=ft.MainAxisAlignment.CENTER, spacing=0, visible=False)
        self.preview_state = {"started": False, "tokens": {"start": 0, "end": 0}, "times": {"start": None, "end": None},
                              "files": {"start": None, "end": None}, "dir": None}
        self.preview_boxes = {"start": make_preview_box(), "end": make_preview_box()}
        self.preview_captions = {"start": caption("Start"), "end": caption("End")}
        self.preview_columns = {k: ft.Column([self.preview_boxes[k], self.preview_captions[k]], spacing=XS, expand=True)
                                for k in ("start", "end")}
        self.previews_row = ft.Row(list(self.preview_columns.values()), spacing=S, visible=False)
        self.add_btn = ft.TextButton("Add another clip", icon=ft.Icons.ADD, visible=False, on_click=self.add_clip)
        self.clips_row = ft.Row(wrap=True, spacing=XS, run_spacing=XS, visible=False)
        self.clips_note = caption("Each clip is saved as its own file.", visible=False)
        self.box = ft.Column(
            [self.title,
             self.clips_row,
             self.slider,
             self.moment,
             self.steps,
             self.previews_row,
             row([self.start, self.end]),
             self.add_btn,
             self.clips_note,
             self.note],
            spacing=XS, visible=False,
        )
        for field in (self.start, self.end):
            field.on_change = self.on_field_change
            field.on_blur = self.on_field_done
            field.on_submit = self.on_field_done
        self.slider.on_change = self.on_slider_change
        self.slider.on_change_end = self.on_slider_end
        self.moment.on_change = self.on_moment_change
        self.moment.on_change_end = self.on_moment_end

    def clip_duration(self):
        duration = self.duration
        return float(duration) if duration and duration >= 2 else None

    def update_captions(self):
        total = self.clip_duration()
        if not total:
            return
        self.preview_captions["start"].value = (
            "Moment · %s" % (format_moment(self.slider.start_value) or "0:00") if self.mode == "still"
            else "Start · %s" % (format_duration(self.slider.start_value) or "0:00"))
        self.preview_captions["end"].value = "End · %s" % (format_duration(self.slider.end_value) or "0:00")

    def sync_fields_from_slider(self):
        total = self.clip_duration()
        if not total:
            return
        start, end = round(self.slider.start_value), round(self.slider.end_value)
        if end - start < 1:
            end = min(start + 1, int(total))
            start = max(end - 1, 0)
            self.slider.start_value, self.slider.end_value = float(start), float(end)
        self.start.value = "" if start <= 0 else format_duration(start)
        self.end.value = "" if end >= round(total) else format_duration(end)

    def sync_slider_from_fields(self):
        total = self.clip_duration()
        if not total:
            return
        try:
            start = parse_time(self.start.value) or 0.0
            end = None if self.mode == "still" else parse_time(self.end.value)
        except ValueError:
            return
        end = total if end is None else end
        start, end = min(max(start, 0.0), total), min(max(end, 0.0), total)
        if end >= start:
            self.slider.start_value, self.slider.end_value = start, end
            self.moment.value = start

    def reset(self, duration, source, fps=None):
        """A new video was analyzed: no clip, and previews read from `source`."""
        self.duration = duration
        self.fps = fps or 30.0
        self.clips = []
        self.refresh_clips()
        self.start.value = ""
        self.end.value = ""
        self.end.hint_text = format_duration(duration) or "End of video"
        self.source = source
        self.preview_state["started"] = False
        for key in ("start", "end"):
            self.preview_state["tokens"][key] += 1
            self.preview_state["times"][key] = None
            self.preview_boxes[key].content = preview_placeholder("Preview")
        total = self.clip_duration()
        self.slider.visible = bool(total)
        if total:
            self.slider.min, self.slider.max = 0, total
            self.slider.start_value, self.slider.end_value = 0, total
            self.moment.min, self.moment.max, self.moment.value = 0, total, 0
            self.update_captions()

    def previews_possible(self):
        # Also shown for audio formats: the frames help find the right moment to cut.
        return bool(BACKEND) and not self.page.web and bool(self.source) and bool(self.clip_duration())

    def show(self, is_media, is_video, mode="clip"):
        """Show the clip controls for a video or audio format (not for photo posts), or to choose the
        passage of a GIF or the moment of an image (`mode`)."""
        self.mode = mode
        still = mode == "still"
        self.title.value = TITLES[mode]
        self.box.visible = CAN_CLIP and is_media
        has_range = self.box.visible and bool(self.clip_duration())
        self.slider.visible = has_range and not still
        self.moment.visible = self.steps.visible = has_range and still
        self.moment.value = self.slider.start_value
        self.end.visible = self.preview_columns["end"].visible = not still
        self.start.label = "Moment" if still else "Start"
        self.update_captions()
        self.add_btn.visible = self.box.visible and self.several and mode == "clip"
        self.refresh_clips()
        self.previews_row.visible = self.box.visible and self.previews_possible()
        if self.previews_row.visible and not self.preview_state["started"]:
            self.preview_state["started"] = True
            self.page.run_task(self.refresh_previews, True)
        self.note.value = ("Without ffmpeg, a clip can start a few seconds early." if BACKEND == "av" and is_video else "")
        self.note.visible = bool(self.note.value)

    async def load_preview(self, which, seconds):
        preview_state = self.preview_state
        preview_state["tokens"][which] += 1
        token = preview_state["tokens"][which]
        preview_state["times"][which] = seconds
        self.preview_boxes[which].content = ft.ProgressRing(width=22, height=22, stroke_width=2)
        self.page.update()
        if preview_state["dir"] is None:
            preview_state["dir"] = tempfile.mkdtemp(prefix="videodl_prev_", dir=workdir_parent())
        out = os.path.join(preview_state["dir"], "%s_%d.jpg" % (which, token))
        try:
            path = await asyncio.to_thread(make_preview, self.source, seconds, out, self.duration)
            if token != preview_state["tokens"][which]:
                Path(path).unlink(missing_ok=True)
                return
            self.preview_boxes[which].content = ft.Image(src=str(path), fit=ft.BoxFit.COVER, expand=True)
            previous = preview_state["files"][which]
            preview_state["files"][which] = str(path)
            if previous and previous != str(path):
                Path(previous).unlink(missing_ok=True)
        except Exception:
            if token != preview_state["tokens"][which]:
                return
            self.preview_boxes[which].content = preview_placeholder("Preview unavailable")
        self.page.update()

    async def refresh_previews(self, force=False):
        if not self.previews_possible():
            return
        jobs = []
        points = [("start", self.slider.start_value)]
        if self.mode != "still":
            points.append(("end", self.slider.end_value))
        for which, value in points:
            last = self.preview_state["times"][which]
            # A moment is chosen to the frame: every change shows its picture.
            if force or last is None or abs(value - last) >= (0.001 if self.mode == "still" else 0.5):
                jobs.append(self.load_preview(which, float(value)))
        if jobs:
            await asyncio.gather(*jobs)

    def scale(self):
        """Share of the video the clips keep (1.0 without a clip), for the size estimate."""
        try:
            clips = self.all_clips()
        except ValueError:
            return 1.0
        duration = self.duration
        if not clips or not duration:
            return 1.0
        kept = sum((min(end, duration) if end is not None else duration) - start for start, end in clips)
        return min(max(kept / duration, 0.0), 1.0)

    def several_clips(self):
        """Whether clips were kept with "Add another clip" (shown, in clip mode)."""
        return self.box.visible and self.add_btn.visible and bool(self.clips)

    def all_clips(self):
        """The clips kept, then the one in the fields (if any and not kept yet); ValueError if it is invalid."""
        current = self.read()
        clips = list(self.clips) if self.several_clips() else []
        if current is not None and current not in clips:
            clips.append(current)
        return clips

    def refresh_clips(self):
        self.clips_row.controls = [
            ft.Chip(label=ft.Text("%s – %s" % (format_duration(start) or "0:00",
                                               format_duration(end) if end is not None else "end")),
                    delete_icon_tooltip="Remove this clip", on_delete=lambda e, i=i: self.remove_clip(i))
            for i, (start, end) in enumerate(self.clips)]
        self.clips_row.visible = self.clips_note.visible = self.several_clips()

    def add_clip(self, e=None):
        try:
            clip = self.read()
        except ValueError as exc:
            self.note.value, self.note.visible = str(exc), True
            self.page.update()
            return
        if clip is None:
            self.note.value, self.note.visible = "Choose the start and end of the clip first.", True
            self.page.update()
            return
        if clip not in self.clips:
            self.clips.append(clip)
        self.note.visible = False
        self.start.value = self.end.value = ""
        self.sync_slider_from_fields()
        self.update_captions()
        self.refresh_clips()
        self.on_change()
        self.page.update()

    def remove_clip(self, index):
        if index < len(self.clips):
            del self.clips[index]
        self.refresh_clips()
        self.on_change()
        self.page.update()

    def read(self):
        """The clip as (start, end) in seconds (end None = until the end), or None; ValueError if invalid."""
        start = parse_time(self.start.value)
        end = None if self.mode == "still" else parse_time(self.end.value)
        if start is None and end is None:
            return None
        start = start or 0.0
        duration = self.duration
        if end is not None and end <= start:
            raise ValueError("The clip end must be after its start.")
        if duration and start >= duration:
            raise ValueError("The clip start is after the end of the video (%s)." % format_duration(duration))
        if end is not None and duration and end > duration:
            end = float(duration)
        return (start, end)

    def on_field_change(self, e):
        self.sync_slider_from_fields()
        self.update_captions()
        self.on_change()
        self.page.update()

    def on_slider_change(self, e):
        self.sync_fields_from_slider()
        self.update_captions()
        self.on_change()
        self.page.update()

    def on_moment_change(self, e):
        self.set_moment(self.moment.value)

    def set_moment(self, seconds):
        """The moment of the image, on a frame of the video."""
        total = self.clip_duration() or 0.0
        moment = min(max(round(seconds * self.fps) / self.fps, 0.0), max(total - 1 / self.fps, 0.0))
        self.moment.value = self.slider.start_value = moment
        self.slider.end_value = max(self.slider.end_value, moment)
        self.start.value = format_moment(moment)
        self.update_captions()
        self.on_change()
        self.page.update()

    async def step_moment(self, step):
        """A frame (an int step) or a second (a float one) before or after the moment."""
        try:
            now = parse_time(self.start.value) or 0.0
        except ValueError:
            now = self.moment.value
        self.set_moment(now + (step / self.fps if isinstance(step, int) else step))
        await self.refresh_previews()

    async def on_moment_end(self, e):
        self.on_moment_change(e)
        await self.refresh_previews()

    async def on_slider_end(self, e):
        self.sync_fields_from_slider()
        self.update_captions()
        self.page.update()
        await self.refresh_previews()

    async def on_field_done(self, e):
        self.sync_slider_from_fields()
        self.update_captions()
        self.page.update()
        await self.refresh_previews()
