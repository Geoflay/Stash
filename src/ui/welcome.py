"""The welcome pages shown at the first start, before the tabs are built: a simple set-up, one question per page
(language, where to save, simple or complete and the look, notifications, access). "Skip" keeps the default of
every page not seen (notifications off); everything can be changed later in Settings."""

import flet as ft

from ui.i18n import install_language, language_options
from ui.style import L, M, XS, caption, column, row


class Welcome:
    """`on_done()` is called once the pages are finished or skipped."""

    def __init__(self, app, on_done):
        self.app = app
        self.page = app.page
        self.on_done = on_done
        self.index = 0
        if "notify" not in app.settings:  # off until turned on here or in Settings
            app.remember("notify", False)
        self.view = ft.Container(alignment=ft.Alignment(0, 0))
        self.build()

    def build(self):
        """Every page and button made again (in the language chosen)."""
        app = self.app
        self.pages = [page for page in (self.language_page(), self.folder_page(), self.mode_page(),
                                        self.notify_page(), self.access_page()) if page is not None]
        self.title = ft.Text("", size=24, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)
        self.icon = ft.Icon(ft.Icons.DOWNLOAD, size=56, color=ft.Colors.PRIMARY)
        self.body = column([], spacing=M, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.dots = ft.Row(spacing=XS, alignment=ft.MainAxisAlignment.CENTER)
        self.back_btn = ft.TextButton("Back", icon=ft.Icons.ARROW_BACK, on_click=self.back)
        self.next_btn = ft.FilledButton("Next", on_click=self.next)
        self.view.content = ft.Container(
            content=column([
                ft.Row([ft.TextButton("Skip", on_click=self.skip)], alignment=ft.MainAxisAlignment.END),
                ft.Container(self.icon, alignment=ft.Alignment(0, 0)),
                self.title, self.body,
                ft.Container(height=L),
                row([self.back_btn, ft.Container(self.dots, expand=True), self.next_btn]),
            ], spacing=M, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            padding=L, width=560 if app.desktop else None,
        )
        self.show()

    # The pages: (title, icon, controls), or None when it does not apply here

    def language_page(self):
        app = self.app

        def on_select(e):
            app.remember("language", e.control.value)
            install_language(e.control.value)
            self.build()  # this page again, in that language
            self.page.update()
        return ("Welcome", ft.Icons.TRANSLATE, [
            ft.Text("Save videos and music from YouTube, TikTok, Instagram…", text_align=ft.TextAlign.CENTER),
            ft.Row([ft.Dropdown(label="Language", expand=True, value=app.settings.get("language") or "system",
                                options=language_options(), on_select=on_select)])])

    def folder_page(self):
        app = self.app
        if not app.can_pick_folder:
            return None
        self.folder_text = caption(text_align=ft.TextAlign.CENTER)
        self.refresh_folder()

        async def choose(e):
            if app.android and not await app.request_storage_access():
                return
            folder = await app.picker.get_directory_path(dialog_title="Choose a folder")
            if folder:
                app.remember("save_dir", folder)
            self.refresh_folder()
            self.page.update()

        def ask(e):
            app.remember("save_dir", None)
            self.refresh_folder()
            self.page.update()
        return ("Where to save", ft.Icons.FOLDER_OUTLINED, [
            ft.Row([ft.Button("Choose a folder…", icon=ft.Icons.FOLDER_OPEN, on_click=choose),
                    ft.TextButton("Ask every time", on_click=ask)], alignment=ft.MainAxisAlignment.CENTER,
                   wrap=True),
            self.folder_text])

    def refresh_folder(self):
        folder = self.app.settings.get("save_dir")
        self.folder_text.value = ("Saving to: %s" % folder) if folder else "Asked for each file."

    def mode_page(self):
        from ui.settings_tab import THEMES
        app = self.app

        def on_simple(e):
            app.remember("simple_mode", bool(e.control.value))

        def on_theme(e):
            app.remember("theme", e.control.value)
            app.apply_theme()
            self.page.update()
        return ("Simple or complete", ft.Icons.TUNE, [
            caption("Simple: only the format and the presets.", text_align=ft.TextAlign.CENTER),
            ft.Switch(label="Simple mode", value=bool(app.settings.get("simple_mode")), on_change=on_simple),
            ft.Row([ft.Dropdown(label="Theme", expand=True, value=app.settings.get("theme") or "system",
                                options=[ft.DropdownOption(key=k, text=t) for k, (t, _) in THEMES.items()],
                                on_select=on_theme)])])

    def notify_page(self):
        app = self.app
        if not (app.desktop or app.android):
            return None

        async def on_notify(e):
            app.remember("notify", bool(e.control.value))
            if e.control.value:
                await app.ask_notification_permission()  # Android 13 and later
        return ("Notifications", ft.Icons.NOTIFICATIONS_OUTLINED, [
            ft.Switch(label="When a download is finished", value=bool(app.settings.get("notify")),
                      on_change=on_notify),
            ft.Switch(label="Sound or vibration", value=bool(app.settings.get("alert", True)),
                      on_change=lambda e: app.remember("alert", bool(e.control.value))),
            ft.Switch(label="One notification per list", value=bool(app.settings.get("notify_lists_once", True)),
                      on_change=lambda e: app.remember("notify_lists_once", bool(e.control.value)))])

    def access_page(self):
        app = self.app
        if app.android:
            self.access_text = caption(text_align=ft.TextAlign.CENTER)

            async def grant(e):
                granted = await app.request_storage_access()
                self.access_text.value = "Granted." if granted else "Not granted: asked for each file."
                self.page.update()
            return ("Access to your files", ft.Icons.LOCK_OPEN, [
                caption("Needed to save into your folder.", text_align=ft.TextAlign.CENTER),
                ft.Row([ft.Button("Allow", icon=ft.Icons.CHECK, on_click=grant)],
                       alignment=ft.MainAxisAlignment.CENTER),
                self.access_text])
        if not app.desktop:
            return None
        browsers = {"none": "None"} | {k: k.capitalize() for k in app.cookies_browsers()}
        return ("Access", ft.Icons.LOCK_OPEN, [
            ft.Switch(label="Analyze the links I copy", value=bool(app.settings.get("watch_clipboard")),
                      on_change=lambda e: app.remember("watch_clipboard", bool(e.control.value))),
            ft.Row([ft.Dropdown(label="Logins of a browser (cookies)", expand=True, value=app.cookies_browser(),
                                options=[ft.DropdownOption(key=k, text=t) for k, t in browsers.items()],
                                on_select=lambda e: app.remember("cookies_browser", e.control.value))])])

    # Moving between the pages

    def show(self):
        title, icon, controls = self.pages[self.index]
        self.title.value, self.icon.icon = title, icon
        self.body.controls = controls
        last = self.index == len(self.pages) - 1
        self.next_btn.content = "Start" if last else "Next"
        self.back_btn.visible = self.index > 0
        self.dots.controls = [ft.Container(width=8, height=8, border_radius=4,
                                           bgcolor=ft.Colors.PRIMARY if i == self.index
                                           else ft.Colors.OUTLINE_VARIANT) for i in range(len(self.pages))]

    def next(self, e=None):
        if self.index == len(self.pages) - 1:
            self.finish()
            return
        self.index += 1
        self.show()
        self.page.update()

    def back(self, e=None):
        self.index = max(0, self.index - 1)
        self.show()
        self.page.update()

    def skip(self, e=None):
        self.finish()

    def finish(self):
        self.app.remember("welcomed", True)
        self.on_done()
