"""The command palette (computer, Ctrl+K or the title bar's search button): words typed find a tab, a setting
("proxy", "cover"), a format or a preset for the video shown ("mp3"), or an action (pause all…); Enter runs the
first one."""

import flet as ft

from core.backend import AUDIO_FORMATS, IMAGE_FORMATS, VIDEO_FORMATS
from core.history import searchable
from ui.i18n import translate
from ui.presets import saved_presets
from ui.style import RADIUS, S, caption, column, row

TAB_NAMES = {"home": "Home", "queue": "Queue", "history": "Library", "subscriptions": "Subscriptions",
             "settings": "Settings"}
SHOWN = 8  # commands listed at most


class CommandPalette:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.field = ft.TextField(hint_text="A tab, a setting, a format, an action…", prefix_icon=ft.Icons.SEARCH,
                                  autofocus=True, on_change=self.show, on_submit=self.run_first)
        self.list = column([], spacing=0)
        self.found = []

    def commands(self):
        """[(label, where it is, words it is found by, action)]."""
        app, found = self.app, []
        for index, name in enumerate(app.view_names):
            found.append((TAB_NAMES[name], "Tab", TAB_NAMES[name] + " " + name,
                          lambda index=index: self.go(index)))
        settings = app.settings_tab
        if settings is not None:
            from ui.settings_tab import CATEGORIES, card_title
            for key, label, _, _, names in CATEGORIES:
                for name in names:
                    if name in settings.cards:
                        found.append((card_title(settings.cards[name]), "Settings > %s" % label,
                                      settings.index[name], lambda key=key: self.open_settings(key)))
        download = app.download
        if download.result_card.visible:  # the video shown: its format, or a preset
            for kind, table in (("video", VIDEO_FORMATS), ("audio", AUDIO_FORMATS), ("image", IMAGE_FORMATS)):
                for fmt, text in table.items():
                    kind_of = "gif" if fmt == "gif" else kind
                    found.append(("Format: %s" % text, "The video shown", "format %s %s" % (fmt, text),
                                  lambda kind=kind_of, fmt=fmt: download.presets.apply({"kind": kind, "fmt": fmt})))
            for preset in saved_presets(app):
                found.append(("Preset: %s" % preset["name"], "The video shown", "preset " + preset["name"],
                              lambda preset=preset: download.presets.apply(preset)))
        queue = app.queue
        found += [("Paste a link", "Home", "paste link analyze download", lambda: self.page.run_task(queue.paste_link)),
                  ("Pause or resume all downloads", "Queue", "pause resume all", queue.pause_all),
                  ("Retry the failed downloads", "Queue", "retry failed again", queue.retry_failed),
                  ("Clear the finished downloads", "Queue", "clear finished", queue.clear_finished)]
        for theme, text in (("dark", "Dark theme"), ("light", "Light theme"), ("system", "Theme of the system")):
            found.append((text, "Appearance", "theme " + text, lambda theme=theme: self.set_theme(theme)))
        return found

    def open(self):
        self.field.value = ""
        self.show()
        self.page.show_dialog(ft.AlertDialog(content=column([self.field, self.list], tight=True, width=520, spacing=S),
                                             content_padding=S))
        self.page.update()

    def show(self, e=None):
        words = searchable(self.field.value).split()
        self.found = [c for c in self.commands()
                      if all(word in searchable(" ".join((translate(c[0]), c[0], c[2]))) for word in words)]
        self.list.controls = [
            ft.Container(row([ft.Text(label, weight=ft.FontWeight.W_500, expand=True), caption(where)]),
                         padding=ft.Padding(left=S, right=S, top=8, bottom=8), border_radius=RADIUS, ink=True,
                         bgcolor=ft.Colors.SECONDARY_CONTAINER if index == 0 and words else None,
                         on_click=lambda e, action=action: self.run(action))
            for index, (label, where, _, action) in enumerate(self.found[:SHOWN])] or [caption("Nothing found.")]
        if e is not None:
            self.page.update()

    def run(self, action):
        self.page.pop_dialog()
        action()
        self.page.update()

    def run_first(self, e=None):
        if self.found:
            self.run(self.found[0][3])

    # What the commands do

    def go(self, index):
        self.app.show_view(index)

    def open_settings(self, key):
        self.go(self.app.view_names.index("settings"))
        self.app.settings_tab.select(key)

    def set_theme(self, theme):
        self.app.remember("theme", theme)
        self.app.apply_theme()
        self.app.settings_tab.sync()
