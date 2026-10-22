"""Android: receive the text (a link) shared to the app with another app's Share button.

The Android side (src/flutter/flet_share_target) adds the app to the Share menu. A link shared while the app
is closed waits until take_shared_text() reads it; one shared while it is open arrives through on_receive.
It also adds a "Paste & download" tile to the quick settings and a widget to the home screen (PASTE_REQUEST).
"""

from dataclasses import dataclass
from typing import Optional

import flet as ft

__all__ = ["PASTE_REQUEST", "ShareReceiveEvent", "ShareTarget"]

# Received instead of a shared text when the "Paste & download" tile or widget opened the app: the app downloads
# the link copied (Android only lets the app on screen read the clipboard).
PASTE_REQUEST = "videodownloader:paste"


@dataclass
class ShareReceiveEvent(ft.Event["ShareTarget"]):
    text: str
    """The shared text: a link, sometimes with words around it."""


@ft.control("ShareTarget")
class ShareTarget(ft.Service):
    """Android only."""

    on_receive: Optional[ft.EventHandler[ShareReceiveEvent]] = None
    """Called when text is shared to the app while it is open."""

    async def take_shared_text(self) -> Optional[str]:
        """The text shared to the app that has not been read yet (it opened the app), else None."""
        return await self._invoke_method("take_shared_text")
