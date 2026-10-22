"""Where the app keeps its files, and its settings (settings.json)."""

import functools
import json
import os
import sys
from pathlib import Path


@functools.lru_cache(maxsize=None)
def data_dir():
    base = os.environ.get("FLET_APP_STORAGE_DATA")
    if base:
        path = Path(base)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA") or Path.home()) / "VideoDownloader"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "video-downloader"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(name, default):
    try:
        return json.loads((data_dir() / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(name, value):
    folder = data_dir()
    tmp = folder / (name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / name)


def load_settings():
    data = read_json("settings.json", {})
    return data if isinstance(data, dict) else {}


def save_settings(settings):
    try:
        write_json("settings.json", settings)
    except OSError:
        pass


# Exported settings (to set up the app the same way on another device), without what belongs to this one.
SETTINGS_FILE_KIND = "video-downloader-settings"
SETTINGS_FILE_NAME = "video-downloader-settings.json"
DEVICE_SETTINGS = ("save_dir", "backup_dir", "ffmpeg_dir", "ytdlp_latest", "ytdlp_checked_at")


def export_settings(settings):
    """The settings as the bytes of a JSON file."""
    data = {key: value for key, value in settings.items() if key not in DEVICE_SETTINGS}
    return json.dumps({"kind": SETTINGS_FILE_KIND, "settings": data}, ensure_ascii=False, indent=1).encode("utf-8")


def import_settings(content, settings):
    """The settings of an exported file put into `settings` (this device's own are kept); returns how many there
    were. ValueError if it is not such a file."""
    try:
        data = json.loads(bytes(content).decode("utf-8-sig"))
    except ValueError:
        data = None
    if not isinstance(data, dict) or data.get("kind") != SETTINGS_FILE_KIND or not isinstance(data.get("settings"), dict):
        raise ValueError("This is not a settings file exported by the app.")
    imported = {key: value for key, value in data["settings"].items() if key not in DEVICE_SETTINGS}
    settings.update(imported)
    return len(imported)


# Presets of the Download tab shared as a file or a text (Settings, another device, a friend).
PRESETS_FILE_KIND = "video-downloader-presets"
PRESETS_FILE_NAME = "video-downloader-presets.json"


def export_presets(presets):
    return json.dumps({"kind": PRESETS_FILE_KIND, "presets": presets}, ensure_ascii=False, indent=1)


def import_presets(text):
    """The presets of an exported file or text; ValueError if it holds none."""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    presets = data.get("presets") if isinstance(data, dict) and data.get("kind") == PRESETS_FILE_KIND else None
    presets = [p for p in presets if isinstance(p, dict) and p.get("name") and p.get("fmt")] if isinstance(
        presets, list) else []
    if not presets:
        raise ValueError("No presets found: copy or export them from the app first.")
    return presets
