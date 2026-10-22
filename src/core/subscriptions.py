"""Subscriptions: a channel, a playlist or a podcast followed, whose new items are downloaded on their own into
a folder named after it, and (when chosen) whose files are deleted once their item leaves the list: an offline
copy kept up to date. Saved in subscriptions.json."""

import time

from core.storage import read_json, write_json

# How often the subscriptions are checked while the app is open (Settings), in hours; 0: only by hand.
CHECK_EVERY = {0: "Only when I press Check", 1: "Every hour", 3: "Every 3 hours", 6: "Every 6 hours",
               12: "Every 12 hours", 24: "Once a day"}
DEFAULT_CHECK_EVERY = 6


def load_subscriptions():
    data = read_json("subscriptions.json", [])
    return [s for s in data if isinstance(s, dict) and s.get("url")] if isinstance(data, list) else []


def save_subscriptions(subscriptions):
    try:
        write_json("subscriptions.json", subscriptions)
    except OSError:
        pass


def new_subscription(url, title, options, podcast, entries, download_existing=False):
    """A list followed from now: its items are known (`seen`) unless the ones already there are downloaded
    too. `options`: the options of every item of the Download tab's list (format, quality…)."""
    return {"url": url, "title": title, "options": options, "podcast": bool(podcast),
            "seen": [] if download_existing else [e["url"] for e in entries], "remove_gone": False,
            "checked": time.time(), "added": 0}


def is_due(subscription, hours, now=None):
    return bool(hours) and (now or time.time()) - (subscription.get("checked") or 0) >= hours * 3600


def changes(subscription, entries, complete):
    """(the new items, the links of the items gone) since the last check. Items are only gone when the whole
    list was read (`complete`): one past the limit of Settings is not gone."""
    seen = set(subscription.get("seen") or [])
    links = {e["url"] for e in entries}
    new = [e for e in entries if e["url"] not in seen]
    gone = [url for url in subscription.get("seen") or [] if url not in links] if complete else []
    return new, gone


SHORT_SECONDS = 60  # a YouTube Short lasts this long at most


def wanted(entry, filters):
    """Whether a new item passes the subscription's filters: {"include" and "exclude" (words, separated by commas,
    in its title), "min_minutes", "max_minutes", "no_shorts"}. An item of unknown length passes the lengths."""
    filters = filters or {}
    title = (entry.get("title") or "").casefold()
    include = [w.strip().casefold() for w in str(filters.get("include") or "").split(",") if w.strip()]
    exclude = [w.strip().casefold() for w in str(filters.get("exclude") or "").split(",") if w.strip()]
    if include and not any(word in title for word in include):
        return False
    if any(word in title for word in exclude):
        return False
    duration = entry.get("duration")
    if filters.get("no_shorts") and ("/shorts/" in entry.get("url", "") or (duration and duration <= SHORT_SECONDS)):
        return False
    if duration and filters.get("min_minutes") and duration < filters["min_minutes"] * 60:
        return False
    if duration and filters.get("max_minutes") and duration > filters["max_minutes"] * 60:
        return False
    return True


def beyond_newest(history, url, keep):
    """The Library's entries of the subscription `url` past its `keep` newest ones (0: none), newest first."""
    if not keep:
        return []
    return [e for e in history if e.get("subscription") == url][keep:]
