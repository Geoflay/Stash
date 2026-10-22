"""The Subscriptions tab: the channels, playlists and podcasts followed (subscribed to from their list on the
Download page). They are checked now and then while the app is open (Settings > Downloads): their new
items go to the queue (those its filters keep: words, length, no Shorts), into a folder named after the list,
and, when chosen, the files of the items gone from the list, or past its newest ones, are deleted."""

import asyncio
import time

import flet as ft

from core.cookies import saved_cookies_path
from core.errors import clean_error
from core.history import delete_file, load_history, save_history
from core.links import playlist_site
from core.network import call_with_retries
from core.playlist import fetch_playlist
from core.podcast import fetch_feed
from core.subscriptions import (
    CHECK_EVERY, DEFAULT_CHECK_EVERY, beyond_newest, changes, is_due, load_subscriptions, new_subscription,
    save_subscriptions, wanted,
)
from ui.style import S, XS, caption, column, row, section_title
from ui.widgets import ask, empty_state

LOOK_EVERY = 60  # seconds between two looks at which subscriptions are due


class Subscriptions:
    def __init__(self, app):
        self.app = app
        self.page = app.page
        self.items = load_subscriptions()
        self.checking = set()  # links being checked now
        self.list = column([], spacing=S)
        self.check_btn = ft.TextButton("Check now", icon=ft.Icons.REFRESH, on_click=self.check_all)
        self.empty = empty_state(ft.Icons.SUBSCRIPTIONS_OUTLINED, "No subscription yet",
                                 "Subscribe to a channel, a playlist or a podcast from its list (Download page): its "
                                 "new items are then downloaded on their own.", None)
        self.card = app.card([
            row([section_title("Subscriptions", expand=True), self.check_btn]),
            caption("Their new items are downloaded on their own."),
            self.empty, self.list])
        self.view = app.view("Subscriptions", [self.card], visible=False)
        self.refresh()

    def hours(self):
        hours = self.app.settings.get("subscription_hours")
        return hours if hours in CHECK_EVERY else DEFAULT_CHECK_EVERY

    def refresh(self):
        self.empty.visible = not self.items
        self.check_btn.visible = bool(self.items)
        self.list.controls = [self.make_row(sub) for sub in self.items]

    def make_row(self, sub):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(sub["checked"])) if sub.get("checked") else "never"
        details = " · ".join(x for x in ("%d downloaded" % sub.get("added", 0), "checked %s" % when,
                                         sub.get("note") or "") if x)

        def on_remove_gone(e):
            sub["remove_gone"] = bool(e.control.value)
            save_subscriptions(self.items)
        return column([
            row([ft.Icon(ft.Icons.PODCASTS if sub.get("podcast") else ft.Icons.SUBSCRIPTIONS_OUTLINED),
                 ft.Column([ft.Text(sub["title"], weight=ft.FontWeight.W_500, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            caption(details)], spacing=XS, tight=True, expand=True),
                 ft.ProgressRing(width=16, height=16, stroke_width=2, visible=sub["url"] in self.checking),
                 ft.IconButton(icon=ft.Icons.REFRESH, tooltip="Check now",
                               on_click=lambda e: self.page.run_task(self.check, sub)),
                 ft.IconButton(icon=ft.Icons.FILTER_LIST, tooltip="Which items: words, length, Shorts, how many",
                               on_click=lambda e: self.page.run_task(self.ask_filters, sub)),
                 ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Unsubscribe (the files are kept)",
                               on_click=lambda e: self.remove(sub))]),
            ft.Switch(label="Delete the files of the items that leave the list", value=bool(sub.get("remove_gone")),
                      on_change=on_remove_gone),
        ], spacing=0)

    async def ask_filters(self, sub):
        """The items downloaded: words in their title, their length, no Shorts, and how many are kept."""
        filters = sub.get("filters") or {}

        def number(value):
            return str(value) if value else ""
        include = ft.TextField(label="Only with one of these words (separated by commas)",
                               value=filters.get("include", ""))
        exclude = ft.TextField(label="Never with these words", value=filters.get("exclude", ""))
        shortest = ft.TextField(label="Shortest (minutes)", value=number(filters.get("min_minutes")), expand=True,
                                keyboard_type=ft.KeyboardType.NUMBER)
        longest = ft.TextField(label="Longest (minutes)", value=number(filters.get("max_minutes")), expand=True,
                               keyboard_type=ft.KeyboardType.NUMBER)
        shorts = ft.Switch(label="No Shorts (a minute or less)", value=bool(filters.get("no_shorts")))
        keep = ft.TextField(label="Keep only the newest (how many; empty: all)", value=number(sub.get("keep")),
                            keyboard_type=ft.KeyboardType.NUMBER)
        if not await ask(self.page, "Which items of %s" % sub["title"], column(
                [include, exclude, row([shortest, longest]), shorts, keep,
                 caption("Older files past that number are deleted.")],
                tight=True, width=440), "Save"):
            return

        def read(field):
            try:
                return max(float((field.value or "").replace(",", ".")), 0) or None
            except ValueError:
                return None
        sub["filters"] = {"include": (include.value or "").strip(), "exclude": (exclude.value or "").strip(),
                          "min_minutes": read(shortest), "max_minutes": read(longest), "no_shorts": bool(shorts.value)}
        sub["keep"] = int(read(keep) or 0)
        save_subscriptions(self.items)
        self.refresh()
        self.page.update()

    def find(self, url):
        return next((sub for sub in self.items if sub["url"] == url), None)

    # Subscribing (from the list of the Download tab) and unsubscribing

    def subscribe(self, url, data, options, download_existing):
        """Follow the list `data` (shown by the Download tab) with the options of every item."""
        sub = new_subscription(url, data["title"], options, data.get("podcast"), data["entries"], download_existing)
        self.items = [s for s in self.items if s["url"] != url] + [sub]
        save_subscriptions(self.items)
        self.refresh()
        if download_existing:
            self.page.run_task(self.check, sub)

    def remove(self, sub):
        index = self.items.index(sub)
        self.items.remove(sub)
        save_subscriptions(self.items)
        self.refresh()

        def undo():
            self.items.insert(index, sub)
            save_subscriptions(self.items)
            self.refresh()
        self.app.offer_undo("Unsubscribed from %s." % sub["title"], undo)
        self.page.update()

    # Checking

    async def keep_checking(self):
        """While the app is open: the subscriptions due are checked (every few hours, as set in Settings)."""
        while True:
            for sub in list(self.items):
                if is_due(sub, self.hours()):
                    await self.check(sub)
            await asyncio.sleep(LOOK_EVERY)

    async def check_all(self, e=None):
        for sub in list(self.items):
            await self.check(sub)

    async def read_list(self, sub):
        """(the list's items now, whether that is all of them)."""
        app = self.app
        if sub.get("podcast"):
            data = await asyncio.to_thread(fetch_feed, sub["url"], app.playlist_limit("podcast"))
        else:
            data = await asyncio.to_thread(
                call_with_retries, lambda *a: None, fetch_playlist, sub["url"], app.cookies_value(),
                cookie_file=saved_cookies_path(), limit=app.playlist_limit(playlist_site(sub["url"])))
        complete = data.get("total") is not None and len(data["entries"]) >= data["total"]
        return data, complete

    async def check(self, sub):
        """Download the new items of `sub`; delete the files of those gone, when chosen."""
        from ui.items import entry_params
        if sub["url"] in self.checking:
            return
        self.checking.add(sub["url"])
        self.refresh()
        self.page.update()
        try:
            data, complete = await self.read_list(sub)
            new, gone = changes(sub, data["entries"], complete)
            skipped = [entry for entry in new if not wanted(entry, sub.get("filters"))]
            new = [entry for entry in new if entry not in skipped]  # left out, but known: not looked at again
            for entry in reversed(new):  # the oldest first
                params = await entry_params(entry, data, sub["options"])
                params.update(folder=sub["title"], pace=True, podcast=bool(sub.get("podcast")),
                              subscription=sub["url"])
                self.page.run_task(self.app.queue.run_download, params)
            sub["seen"] = list(dict.fromkeys((sub.get("seen") or []) + [e["url"] for e in new + skipped]))
            removed = await asyncio.to_thread(self.delete_gone, sub, gone) if sub.get("remove_gone") else 0
            removed += await asyncio.to_thread(self.delete_older, sub)
            if removed:
                self.app.history.refresh()
            sub["added"] = sub.get("added", 0) + len(new)
            sub["note"] = ", ".join(x for x in ("%d new" % len(new) if new else "",
                                                "%d deleted" % removed if removed else "") if x)
            if new:
                await self.app.notify_if_away("New in %s" % sub["title"], "%d to download" % len(new))
        except Exception as exc:
            sub["note"] = "Could not check: %s" % clean_error(exc)
        finally:
            sub["checked"] = time.time()
            self.checking.discard(sub["url"])
            if sub in self.items:
                save_subscriptions(self.items)
            self.refresh()
            self.page.update()

    def delete_older(self, sub):
        """Delete the files of the subscription's downloads past its newest ones (as many as chosen to keep)."""
        history = load_history()
        older = beyond_newest(history, sub["url"], sub.get("keep") or 0)
        removed = 0
        for entry in older:
            try:
                delete_file(entry)
                removed += 1
            except OSError:
                continue
        if older:
            keys = {id(entry) for entry in older}
            save_history([e for e in history if id(e) not in keys])
        return removed

    def delete_gone(self, sub, gone):
        """Delete the files downloaded for this subscription whose items left the list; returns how many."""
        history = load_history()
        removed = 0
        for entry in history:
            if entry.get("subscription") == sub["url"] and entry.get("url") in gone:
                try:
                    delete_file(entry)
                    removed += 1
                except OSError:
                    continue
        sub["seen"] = [url for url in sub.get("seen") or [] if url not in gone]
        save_history([e for e in history if not (e.get("subscription") == sub["url"] and e.get("url") in gone)])
        return removed
