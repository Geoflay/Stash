"""Times and sizes: reading what the user types, writing what the app shows."""

import math


def format_bytes(n):
    size = float(max(n or 0, 0))
    if size < 1024:
        return "%d B" % size
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024 or unit == "TB":
            return "%.1f %s" % (size, unit)


def transfer_text(done, total, speed):
    text = "%s / %s - %s/s" % (format_bytes(done), format_bytes(total) if total else "?", format_bytes(speed))
    if total and speed and total > done:
        text += " - " + time_left_text((total - done) / speed)
    return text


def time_left_text(seconds):
    """"about 2 min left", rounded so it does not jump around."""
    seconds = max(seconds, 0)
    if seconds < 10:
        return "a few seconds left"
    if seconds < 60:
        return "about %d s left" % (round(seconds / 5) * 5)
    minutes = round(seconds / 60)
    if minutes < 60:
        return "about %d min left" % minutes
    return "about %d h %02d min left" % divmod(minutes, 60)


def parse_size_mb(text):
    """A size limit typed in MB ("10", "8,5"): the number, or None when nothing is typed; ValueError otherwise."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        value = 0
    if not value > 0:
        raise ValueError("The maximum size must be a number of MB, for example 10.")
    return value


def parse_time(text):
    text = (text or "").strip()
    if not text:
        return None
    parts = text.replace(",", ".").split(":")
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        numbers = []
    if not numbers or len(numbers) > 3 or any(not math.isfinite(n) or n < 0 for n in numbers):
        raise ValueError("Invalid time \"%s\" (use 90, 1:30 or 1:02:03)." % text)
    total = 0.0
    for n in numbers:
        total = total * 60 + n
    return total


def format_moment(sec):
    """A moment to the frame: "1:23.417" (format_duration's, with the milliseconds when there are)."""
    if not sec:
        return ""
    whole, millis = divmod(round(sec * 1000), 1000)
    return (format_duration(whole) or "0:00") + (".%03d" % millis if millis else "")


def format_duration(sec):
    if not sec:
        return ""
    sec = round(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)
