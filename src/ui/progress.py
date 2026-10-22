"""Progress shown while something runs in a worker thread: bar, message and Cancel button."""

import asyncio
import os
import threading
import time

import flet as ft

from core.errors import Cancelled
from core.system import open_file, show_in_folder
from core.units import time_left_text
from ui.style import M, RADIUS, THUMB_HEIGHT, THUMB_WIDTH, XS, caption, row
from ui.widgets import make_opener, set_status, thumb_tile


PROGRESS_INTERVAL = 0.3
ETA_AFTER = 3.0  # seconds of a step before its time left is guessed


class TimeLeft:
    """The time left of the step shown (downloading, converting…): yt-dlp's, from the speed, while
    downloading; else guessed from how long the step has taken so far."""

    def __init__(self):
        self.message, self.started = None, 0.0

    def text(self, fraction, message, detail=None):
        """("…left" or "", the detail without it)."""
        now = time.monotonic()
        if message != self.message:
            self.message, self.started = message, now
        if detail and " - " in detail and detail.endswith(" left"):
            detail, left = detail.rsplit(" - ", 1)
            return left, detail
        elapsed = now - self.started
        if fraction and 0.01 < fraction < 1 and elapsed > ETA_AFTER:
            return time_left_text(elapsed * (1 - fraction) / fraction), detail
        return "", detail


def make_reporter(apply, cancelled):
    """Progress callback for a worker thread: forwards (fraction, message, detail) to `apply` on the
    event loop, at most a few times per second, and raises Cancelled once `cancelled` is set."""
    loop = asyncio.get_running_loop()
    last = {"pct": -1, "msg": "", "time": 0.0}

    def report(fraction, message, detail=None):
        if cancelled.is_set():
            raise Cancelled()
        pct = -1 if fraction is None else int(fraction * 100)
        now = time.monotonic()
        if pct == last["pct"] and message == last["msg"] and now - last["time"] < PROGRESS_INTERVAL:
            return
        last["pct"], last["msg"], last["time"] = pct, message, now
        loop.call_soon_threadsafe(apply, fraction, message, detail)

    return report


class ProgressPanel:
    """Progress bar + message + cancel button, fed from a worker thread.

    Each panel has its own cancel event, so cancelling a playlist never stops a single download.
    """

    def __init__(self, page):
        self.page = page
        self.cancelled = threading.Event()
        self.text = caption(expand=True)
        self.bar = ft.ProgressBar(value=None)
        self.detail = caption(visible=False)
        self.cancel_btn = ft.TextButton("Cancel", icon=ft.Icons.CANCEL, on_click=self.cancel)
        self.box = ft.Column([row([self.text, self.cancel_btn]), self.bar, self.detail], spacing=XS, visible=False)
        self.time_left = TimeLeft()

    def start(self):
        self.cancelled.clear()
        self.cancel_btn.disabled = False
        self.cancel_btn.visible = True
        self.show("Preparing…")
        self.box.visible = True

    def show(self, message, fraction=None, detail=None):
        self.bar.value = fraction
        left, detail = self.time_left.text(fraction, message, detail)
        self.text.value = message if fraction is None else "%s %d %%" % (message, round(fraction * 100))
        if left:
            self.text.value += " · " + left
        self.detail.value = detail or ""
        self.detail.visible = bool(detail)

    def cancel(self, e=None):
        self.cancelled.set()
        self.cancel_btn.disabled = True
        self.text.value = "Cancelling…"
        self.page.update()

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def reporter(self, on_update=None):
        """Thread-safe progress callback; raises Cancelled in the worker once Cancel is pressed."""
        def apply(fraction, message, detail):
            self.show(message, fraction, detail)
            if on_update:
                on_update()
            self.page.update()

        return make_reporter(apply, self.cancelled)


