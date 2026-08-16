# Downstream

A feature-rich video downloader with a graphical user interface that allows you to download videos in your preferred quality and format.

## Supported sites

- **YouTube** — videos, Shorts, and playlists
- **Instagram** — reels, posts, and IGTV (public posts work without a login;
  for login-walled posts set a browser in Settings so its session cookies are reused)
- **Threads** — video posts on threads.net / threads.com (via a bundled
  yt-dlp extractor plugin in `yt_dlp_plugins/`, since mainline yt-dlp does
  not support Threads)

## Features

- Download videos with both video and audio
- Download video-only streams
- Download audio-only streams
- Select specific video quality and format
- Clipboard detection for video URLs
- Download history tracking
- Progress bar with download status
- Customizable download settings
- **Enhanced Playlist Support**:
  - Download entire playlists
  - Select specific video ranges
  - Reverse download order option
  - Progress tracking for playlist downloads

## Project layout

```
.
├── downstream.py           # Desktop app (Tkinter GUI + local API server)
├── install.py              # Interactive installer
├── build.py                # PyInstaller build script (Windows exe)
├── yt_dlp_plugins/         # Bundled yt-dlp extractor plugin for Threads
└── chrome_extension/       # Companion browser extension (see its README)
```

The desktop app and the Chrome extension work together: the app includes a
small local API server the extension sends download requests to, so the
extension requires the desktop app to be running.

## Requirements

- Python 3.11 or later
- ffmpeg — required to merge video+audio and to convert audio-only downloads to MP3
- Deno (recommended) — yt-dlp uses it to run YouTube's player JavaScript; without it some formats are missing
- Windows, Linux, or macOS

## Installation

Run the interactive installer from this directory:

```bash
python install.py
```

It walks you through everything needed at runtime:

1. Checks your Python version
2. Creates a virtual environment (`.venv`) and installs the Python packages into it
3. Checks for ffmpeg and Deno, and offers to install them (via winget on Windows)
4. Asks where downloads should be saved (writes `settings.json`)
5. Creates `Downstream.bat` and an optional desktop shortcut (Windows)
6. Optionally helps you install the Chrome extension

Every prompt has a sensible default — pressing Enter through the installer gives a working setup.

### Manual Installation

If you prefer to set up manually:

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install yt-dlp pyperclip flask flask-cors
# Linux/Mac
.venv/bin/pip install yt-dlp pyperclip flask flask-cors
```

Then install ffmpeg (and ideally Deno) with your system's package manager.

## Usage

1. Start the application — double-click `Downstream.bat` (Windows), or run:
```bash
.venv\Scripts\python downstream.py
```

2. Basic Video Download:
   - Enter a YouTube, Instagram, or Threads URL or paste from clipboard
   - Select download type (Video+Audio, Video Only, Audio Only)
   - Click "Download" and select your preferred quality
   - Monitor progress in the status bar

3. Playlist Download:
   - Enter a YouTube playlist URL
   - Use playlist options:
     - "Download All Videos" to download entire playlist
     - Enter start/end indices for specific videos
     - Toggle "Reverse Order" to download in reverse
   - Click "Download" and select quality
   - Monitor progress for each video

4. Settings (click the icon in the window's title bar, top left, then "Settings..."):
   - Source folder for in-progress downloads (optional; finished files are moved to the destination)
   - Destination folder for finished downloads
   - Default download type (Video + Audio, Video Only, Audio Only)
   - Default video format
   - Instagram/Threads login: pick the browser you're logged in with to
     reuse its cookies for posts that require a login
   - Auto download: skip the format picker entirely — pasting a URL starts
     the download immediately at a preset quality (best / medium ≈720p /
     low ≈480p; for audio-only downloads ≈128 / ≈64 kbps). Quality caps use
     the shorter video side, so vertical reels/Shorts pick sensibly.
   - Threads cross-posts: when a Threads post's video actually lives on
     another supported site (e.g. an Instagram reel shared to Threads) the
     app downloads it from there. Tick "ask before downloading from the
     source site" to get a confirmation dialog first (off by default).

5. History (title-bar icon menu, "Download History..."):
   - View past downloads with timestamp, format, and status

## Built-in server for the Chrome extension

The desktop app doubles as the backend for the companion Chrome extension —
there is no separate server to install or run. When the app starts, it also
starts a small API on `http://127.0.0.1:47811` (reachable only from this
machine). The extension sends the video URL and your preferences there, and
the desktop app performs the download.

- `GET /` — health check; the extension's downloads work only while the app is running
- `POST /api/download` — starts a download (`{"url": ..., "settings": {"downloadType": ..., "quality": ...}}`)

API downloads are saved to the destination folder configured in the app's
Settings. See [chrome_extension/INSTALL.md](chrome_extension/INSTALL.md)
for installing the extension itself.

## Troubleshooting

### Common Issues

1. **Application Won't Start**:
   - Verify Python 3.11+ is installed
   - Ensure virtual environment is activated
   - Check if required packages are installed

2. **Download Fails**:
   - Check internet connection
   - Verify YouTube URL is valid
   - Try a different video quality
   - Check download history for error details

3. **Playlist Issues**:
   - Ensure playlist URL is correct (should contain "list=" parameter)
   - Verify playlist is not private
   - Check start/end indices are valid numbers
   - Try downloading a single video first

4. **GUI Problems**:
   - Restart the application
   - Check for error messages in terminal
   - Verify display settings

### Getting Help

If you encounter issues:
1. Check the terminal for error messages
2. Review the download history
3. Verify your Python version and dependencies
4. Try reinstalling the required packages

## License

This project is open source and available under the MIT License.