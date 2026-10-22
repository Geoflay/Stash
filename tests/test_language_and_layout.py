"""The language of the app (texts translated as they are given to the controls), the one-line rows of Settings,
and editing a file without ffmpeg (a phone: PyAV)."""

import re
import subprocess

import flet as ft
import pytest
from conftest import needs_ffmpeg, probe

import core.editor as editor
from ui import i18n
from ui.i18n_fr import FRENCH
from ui.welcome import Welcome
from ui.widgets import COMPACT_DROPDOWN, setting_rows


@pytest.fixture
def french():
    i18n.install_language("fr")
    yield i18n.translate
    i18n.install_language("en")  # the other tests read the English texts


def test_texts_translated(french):
    assert french("Settings") == "Paramètres"
    assert french("Saved: clip.mp4") == "Enregistré : clip.mp4"  # a template
    assert french("3 downloads") == "3 téléchargements" and french("1 download") == "1 téléchargement"
    assert french("2 deleted files forgotten.") == "2 fichiers supprimés oubliés."  # numbered placeholders
    assert french("Download complete\nSaved: a.mp3") == "Téléchargement terminé\nEnregistré : a.mp3"
    assert french("2 downloading, 1 waiting") == "2 en téléchargement, 1 en attente"  # not "download" + "ing…"
    assert french("YouTube · 3:12 · Video") == "YouTube · 3:12 · Vidéo"
    assert french("Some words of a video title") == "Some words of a video title"


def test_every_template_can_be_used(french):
    """Each template of the table, filled with numbers and words, gives a translation without its placeholders."""
    samples = {"%d": "3", "%g": "2.5", "%s": "x", "%02d": "05", "%.1f": "1.5", "%%": "%"}
    for key in FRENCH:
        if not re.search(i18n.PLACEHOLDER, key):
            continue
        text = re.sub(r"%(?:%|s|d|g|02d|\.\d+f)", lambda m: samples.get(m.group(0), "1.5"), key)
        result = french(text)
        assert not re.search(r"%[sdg1-9]", result), (key, result)


def test_controls_translated_but_not_what_is_typed(french):
    assert ft.Text("Settings").value == "Paramètres"
    field = ft.TextField(label="Name", value="Settings")
    assert field.label == "Nom" and field.value == "Settings"  # a field's value is what was typed
    option = ft.DropdownOption(key="best", text="Best available")
    assert option.key == "best" and option.text == "La meilleure disponible"
    i18n.install_language("en")
    assert ft.Text("Settings").value == "Settings"


def test_language_of_the_system(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "fr_BE:fr")
    assert i18n.language_code("system") == "fr" and i18n.language_code(None) == "fr"
    assert i18n.language_code("en") == "en"
    monkeypatch.setenv("LANGUAGE", "de_DE")
    assert i18n.language_code("system") == "en"  # not translated yet: English


def test_welcome_language_page(fake_app):
    fake_app.remember = fake_app.settings.__setitem__
    fake_app.can_pick_folder = False
    fake_app.cookies_browsers, fake_app.cookies_browser = (lambda: []), (lambda: "none")
    welcome = Welcome(fake_app, lambda: None)
    dropdown = welcome.body.controls[1].controls[0]
    assert dropdown.value == "system" and set(o.key for o in dropdown.options) == {"system", "en", "fr"}
    try:
        dropdown.value = "fr"
        dropdown.on_select(ft.Event("select", dropdown))
        assert fake_app.settings["language"] == "fr"
        assert welcome.title.value == "Bienvenue" and welcome.next_btn.content == "Suivant"
        names = [o.text for o in welcome.body.controls[1].controls[0].options]
        assert names == ["Comme le système", "English", "Français"]  # each language named in itself
    finally:
        i18n.install_language("en")


def test_setting_rows():
    switch = ft.Switch(label="Pure black dark theme")
    menu = ft.Dropdown(label="Theme", options=[ft.DropdownOption(key="dark", text="Dark")])
    number = ft.TextField(label="Pause between videos", keyboard_type=ft.KeyboardType.NUMBER)
    text = ft.TextField(label="Proxy")
    rows = setting_rows([switch, ft.Row([menu, number]), text])
    assert len(rows) == 4 and rows[-1] is text  # the row split in two lines; a text field kept as it is
    assert switch.label is None and menu.label is None and menu.width == COMPACT_DROPDOWN
    names, control = rows[0].content.controls  # the name on the left, the switch on the right
    assert names.controls[0].value == "Pure black dark theme" and control is switch


@needs_ffmpeg
def test_edit_without_ffmpeg(media, tmp_path, monkeypatch):
    """With PyAV only (a phone): a part kept, the sound removed, a sound file converted and tagged."""
    pytest.importorskip("av")
    monkeypatch.setattr(editor, "BACKEND", "av")
    video = media("video.mp4")
    info = editor.probe_media(video)
    work = tmp_path / "work"
    work.mkdir()
    out, _ = editor.edit_media(video, "mkv", info, {"clip": (1.0, 3.0), "no_sound": True}, work, lambda *a: None)
    assert out.suffix == ".mkv" and probe(out) == ["h264"]
    work2 = tmp_path / "work2"
    work2.mkdir()
    song, _ = editor.edit_media(video, "mp3", info, {"tags": {"title": "Tone"}}, work2, lambda *a: None)
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title", "-of", "csv=p=0",
                           str(song)], capture_output=True, text=True, check=True).stdout.strip()
    assert probe(song) == ["mp3"] and tags == "Tone"
