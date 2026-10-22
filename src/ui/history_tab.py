"""The Library tab (the history): the downloads made with the app in a table sorted by a tap on a column (title,
type, site, format, date, size), with a search and filters (type, site, format, date, file still there, saved more
than once) opened by the filter button, the room they take, and exported as a table. Each one has a menu (its ⋮
button, a right click or a long press): play it here, open, share, send to a phone, open in Edit file(s),
download again, delete the file, forget it. Several can be selected to do it to all of them."""

import asyncio
import os
from pathlib import Path

import flet as ft

from core.editor import MEDIA_EXTENSIONS
from core.history import (
    FILE_STATES, HISTORY_PERIODS, LIBRARY_TYPES, clear_history, delete_file, entry_size, entry_type, file_state,
    filter_history, forget_missing, history_csv, history_values, load_history, save_history, thumbnail_source,
)
from core.system import show_in_folder
from core.units import format_bytes
from ui.style import RADIUS, S, THUMB_HEIGHT, THUMB_WIDTH, caption, column, row
from ui.player import Preview
from ui.table import Filters, ListTable, TableColumn
from ui.widgets import ask, empty_state, make_opener, thumb_tile

AUDIO_FILES = (".mp3", ".m4a", ".aac", ".opus", ".ogg", ".flac", ".wav", ".wma")

PAGE_SIZE = 30  # entries shown at first, then at each "Show more"
# The order chosen before the columns could be tapped -> (column, ascending).
OLD_SORTS = {"": ("time", False), "oldest": ("time", True), "title": ("title", True), "big": ("size", False),
             "small": ("size", True)}


def entry_key(entry):
    """What tells an entry from the others, also once the Library is read again."""
    return (entry.get("time"), entry.get("url"), entry.get("name"), entry.get("path"))


