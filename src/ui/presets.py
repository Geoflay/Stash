"""Presets: the Download tab's one-tap chips above the format, that set the type, the format, the quality, the
size limit, the sound and the picture at once (saved from the options shown, removed with the chip's cross, with
Undo for a few seconds), and their card in Settings: their order, export, import, copy and paste to share them."""

import asyncio
from pathlib import Path

import flet as ft

from core.storage import PRESETS_FILE_NAME, export_presets, import_presets
from ui.style import S, XS, caption, column, row
from ui.widgets import ask, set_status


def saved_presets(app):
    presets = app.settings.get("presets")
    return [p for p in presets if isinstance(p, dict) and p.get("name")] if isinstance(presets, list) else []


def save_presets(app, presets):
    """Keep `presets`, and show them again wherever they are: the Download tab's chips, Settings' card and the
    watched folder's menu."""
    app.remember("presets", presets)
    download = getattr(app, "download", None)
    if download is not None:
        download.presets.refresh()
    settings_tab = getattr(app, "settings_tab", None)
    if settings_tab is not None:
        settings_tab.presets.show()
        settings_tab.refresh_watch()


def remove_preset(app, preset):
    """Remove `preset`, with Undo."""
    presets = saved_presets(app)
    index = presets.index(preset)
    save_presets(app, [p for p in presets if p != preset])

    def undo():
        presets = saved_presets(app)
        presets.insert(min(index, len(presets)), preset)
        save_presets(app, presets)
    app.offer_undo("Preset “%s” removed." % preset["name"], undo)
    app.page.update()


class PresetBar:
    """`tab`: the Download tab, whose controls a preset sets (see DownloadTab.apply_preset)."""

    def __init__(self, tab):
        self.tab = tab
        self.app = tab.app
        self.page = tab.page
        self.chips = ft.Row(wrap=True, spacing=S, run_spacing=S)
        self.status = ft.Container(visible=False)
        self.box = column([self.chips, self.status], spacing=XS)
        self.refresh()

    def saved(self):
        return saved_presets(self.app)

    def refresh(self):
        chips = [ft.Chip(label=ft.Text(p["name"]), leading=ft.Icon(ft.Icons.STAR_OUTLINE, size=16),
                         on_click=lambda e, p=p: self.apply(p), delete_icon_tooltip="Remove this preset",
                         on_delete=lambda e, p=p: self.remove(p)) for p in self.saved()]
        chips.append(ft.Chip(label=ft.Text("Save as a preset"), leading=ft.Icon(ft.Icons.ADD, size=16),
                             on_click=self.ask_name))
        self.chips.controls = chips

    def apply(self, preset):
        problem = self.tab.apply_preset(preset)
        if problem:
            set_status(self.status, "err", problem)
        else:
            self.status.visible = False
        self.page.update()

    def remove(self, preset):
        remove_preset(self.app, preset)

    async def ask_name(self, e=None):
        """The options shown (type, format, quality, size limit, sound, picture) saved under a name."""
        name = ft.TextField(label="Name", autofocus=True)
        if not await ask(self.page, "Save as a preset", column(
                [caption("The type, format, quality, size limit, sound and picture chosen now."), name], tight=True),
                "Save", submit=name) or not (name.value or "").strip():
            return
        preset = dict(self.tab.current_preset(), name=name.value.strip())
        save_presets(self.app, [p for p in self.saved() if p["name"] != preset["name"]] + [preset])
        self.page.update()


