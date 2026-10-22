"""The Flet interface. main() in app.py builds the window; each tab has its own file.

  app.py            App (what the tabs share) and main()
  home_tab.py       Home tab: the field to paste a link or search, the tiles of the tools, and each tool's page
  download_tab.py   the Download page: analyze a link, choose the format and options
  clip.py           the "Extract a clip" part of the Download tab
  items.py          the Download tab's list: a playlist, a podcast, several links (each with its options)
  search_card.py    the results of a search typed in the Download tab
  presets.py        the one-tap presets of the Download tab, and their card in Settings
  montage.py        the Montage page: passages of several videos or files in one file
  player.py         the preview player of the Download and Edit tabs
  edit_tab.py       Edit file(s): the editor of one video or audio file of the device
  edit_more.py      the tools' lists of files: Edit file(s), Join files, Tag music
  table.py          a list shown as a table: sorted by its columns, rows dragged, filters
  subtitle_editor.py  correcting a subtitle file (Edit tab)
  queue_tab.py      Queue tab: running downloads, resuming after a restart
  history_tab.py    Library tab (the history)
  subscriptions.py  Subscriptions tab: lists whose new items are downloaded on their own
  settings_tab.py   Settings tab
  watch.py          the watched folder of Settings (computer)
  welcome.py        the welcome pages of the first start
  palette.py        the command palette (Ctrl+K, computer)
  progress.py       progress bar + Cancel button, one row of the queue
  widgets.py        small shared building blocks
  style.py          spacing and text sizes shared by every screen
  i18n.py           the language of the app: the texts translated as they are given to the controls
  i18n_fr.py        the French texts
"""
