Video downloader for Youtube, Tiktok, Instagram, Twitter and the hundreds of other sites
[yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).
Vibe coded.

## Run

```
flet run src/main.py
```

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
    download_tab.py, queue_tab.py, history_tab.py, settings_tab.py   one file per tab
    ...             listed in ui/__init__.py
extensions/
  flet-share-target/         Android: puts the app in the Share menu of other apps (Python + Flutter + Java)
  flet-local-notification/   Android: a notification when a download finishes
  flet-open-file/            Android: open a downloaded file with the phone's apps
```

`ui` uses `core`, never the other way around: the download code can be used and tested without the
interface.

The Android build (`flet build apk`) includes these extensions (see `[tool.flet.android]` in pyproject.toml):
in Instagram, TikTok, YouTube… Share > Video downloader opens the app with the link analyzed, and a
notification says when a download is finished while the app is in the background.

## Tests

```
pip install pytest pytest-asyncio
python -m pytest              # fast tests, no network (about 10 s; needs ffmpeg for the media tests)
python -m pytest -m network   # real downloads from YouTube, Dailymotion, Bandcamp
```

The tests use a temporary data folder: your settings, history and cookies are never touched.
