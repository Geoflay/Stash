"""The Home tab: the field to paste a link or search words, what a search finds, and the tiles of the tools. A link
analyzed or a tile tapped opens the tool's page in its place, under a line with the back button, the tool's icon
and name, and a button clearing the page. Each page keeps what it holds until it is cleared or replaced."""

import flet as ft

from core.backend import BACKEND
from ui.style import M, TITLE_SIZE, XS, column, row
from ui.widgets import ask, tool_tile

RECENT_ROOM = 280  # the height the list of recent links can take under the field
FULL = BACKEND == "ffmpeg"  # without ffmpeg (a phone): only what PyAV can do


class HomeTab:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        download, edit = app.download, app.edit
        # The tools: (key, icon, name, what it does, its page or None, what clears it, shown).
        tools = [("download", ft.Icons.DOWNLOAD, "Download", "A link analyzed: its formats and options",
                  download.view, download.clear, True)]
        if edit is not None:
            tools += [("file", ft.Icons.MOVIE_EDIT, "Edit file(s)",
                       "Cut, convert, crop, tags…" if FULL else "Cut, convert, tags…", edit.view, edit.files.clear,
                       BACKEND is not None),
                      ("join", ft.Icons.MERGE_TYPE, "Join files", "One after the other, in one file", edit.join.card,
                       edit.join.clear, FULL)]
        tools.append(("montage", ft.Icons.MOVIE_FILTER_OUTLINED, "Montage", "Passages of several videos in one file",
                      download.montage.card, download.montage.clear, FULL))
        if edit is not None:
            tools += [("music", ft.Icons.LIBRARY_MUSIC_OUTLINED, "Tag music", "Titles, covers and lyrics found for you",
                       edit.music.card, edit.music.clear, True),
                      ("subtitles", ft.Icons.SUBTITLES_OUTLINED, "Correct subtitles",
                       "Times and words of a subtitle file", None, None, True)]
        tools = [tool for tool in tools if tool[6]]
        self.tools = {key: (icon, title, content, clear) for key, icon, title, _, content, clear, _ in tools}
        self.current = None  # the tool whose page is shown (None: the field and the tiles)
        tiles = ft.ResponsiveRow([tool_tile(icon, title, text, lambda e, key=key: self.page.run_task(self.on_tile, key),
                                            col={"xs": 12, "sm": 6, "md": 4})
                                  for key, icon, title, text, *_ in tools], spacing=M, run_spacing=M)
        tiles.on_size_change = self.on_tools_size

        # Room under the field for the list of recent links (a tap outside the page's controls is lost): what the
        # tiles of the tools do not take.
        self.room = ft.Container(height=RECENT_ROOM)
        content = column([download.link_card, download.search_card.card, tiles, self.room],
                         spacing=M, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        # The links typed lately float under the field, over the page: showing them moves nothing.
        recent = download.recent_box
        recent.left, recent.right, recent.top = 0, 0, 0
        download.link_card.on_size_change = self.place_recent
        self.main = ft.Stack([content, recent], clip_behavior=ft.ClipBehavior.NONE)

        # A tool's page: its line (back, icon, name, clear), then the page.
        self.icon = ft.Icon(ft.Icons.DOWNLOAD)
        self.title = ft.Text("", size=TITLE_SIZE, weight=ft.FontWeight.W_600, expand=True)
        self.clear_btn = ft.IconButton(icon=ft.Icons.DELETE_SWEEP_OUTLINED, tooltip="Clear this page",
                                       on_click=self.on_clear)
        header = row([ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Back", on_click=self.on_back),
                      self.icon, self.title, self.clear_btn], spacing=XS)
        self.contents = [content for _, _, content, _ in self.tools.values() if content is not None]
        for page in self.contents:
            page.visible = False
        self.tool_page = column([header, *self.contents], spacing=M, visible=False,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.view = app.view("Home", [self.main, self.tool_page])

    def place_recent(self, e):
        self.app.download.recent_box.top = e.height + XS
        self.app.page.update()

    def on_tools_size(self, e):
        height = max(0, RECENT_ROOM - round(e.height))
        if self.room.height != height:
            self.room.height = height
            self.app.page.update()

    # The pages of the tools

    async def on_tile(self, key):
        if key == "subtitles":  # a dialog, not a page
            await self.app.edit.open_subtitle_file()
            return
        await self.remember_preview()
        self.app.open_tool(key)
        self.page.update()

    def show(self, key):
        """`key`'s page (None: the field and the tiles); the other pages keep what they hold."""
        self.current = key
        self.main.visible = key is None
        self.tool_page.visible = key is not None
        if key is not None:
            icon, title, content, _ = self.tools[key]
            self.icon.icon, self.title.value = icon, title
            for page in self.contents:
                page.visible = page is content

    def preview(self):
        """The player of the page shown (it comes back where it was once the page is shown again), or None."""
        if self.current == "download":
            return self.app.download.preview
        return self.app.edit.preview if self.current == "file" else None

    async def remember_preview(self):
        if self.preview() is not None:
            await self.preview().remember()

    async def on_back(self, e=None):
        await self.remember_preview()
        self.back()
        self.page.update()

    def back(self):
        self.app.open_tool(None)

    async def on_clear(self, e=None):
        clear = self.tools[self.current][3]
        if await ask(self.page, "Clear this page?", ft.Text("Everything on this page is removed."), "Clear",
                     danger=True):
            clear()
            self.page.update()
