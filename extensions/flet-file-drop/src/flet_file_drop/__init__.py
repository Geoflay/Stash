"""Computer (a `flet build` app): files dropped onto the window from the file explorer.

FileDrop wraps the page's content; dropping files anywhere on it calls on_drop with their paths.
"""

from dataclasses import dataclass, field
from typing import Optional

import flet as ft

__all__ = ["FileDrop", "FileDropEvent"]


@dataclass
class FileDropEvent(ft.Event["FileDrop"]):
    files: list[str] = field(default_factory=list)
    """The paths of the files dropped."""


@ft.control("FileDrop")
class FileDrop(ft.LayoutControl):
    """Computer only."""

    content: Optional[ft.Control] = None

    on_drop: Optional[ft.EventHandler[FileDropEvent]] = None
    """Called when files are dropped onto the content."""
