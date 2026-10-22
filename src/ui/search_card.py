"""The Download tab's card of a search typed instead of a link: what YouTube (with its filters) or SoundCloud
finds, ten at a time."""

import flet as ft

from core.search import SEARCH_FILTERS, SEARCH_RESULTS, SEARCH_SOURCES
from core.units import format_duration
from ui.style import HEADING_SIZE, RADIUS, S, THUMB_HEIGHT, THUMB_WIDTH, XS, caption, column, row
from ui.widgets import thumb_tile

KIND_NAMES = {"playlist": "Playlist", "channel": "Channel"}


class SearchCard:
    """`on_pick(url)` analyzes a result; `on_search(query, source, kind, start)` searches (again, or further)."""

    def __init__(self, app, on_pick, on_search):
        self.app = app
        self.on_pick = on_pick
        self.on_search = on_search
        self.query = ""
        self.count = 0  # results shown
        self.title = ft.Text("", size=HEADING_SIZE, weight=ft.FontWeight.W_600)
        self.source_dd = ft.Dropdown(label="Search on", value="youtube", expand=True, on_select=self.again,
                                     options=[ft.DropdownOption(key=k, text=t) for k, t in SEARCH_SOURCES.items()])
        self.kind_dd = ft.Dropdown(label="Show", value="", expand=True, on_select=self.again,
                                   options=[ft.DropdownOption(key=k, text=t) for k, t in SEARCH_FILTERS.items()])
        self.list = column([], spacing=S)
        self.more_btn = ft.TextButton("More results", icon=ft.Icons.EXPAND_MORE, visible=False, on_click=self.more)
        self.card = app.stretch_card([self.title, row([self.source_dd, self.kind_dd]), self.list,
                                      ft.Row([self.more_btn], alignment=ft.MainAxisAlignment.CENTER)], visible=False)

    def build(self, query, results, more=False):
        """The results of a search, or `more` of them under those shown."""
        self.query = query
        self.title.value = "%s results for “%s”" % (SEARCH_SOURCES[self.source_dd.value], query)
        self.kind_dd.visible = self.source_dd.value == "youtube"
        rows = [self.result_row(r) for r in results]
        self.list.controls = (self.list.controls if more else []) + rows
        self.count = (self.count if more else 0) + len(results)
        if not self.list.controls:
            self.list.controls = [caption("Nothing found.")]
        self.more_btn.visible = len(results) >= SEARCH_RESULTS

    def result_row(self, r):
        details = " · ".join(x for x in (KIND_NAMES.get(r.get("kind")), r["uploader"],
                                         format_duration(r["duration"])) if x)
        return ft.Container(
            content=row([thumb_tile(r["thumbnail"], THUMB_WIDTH, THUMB_HEIGHT, RADIUS),
                         ft.Column([ft.Text(r["title"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                            weight=ft.FontWeight.W_500), caption(details)],
                                   spacing=XS, tight=True, expand=True)]),
            border_radius=RADIUS, ink=True, padding=XS,
            on_click=lambda e, url=r["url"]: self.app.page.run_task(self.on_pick, url),
        )

    def kind(self):
        return self.kind_dd.value if self.source_dd.value == "youtube" else ""

    async def again(self, e=None):
        """Another source or filter: searched again."""
        if self.query:
            await self.on_search(self.query, self.source_dd.value, self.kind(), 1)

    async def more(self, e=None):
        await self.on_search(self.query, self.source_dd.value, self.kind(), self.count + 1)
