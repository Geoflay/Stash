"""The tools of the Home tab working on files of the device: editing them (EditFilesCard: each file its own changes,
made in the editor of one file, edit_tab.py, or the changes of every file), joining them one after the other into
a single file (JoinCard), and finding the tags of songs (MusicCard). Each one lists its files in a table, sorted by
a tap on a column and narrowed by its filters, under its options."""

import asyncio
import shutil
import time
from pathlib import Path

import flet as ft

from core.backend import AUDIO_FORMATS, BACKEND, VIDEO_FORMATS
from core.editor import EDIT_AUDIO_FORMATS, MEDIA_EXTENSIONS, edit_media, join_media, probe_media, small_picture
from core.errors import Cancelled
from core.filenames import filename_fields, render_filename, reserve_file, unique_path
from core.music import SONG_EXTENSIONS, find_song_tags, is_tagged, read_song
from core.system import default_download_dir, show_in_folder
from core.tags import LABELS, tag_file
from core.units import format_bytes, format_duration
from core.workdirs import work_folder
from ui.edit_options import CommonOptions
from ui.progress import ProgressPanel
from ui.style import RADIUS, S, THUMB_HEIGHT, THUMB_WIDTH, TOUCH, caption, column, row, section_title
from ui.table import Filters, ListTable, TableColumn
from ui.widgets import ask, hide_status, make_opener, set_status

FORMAT_CHOICES = [(k, t) for k, t in VIDEO_FORMATS.items()] + [(k, AUDIO_FORMATS[k]) for k in EDIT_AUDIO_FORMATS]
CORRECTED = ("title", "artist", "album", "album_artist", "genre", "track", "date")  # typed for a song
AUDIO_EXTENSIONS = ("mp3", "m4a", "aac", "opus", "ogg", "flac", "wav", "wma")
READ_AT_ONCE = 3  # files read at the same time (length, picture size, a picture)
SONGS_AT_ONCE = 4  # songs looked up at the same time (MusicBrainz still answers one request a second)
SHOW_EVERY = 0.5  # seconds between two refreshes of a list while its files are read


def extension(path):
    return Path(path).suffix.lower().lstrip(".")


