# Stash

A Windows and Android app that downloads videos and sounds from a link, and edits local media files. Built with Tauri 2, React and Rust: links are read by [yt-dlp](https://github.com/yt-dlp/yt-dlp), and media is cut and converted by FFmpeg, built into the app.

## What it does

- **Download**: paste a link from any site yt-dlp supports, then save the video (MP4, MKV, MOV) or the sound only (M4A, MP3, FLAC, WAV), at the quality you pick.
- **Options**: keep only an extract (start / end), remove the sound, change the speed or the frame rate, or set a size limit that the file is re-encoded to fit.
- **SponsorBlock**: on YouTube, cuts the chosen parts (sponsor, intro, outro…). When that is the only change, the cut is made without re-encoding.
- **TikTok photo posts**: saved as a slideshow video, one photo with the sound, the photos only, or the sound only.
- **Edit**: open or drop videos and sounds from your device, preview them (a video the app can't play as it is, HEVC for example, is converted a few seconds at a time from where it plays), and save edited copies with the same options. With several files, options set for all apply to each as far as it allows (no size or frame rate above its own), and each file can still get its own options and extract. On Windows, **Edit with Stash** in a video or sound file's right-click menu opens it there, in place of the files open (under "Show more options" on Windows 11).
- **Presets**: save the options (all but the extract) under a name, and pick them again in Download or Edit; rename or delete them in Settings.
- **Rename**: the title of a link, a file being edited or a job in the queue is the name it is saved under; type over it to change it, even once saved.
- **Queue**: several jobs at once, with progress and time left; cancel, retry, open, share or show the file.
- **Settings**: download folder, theme, video and sound codecs with each encoder's speed, the encoding priority and, on Windows, the graphics card (NVIDIA or AMD) and its speed, presets, SponsorBlock parts, a `cookies.txt` for sign-in-only videos, and yt-dlp updates.

Streams are copied as they are whenever possible; otherwise video is encoded in H.264 (x264) or AV1 (SVT-AV1, about half the size for the same picture), on the processor or the graphics card (which then also decodes the source; an NVIDIA card also trims and resizes it, the pictures never leaving the card; with the card on, it also decodes and resizes for the processor's encoders), and sound in AAC, MP3, FLAC or PCM.

## Build

Needs Node.js, Rust, Visual Studio C++ build tools, Git Bash, [vcpkg](https://vcpkg.io) (`VCPKG_ROOT`), the Android SDK (`ANDROID_HOME`) with NDK 28.2, and for Windows [LLVM](https://llvm.org) (`winget install LLVM.LLVM`), whose clang compiles FFmpeg's resize on NVIDIA cards.

1. Build FFmpeg, once per platform: `bash src-tauri/ffmpeg/build.sh windows` (or `android`).
2. For Windows, put [yt-dlp.exe](https://github.com/yt-dlp/yt-dlp/releases) and the Windows x86_64 `qjs` of [QuickJS-NG](https://github.com/quickjs-ng/quickjs/releases) in `src-tauri/binaries/`, renamed `yt-dlp-x86_64-pc-windows-msvc.exe` and `qjs-x86_64-pc-windows-msvc.exe`.
3. `npm install`, then:
   - `npm run tauri dev`: run on Windows
   - `npm run tauri build`: Windows installer
   - `npm run tauri android build -- --apk --target aarch64`: Android APK (arm64, signed with this machine's debug key)

Tests: `npm test`, and `cargo test` in `src-tauri`.
