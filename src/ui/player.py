"""The preview player of the Download and Edit tabs (flet-video: mpv inside the app).

A video plays with its own controls; sound alone gets a plain audio bar (play/pause, position, time) and the
player itself stays hidden, as it would only show a black frame. The volume slider and its mute button are the
app's own (the player's would not tell the app its level).

A tab that is hidden takes its player off the screen, and Flutter then disposes of it: remember() keeps
where it was and whether it was playing, so it comes back paused (or playing) at the same moment.
"""

import asyncio

import flet as ft

from core.units import format_duration
from ui.style import RADIUS, caption, row

try:
    import flet_video
except Exception:
    flet_video = None


POLL = 0.5  # seconds between two readings of the position, for the audio bar


def seconds_of(duration):
    return duration.in_milliseconds / 1000 if isinstance(duration, ft.Duration) else float(duration or 0) / 1000


class Preview:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.player = None
        self.sound_only = False
        self.muted = False  # the mute button
        self.silent = False  # the file will have no sound ("No sound")
        self.playing = False
        self.completed = False
        self.dragging = False  # the audio bar's position is being dragged
        self.resume = None  # where it was when its tab was hidden (see remember)
        self.follow_token = 0

        self.box = ft.Container(visible=False, border_radius=RADIUS, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
        self.note = caption(visible=False)
        self.mute_btn = ft.IconButton(icon=ft.Icons.VOLUME_UP, icon_size=18, tooltip="Mute", visible=False,
                                      on_click=self.toggle_mute)
        self.volume = ft.Slider(min=0, max=100, value=app.settings.get("preview_volume", 50), width=160,
                                visible=False, on_change=self.on_volume, on_change_end=self.remember_volume)
        self.play_btn = ft.IconButton(icon=ft.Icons.PAUSE, tooltip="Pause", on_click=self.toggle_play)
        self.position = ft.Slider(min=0, max=1, value=0, expand=True, on_change_start=self.on_drag_start,
                                  on_change_end=self.on_drag_end)
        self.time_text = caption("0:00 / 0:00")
        self.audio_bar = row([self.play_btn, self.position, self.time_text], spacing=0, visible=False)

    @property
    def available(self):
        return flet_video is not None and not self.page.web

    def start(self, resource, headers=None, sound_only=False, speed=1.0, subtitles=None, title=""):
        """Play `resource` (a link, a file, or an edl:// link from player_resource) from the start."""
        options = {}
        if self.app.desktop and not sound_only:
            # A click on the picture plays or pauses; no volume button (see the app's own slider).
            options["controls"] = flet_video.MaterialDesktopVideoControls(
                play_and_pause_on_tap=True, modify_volume_on_scroll=False,
                bottom_button_bar=[flet_video.VideoPlayOrPauseButton(), flet_video.VideoPositionIndicator(),
                                   flet_video.VideoSpacer(), flet_video.VideoFullscreenButton()])
        self.sound_only = sound_only
        self.player = flet_video.Video(
            playlist=[flet_video.VideoMedia(resource, http_headers=headers or None)],
            autoplay=True, title=title or "Preview", volume=self.volume.value, muted=self.muted or self.silent,
            playback_rate=speed, subtitle_track=subtitles,
            # Sound alone: the player stays out of sight, the audio bar replaces it.
            height=1 if sound_only else None, aspect_ratio=None if sound_only else 16 / 9,
            on_error=self.on_error, on_load=self.on_load, on_complete=self.on_complete, **options,
        )
        self.box.content = self.player
        self.box.visible = True
        self.box.opacity = 0 if sound_only else 1
        self.box.height = 1 if sound_only else None
        self.audio_bar.visible = sound_only
        self.playing, self.completed, self.resume = True, False, None
        self.position.value, self.position.max = 0, 1
        self.time_text.value = "0:00 / 0:00"
        self.show_play_state()
        self.mute_btn.visible = self.volume.visible = True
        self.note.visible = False
        self.follow_token += 1
        if sound_only:
            self.page.run_task(self.follow, self.follow_token)

    def stop(self):
        self.player = None
        self.follow_token += 1
        self.box.content = None
        self.box.visible = self.audio_bar.visible = False
        self.mute_btn.visible = self.volume.visible = False
        self.note.visible = False

    def set_speed(self, speed):
        if self.player is not None:
            self.player.playback_rate = speed

    def set_silent(self, silent):
        """The file will have no sound: the preview is muted too."""
        self.silent = silent
        self.apply_mute()

    def show_note(self, text):
        self.note.value = text
        self.note.visible = bool(text)

    # Sound

    def apply_mute(self):
        muted = self.muted or self.silent
        self.mute_btn.icon = ft.Icons.VOLUME_OFF if muted else ft.Icons.VOLUME_UP
        self.mute_btn.tooltip = "Unmute" if self.muted else "Mute"
        if self.player is not None:
            self.player.muted = muted

    def toggle_mute(self, e=None):
        self.muted = not self.muted
        self.apply_mute()
        self.page.update()

    def on_volume(self, e):
        if self.player is not None:
            self.player.volume = self.volume.value
            self.page.update()

    def remember_volume(self, e):
        self.app.remember("preview_volume", round(self.volume.value))

    # Playing

    def show_play_state(self):
        self.play_btn.icon = ft.Icons.PAUSE if self.playing else ft.Icons.PLAY_ARROW
        self.play_btn.tooltip = "Pause" if self.playing else "Play"

    async def toggle_play(self, e=None):
        if self.player is None:
            return
        if self.completed:  # played to the end: from the start again
            self.completed = False
            await self.player.seek(ft.Duration(milliseconds=0))
            await self.player.play()
            self.playing = True
        else:
            await self.player.play_or_pause()
            self.playing = not self.playing
        self.show_play_state()
        self.page.update()

    def on_complete(self, e):
        self.playing, self.completed = False, True
        self.show_play_state()
        self.page.update()

    async def seek(self, seconds):
        if self.player is not None:
            self.completed = False
            await self.player.seek(ft.Duration(milliseconds=int(seconds * 1000)))

    def on_drag_start(self, e):
        self.dragging = True

    async def on_drag_end(self, e):
        self.dragging = False
        await self.seek(self.position.value)

    async def follow(self, token):
        """The audio bar follows the sound while it plays."""
        while token == self.follow_token and self.player is not None:
            if self.resume is None and not self.dragging:
                try:
                    position = seconds_of(await self.player.get_current_position())
                    length = seconds_of(await self.player.get_duration())
                except Exception:
                    position = length = None
                if token != self.follow_token:
                    return
                if length:
                    self.position.max = length
                    self.position.value = min(position, length)
                    self.time_text.value = "%s / %s" % (format_duration(position) or "0:00", format_duration(length))
                    self.page.update()
            await asyncio.sleep(POLL)

    # Hidden and shown again

    async def remember(self):
        """Its tab is about to be hidden (the player is then disposed of): keep where it was and whether it was
        playing; the player made when the tab is shown again starts there, paused if it was paused."""
        if self.player is None:
            return
        try:
            playing = await asyncio.wait_for(self.player.is_playing(), 1)
            position = await asyncio.wait_for(self.player.get_current_position(), 1)
        except Exception:
            return
        self.resume = position
        self.playing = self.player.autoplay = bool(playing)
        self.show_play_state()

    async def on_load(self, e):
        if self.resume is not None and self.player is not None:
            position, self.resume = self.resume, None
            await self.player.seek(position)

    def on_error(self, e):
        self.show_note("This preview cannot be played (%s). The file itself can still work." % (e.data or "unknown error"))
        self.page.update()
