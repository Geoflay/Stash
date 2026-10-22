"""Everything that works without the interface: reading links, downloading, converting, tagging,
and the files the app keeps (settings, history, cookies, unfinished downloads).

  analyze.py       reading a link: title, formats, photos, subtitles
  app_update.py    whether a newer version of the app is published
  backend.py       ffmpeg or PyAV, and the output formats they can make
  chapters.py      one file per chapter
  compress.py      re-encoding a file to fit a size limit (Discord…)
  cookies.py       cookies.txt, browser cookies, checking them
  download.py      downloading a video or its audio (download_media)
  editor.py        the Edit tab's work: changing a file of the computer
  errors.py        cancellation, readable errors, hints
  ffmpeg.py        running ffmpeg, a file's length, changing a video's container
  filenames.py     file name template, names that never overwrite a file
  history.py       download history (the Library) and its thumbnails
  jobs.py          parallel download and conversion slots
  links.py         which platform a link is from, playlist or not
  live.py          lives: waiting for one to start, recording it
  montage.py       passages of several videos joined into one file
  network.py       yt-dlp options: retries, speed limit, cookies
  playlist.py      listing the videos of a playlist, channel or profile; their order; .m3u8 files
  preview.py       still frames for choosing a clip
  pyav.py          merging, cutting and converting without ffmpeg
  slideshow.py     photo posts turned into a video
  speed.py         playing speed (faster or slower)
  stills.py        GIFs and still images taken from a video
  storage.py       the app's data folder and settings.json
  streams.py       which streams to download, qualities, size estimates
  subscriptions.py lists followed: their new items downloaded on their own
  subtitles.py     subtitle languages, .srt files, embedding
  system.py        Downloads folder, opening files, notifications
  tags.py          title, artist, cover art… written into the files
  units.py         times and sizes, as typed and as shown
  watch.py         the watched folder: files converted with a preset
  web.py           HTTPS requests made outside yt-dlp
  workdirs.py      work folders, unfinished downloads (queue.json)
  ytdlp_update.py  updating yt-dlp from the app
"""

# Must run before anything imports yt_dlp: loads the yt-dlp update downloaded from Settings, if any.
from core.ytdlp_update import YTDLP_UPDATE_ACTIVE

__all__ = ["YTDLP_UPDATE_ACTIVE"]
