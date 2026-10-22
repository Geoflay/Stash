"""The preview player shared by the Download and Edit tabs: audio bar, mute, and coming back to a hidden tab
(no window: the player's own calls are replaced by stand-ins)."""

import asyncio

import flet as ft
import pytest

import ui.player
from ui.player import Preview


class FakePlayer:
    """Stands in for flet_video.Video's calls to the app's window."""

    def __init__(self, playing=True, position=12.0, length=200.0):
        self.playing, self.position, self.length = playing, position, length
        self.autoplay, self.muted, self.volume, self.calls = True, False, 50, []

    async def is_playing(self):
        return self.playing

    async def get_current_position(self):
        return ft.Duration(milliseconds=int(self.position * 1000))

    async def get_duration(self):
        return ft.Duration(milliseconds=int(self.length * 1000))

    async def seek(self, position):
        self.calls.append(("seek", position.in_milliseconds))
        self.position = position.in_milliseconds / 1000

    async def play(self):
        self.calls.append("play")
        self.playing = True

    async def play_or_pause(self):
        self.calls.append("play_or_pause")
        self.playing = not self.playing


@pytest.fixture
def preview(fake_app, monkeypatch):
    if ui.player.flet_video is None:
        pytest.skip("flet-video is not installed")
    monkeypatch.setattr(ui.player, "POLL", 0.01)
    fake_app.page.run_task = lambda handler, *args: asyncio.ensure_future(handler(*args))
    fake_app.remember = fake_app.settings.__setitem__
    return Preview(fake_app)


async def test_audio_bar(preview):
    preview.start("https://cdn.test/a.m4a", sound_only=True)
    preview.player = player = FakePlayer()
    await asyncio.sleep(0.1)
    assert preview.time_text.value == "0:12 / 3:20" and preview.position.max == 200.0
    await preview.toggle_play()
    assert player.calls == ["play_or_pause"] and preview.play_btn.icon == ft.Icons.PLAY_ARROW
    preview.position.value = 150.0  # dragged
    await preview.on_drag_end(None)
    assert player.calls[-1] == ("seek", 150000)
    preview.on_complete(None)
    await preview.toggle_play()  # played to the end: from the start
    assert player.calls[-2:] == [("seek", 0), "play"] and preview.playing
    preview.stop()
    assert not preview.audio_bar.visible and not preview.mute_btn.visible


async def test_mute_keeps_the_volume(preview):
    preview.start("https://cdn.test/v.mp4")
    preview.volume.value = 70
    preview.on_volume(None)
    preview.toggle_mute()
    assert preview.player.muted and preview.player.volume == 70 and preview.mute_btn.tooltip == "Unmute"
    preview.start("https://cdn.test/other.mp4")  # still muted for the next one
    assert preview.player.muted
    preview.toggle_mute()
    preview.set_silent(True)  # "No sound": muted whatever the button says
    assert preview.player.muted and preview.mute_btn.icon == ft.Icons.VOLUME_OFF


@pytest.mark.parametrize("playing", [False, True])
async def test_back_to_the_tab_as_it_was_left(preview, playing):
    preview.start("https://cdn.test/v.mp4")
    preview.player = player = FakePlayer(playing=playing, position=42.5)
    await preview.remember()  # the tab is hidden: the player is going to be disposed of
    assert player.autoplay is playing  # a paused preview does not start again on its own
    await preview.on_load(None)  # the tab is shown again: a new player is made from the same control
    assert player.calls == [("seek", 42500)] and preview.resume is None