class HistoryTab:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.items = []  # the whole history, newest first
        self.sizes = {}  # id(entry) -> bytes its file takes now
        self.shown = PAGE_SIZE
        self.selecting = False
        self.selected = set()  # entry_key of the entries selected

        self.search = ft.TextField(hint_text="Search a title or file name", prefix_icon=ft.Icons.SEARCH,
                                   expand=True, dense=True, on_change=self.on_filter)
        self.platform_dd = ft.Dropdown(label="Platform", value="")
        self.format_dd = ft.Dropdown(label="Format", value="")
        self.period_dd = ft.Dropdown(
            label="Date", value="", options=[ft.DropdownOption(key=k, text=t) for k, t in HISTORY_PERIODS.items()])
        self.type_dd = ft.Dropdown(
            label="Type", value="",
            options=[ft.DropdownOption(key="", text="All types")] + [ft.DropdownOption(key=k, text=k) for k in LIBRARY_TYPES])
        self.state_dd = ft.Dropdown(
            label="File", value="", options=[ft.DropdownOption(key=k, text=t) for k, t in FILE_STATES.items()])
        self.filters = Filters([self.type_dd, self.platform_dd, self.format_dd, self.period_dd, self.state_dd],
                               self.on_filter)
        # The table: a tap on a column sorts by it (remembered); newest first at first.
        saved = app.settings.get("library_sort")
        if isinstance(saved, dict) and isinstance(saved.get("key"), str):
            sort_key, ascending = saved["key"], bool(saved.get("ascending"))
        else:
            sort_key, ascending = OLD_SORTS.get(saved if isinstance(saved, str) else "", OLD_SORTS[""])
        self.check_column = TableColumn("check", width=0)
        size = lambda entry: self.sizes.get(id(entry), 0)  # noqa: E731
        self.table = ListTable(app, [
            self.check_column,
            TableColumn("picture", width=THUMB_WIDTH),
            TableColumn("title", "Title", sort=lambda e: e.get("title") or "", expand=3),
            TableColumn("type", "Type", width=80, sort=entry_type, wide=True),
            TableColumn("platform", "Site", width=110, sort=lambda e: e.get("platform") or "", wide=True),
            TableColumn("format", "Format", width=70, sort=lambda e: e.get("format") or ""),
            TableColumn("time", "Date", width=130, sort=lambda e: e.get("time") or "", wide=True),
            TableColumn("size", "Size", width=80, sort=size, wide=True),
            TableColumn("actions", width=48 if app.needs_bytes else 136),
        ], self.cells, self.on_sort, sort_key=sort_key, ascending=ascending,
            wrap=lambda entry, control: self.with_menu(self.actions(entry), control))
        # A file played here, without leaving the app.
        self.preview = Preview(app)
        self.playing = ft.Text("", weight=ft.FontWeight.W_500, expand=True, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS)
        self.player_box = column([row([ft.Icon(ft.Icons.PLAY_CIRCLE_OUTLINE), self.playing,
                                       self.preview.mute_btn, self.preview.volume,
                                       ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Close the player",
                                                     on_click=self.close_player)]),
                                  self.preview.box, self.preview.audio_bar, self.preview.note], visible=False)
        self.empty = empty_state(ft.Icons.VIDEO_LIBRARY_OUTLINED, "Your downloads show here",
                                 "Your downloads show here.",
                                 self.paste_link)
        self.export_btn = ft.IconButton(icon=ft.Icons.TABLE_VIEW_OUTLINED, tooltip="Export the list (a CSV table)",
                                        on_click=self.export_csv)
        self.count = caption()
        self.no_match = caption("No download matches these filters.", visible=False)
        self.more_btn = ft.TextButton("Show more", icon=ft.Icons.EXPAND_MORE, visible=False, on_click=self.show_more)
        self.clear_btn = ft.TextButton("Clear the library", icon=ft.Icons.DELETE_OUTLINE, visible=False)
        self.clear_btn.on_click = self.on_clear
        # Only the entries are forgotten: no file is deleted.
        self.forget_btn = ft.TextButton("Forget the deleted files", icon=ft.Icons.LINK_OFF, visible=False,
                                        on_click=self.on_forget)
        self.select_btn = ft.IconButton(icon=ft.Icons.CHECKLIST, tooltip="Select several", on_click=self.toggle_select)
        # Selecting several: what can be done to all of them.
        self.selection_text = ft.Text("", weight=ft.FontWeight.W_500, expand=True)
        self.selection_bar = ft.Container(
            content=row([self.selection_text,
                         ft.TextButton("Download again", icon=ft.Icons.REPLAY, on_click=self.reuse_selected),
                         ft.TextButton("Delete the files", icon=ft.Icons.DELETE_FOREVER_OUTLINED,
                                       on_click=self.delete_selected),
                         ft.TextButton("Forget", icon=ft.Icons.LINK_OFF, on_click=self.forget_selected),
                         ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Stop selecting", on_click=self.toggle_select)],
                        wrap=False),
            padding=ft.Padding(left=S, right=0, top=0, bottom=0), border_radius=RADIUS,
            bgcolor=ft.Colors.SECONDARY_CONTAINER, visible=False)
        self.refresh()
        self.view = app.view("Library", [
            app.card([
                self.player_box,
                row([self.search, self.filters.buttons]),
                self.filters.panel,
                row([ft.Container(self.count, expand=True), self.forget_btn, self.clear_btn, self.select_btn,
                     self.export_btn]),
                self.selection_bar,
                self.empty,
                self.table.box,
                self.no_match,
                ft.Row([self.more_btn], alignment=ft.MainAxisAlignment.CENTER),
            ]),
        ], visible=False)

    def make_reuse(self, url):
        async def reuse(e=None):
            await self.app.download.analyze_link(url)
        return reuse

    def refresh(self):
        """Read the history again (after a download, or when the tab is opened)."""
        self.items = load_history()
        self.sizes = {id(entry): entry_size(entry) for entry in self.items} if self.app.can_open_files() else {}
        for dd, key, everything in ((self.platform_dd, "platform", "All platforms"),
                                    (self.format_dd, "format", "All formats")):
            values = history_values(self.items, key)
            dd.options = [ft.DropdownOption(key="", text=everything)] + [
                ft.DropdownOption(key=v, text=v.capitalize() if v.islower() else v) for v in values]
            if dd.value not in values:
                dd.value = ""
        self.show_rows()

    def show_rows(self):
        found = self.table.sort(filter_history(self.items, self.search.value, self.platform_dd.value,
                                               self.format_dd.value, self.period_dd.value, kind=self.type_dd.value,
                                               state=self.state_dd.value))
        self.found = found
        self.check_column.width = 40 if self.selecting else 0
        self.table.show_header()
        self.table.show(found[:self.shown])
        filtered = len(found) != len(self.items)
        self.empty.visible = not self.items
        self.no_match.visible = bool(self.items) and not found
        self.filters.buttons.visible = self.search.visible = bool(self.items)
        self.filters.refresh()
        if filtered:
            self.count.value = "%d of %d downloads" % (len(found), len(self.items))
        else:
            self.count.value = "%d download%s" % (len(self.items), "s" if len(self.items) != 1 else "")
        # The room taken by the files shown, and how many were deleted since.
        size = sum(self.sizes.get(id(entry), 0) for entry in found)
        missing = sum(1 for entry in found if self.sizes and file_state(entry) == "missing")
        if size:
            self.count.value += " · %s on this device" % format_bytes(size)
        if missing:
            self.count.value += " · %d deleted since" % missing
        if self.state_dd.value == "duplicates" and found:
            self.count.value += " · keep one of each and delete the others to free room"
        self.count.visible = bool(self.items)
        self.forget_btn.visible = bool(missing)
        self.more_btn.visible = len(found) > self.shown
        self.clear_btn.visible = bool(self.items) and not self.selecting
        self.export_btn.visible = self.select_btn.visible = bool(self.items)
        self.selected &= {entry_key(entry) for entry in self.items}
        self.selection_bar.visible = self.selecting
        self.selection_text.value = "%d selected" % len(self.selected)

    # One entry: a row of the table

    def details(self, entry):
        size = self.sizes.get(id(entry), 0)
        return " · ".join(x for x in (entry.get("platform"), entry.get("format"), entry.get("time"),
                                      format_bytes(size) if size else "") if x)

    def checkbox(self, entry):
        key = entry_key(entry)

        def on_change(e):
            if e.control.value:
                self.selected.add(key)
            else:
                self.selected.discard(key)
            self.selection_text.value = "%d selected" % len(self.selected)
            self.page.update()
        return ft.Checkbox(value=key in self.selected, on_change=on_change)

    def cells(self, entry):
        gone = bool(self.sizes) and file_state(entry) == "missing"
        saved_path = entry.get("path") if self.app.can_open_files() else ""
        exists = bool(saved_path) and os.path.exists(saved_path)
        is_file = exists and os.path.isfile(saved_path)
        size = self.sizes.get(id(entry), 0)
        details = self.details(entry) if self.app.needs_bytes else ""  # a phone: no room for those columns
        buttons = [] if self.app.needs_bytes else [
            ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Open folder", visible=exists and self.app.desktop,
                          on_click=make_opener(show_in_folder, saved_path)),
            ft.IconButton(icon=ft.Icons.PLAY_CIRCLE_OUTLINE, tooltip="Open file", visible=is_file,
                          on_click=make_opener(self.app.open_saved_file, saved_path))]
        return {
            "check": self.checkbox(entry),
            "picture": thumb_tile(thumbnail_source(entry, allow_local=not self.page.web), THUMB_WIDTH, THUMB_HEIGHT,
                                  RADIUS),
            "title": ft.Column([ft.Text(entry.get("title") or "video", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                        weight=ft.FontWeight.W_500),
                                caption(details, visible=bool(details)),
                                caption("File deleted or moved since", color=ft.Colors.ORANGE_700, visible=gone)],
                               spacing=0, tight=True),
            "type": caption(entry_type(entry)),
            "platform": caption(entry.get("platform") or "", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            "format": caption(entry.get("format") or ""),
            "time": caption(entry.get("time") or ""),
            "size": caption(format_bytes(size) if size else ""),
            "actions": row(buttons + [self.menu_button(self.actions(entry))], spacing=0,
                           alignment=ft.MainAxisAlignment.END),
        }

    # The menu of an entry

    def actions(self, entry):
        """[(icon, label, handler)] of what can be done with this entry here."""
        app = self.app
        path = entry.get("path") if app.can_open_files() else ""
        exists = bool(path) and os.path.exists(path)
        is_file = exists and os.path.isfile(path)
        url = entry.get("url") or ""
        found = []
        if is_file and self.preview.available:
            found.append((ft.Icons.SMART_DISPLAY_OUTLINED, "Play here", lambda e: self.play(entry, path)))
        if is_file:
            found.append((ft.Icons.PLAY_CIRCLE_OUTLINE, "Open", make_opener(app.open_saved_file, path)))
        if exists and app.desktop:
            found.append((ft.Icons.FOLDER_OPEN, "Show in its folder", make_opener(show_in_folder, path)))
        if is_file and app.mobile:
            found.append((ft.Icons.SHARE, "Share", lambda e: self.page.run_task(app.share_file, path)))
        if exists and app.desktop:
            found.append((ft.Icons.QR_CODE_2, "Send to a phone" + ("" if is_file else " (as a .zip)"),
                          lambda e: self.page.run_task(app.send_to_phone, path)))
        if is_file and getattr(app, "edit", None) is not None and Path(path).suffix.lower().lstrip(".") in MEDIA_EXTENSIONS:
            found.append((ft.Icons.MOVIE_EDIT, "Open in Edit file(s)", lambda e: self.open_in_edit(path)))
        if url:
            found.append((ft.Icons.REPLAY, "Download again (other options)", self.make_reuse(url)))
            found.append((ft.Icons.LINK, "Copy the link", lambda e: self.page.run_task(app.clipboard.set, url)))
        if exists:
            found.append((ft.Icons.DELETE_FOREVER_OUTLINED, "Delete the file",
                          lambda e: self.page.run_task(self.confirm_delete, [entry])))
        found.append((ft.Icons.LINK_OFF, "Forget it (the file is kept)", lambda e: self.forget([entry])))
        return found

    @staticmethod
    def menu_items(actions):
        """The items of a menu (made for each menu: a control has a single place)."""
        return [ft.PopupMenuItem(content=label, icon=icon, on_click=handler) for icon, label, handler in actions]

    def menu_button(self, actions):
        return ft.PopupMenuButton(icon=ft.Icons.MORE_VERT, tooltip="More", items=self.menu_items(actions))

    def with_menu(self, actions, content):
        """The menu also opens with a right click, or a long press on a phone."""
        items = self.menu_items(actions)
        return ft.ContextMenu(content=content, secondary_items=items, primary_items=items if self.app.needs_bytes
                              else [], primary_trigger=ft.ContextMenuTrigger.LONG_PRESS if self.app.needs_bytes
                              else None)

    # Playing a file here

    def play(self, entry, path):
        sound = Path(path).suffix.lower() in AUDIO_FILES
        self.preview.start(str(path), sound_only=sound, title=entry.get("title") or Path(path).name)
        self.playing.value = entry.get("title") or Path(path).name
        self.player_box.visible = True
        self.page.update()

    def close_player(self, e=None):
        self.preview.stop()
        self.player_box.visible = False
        self.page.update()

    async def paste_link(self, e=None):
        self.app.open_tool(None)
        await self.app.download.paste_link()
        self.page.update()

    async def export_csv(self, e=None):
        """The downloads shown (with their filters and order), as a table for a spreadsheet."""
        app = self.app
        data = history_csv(self.found, lambda entry: self.sizes.get(id(entry), 0)).encode("utf-8-sig")
        try:
            if app.needs_bytes:
                saved = await app.picker.save_file(dialog_title="Export the list", file_name="downloads.csv",
                                                   src_bytes=data)
            else:
                saved = await app.picker.save_file(dialog_title="Export the list", file_name="downloads.csv")
                if saved:
                    await asyncio.to_thread(Path(saved).write_bytes, data)
            if saved:
                self.page.show_dialog(ft.SnackBar(ft.Text("%d downloads exported: %s" % (len(self.found), saved))))
        except Exception as exc:
            self.page.show_dialog(ft.SnackBar(ft.Text("Could not export the list: %s" % exc)))
        self.page.update()

    def open_in_edit(self, path):
        """The file in Edit file(s) (Home), instead of the files there."""
        self.app.open_tool("file")
        self.app.edit.files.set_files([path])
        self.page.update()

    # Several entries

    def toggle_select(self, e=None):
        self.selecting = not self.selecting
        self.selected.clear()
        self.show_rows()
        self.page.update()

    def chosen(self):
        return [entry for entry in self.items if entry_key(entry) in self.selected]

    async def reuse_selected(self, e=None):
        links = list(dict.fromkeys(entry.get("url") for entry in self.chosen() if entry.get("url")))
        if not links:
            return
        await self.app.download.analyze_link(" ".join(links))  # several: a list to download again

    async def delete_selected(self, e=None):
        await self.confirm_delete([entry for entry in self.chosen() if file_state(entry) == "present"])

    def forget_selected(self, e=None):
        self.forget(self.chosen())

    async def confirm_delete(self, entries):
        """Delete the files of `entries` once confirmed (they cannot come back)."""
        if not entries:
            return
        page = self.page
        size = sum(entry_size(entry) for entry in entries)
        if not await ask(page, "Delete %s?" % ("this file" if len(entries) == 1 else "%d files" % len(entries)),
                         ft.Text("%s\n\n%s freed. The files are deleted for good (not put in the bin)." % (
                             "\n".join(Path(entry["path"]).name for entry in entries[:5])
                             + ("\n…" if len(entries) > 5 else ""), format_bytes(size))), "Delete", danger=True):
            return
        freed, failed = 0, []
        for entry in entries:
            try:
                freed += await asyncio.to_thread(delete_file, entry)
            except OSError as exc:
                failed.append("%s (%s)" % (Path(entry["path"]).name, exc.strerror or exc))
        self.selected.clear()
        self.refresh()
        message = "%s freed." % format_bytes(freed) + (" Not deleted: %s" % ", ".join(failed) if failed else "")
        page.show_dialog(ft.SnackBar(ft.Text(message)))
        page.update()

    def forget(self, entries):
        """Remove `entries` from the Library (their files are kept), with Undo."""
        keys = {entry_key(entry) for entry in entries}
        if not keys:
            return
        before = load_history()
        save_history([entry for entry in before if entry_key(entry) not in keys])
        self.selected -= keys
        self.refresh()
        self.offer_restore(before, "%d forgotten." % len(keys))

    def offer_restore(self, before, message):
        """Undo: the Library as it was before."""
        def undo():
            save_history(before)
            self.refresh()
        self.app.offer_undo(message, undo)
        self.page.update()

    # The view

    def on_sort(self):
        self.app.remember("library_sort", {"key": self.table.sort_key, "ascending": self.table.ascending})
        self.on_filter()

    def on_filter(self, e=None):
        self.shown = PAGE_SIZE
        self.show_rows()
        self.page.update()

    def show_more(self, e=None):
        self.shown += PAGE_SIZE
        self.show_rows()
        self.page.update()

    def on_forget(self, e=None):
        before = load_history()
        count = forget_missing()
        self.refresh()
        self.offer_restore(before, "%d deleted file%s forgotten." % (count, "" if count == 1 else "s"))

    def on_clear(self, e=None):
        before = load_history()
        clear_history()
        self.refresh()
        self.offer_restore(before, "Library cleared.")
