"""File names: the name template from Settings, how names are cleaned up (NAME_RULES), and names that never
overwrite a file."""

import re
import unicodedata
from pathlib import Path

from core.backend import AUDIO_FORMATS, IMAGE_FORMATS
from core.music import NOISE, guess_song

# Settings > General > Files: how the names made from the templates are cleaned up.
NAME_RULES = {"emoji": False, "ascii": False, "noise": False, "spaces": "", "case": "", "date": "ymd",
              "max_length": 120, "remove": ""}
SPACES = {"": "Kept", "_": "Replaced by _", "-": "Replaced by -", ".": "Replaced by ."}
CASES = {"": "As it is", "lower": "lowercase", "title": "Title Case", "upper": "UPPERCASE"}
DATE_FORMATS = {"ymd": "2024-05-31", "dmy": "31-05-2024", "mdy": "05-31-2024", "compact": "20240531",
                "dots": "31.05.2024"}
NAME_LENGTHS = (60, 80, 120, 180)
# Emojis and pictographs, their variation selectors and the joiners that glue them together.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F"
                    "\U0000200D\U000020E3\U000E0020-\U000E007F]")


def safe_filename(title, ext, max_length=120):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title or "").strip(" .")[:max_length].strip(" .")
    return (base or "video") + ext.lower()


def clean_name(text, rules=None):
    """`text` cleaned up as set in Settings (NAME_RULES): emojis, accents, "(Official Video)", words removed,
    spaces and capitals."""
    rules = NAME_RULES if rules is None else rules
    if rules.get("noise"):
        text = NOISE.sub("", text)
    for word in (w.strip() for w in str(rules.get("remove") or "").split(",")):
        if word:
            text = re.sub(re.escape(word), "", text, flags=re.I)
    if rules.get("emoji"):
        text = _EMOJI.sub("", text)
    if rules.get("ascii"):
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", re.sub(r"\(\s*\)|\[\s*\]", "", text)).strip(" -_.")
    case = rules.get("case")
    if case == "lower":
        text = text.lower()
    elif case == "upper":
        text = text.upper()
    elif case == "title":
        text = re.sub(r"[^\W_]+", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), text)
    if rules.get("spaces"):
        text = text.replace(" ", rules["spaces"])
    return text


def format_date(date, style=None):
    """"20240531" written as chosen in Settings (DATE_FORMATS)."""
    y, m, d = date[:4], date[4:6], date[6:8]
    return {"dmy": "%s-%s-%s" % (d, m, y), "mdy": "%s-%s-%s" % (m, d, y), "compact": date[:8],
            "dots": "%s.%s.%s" % (d, m, y)}.get(style or NAME_RULES.get("date"), "%s-%s-%s" % (y, m, d))


# File name template (Settings): "{uploader} - {title} ({date})" gives "Rick Astley - Never Gonna… (2009-10-25)".
DEFAULT_FILENAME_TEMPLATE = "{title}"
FILENAME_FIELDS = {
    "title": "the video's title",
    "uploader": "channel or account",
    "date": "upload date, 2024-05-31",
    "year": "upload year",
    "platform": "YouTube, TikTok…",
    "id": "the video's ID on the site",
    "resolution": "1080p (videos only)",
    "artist": "a song's artist, else the channel",
    "song": "a song's title, without “(Official Video)”…",
}
# Sorting into sub-folders (Settings): "{platform}/{uploader}" puts a video in "YouTube/Rick Astley".
FOLDER_FIELDS = dict(FILENAME_FIELDS, type="Videos, Music, Images or Subtitles")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_SEPARATORS = " -_.,·|–—"


def unknown_filename_fields(template):
    return list(dict.fromkeys(name for name in _PLACEHOLDER.findall(template or "") if name not in FILENAME_FIELDS))


def filename_fields(info, fallback, video=True):
    """Template values: the downloaded video's details (info), else those known from the analysis."""
    info, fallback = info or {}, fallback or {}
    date = str(info.get("upload_date") or fallback.get("upload_date") or "")
    height = info.get("height")
    artist, song = guess_song({"title": info.get("title") or fallback.get("title") or "",
                               "uploader": info.get("uploader") or fallback.get("uploader") or "",
                               **{k: info[k] for k in ("artist", "artists", "track", "channel") if info.get(k)}})
    return {
        "title": info.get("title") or fallback.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or fallback.get("uploader") or "",
        "date": format_date(date) if re.fullmatch(r"\d{8}", date) else "",
        "year": date[:4] if date[:4].isdigit() else "",
        "platform": info.get("extractor_key") or fallback.get("platform") or "",
        "id": info.get("id") or fallback.get("video_id") or "",
        "resolution": "%dp" % height if video and isinstance(height, int) and height > 0 else "",
        "artist": artist,
        "song": song,
    }


def _fill(template, fields):
    """The template with its fields; the text around an empty field is tidied up ("Title ()" -> "Title")."""
    text = _PLACEHOLDER.sub(lambda m: str(fields.get(m.group(1), m.group(0))), template)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"\s+-(\s+-)+\s+", " - ", text)  # "A -  - B" when a middle field is empty
    return " ".join(text.split()).strip(_SEPARATORS)


def render_filename(template, fields, ext):
    """File name from the template."""
    # No sub-folders: a slash typed in the template becomes a separator (one in a title is just removed).
    template = re.sub(r"\s*[\\/]\s*", " - ", template or DEFAULT_FILENAME_TEMPLATE)
    name = clean_name(_fill(template, fields) or fields.get("title") or "video")
    return safe_filename(name, ext, NAME_RULES.get("max_length") or 120)


def file_type(fmt, subs_only=False):
    """The {type} of the sorting template."""
    if subs_only:
        return "Subtitles"
    return ("Music" if fmt in AUDIO_FORMATS else "Images" if fmt in IMAGE_FORMATS or fmt in ("photos", "thumbnail")
            else "Videos")


def render_folder(template, fields):
    """The sub-folders of the sorting template ("{platform}/{uploader}"), one safe name per level; a level
    whose fields are all empty is left out."""
    parts = []
    for level in re.split(r"[\\/]+", template or ""):
        text = clean_name(_fill(level, fields))
        if text:
            parts.append(safe_filename(text, ""))
    return parts


def unique_path(folder, name):
    target = Path(folder) / name
    stem, suffix = target.stem, target.suffix
    n = 1
    while target.exists():
        target = Path(folder) / ("%s (%d)%s" % (stem, n, suffix))
        n += 1
    return target


def reserve_file(folder, name):
    """Like unique_path, but creates the (empty) file right away, so parallel downloads with the
    same title can never pick the same name."""
    while True:
        target = unique_path(folder, name)
        try:
            with open(target, "xb"):
                return target
        except FileExistsError:
            continue