class PresetsCard:
    """Settings' presets: dragged to change their order, removed, exported, imported, copied and pasted."""

    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.message = ft.Container(visible=False)
        self.list = ft.ReorderableListView([], height=220, on_reorder=lambda e: self.reorder(e.old_index, e.new_index))
        self.controls = [
            self.list,
            row([ft.TextButton("Export…", icon=ft.Icons.SAVE_ALT, on_click=self.export),
                 ft.TextButton("Import…", icon=ft.Icons.UPLOAD_FILE, on_click=self.import_file),
                 ft.TextButton("Copy", icon=ft.Icons.CONTENT_COPY, on_click=self.copy),
                 ft.TextButton("Paste", icon=ft.Icons.CONTENT_PASTE, on_click=self.paste)], wrap=True, spacing=0),
            self.message]
        self.show()

    def show(self):
        presets = saved_presets(self.app)
        self.list.controls = [
            ft.ListTile(title=ft.Text(p["name"]), subtitle=ft.Text(preset_summary(p)), dense=True,
                        tooltip="Tap to use it on its own for some sites",
                        on_click=lambda e, p=p: self.page.run_task(self.ask_sites, p),
                        trailing=ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Remove",
                                               on_click=lambda e, p=p: remove_preset(self.app, p)))
            for p in presets] or [ft.ListTile(title=caption("No preset yet: save one from the options of a video in "
                                                            "the Download tab."))]
        self.list.height = min(220, 60 * max(len(presets), 1))

    async def ask_sites(self, preset):
        """The sites a preset is used on by itself (SoundCloud always in MP3…)."""
        field = ft.TextField(label="Sites, separated by commas", value=", ".join(preset.get("sites") or []),
                             hint_text="For example: SoundCloud, Bandcamp", autofocus=True)
        if not await ask(self.page, "Use “%s” on its own" % preset["name"], column(
                [caption("YouTube, TikTok, SoundCloud…"), field],
                tight=True), "Save", submit=field):
            return
        sites = [site.strip() for site in (field.value or "").split(",") if site.strip()]
        save_presets(self.app, [dict(p, sites=sites) if p == preset else p for p in saved_presets(self.app)])
        self.page.update()

    def reorder(self, old, new):
        presets = saved_presets(self.app)
        if 0 <= old < len(presets) and 0 <= new < len(presets):
            presets.insert(new, presets.pop(old))
            save_presets(self.app, presets)
        self.page.update()

    def done(self, text, kind="ok"):
        set_status(self.message, kind, text)
        self.page.update()

    def merge(self, presets):
        """Presets imported: added, replacing those of the same name."""
        names = {p["name"] for p in presets}
        save_presets(self.app, [p for p in saved_presets(self.app) if p["name"] not in names] + presets)
        self.done("%d preset%s added." % (len(presets), "" if len(presets) == 1 else "s"))

    async def export(self, e=None):
        app = self.app
        data = export_presets(saved_presets(app)).encode("utf-8")
        try:
            if app.needs_bytes:
                saved = await app.picker.save_file(dialog_title="Export the presets", file_name=PRESETS_FILE_NAME,
                                                   src_bytes=data)
            else:
                saved = await app.picker.save_file(dialog_title="Export the presets", file_name=PRESETS_FILE_NAME)
                if saved:
                    await asyncio.to_thread(Path(saved).write_bytes, data)
            if saved or self.page.web:
                self.done("Presets exported%s." % (": %s" % saved if saved else ""))
        except Exception as exc:
            self.done("Could not export them: %s" % exc, "err")

    async def import_file(self, e=None):
        try:
            files = await self.app.picker.pick_files(dialog_title="Choose the exported presets", allow_multiple=False,
                                                     with_data=self.page.web)
            if not files:
                return
            f = files[0]
            data = await asyncio.to_thread(Path(f.path).read_bytes) if f.path else bytes(f.bytes or b"")
            self.merge(import_presets(data.decode("utf-8-sig")))
        except ValueError as exc:
            self.done(str(exc), "err")
        except Exception as exc:
            self.done("Could not read the file: %s" % exc, "err")

    async def copy(self, e=None):
        await self.app.clipboard.set(export_presets(saved_presets(self.app)))
        self.done("Copied: paste them in a message, then “Paste” on the other device.")

    async def paste(self, e=None):
        try:
            self.merge(import_presets(await self.app.clipboard.get() or ""))
        except ValueError as exc:
            self.done(str(exc), "err")


def preset_summary(preset):
    """"Video · MP4 · 720p · 16 MB · volume evened out": what a preset sets."""
    parts = [str(preset.get("kind") or "").capitalize(), str(preset.get("fmt") or "").upper()]
    if preset.get("quality") and preset["quality"] != "best":
        parts.append("%sp" % preset["quality"])
    if preset.get("max_mb"):
        parts.append("%g MB" % preset["max_mb"])
    for key, text in (("no_sound", "no sound"), ("loudness", "volume evened out"), ("no_silence", "no silences"),
                      ("crop", "cropped"), ("turn", "turned")):
        if preset.get(key):
            parts.append(text)
    if preset.get("sites"):
        parts.append("always for %s" % ", ".join(preset["sites"]))
    return " · ".join(p for p in parts if p)
