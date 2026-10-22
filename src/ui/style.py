"""Spacing and text sizes shared by every screen."""

import flet as ft

from core.storage import load_settings

# The size of the texts and of the app (Settings > Appearance, two sliders): 1 is the normal size. The app's own
# sizes below are read when the app starts; Flutter's (its text theme, the density of its controls) follow at once.
TEXT_SCALES = (0.8, 1.5)
APP_SCALES = (0.8, 1.3)
OLD_SIZES = {"small": 0.9, "medium": 1.0, "large": 1.1, "larger": 1.25}  # the menu the sliders replaced


def scale_of(settings, key, limits):
    """The size saved under `key` ("text_scale" or "app_scale"), within `limits`."""
    value = settings.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = OLD_SIZES.get(settings.get("app_size"), 1.0)
    return min(max(float(value), limits[0]), limits[1])


def density_of(app_scale):
    """How close together Flutter's controls are, for the size of the app."""
    return "compact" if app_scale < 1.05 else "standard" if app_scale < 1.2 else "comfortable"


_settings = load_settings()
TEXT_SCALE = scale_of(_settings, "text_scale", TEXT_SCALES)
APP_SCALE = scale_of(_settings, "app_scale", APP_SCALES)
TITLE_SIZE = round(20 * TEXT_SCALE)
HEADING_SIZE = round(16 * TEXT_SCALE)
SECTION_SIZE = round(14 * TEXT_SCALE)
SMALL_SIZE = round(12 * TEXT_SCALE)

XS, S, M, L = (round(n * APP_SCALE) for n in (4, 8, 12, 16))
RADIUS = 8
TOUCH = round(44 * APP_SCALE)
RAIL_WIDTH = round(72 * APP_SCALE)
MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT = 900, 600
WIDE_WINDOW = 1400
THUMB_WIDTH, THUMB_HEIGHT = round(80 * APP_SCALE), round(45 * APP_SCALE)
FAB_CLEARANCE = 72


def section_title(text, **kwargs):
    return ft.Text(text, size=SECTION_SIZE, weight=ft.FontWeight.W_600, **kwargs)


def caption(text="", **kwargs):
    kwargs.setdefault("color", ft.Colors.ON_SURFACE_VARIANT)
    return ft.Text(text, size=SMALL_SIZE, **kwargs)


def row(controls, **kwargs):
    kwargs.setdefault("spacing", S)
    kwargs.setdefault("vertical_alignment", ft.CrossAxisAlignment.CENTER)
    return ft.Row(controls, **kwargs)


def column(controls, **kwargs):
    kwargs.setdefault("spacing", S)
    return ft.Column(controls, **kwargs)
