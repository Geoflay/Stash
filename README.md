# Stash

Download videos, music, photos and subtitles from YouTube, TikTok, Instagram, Twitter and the hundreds of other
sites [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), then edit, join, tag and
organize them, all in one app for Windows, macOS, Linux and Android. The app is available in English and French
(Settings > Appearance > Language).

Built with [Flet](https://flet.dev) (Flutter) and Python. Vibe coded.

## Contents

- [Run](#run) · [Using the app](#using-the-app) · [Features](#features) · [Settings](#settings)
- [Phone and other devices](#phone-and-other-devices) · [Build](#build) · [Project structure](#project-structure) · [Tests](#tests)

## Run

```
pip install -r requirements.txt
flet run src/main.py          # a window
flet run --web src/main.py    # in a browser (the tools that edit files of the device are left out)
```

[ffmpeg](https://ffmpeg.org) is used when it is found (on the PATH, or downloaded from Settings > Advanced). Without
it (a phone), the app does what it can with PyAV: cut, convert, tags, size limit.

## Using the app

The navigation has up to five tabs: **Home**, **Queue**, **Library**, **Subscriptions** and **Settings**. Home and
Settings are always there; the others can be hidden (Settings > Appearance > Tabs shown).

### Home

The field at the top takes:

- **a link** (or several): the video is analyzed and opens on the **Download** page: choose the type (video, audio,
  GIF, image), the format and the quality, then **Download**. Several links open a list instead.
- **words**: they are searched on YouTube (or SoundCloud), and tapping a result analyzes it.
- the links typed lately are suggested under the field.

Under it, the tiles of the tools. Each one opens its own page, with a back button, its icon and name, and a button
that clears the page (after a confirmation). **Each page keeps what it holds** until you clear it or replace it.

| Tool | What it does |
| --- | --- |
| **Download** | A link analyzed: formats, qualities and options. |
| **Edit file(s)** | Change video or audio files of the device. |
| **Join files** | Put files one after the other in a single file. |
| **Montage** | Passages of several videos (links or files) in one file. |
| **Tag music** | Find titles, artists, albums, covers and lyrics for songs. |
| **Correct subtitles** | Fix the times and words of a .srt or .vtt file. |

Downloads go to the **Queue**, and finished ones to the **Library**.

### Queue

Every download with its progress. Several run at once (Settings sets how many); the others wait for a slot. You can
pause, resume, cancel, retry, drag to change the order, or pause everything. Downloads that were not finished when the
app was closed can be resumed. While YouTube blocks the app (bot check), its downloads are held until you press Resume.

### Library

The history of what was downloaded, as a table. Tap a column title (title, type, site, format, date, size) to sort by
it, again to reverse. The filter button opens the filters (type, site, format, date, file still there, saved more than
once); the button next to it clears them. A search field finds a title or file name. Each row has a menu (⋮, right
click, or long press): play it in the app, open, show in its folder, share, send to a phone, open in Edit file(s),
download again, copy the link, delete the file, forget it. Several can be selected, and the list can be exported as
a CSV table.

### Subscriptions

Subscribe to a channel, playlist or podcast from its list on the Download page. Its new items are downloaded on their
own, with the options you had chosen, into a folder named after the list. A subscription can be filtered (words,
length, no Shorts, how many), and can delete the files of the items that leave the list.

## Features

### Downloading

- **Types and formats**: video (MP4, MKV, WebM, MOV…), audio (MP3, M4A, Opus, FLAC, WAV…, or the original sound), GIF
  (animated, optionally back and forth), image (a frame, the thumbnail, or all the photos of a post).
- **Quality** with the size of the file shown at each quality; AV1, VP9 or H.264 can be preferred.
- **Watch or listen first**: tap the thumbnail to play the video as it will be downloaded (quality, part, speed and
  subtitles chosen), save a picture of the frame shown, or mark the start and end of a clip at the moment shown.
- **Clips and chapters**: download a passage only (several passages, one file each), one file per chapter or album
  track, or leave some chapters out.
- **Sound**: several sound tracks (dubbed languages), no sound, even out the volume, cut the silences, another speed.
- **Picture**: crop (vertical 9:16, square, black bars removed…) and turn.
- **Subtitles**: several languages at once, embedded in the video, as .srt files, drawn onto the picture (with a size,
  colour and position), or as a transcript (.txt, .html); or the subtitles only.
- **Size limit**: re-encode to fit a size (for example 10 MB for a message).
- **Tags and cover art** written into the files, corrected by hand if wanted; songs get their real tags from
  MusicBrainz and their lyrics from LRCLIB.
- **SponsorBlock**: sponsors, intros and other parts are cut out (categories chosen in Settings).
- **Presets**: save the type, format, quality and options as a preset (one tap), and choose one per site.
- **Lists**: playlists, channels, albums, podcast feeds (with their episodes tagged from the feed) and several links
  pasted together. Each item can be previewed and given its own options; items already in the Library are marked;
  the list can be sorted; a playlist file (.m3u8) is written.
- **Photo posts** (TikTok, Instagram…) as one photo with the sound, or a slideshow of all of them.
- **Lives**: recorded as they come, or waited for until they start.
- **Duplicates**: asked before downloading again what the Library already has.
- **Reliability**: automatic retries, resuming after a lost connection, a speed limit (also a different one at night),
  a proxy, browser cookies or a cookies.txt file for sites that need a login.

### Edit file(s)

Add video or audio files (or a folder, or drop them on the window). Each one is a row of a table (picture, name, format,
length, size) that can be sorted and filtered.

- **One file**: its editor opens at once. A tap on its picture plays it with the changes chosen.
- **Several files**: the changes of every file are at the top; a file can have its own changes (the ⚙ button of its
  row), kept until you reset them.
- **The changes**: another format; a smaller picture; another speed (or for a passage only); a part only; the sound and
  subtitle tracks kept; subtitles added (drawn or as a track); a size limit; new tags and cover art; crop and turn;
  a text or a logo on the picture; the volume evened out; the sound faded in and out; the silences cut out; a GIF.
- **Subtitles saved as a file**: any subtitle track of the file can be saved next to it (.srt, .ass, .vtt).
- One picture of the result can be seen before saving. Copies are saved next to the original (the original is never
  changed).

### Join files

Files one after the other in a single file. Drag the rows to change the order. Files of the same kind are joined
without re-encoding; others are re-encoded to fit the first one.

### Montage

Add a video link (one video, not a playlist) or files of the device, type each passage's start and end, drag them in
order, choose the format, then make the montage (it goes to the Queue). Passages can also be added from the Download
page with **Add to the montage**.

### Tag music

Add songs (or a folder). Each song is looked up by its tags, else by its name (“Artist - Title”) on MusicBrainz (title,
artist, album, date, cover art) and LRCLIB (lyrics); a few at a time. Only sure matches (same title, same length) are
kept. Check what was found, correct it by hand (also for songs nothing was found for), then write it. The files can be
renamed with the music file name of Settings. Only sound files are accepted.

### Correct subtitles

Open a .srt or .vtt file and fix its words and times line by line.

### Other

- **Command palette** (computer, Ctrl+K): find a tab, a setting, a format, a preset or an action by typing.
- **Keyboard shortcuts** (Settings): Ctrl+V pastes a link, Enter downloads, Escape closes the player.
- **Watch the clipboard** (computer): a link copied anywhere is analyzed at once, and can be downloaded at once.
- **Watched folder** (computer): files that appear in a folder are converted with a preset.
- **Drag and drop** (Windows): files and link files dropped on the window.
- **Notifications and sounds** when downloads finish; progress in the taskbar (computer) or in a notification (Android).
- **Media server files**: names and .nfo files for Plex, Jellyfin or Kodi.

## Settings

Settings are grouped in categories, with a search field at the top:

- **General**: destination folder, file and folder name templates, music file name, notifications, other devices.
- **Appearance**: language, theme (light, dark, pure black), corners, accent colour, **text size** and **app size**
  (two sliders), tabs shown, first tab, simple mode (fewer options after analyzing a link).
- **Downloads**: presets, languages (subtitles and sound), tags, lyrics, playlists, SponsorBlock, schedule (night
  only, only while charging on Wi-Fi), subscriptions.
- **Network**: speed limit, proxy, retries, cookies.
- **Storage**: how long the Library keeps entries, clean-up, backup folder, export and import of the settings.
- **Advanced**: ffmpeg, yt-dlp (updated from the app), the compatibility of the files, reset.

Changing a setting that is read once at the start (language, sizes, ffmpeg, yt-dlp) offers to restart the app.

## Phone and other devices

- **Android**: the app is in the Share menu of other apps (Instagram, TikTok, YouTube…): Share > Stash opens
  it with the link analyzed. A notification shows the progress and says when a download is finished; files open with the
  phone's apps and can be shared.
- **Links between your devices**: with “Receive links from other devices” on (Settings > General > Other devices), the
  app finds the other devices running it on the same Wi-Fi. The **Send to a device** button (on the Download page)
  sends them a link, which downloads there; the other device asks before taking it (it can remember trusted devices).
  On a computer, a QR code also opens a page where a phone's browser can send links, and **Send to a phone** shows a QR
  code that downloads a file or a folder (as a .zip) on a phone.

## Build

```
flet build windows     # or macos, linux
flet build apk         # Android, with the extensions below
flet build web
```

The Android build includes the extensions of `extensions/` (see `[tool.flet.android]` in pyproject.toml).

## Project structure

```
src/
  main.py      entry point (flet run / flet build)
  core/        everything that works without the interface
    analyze.py      reading a link: title, formats, photos, subtitles
    download.py     downloading a video or its audio
    ...             one file per topic, listed in core/__init__.py
  ui/          the Flet interface
    app.py          App (what the tabs share) and main()
    home_tab.py     Home: the field, the tiles and the page of each tool
    download_tab.py, edit_tab.py, edit_more.py, montage.py   the tools' pages
    queue_tab.py, history_tab.py, subscriptions.py, settings_tab.py   the other tabs
    ...             listed in ui/__init__.py
extensions/
  flet-share-target/         Android: puts the app in the Share menu of other apps (Python + Flutter + Java)
  flet-local-notification/   Android: a notification when a download finishes
  flet-open-file/            Android: open a downloaded file with the phone's apps
  flet-file-drop/            Windows: files dropped onto the window
tests/         pytest tests
```

`ui` uses `core`, never the other way around: the download code can be used and tested without the
interface. Texts of the interface are written in English and translated by `ui/i18n_fr.py`.

## Tests

```
pip install pytest pytest-asyncio
python -m pytest              # fast tests, no network (about 1 minute; needs ffmpeg for the media tests)
python -m pytest -m network   # real downloads from YouTube, Dailymotion, Bandcamp
```

The tests use a temporary data folder: your settings, history and cookies are never touched.
