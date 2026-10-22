"""Stash: downloads videos and audio from YouTube, TikTok, Instagram, Twitter and every other site
yt-dlp supports.

Entry point for `flet run` and `flet build`. The code lives in two packages:
  core/  everything that works without the interface (yt-dlp, ffmpeg or PyAV, files, settings)
  ui/    the Flet interface, one file per tab
"""

import flet as ft

from ui.app import main

if __name__ == "__main__":
    ft.run(main)
