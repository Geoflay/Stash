"""What the operating system provides: Downloads folder, opening files, notifications."""

import base64
import html
import os
import shutil
import subprocess
import sys
from pathlib import Path


# Keeps ffmpeg, PowerShell and explorer from flashing a console window on Windows.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def default_download_dir():
    folder = Path.home() / "Downloads"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    except OSError:
        return Path.home()


def _spawn(args):
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **NO_WINDOW)


def open_file(path):
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        _spawn(["open", str(path)])
    else:
        _spawn(["xdg-open", str(path)])


def show_in_folder(path):
    path = Path(path)
    if sys.platform == "win32":
        if path.is_file():
            _spawn(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))
    elif sys.platform == "darwin":
        _spawn(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    else:
        _spawn(["xdg-open", str(path if path.is_dir() else path.parent)])


def beep():
    """The system's notification sound (Windows; elsewhere the terminal bell)."""
    if sys.platform == "win32":
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    else:
        sys.stdout.write("\a")
        sys.stdout.flush()


def desktop_notify(title, message):
    """Best-effort native notification (Windows, macOS, Linux). Returns True if it was sent.

    No extra dependency: it relies on tools already present on each OS. Flet itself has
    no notification API, and on Android/iOS this does nothing.
    """
    title, message = (title or "")[:100], (message or "")[:250]
    kwargs = {"capture_output": True, "timeout": 15}
    try:
        if sys.platform == "win32":
            xml = ('<toast><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text>'
                   "</binding></visual></toast>" % (html.escape(title, quote=False),
                                                     html.escape(message, quote=False)))
            script = (
                "$ErrorActionPreference='Stop';"
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] | Out-Null;"
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
                "ContentType = WindowsRuntime] | Out-Null;"
                "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
                "$x.LoadXml('%s');"
                "$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
                ").Show($n)" % xml.replace("'", "''")
            )
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-EncodedCommand", encoded],
                **NO_WINDOW, **kwargs)
            return r.returncode == 0
        if sys.platform == "darwin":
            r = subprocess.run(
                ["osascript",
                 "-e", "on run argv",
                 "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run", title, message], **kwargs)
            return r.returncode == 0
        tool = shutil.which("notify-send")
        if tool:
            r = subprocess.run([tool, "-a", "Stash", title, message], **kwargs)
            return r.returncode == 0
    except Exception:
        pass
    return False
