"""Night hours (Settings > Downloads): downloads that wait for them, and another speed limit during them."""

from datetime import datetime

DEFAULT_NIGHT = (1, 7)  # from 1:00 to 7:00
HOURS = range(24)


def night_hours(settings):
    """(start hour, end hour) chosen in Settings."""
    start, end = settings.get("night_start"), settings.get("night_end")
    return (start if start in HOURS else DEFAULT_NIGHT[0], end if end in HOURS else DEFAULT_NIGHT[1])


def is_night(hours, now=None):
    """Whether `now` is in the night hours (they may go past midnight: 22 to 6)."""
    start, end = hours
    hour = (now or datetime.now()).hour
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end