def new_file(path):
    """A file of a list: its path, what probe_media read of it ("info", None until read), a small picture, its own
    changes ("own", EditFilesCard), what a song says and what was found for it (MusicCard)."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": path, "info": None, "picture": None, "size": size, "own": None, "song": None, "found": None,
            "write": None}


def is_video(record):
    info = record["info"]
    if info and "video" in info:
        return bool(info["video"])
    return extension(record["path"]) not in AUDIO_EXTENSIONS


async def save_next_to(source, name, made, app=None):
    """Copy `made` next to `source` as `name` (in Downloads if that folder cannot be written to). On a phone (the
    file opened is the app's own copy of it): into the destination folder of Settings, else where the user chooses
    (None when not saved, or when where is not known)."""
    if app is not None and app.needs_bytes:
        if app.android:
            await app.check_storage_access()
        folder = app.destination_folder()
        if folder is None:
            data = await asyncio.to_thread(Path(made).read_bytes)
            async with app.save_dialog_lock:
                saved = await app.picker.save_file(dialog_title="Save", file_name=name, src_bytes=data)
            return Path(saved) if saved and Path(saved).is_file() else None
        source = folder / name
    try:
        target = await asyncio.to_thread(reserve_file, Path(source).parent, name)
    except OSError:
        target = await asyncio.to_thread(reserve_file, default_download_dir(), name)
    await asyncio.to_thread(shutil.copyfile, str(made), str(target))
    return target


class FilesCard:
    """Files of the device, added with a button (or dropped onto the window), in a table narrowed by its filters,
    then one job on the files shown."""

    description = ""
    button_text = ""
    button_icon = ft.Icons.SAVE
    ordered = False  # the order matters: the rows are dragged to move them, a file can be there twice
    minimum = 1
    extensions = MEDIA_EXTENSIONS
    pick_title = "Choose video or audio files"
    folder_title = "Choose a folder of video or audio files"
    pictures = True  # a picture of each file in its row
    go_above = False  # the button of the job above the list (else under it)

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.files = []
        self.running = False
        self.read_token = 0
        self.kind_dd = ft.Dropdown(label="Type", value="", options=[
            ft.DropdownOption(key="", text="All types"), ft.DropdownOption(key="video", text="Videos"),
            ft.DropdownOption(key="audio", text="Sound only")])
        self.ext_dd = ft.Dropdown(label="Format", value="")
        self.filters = Filters(self.filter_controls(), self.on_filter)
        self.table = ListTable(app, self.columns(), self.cells, self.on_sort,
                               self.on_reorder if self.ordered else None)
        self.count = caption(expand=True)
        self.empty = caption()
        self.format_dd = ft.Dropdown(label="Format", expand=True, value="mp4",
                                     options=[ft.DropdownOption(key=k, text=t) for k, t in self.format_choices()])
        self.go_btn = ft.FilledButton(self.button_text, icon=self.button_icon, height=TOUCH, on_click=self.run)
        self.progress = ProgressPanel(self.page)
        self.result = ft.Container(visible=False)
        self.folder_btn = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Open folder", visible=False)
        go = [row([self.go_btn, self.folder_btn]), self.progress.box, self.result]
        self.card = app.stretch_card(
            [*([caption(self.description)] if self.description else []),
             row([ft.TextButton("Add files…", icon=ft.Icons.ADD, on_click=self.add_files),
                  ft.TextButton("Add a folder…", icon=ft.Icons.CREATE_NEW_FOLDER_OUTLINED, on_click=self.add_folder)],
                 wrap=True),
             *self.options(), *(go if self.go_above else []),
             row([self.count, self.filters.buttons]), self.filters.panel, self.table.box, self.empty,
             *([] if self.go_above else go), *self.after()])
        self.refresh()

    # What the subclasses change

    def format_choices(self):
        return FORMAT_CHOICES

    def options(self):
        """Controls above the list."""
        return [ft.Row([self.format_dd])]

    def after(self):
        """Controls under the result (MusicCard: writing what was found)."""
        return []

    def filter_controls(self):
        return [self.kind_dd, self.ext_dd]

    def columns(self):
        return ([TableColumn("picture", width=THUMB_WIDTH)] if self.pictures else []) + [
            TableColumn("name", "Name", sort=lambda r: r["path"].name, expand=3),
            TableColumn("type", "Type", width=70, sort=lambda r: "video" if is_video(r) else "audio", wide=True),
            TableColumn("format", "Format", width=60, sort=lambda r: extension(r["path"])),
            TableColumn("length", "Length", width=70, sort=lambda r: (r["info"] or {}).get("duration"), wide=True),
            TableColumn("size", "Size", width=80, sort=lambda r: r["size"], wide=True),
            *self.more_columns(),
            TableColumn("remove", width=40)]

    def more_columns(self):
        return []

    def details(self, record):
        """Under the file's name: its picture size and codecs (on a phone, its length and size too)."""
        info = record["info"] or {}
        parts = ["%dp" % info["height"] if info.get("height") else "",
                 " + ".join(c for c in (info.get("video"), info.get("audio")) if c)]
        if self.app.needs_bytes:
            parts += [format_duration(info.get("duration")) or "", format_bytes(record["size"])]
        return " · ".join(x for x in parts if x)

    def cells(self, record):
        info = record["info"] or {}
        details = self.details(record)
        return {"picture": self.picture_cell(record),
                "name": ft.Column([ft.Text(record["path"].name, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                           weight=ft.FontWeight.W_500, tooltip=str(record["path"])),
                                   caption(details, max_lines=1, visible=bool(details))], spacing=0, tight=True),
                "type": caption("Video" if is_video(record) else "Sound"),
                "format": caption(extension(record["path"]).upper()),
                "length": caption(format_duration(info.get("duration")) or "–"),
                "size": caption(format_bytes(record["size"])),
                "remove": ft.IconButton(icon=ft.Icons.CLOSE, tooltip="Leave this file out",
                                        on_click=lambda e, r=record: self.remove(r))}

    def picture_cell(self, record):
        icon = ft.Icons.MOVIE_OUTLINED if is_video(record) else ft.Icons.MUSIC_NOTE_OUTLINED
        return ft.Container(
            ft.Image(src=record["picture"], fit=ft.BoxFit.COVER, width=THUMB_WIDTH, height=THUMB_HEIGHT)
            if record["picture"] else ft.Icon(icon, color=ft.Colors.ON_SURFACE_VARIANT),
            width=THUMB_WIDTH, height=THUMB_HEIGHT, border_radius=RADIUS, alignment=ft.Alignment(0, 0),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS, bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST)

    # The files

    def shown(self):
        """The files the job works on: those the filters keep, in the order of the list."""
        kind, ext = self.kind_dd.value, self.ext_dd.value
        return [r for r in self.files if (not kind or (kind == "video") == is_video(r))
                and (not ext or extension(r["path"]) == ext)]

    def add(self, paths):
        """Add the files of `paths` this tool takes (once each, unless the order matters)."""
        paths = [Path(p) for p in paths if extension(p) in self.extensions]
        if not self.ordered:
            have = {r["path"] for r in self.files}
            paths = [p for p in dict.fromkeys(paths) if p not in have]
        self.files += [new_file(p) for p in paths]
        self.changed()

    def set_files(self, paths):
        self.files = []
        self.add(paths)

    def changed(self):
        hide_status(self.result)
        self.folder_btn.visible = False
        self.refresh()
        self.read_token += 1
        if any(r["info"] is None for r in self.files):
            self.page.run_task(self.read_all, self.read_token)

    async def add_files(self, e=None):
        files = await self.app.picker.pick_files(dialog_title=self.pick_title, allow_multiple=True,
                                                 file_type=ft.FilePickerFileType.CUSTOM,
                                                 allowed_extensions=list(self.extensions))
        self.add([Path(f.path) for f in files or [] if f.path])
        self.page.update()

    async def add_folder(self, e=None):
        folder = await self.app.picker.get_directory_path(dialog_title=self.folder_title)
        if folder:
            found = await asyncio.to_thread(self.files_in, Path(folder))
            self.add(found)
            self.page.update()

    def files_in(self, folder):
        return sorted(p for p in folder.iterdir() if p.is_file() and extension(p) in self.extensions)

    def remove(self, record):
        self.files.remove(record)
        self.changed()
        self.page.update()

    def clear(self):
        """The page's Clear button: no file left."""
        self.files = []
        self.changed()

    # Reading the files (in the background, a few at a time)

    async def read_all(self, token):
        limit = asyncio.Semaphore(READ_AT_ONCE)
        shown = [time.monotonic()]

        async def one(record):
            async with limit:
                if token != self.read_token:
                    return
                await self.read(record)
                if token == self.read_token and time.monotonic() - shown[0] > SHOW_EVERY:
                    shown[0] = time.monotonic()
                    self.refresh()
                    self.page.update()
        await asyncio.gather(*(one(r) for r in self.files if r["info"] is None))
        if token == self.read_token:
            self.refresh()
            self.page.update()

    async def read(self, record):
        """Its length, picture size and codecs, and a small picture."""
        try:
            record["info"] = await asyncio.to_thread(probe_media, record["path"])
        except Exception as exc:
            record["info"] = {"error": str(exc)}
            return
        info = record["info"]
        if self.pictures and BACKEND == "ffmpeg" and (info["video"] or info["cover"]):
            try:
                async with work_folder() as workdir:
                    record["picture"] = await asyncio.to_thread(
                        small_picture, record["path"], (info["duration"] or 0) / 3 if info["video"] else 0, workdir)
            except Exception:
                pass

    async def info_of(self, record):
        """What probe_media reads of the file (read again when it failed)."""
        if not record["info"] or "error" in record["info"]:
            record["info"] = await asyncio.to_thread(probe_media, record["path"])
        return record["info"]

    # The list

    def on_filter(self, e=None):
        self.refresh()
        self.page.update()

    def on_sort(self):
        self.files = self.table.sort(self.files)
        self.refresh()
        self.page.update()

    def on_reorder(self, old, new):
        """A row dragged from place `old` to `new` (among those shown)."""
        shown = self.shown()
        if not (0 <= old < len(shown) and 0 <= new < len(shown)) or old == new:
            return
        moved, target = shown[old], shown[new]
        self.files.remove(moved)
        self.files.insert(self.files.index(target) + (1 if new > old else 0), moved)
        self.table.sort_key = None  # no longer in a column's order
        self.table.show_header()
        self.refresh()
        self.page.update()

    def refresh(self):
        counts = {}
        for record in self.files:
            counts[extension(record["path"])] = counts.get(extension(record["path"]), 0) + 1
        self.ext_dd.options = [ft.DropdownOption(key="", text="All formats")] + [
            ft.DropdownOption(key=ext, text=".%s (%d)" % (ext, count)) for ext, count in sorted(counts.items())]
        if self.ext_dd.value not in [""] + list(counts):
            self.ext_dd.value = ""
        self.filters.refresh()
        shown = self.shown()
        self.table.show(shown)
        self.empty.value = "No files yet: add some." if not self.files else "No file of this kind: clear the filters."
        self.empty.visible = not shown
        size = sum(r["size"] for r in shown)
        length = sum((r["info"] or {}).get("duration") or 0 for r in shown)
        self.count.value = " · ".join(x for x in (
            ("%d of %d files" % (len(shown), len(self.files)) if len(shown) != len(self.files)
             else "%d file%s" % (len(shown), "" if len(shown) == 1 else "s")),
            format_bytes(size) if size else "", format_duration(length) or "") if x)
        self.count.visible = self.filters.buttons.visible = bool(self.files)
        self.go_btn.disabled = self.running or len(shown) < self.minimum

    # The job

    async def run(self, e=None):
        app, page = self.app, self.page
        self.running = self.go_btn.disabled = True
        self.folder_btn.visible = False
        hide_status(self.result)
        self.progress.start()
        page.update()
        try:
            async with work_folder() as workdir:
                folder = await self.work(workdir)
            if folder:
                self.folder_btn.on_click = make_opener(show_in_folder, str(folder))
                self.folder_btn.visible = app.desktop
            await app.notify_if_away(self.button_text, "Done.")
        except Cancelled:
            set_status(self.result, "info", "Cancelled.")
        except Exception as exc:
            set_status(self.result, "err", "Error: %s" % exc)
        finally:
            self.running = False
            self.progress.box.visible = False
            self.refresh()
            page.update()

    async def converting(self, function, *args):
        """Run `function(*args)` in a worker thread, in a conversion slot shared with the downloads."""
        slot = await self.app.conversion_slots.acquire(self.progress.cancelled)
        if not slot:
            raise Cancelled()
        try:
            return await asyncio.to_thread(function, *args)
        finally:
            self.app.conversion_slots.release()


class JoinCard(FilesCard):
    description = "One after the other, in the order of the list: drag the files to move them."
    button_text = "Join and save"
    ordered = True
    minimum = 2

    def add(self, paths):
        first = not self.files
        super().add(paths)
        if first and self.files and extension(self.files[0]["path"]) in dict(FORMAT_CHOICES):
            self.format_dd.value = extension(self.files[0]["path"])

    async def work(self, workdir):
        self.progress.show("Reading the files…")
        self.page.update()
        records, fmt = self.shown(), self.format_dd.value
        infos = [await self.info_of(record) for record in records]
        files = [record["path"] for record in records]
        made = await self.converting(join_media, files, fmt, infos, workdir, self.progress.reporter())
        target = await save_next_to(files[0], "%s (joined)%s" % (files[0].stem, made.suffix), made, self.app)
        set_status(self.result, "ok", "Saved: %s (%s long)" % (target, format_duration(
            sum(info["duration"] or 0 for info in infos)) or "?"))
        return target.parent if target else None


class EditFilesCard(FilesCard):
    """Edit file(s): the changes of every file at the top (shown once there are several), and a file's own,
    chosen in the editor of one file (`editor`, edit_tab.py) opened by its row's button; a single file opens
    there at once."""

    button_text = "Save the edited copies"

    def __init__(self, app, editor):
        self.editor = editor
        super().__init__(app)

    def format_choices(self):
        return [("same", "Same as each file")] + FORMAT_CHOICES

    def options(self):
        self.format_dd.value = "same"
        self.changes = CommonOptions(self.app)
        self.defaults = ft.Container(
            column([section_title("For every file"), caption("A file's own changes (its ⚙ button) are kept instead."),
                    ft.Row([self.format_dd]), self.changes.box], spacing=S),
            padding=S, border_radius=RADIUS, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT))
        return [self.defaults]

    def more_columns(self):
        return [TableColumn("own", "Changes", width=150, sort=lambda r: r["own"]["fmt"] if r["own"] else ""),
                TableColumn("edit", width=40)]

    def cells(self, record):
        cells = super().cells(record)
        own = record["own"]
        cells["own"] = row([caption("Own: %s" % own["fmt"].upper() if own else "For every file", expand=True,
                                    color=ft.Colors.PRIMARY if own else None),
                            ft.IconButton(icon=ft.Icons.RESTART_ALT, tooltip="Back to the changes of every file",
                                          visible=own is not None, on_click=lambda e, r=record: self.reset(r))],
                           spacing=0)
        cells["edit"] = ft.IconButton(icon=ft.Icons.TUNE, tooltip="Its own changes",
                                      on_click=lambda e, r=record: self.page.run_task(self.open_record, r))
        return cells

    def refresh(self):
        super().refresh()
        self.defaults.visible = len(self.files) > 1

    def changed(self):
        super().changed()
        if len(self.files) == 1 and self.editor.editing is not self.files[0]:  # a single file: its editor at once
            self.page.run_task(self.open_record, self.files[0])
        elif not self.files:
            self.show_list()

    async def add_files(self, e=None):
        self.editor.keep_changes()  # the file being edited, before the list shows
        await super().add_files(e)

    async def add_folder(self, e=None):
        self.editor.keep_changes()
        await super().add_folder(e)

    def show_list(self):
        self.editor.close()
        self.card.visible, self.editor.single_row.visible = True, False

    def back_to_list(self, e=None):
        self.show_list()
        self.refresh()
        self.page.update()

    async def open_record(self, record):
        """The editor of one file, with its own changes as last chosen, else those of every file."""
        self.card.visible, self.editor.single_row.visible = False, True
        if not await self.editor.open_file(record["path"], record):
            return
        if record["own"] is not None:
            self.editor.restore(record["own"]["snapshot"])
        elif len(self.files) > 1:
            self.editor.apply_defaults(self.format_dd.value, self.changes)
        self.editor.refresh()

    def keep_own(self, record, fmt, options, snapshot):
        """The changes chosen in the editor for this file."""
        record["own"] = {"fmt": fmt, "options": options, "snapshot": snapshot}

    def reset(self, record):
        record["own"] = None
        self.refresh()
        self.page.update()

    def clear(self):
        super().clear()
        self.show_list()

    async def work(self, workdir):
        records = self.shown()
        try:
            defaults = self.changes.read()
        except ValueError as exc:
            set_status(self.result, "err", str(exc))
            return None
        done, skipped, failed, folder = 0, [], [], None
        for index, record in enumerate(records):
            path = record["path"]
            self.progress.check()
            self.progress.show("File %d of %d: %s" % (index + 1, len(records), path.name), index / len(records))
            self.page.update()
            work = workdir / str(index)
            work.mkdir()
            try:
                info = await self.info_of(record)
                if record["own"] is not None:
                    fmt, options = record["own"]["fmt"], record["own"]["options"]
                else:
                    fmt, options = self.format_dd.value, defaults
                    if fmt == "same":  # its own format, when the app can write it
                        fmt = extension(path) if extension(path) in dict(FORMAT_CHOICES) else (
                            "mp4" if info["video"] else "mp3")
                if extension(path) == fmt and not options:
                    skipped.append(path.name)
                    continue
                if fmt in VIDEO_FORMATS and not info["video"]:
                    raise RuntimeError("it has no picture")
                made, _ = await self.converting(edit_media, path, fmt, info, options, work, self.progress.reporter())
                target = await save_next_to(path, "%s (edited)%s" % (path.stem, made.suffix), made, self.app)
                folder = folder or (target.parent if target else None)
                done += 1
            except Cancelled:
                raise
            except Exception as exc:
                failed.append("%s: %s" % (path.name, exc))
        lines = ["%d of %d files saved." % (done, len(records))]
        if skipped:
            lines.append("Nothing to change: %s" % ", ".join(skipped))
        lines += ["Failed: %s" % line for line in failed[:5]]
        set_status(self.result, "err" if failed else "ok", "\n".join(lines))
        return folder