class DownloadRow(ProgressPanel):
    """One entry of the downloads list: thumbnail, progress while it runs, then its result and a remove button.

    Pause stops the work like Cancel (the `cancelled` event), but `held` tells the queue to wait for Resume
    and start again from what is already downloaded (the queue is reordered by dragging the rows).
    `on_show(row, message, fraction)` follows its progress (Android's notification)."""

    def __init__(self, page, title, on_remove, thumbnail="", on_show=None, live=False):
        super().__init__(page)
        self.params = None  # what the queue was given, to try again after a failure
        self.failed = False
        # A live being recorded: "Stop and save" ends it and keeps what is recorded (Cancel does not).
        self.live = live
        self.stop = threading.Event()
        self.running = True
        self.paused = False  # waiting for Resume (YouTube blocking downloads)
        self.held = False  # paused by the user
        self.order = time.monotonic()  # its place in the queue (see Slots), swapped when moved
        self.start_now = False  # "Start now": no waiting for the night (Settings)
        self.on_show = on_show
        self.slots = None  # the JobSlots of this download, once queued
        self.title = ft.Text(title or "video", weight=ft.FontWeight.W_600, max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        self.result = ft.Container(visible=False)
        self.remove_btn = ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Remove from the list", visible=False,
                                        on_click=lambda e: on_remove(self))
        # Shown once the file is saved (desktop), like in the Library tab.
        self.folder_btn = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Open folder", visible=False)
        self.play_btn = ft.IconButton(icon=ft.Icons.PLAY_CIRCLE_OUTLINE, tooltip="Open file", visible=False)
        self.share_btn = ft.IconButton(icon=ft.Icons.SHARE, tooltip="Share", visible=False)
        self.pause_btn = ft.IconButton(icon=ft.Icons.PAUSE, tooltip="Pause", on_click=self.toggle_hold,
                                       visible=not live)
        self.stop_btn = ft.TextButton("Stop and save", icon=ft.Icons.STOP_CIRCLE_OUTLINED, visible=live,
                                      on_click=self.stop_recording)
        self.now_btn = ft.TextButton("Start now", icon=ft.Icons.PLAY_ARROW, visible=False, on_click=self.force_start)
        self.box.controls[0].controls[1:1] = [self.now_btn, self.pause_btn, self.stop_btn]
        details = ft.Column(
            [row([self.title, self.folder_btn, self.play_btn, self.share_btn, self.remove_btn], spacing=0),
             *self.box.controls, self.result],
            spacing=XS, expand=True,
        )
        self.box.controls = [row([thumb_tile(thumbnail, THUMB_WIDTH, THUMB_HEIGHT, RADIUS), details], spacing=M,
                                 vertical_alignment=ft.CrossAxisAlignment.START)]

    def show(self, message, fraction=None, detail=None):
        super().show(message, fraction, detail)
        if self.on_show is not None:
            self.on_show(self, message, fraction)

    def cancel(self, e=None):
        self.held = False
        super().cancel(e)

    def stop_recording(self, e=None):
        self.stop.set()
        self.stop_btn.disabled = True
        self.text.value = "Stopping the recording…"
        self.page.update()

    def force_start(self, e=None):
        self.start_now = True
        self.now_btn.visible = False
        self.page.update()

    def toggle_hold(self, e=None):
        if self.held:
            self.held = False
            self.cancelled.clear()
            self.text.value = "Resuming…"
        else:
            self.held = True
            self.cancelled.set()
            self.text.value = "Pausing…"
        self.pause_btn.icon = ft.Icons.PLAY_ARROW if self.held else ft.Icons.PAUSE
        self.pause_btn.tooltip = "Resume" if self.held else "Pause"
        self.page.update()

    def saving(self):
        """Downloaded and converted: it can no longer be paused, cancelled or moved."""
        for control in (self.cancel_btn, self.pause_btn, self.now_btn, self.stop_btn):
            control.visible = False

    def finish(self, kind, text, hint=None, details=None):
        self.running = False
        self.held = False
        self.failed = kind == "err"
        for control in (self.text, self.bar, self.detail, self.cancel_btn, self.pause_btn, self.now_btn,
                        self.stop_btn):
            control.visible = False
        set_status(self.result, kind, text, hint, details)
        self.remove_btn.visible = True

    def show_saved(self, path, open_action=open_file, with_folder=True):
        """The buttons opening the saved file (with `open_action`) and its folder (computer only); a folder of
        chapters only gets the folder button."""
        self.folder_btn.on_click = make_opener(show_in_folder, path)
        self.play_btn.on_click = make_opener(open_action, path)
        self.folder_btn.visible = with_folder
        self.play_btn.visible = os.path.isfile(path)
        self.saved_path = path

    def show_share(self, share):
        """`share(path)`: a phone's Share menu, or a computer's QR code for a phone (None: no button)."""
        path = getattr(self, "saved_path", None)
        if share is None or not path or not os.path.isfile(path):
            return
        self.share_btn.tooltip = "Share" if share.__name__ == "share_file" else "Send to a phone"
        self.share_btn.on_click = lambda e: self.page.run_task(share, path)
        self.share_btn.visible = True
