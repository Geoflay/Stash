"""The language of the app (Settings > Appearance > Language, and the first welcome page): English, the language
the app is written in, or French (i18n_fr.py). Another language is one more table like that one.

The texts are translated as they are given to the controls (a Text's words, a button's, a field's label, a
tooltip…): an exact text is looked up in the table, a text made from a template ("Saved: %s") is matched against
the table's templates, its parts translated in turn. A change of language applies to what is built afterwards:
the app is built once the welcome pages are done, and a change in Settings is seen at the next start."""

import functools
import locale
import os
import re

import flet as ft
from flet.controls.base_control import BaseControl

LANGUAGES = {"system": "Same as the system", "en": "English", "fr": "Français"}
# The attributes holding words people read (a Text's "value" only: a field's value is what was typed).
TRANSLATED = {"label", "tooltip", "hint_text", "helper_text", "error_text", "text", "content", "value"}
_state = {"table": None, "templates": [], "installed": False}
PLACEHOLDER = r"%(?:s|d|g|\.\d+f|02d)"
NATIVE = "native"  # the data of a control whose words are never translated (a language named in itself)


def language_options():
    """The choices of the Language menus: each language named in itself ("English", "Français")."""
    return [ft.DropdownOption(key=k, text=t, data=None if k == "system" else NATIVE) for k, t in LANGUAGES.items()]


def system_language():
    """The language of this device ("fr", "en"…), as the system says it."""
    for value in (os.environ.get("LANGUAGE"), os.environ.get("LC_ALL"), os.environ.get("LANG")):
        if value and value[:2].isalpha():
            return value[:2].lower()
    try:
        name = locale.getlocale()[0] or ""
    except ValueError:
        name = ""
    return "fr" if name.lower().startswith("fr") else name[:2].lower() or "en"


def language_code(setting):
    """The language used for a setting of Settings ("system", "en", "fr")."""
    code = system_language() if setting in (None, "", "system") else setting
    return code if code in LANGUAGES and code != "system" else "en"


def _pattern(template):
    """A regex matching what `template` ("%d of %s") makes; its placeholders become groups."""
    parts, last = [], 0
    for found in re.finditer(r"%(%|s|d|g|02d|\.\d+f)", template):
        parts.append(re.escape(template[last:found.start()]))
        kind = found.group(1)
        if kind == "s" and template[:found.start()][-1:].isalpha():  # "download%s": a plural "s", or a new part
            parts.append(r"(s?|[^A-Za-z].*?)")
            last = found.end()
            continue
        parts.append("%" if kind == "%" else r"(-?\d+)" if kind == "d" else r"(-?[\d.]+)" if kind != "s" else "(.*?)")
        last = found.end()
    parts.append(re.escape(template[last:]))
    return re.compile("".join(parts), re.S)


def install_language(setting):
    """Translate the texts given to the controls from now on, into the language of `setting`."""
    code = language_code(setting)
    if code == "fr":
        from ui.i18n_fr import FRENCH
        table = FRENCH
    else:
        table = None
    _state["table"] = table
    # The templates (texts with %s, %d…), the most words first: "%d download%s" must not take "%d downloading".
    _state["templates"] = [(_pattern(k), _french_template(v)) for k, v in sorted(
        ((k, v) for k, v in (table or {}).items() if re.search(PLACEHOLDER, k)),
        key=lambda item: -len(re.sub(PLACEHOLDER, "", item[0])))]
    translate.cache_clear()
    if not _state["installed"]:
        _state["installed"] = True
        original = BaseControl.__setattr__

        def translating_setattr(control, name, value):
            if _state["table"] and isinstance(value, str) and value and name in TRANSLATED \
                    and (name != "value" or isinstance(control, ft.Text)) and getattr(control, "data", None) != NATIVE:
                value = translate(value)
            original(control, name, value)
        BaseControl.__setattr__ = translating_setattr
    return code


def _french_template(text):
    """The translation of a template, its placeholders all made %s (they get the words matched). They can also be
    numbered (%1, %2…) to change their order or use one twice: "%1 fichier%2 supprimé%2"."""
    return re.sub(r"%(?:d|g|\.\d+f)", "%s", text.replace("%%", "\x00")).replace("\x00", "%%")


@functools.lru_cache(maxsize=4096)
def translate(text):
    """`text` in the language installed (itself when it is not in the table)."""
    table = _state["table"]
    if not table:
        return text
    found = table.get(text)
    if found is not None:
        return found
    if "\n" in text:  # lines put together: each one on its own
        return "\n".join(translate(line) for line in text.split("\n"))
    for pattern, template in _state["templates"]:
        match = pattern.fullmatch(text)
        if match:
            groups = tuple(translate(group) for group in match.groups())
            if re.search(r"%[1-9]", template):
                return re.sub(r"%([1-9])", lambda m: groups[int(m.group(1)) - 1], template).replace("%%", "%")
            return template % groups
    for joint in (" · ", ", "):  # details put together: "YouTube · 3:12 · 4.5 MB", "2 waiting, 1 paused"
        if joint in text:
            return joint.join(translate(part) for part in text.split(joint))
    return text