class MusicCard(FilesCard):
    """Songs looked up on MusicBrainz (title, artist, album, cover art) and LRCLIB (lyrics), a few at a time: what
    is found is shown in their rows, can be corrected by hand (also for the songs nothing sure was found for), then
    is written into the files checked, which can be renamed with the music file name of Settings."""

    description = ("Each song is looked up by its tags, else by its name (“Artist - Title”). Only sure matches are "
                   "kept (same title, same length); its own cover art is kept. Check what was found (the pencil "
                   "corrects it), then write it.")
    button_text = "Find tags"
    button_icon = ft.Icons.TRAVEL_EXPLORE
    extensions = SONG_EXTENSIONS  # sound files only: the tags are written into them
    pick_title = "Choose songs"
    folder_title = "Choose a folder of songs"
    pictures = False
    go_above = True

    def options(self):
        settings = self.app.settings
        self.tags_switch = ft.Switch(label="Title, artist, album, date and cover art (MusicBrainz)",
                                     value=bool(settings.get("music_tags", True)))
        self.lyrics_switch = ft.Switch(label="Lyrics (LRCLIB)", value=bool(settings.get("lyrics", True)))
        self.skip_switch = ft.Switch(label="Skip the songs that already have a title, artist, album and cover art",
                                     value=True)
        self.rename_switch = ft.Switch(label="Rename the files with the music file name (Settings > Files)",
                                       value=False)
        self.write_btn = ft.FilledButton("Write the tags", icon=ft.Icons.SAVE, height=TOUCH, visible=False,
                                         on_click=self.write)
        self.write_status = ft.Container(visible=False)
        return [self.tags_switch, self.lyrics_switch, self.skip_switch, self.rename_switch]

    def after(self):
        return [ft.Row([self.write_btn]), self.write_status]

    def filter_controls(self):
        return [self.ext_dd]

    def files_in(self, folder):
        """The songs of a folder and of the folders in it."""
        return sorted(p for p in folder.rglob("*") if p.is_file() and extension(p) in self.extensions)

    def columns(self):
        def tag(key):
            return lambda r: (r["song"] or {}).get(key) or ""
        return [TableColumn("write", width=40),
                TableColumn("name", "Name", sort=lambda r: r["path"].name, expand=2),
                TableColumn("artist", "Artist", sort=tag("artist"), wide=True),
                TableColumn("title", "Title", sort=tag("title"), wide=True),
                TableColumn("album", "Album", sort=tag("album"), wide=True),
                TableColumn("found", "Found", sort=lambda r: self.found_text(r), expand=2, wide=True),
                TableColumn("format", "Format", width=60, sort=lambda r: extension(r["path"])),
                TableColumn("size", "Size", width=80, sort=lambda r: r["size"], wide=True),
                TableColumn("edit", width=40), TableColumn("remove", width=40)]

    def found_text(self, record):
        new = record["found"]
        if new is None:
            return ""
        return ", ".join(x for x in (
            " – ".join(v for v in (new.get("artist"), new.get("title")) if v), new.get("album", ""),
            "cover art" if new.get("cover") else "", "lyrics" if new.get("lyrics") else "") if x) \
            or "Nothing sure found: correct it by hand"

    def details(self, record):
        song = record["song"] or {}
        if not self.app.needs_bytes:
            return ""
        return self.found_text(record) or " – ".join(v for v in (song.get("artist"), song.get("title")) if v)

    def cells(self, record):
        cells = super().cells(record)
        song = record["song"] or {}
        for key in ("artist", "title", "album"):
            cells[key] = caption(song.get(key) or "", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        found = self.found_text(record)
        cells["found"] = caption(found, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                 color=ft.Colors.PRIMARY if record["found"] else None)
        cells["write"] = ft.Checkbox(value=bool(record["write"]), visible=record["found"] is not None,
                                     tooltip="Write it into the file", on_change=lambda e, r=record: self.on_check(r, e))
        cells["edit"] = ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Correct it",
                                      visible=record["found"] is not None,
                                      on_click=lambda e, r=record: self.page.run_task(self.correct, r))
        return cells

    async def read(self, record):
        """What the song says of itself (its tags, else its name)."""
        try:
            record["song"] = await asyncio.to_thread(read_song, record["path"])
            record["info"] = {"duration": record["song"]["duration"], "video": ""}
        except Exception as exc:
            record["info"] = {"error": str(exc), "video": ""}

    def refresh(self):
        super().refresh()
        found = [r for r in self.files if r["found"] is not None]
        self.write_btn.visible = bool(found)
        self.write_btn.content = "Write the tags (%d)" % sum(1 for r in found if r["write"])

    def changed(self):
        super().changed()
        hide_status(self.write_status)

    def on_check(self, record, e):
        record["write"] = bool(e.control.value)
        self.refresh()
        self.page.update()

    async def correct(self, record):
        """The tags of one song, typed: what was found first, else what the file says."""
        new, own = record["found"], record["song"]["tags"]
        fields = {key: ft.TextField(label=LABELS.get(key, key.capitalize()), value=new.get(key) or own.get(key, ""),
                                    expand=True) for key in CORRECTED}
        if not await ask(self.page, record["path"].name, column(
                [ft.Row([fields["title"]]), row([fields["artist"], fields["album"]]),
                 row([fields["album_artist"], fields["genre"]]), row([fields["track"], fields["date"]])],
                tight=True, width=480), "Keep"):
            return
        for key, field in fields.items():
            value = (field.value or "").strip()
            if value and value != own.get(key):
                new[key] = value
            else:
                new.pop(key, None)
        record["write"] = bool(new)
        self.refresh()
        self.page.update()

    async def work(self, workdir):
        """Look the songs up, a few at a time."""
        records = self.shown()
        tags, lyrics = bool(self.tags_switch.value), bool(self.lyrics_switch.value)
        counts = {"done": 0, "found": 0, "tagged": 0, "missing": 0}
        failed = []
        limit = asyncio.Semaphore(SONGS_AT_ONCE)
        for record in records:
            record["found"] = record["write"] = None
        self.progress.show("Looking up %d songs…" % len(records), 0)
        self.page.update()

        async def one(record):
            async with limit:
                if self.progress.cancelled.is_set():
                    return
                try:
                    song = await asyncio.to_thread(read_song, record["path"])
                    record["song"] = song
                    if self.skip_switch.value and is_tagged(song) and (song["lyrics"] or not lyrics):
                        counts["tagged"] += 1
                    else:
                        _, new = await asyncio.to_thread(find_song_tags, record["path"], tags, lyrics)
                        record["found"], record["write"] = new, bool(new)
                        counts["found" if new else "missing"] += 1
                except Exception as exc:
                    failed.append("%s: %s" % (record["path"].name, exc))
                counts["done"] += 1
                self.progress.show("%d of %d songs looked up" % (counts["done"], len(records)),
                                   counts["done"] / len(records))
                self.refresh()
                self.page.update()
        await asyncio.gather(*(one(r) for r in records))
        self.progress.check()
        lines = ["Found for %d of %d songs." % (counts["found"], len(records))]
        if counts["tagged"]:
            lines.append("%d already tagged (skipped)." % counts["tagged"])
        if counts["missing"]:
            lines.append("Nothing sure for %d: they can be tagged by hand." % counts["missing"])
        lines += ["Failed: %s" % line for line in failed[:5]]
        set_status(self.result, "ok" if counts["found"] or counts["missing"] else "info", "\n".join(lines))
        hide_status(self.write_status)
        return None

    def new_name(self, record):
        """The song's name from the music file name of Settings, with its tags as they will be."""
        new, song = record["found"], record["song"]
        title, artist = new.get("title") or song["title"], new.get("artist") or song["artist"]
        fields = filename_fields({"title": "%s - %s" % (artist, title) if artist else title, "artist": artist,
                                  "track": title, "uploader": artist}, {}, video=False)
        return render_filename(self.app.filename_template("mp3"), fields, record["path"].suffix)

    async def write(self, e=None):
        chosen = [r for r in self.files if r["found"] is not None and r["write"]]
        written, renamed, failed = 0, 0, []
        self.write_btn.disabled = True
        self.page.update()
        for record in chosen:
            path = record["path"]
            try:
                await asyncio.to_thread(tag_file, path, record["found"])
                written += 1
                name = self.new_name(record) if self.rename_switch.value else path.name
                if name != path.name:
                    target = await asyncio.to_thread(unique_path, path.parent, name)
                    await asyncio.to_thread(path.rename, target)
                    record["path"] = target
                    renamed += 1
                record["song"] = await asyncio.to_thread(read_song, record["path"])
                record["found"] = record["write"] = None
            except Exception as exc:
                failed.append("%s: %s" % (path.name, exc))
        self.write_btn.disabled = False
        self.refresh()
        set_status(self.write_status, "err" if failed else "ok", "\n".join(
            ["Tags written into %d song%s%s." % (written, "" if written == 1 else "s",
                                                 ", %d renamed" % renamed if renamed else "")]
            + ["Failed: %s" % line for line in failed[:5]]))
        self.page.update()
