"""Android: open a file (a downloaded video, a song…) with the app that plays or shows it.

The file is handed over through the app's FileProvider (set up by `flet build`), as Android requires.
"""

import mimetypes
from typing import Optional

import flet as ft

__all__ = ["OpenFile"]


@ft.control("OpenFile")
class OpenFile(ft.Service):
    """Android only."""

    async def open(self, path: str, mime: Optional[str] = None) -> bool:
        """Open `path`; False when no app on the phone can open this kind of file."""
        mime = mime or mimetypes.guess_type(path)[0] or "*/*"
        return bool(await self._invoke_method("open", {"path": path, "mime": mime}))
