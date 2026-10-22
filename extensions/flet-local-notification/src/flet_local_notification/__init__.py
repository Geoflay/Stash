"""Android: a notification in the status bar; tapping it opens the app.

Since Android 13 the app needs the notification permission first (flet_permission_handler,
Permission.NOTIFICATION).
"""

from dataclasses import dataclass
from typing import Optional

import flet as ft

__all__ = ["LocalNotification", "NotificationCancelEvent"]


@dataclass
class NotificationCancelEvent(ft.Event["LocalNotification"]):
    id: int
    """The `id` of the progress notification whose Cancel button was pressed."""


@ft.control("LocalNotification")
class LocalNotification(ft.Service):
    """Android only."""

    on_cancel: Optional[ft.EventHandler[NotificationCancelEvent]] = None
    """Called when the Cancel button of a progress notification is pressed."""

    async def show(self, title: str, body: str, id: int = 0, alert: bool = True) -> bool:
        """Show a notification (one with the same `id` is replaced), with the phone's sound and vibration, or
        silently (`alert` False). False if notifications are off for the app."""
        return bool(await self._invoke_method("show", {"title": title, "body": body, "id": id, "alert": alert}))

    async def progress(self, id: int, title: str, body: str, percent: int = -1, cancel_label: str = "Cancel") -> bool:
        """Show or update a silent notification with a progress bar (`percent` -1: no known progress) and a
        Cancel button (see on_cancel); it stays until removed with cancel()."""
        return bool(await self._invoke_method("progress", {"id": id, "title": title, "body": body,
                                                           "percent": percent, "cancel_label": cancel_label}))

    async def cancel(self, id: int) -> None:
        """Remove the notification `id`."""
        await self._invoke_method("cancel", {"id": id})
