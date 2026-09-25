import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog
import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
import os
import json
import re
import shutil
import threading
import sys
import logging
from datetime import datetime
import pyperclip
from flask import Flask, request, jsonify
from flask_cors import CORS

# The windowed exe has no console, so sys.stdout/stderr are None there.
# Libraries that print progress (the Whisper model download's progress bar)
# would crash writing to them; point them at devnull instead.
for _stream in ('stdout', 'stderr'):
    if getattr(sys, _stream) is None:
        setattr(sys, _stream, open(os.devnull, 'w', encoding='utf-8'))
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for more verbose logging
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)
# The Whisper model loader's HTTP client logs every request at DEBUG
for _noisy in ('httpx', 'httpcore', 'huggingface_hub', 'faster_whisper'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

APP_NAME = "Downstream"
APP_VERSION = "1.6.14"

def get_base_path():
    """Get base path for resources, works both in development and when packaged"""
    try:
        if getattr(sys, 'frozen', False):
            # Running in a bundle (PyInstaller)
            base_path = os.path.dirname(sys.executable)
            # Check and set Tcl/Tk paths for frozen environment
            tcl_dir = os.path.join(sys._MEIPASS, "tcl")
            tk_dir = os.path.join(sys._MEIPASS, "tk")

            if os.path.exists(tcl_dir) and os.path.exists(tk_dir):
                vers = [d for d in os.listdir(tcl_dir) if d.startswith('tcl')]
                if vers:
                    tcl_vers = sorted(vers)[-1]
                    tk_vers = 'tk' + tcl_vers[3:]
                    os.environ['TCL_LIBRARY'] = os.path.join(tcl_dir, tcl_vers)
                    os.environ['TK_LIBRARY'] = os.path.join(tk_dir, tk_vers)
                    logger.debug(f"Set TCL_LIBRARY to {os.environ['TCL_LIBRARY']}")
                    logger.debug(f"Set TK_LIBRARY to {os.environ['TK_LIBRARY']}")
        else:
            # Running in a normal Python environment
            base_path = os.path.dirname(os.path.abspath(__file__))

        logger.debug(f"Base path set to: {base_path}")
        return base_path
    except Exception as e:
        logger.error(f"Error in get_base_path: {str(e)}", exc_info=True)
        raise

# yt-dlp discovers plugin packages named yt_dlp_plugins/ on sys.path. Ours
# ships the Threads extractor (Threads isn't supported by mainline yt-dlp).
# In the packaged exe the plugin is bundled into sys._MEIPASS (already on
# sys.path); adding the exe's own folder too lets a user drop in an updated
# plugin without rebuilding.
sys.path.insert(0, get_base_path())

DEFAULT_SETTINGS = {
    # Final destination for finished downloads
    "download_path": os.path.join(os.path.expanduser("~"), "Downloads"),
    # Source/staging folder for in-progress downloads; "" = download
    # directly into the destination
    "temp_path": "",
    "default_download_type": "video+audio",
    "format": "mp4",
    # Browser to borrow cookies from for Instagram/Threads downloads
    # ("" = none). Much of Meta's content is behind a login; yt-dlp can
    # reuse an existing browser session instead of asking for credentials.
    "cookies_browser": "",
    # Auto download: skip the format picker and download immediately at the
    # configured quality when a URL is entered/pasted
    "auto_download": False,
    "auto_download_quality": "best",
    # Threads cross-posts (the video really lives on e.g. Instagram): ask
    # before switching to the source site, or just go ahead and download
    # from there
    "confirm_crosspost": False,
    # Save the platform's captions alongside the video: a plain-text
    # transcript (.txt), closed captions (.srt), or both. When either is on,
    # each download gets its own subfolder (several files arrive per video).
    "transcript_txt": False,
    "transcript_srt": False,
}

# Quality presets for auto download and the extension API: a yt-dlp format
# expression plus an optional format_sort. yt-dlp resolves these itself, so
# no metadata extraction is paid up front. Quality caps use format_sort
# ('res' = the SHORTER video dimension) instead of [height<=N] filters:
# vertical reels/Shorts aren't over-throttled by their large height, and a
# preference (unlike a filter) still succeeds on sites that only serve one
# resolution.
QUALITY_FORMATS = {
    "video+audio": {
        "best": ("bestvideo+bestaudio/best", None),
        "medium": ("bestvideo+bestaudio/best", ["res:720"]),
        "low": ("bestvideo+bestaudio/best", ["res:480"]),
    },
    "video-only": {
        "best": ("bestvideo", None),
        "medium": ("bestvideo", ["res:720"]),
        "low": ("bestvideo", ["res:480"]),
    },
    # Audio uses proximity sort (~): 'abr:128' (a cap) proved unreliable in
    # testing - it picked 48kbps on YouTube; '~' picks the closest bitrate
    "audio-only": {
        "best": ("bestaudio/best", None),
        "medium": ("bestaudio/best", ["abr~128"]),
        "low": ("bestaudio/best", ["abr~64"]),
    },
}


def get_config_dir():
    """Per-user config/data folder: %APPDATA%\\Downstream on Windows.

    Settings and download history live here - NOT next to the exe - so
    they survive updates, rebuilds (which wipe dist\\), and moving or
    duplicating the exe.
    """
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.path.join(os.path.expanduser('~'), '.config')
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _migrate_legacy_file(base_path, filename):
    """One-time move of a data file from the old location (next to the
    exe/script) into the config dir, preserving existing settings from
    installs that predate get_config_dir()."""
    new_path = os.path.join(get_config_dir(), filename)
    if not os.path.exists(new_path):
        legacy = os.path.join(base_path, filename)
        if os.path.exists(legacy):
            try:
                shutil.copy(legacy, new_path)
                logger.info("Migrated %s to %s", filename, new_path)
            except OSError:
                logger.warning("Could not migrate %s", legacy, exc_info=True)
    return new_path


def load_settings(base_path):
    """Read settings.json, falling back to defaults for missing/invalid values.

    Shared by the GUI and the extension API so both honor the same folders.
    base_path is only used to migrate a settings file saved by older
    versions next to the exe.
    """
    settings = dict(DEFAULT_SETTINGS)
    try:
        settings_path = _migrate_legacy_file(base_path, "settings.json")
        if os.path.exists(settings_path):
            with open(settings_path, "r") as f:
                settings.update(json.load(f))
        # Fall back to defaults if saved paths don't exist (e.g. a settings
        # file created on another machine/OS)
        if not os.path.isdir(settings.get("download_path", "")):
            settings["download_path"] = DEFAULT_SETTINGS["download_path"]
        if settings.get("temp_path") and not os.path.isdir(settings["temp_path"]):
            settings["temp_path"] = ""
        settings["auto_download"] = bool(settings.get("auto_download"))
        if settings.get("auto_download_quality") not in ("best", "medium", "low"):
            settings["auto_download_quality"] = DEFAULT_SETTINGS["auto_download_quality"]
        settings["confirm_crosspost"] = bool(settings.get("confirm_crosspost"))
        settings["transcript_txt"] = bool(settings.get("transcript_txt"))
        settings["transcript_srt"] = bool(settings.get("transcript_srt"))
    except Exception:
        return dict(DEFAULT_SETTINGS)
    return settings


def build_download_paths(settings):
    """yt-dlp 'paths' dict: partial files go to temp, finished files to home."""
    paths = {"home": settings["download_path"]}
    if settings.get("temp_path"):
        paths["temp"] = settings["temp_path"]
    return paths


# Let yt-dlp fetch its official JS challenge solver (cached after first use);
# without it YouTube's "n challenge" fails, hiding formats and throttling
# download speed
BASE_YDL_OPTS = {"remote_components": ["ejs:github"]}

# Sites the app accepts. yt-dlp can handle many more, but the GUI's format
# filtering and options are only tuned for these.
SUPPORTED_URL_RE = re.compile(
    r'(youtube\.com|youtu\.be|instagram\.com|threads\.(?:net|com)'
    r'|tiktok\.com)',  # also covers the vm./vt. short-link hosts
    re.IGNORECASE)

META_URL_RE = re.compile(r'(instagram\.com|threads\.(?:net|com))', re.IGNORECASE)

# Matches the error our Threads extractor plugin raises for cross-posts
# (posts whose video is actually hosted on another site, e.g. an Instagram
# reel shared to Threads). The phrasing is fixed in threads.py.
CROSSPOST_ERROR_RE = re.compile(
    r'Download it from the source instead - (?P<source>[^:]+): (?P<url>https?://\S+)')


def site_ydl_opts(url, settings):
    """Per-site yt-dlp options on top of BASE_YDL_OPTS.

    Instagram and Threads gate much of their content behind a login; when a
    browser is configured in Settings, reuse its session cookies.
    """
    opts = dict(BASE_YDL_OPTS)
    browser = (settings.get("cookies_browser") or "").strip()
    if browser and META_URL_RE.search(url):
        opts["cookiesfrombrowser"] = (browser,)
    return opts


# Human-made subtitle tracks to prefer over speech recognition when a site
# has them: English in any region variant ('en', 'en-GB', TikTok's
# 'eng-US'). Only *manual* subtitles are requested - auto-generated
# captions are skipped because local Whisper transcription reads better
# (punctuation, no rolling duplicates), and YouTube lists dozens of
# auto-translated caption tracks that 'en.*' would also match and that
# get the download rate-limited (HTTP 429).
TRANSCRIPT_SUB_LANGS = ['en.*']

# Whisper model for local speech-to-text. 'base' transcribes a 60 s reel in
# ~5 s on CPU with near-identical text to 'small' (3x slower); it is
# downloaded once (~145 MB) into the config dir on first use.
WHISPER_MODEL = 'base'


def transcript_enabled(settings):
    return bool(settings.get('transcript_txt') or settings.get('transcript_srt'))


def transcript_ydl_opts(settings):
    """Extra yt-dlp options when transcript saving is on ({} when off).

    Requests the site's human-made English subtitles, normalized to SRT by
    ffmpeg. When there are none (Instagram, Threads, and most TikTok and
    YouTube videos), TranscriptPostProcessor transcribes the audio itself.
    """
    if not transcript_enabled(settings):
        return {}
    return {
        'writesubtitles': True,
        'subtitleslangs': list(TRANSCRIPT_SUB_LANGS),
        'postprocessors': [
            {'key': 'FFmpegSubtitlesConvertor', 'format': 'srt'}],
    }


def transcript_outtmpl(outtmpl, settings):
    """Nest each download in its own folder when transcripts are on -
    several files arrive per video, so give them a home named after it.

    The title then appears twice in the path (folder and file), and
    Instagram/Threads titles are whole post captions. Windows paths stop at
    260 characters unless long paths are enabled, and past that the
    download fails outright, so the title is trimmed to fit the configured
    folders with room left for yt-dlp's temporary suffixes
    ('.fdash-1107942951774716a.m4a.part').
    """
    if not transcript_enabled(settings) or not outtmpl.endswith('.%(ext)s'):
        return outtmpl
    base = max(len(settings.get('download_path') or ''),
               len(settings.get('temp_path') or ''))
    title_bytes = max(16, min(80, (250 - base - 45) // 2))
    stem = outtmpl[:-len('.%(ext)s')].replace(
        '%(title)s', f'%(title).{title_bytes}B')
    return f'{stem}/{stem}.%(ext)s'


def srt_to_text(srt):
    """Plain-text transcript from SRT: cue numbers, timestamps and inline
    tags dropped, consecutive duplicate lines (rolling captions) collapsed."""
    out = []
    prev = None
    for block in re.split(r'\r?\n\s*\r?\n', srt.strip()):
        for line in block.splitlines():
            line = re.sub(r'<[^>]+>', '', line).strip()
            if not line or line.isdigit() or '-->' in line:
                continue
            if line != prev:
                out.append(line)
                prev = line
    return '\n'.join(out) + '\n'


def _srt_timestamp(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def segments_to_srt(segments):
    """SRT text from (start, end, text) tuples."""
    cues = []
    for i, (start, end, text) in enumerate(segments, 1):
        cues.append(f'{i}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n'
                    f'{text}\n')
    return '\n'.join(cues)


_whisper_model = None
_whisper_lock = threading.Lock()


def transcribe_media(path):
    """Speech-to-text for a downloaded audio/video file with faster-whisper.

    Returns ([(start, end, text), ...], language). Transcriptions run one at
    a time: the model is shared, and each run already uses every CPU core.
    """
    global _whisper_model
    # Imported lazily: loading ctranslate2/onnxruntime costs startup time
    # and memory that downloads without transcripts don't need
    from faster_whisper import WhisperModel
    with _whisper_lock:
        if _whisper_model is None:
            _whisper_model = WhisperModel(
                WHISPER_MODEL, device='cpu', compute_type='int8',
                download_root=os.path.join(get_config_dir(), 'models'))
        segments, info = _whisper_model.transcribe(
            path, vad_filter=True, beam_size=5)
        result = [(seg.start, seg.end, seg.text.strip())
                  for seg in segments if seg.text.strip()]
    return result, info.language


class TranscriptPostProcessor(PostProcessor):
    """Writes the transcript files Settings asked for, after the download
    reaches its final folder.

    Uses the site's human-made subtitles when yt-dlp fetched some;
    otherwise transcribes the downloaded audio locally with Whisper.
    Transcription is best-effort: a failure is logged and reported in the
    status bar but never fails the download itself.
    """

    def __init__(self, want_txt, want_srt, status=None):
        super().__init__(None)
        self._want_txt = want_txt
        self._want_srt = want_srt
        self._status = status or (lambda message: None)

    def _write(self, path, text):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        self.to_screen(f'Transcript saved: {os.path.basename(path)}')

    def run(self, info):
        if self._use_site_subtitles(info):
            return [], info
        media = info.get('filepath')
        if not media or not os.path.exists(media):
            return [], info
        self._status('Transcribing audio...')
        try:
            segments, language = transcribe_media(media)
        except Exception as e:
            logger.error('Transcription failed for %s', media, exc_info=True)
            self._status(f'Download complete - transcription failed: {e}')
            return [], info
        if not segments:
            self.to_screen('No speech detected - transcript not saved')
            self._status('Download complete - no speech to transcribe')
            return [], info
        stem = os.path.splitext(media)[0]
        if self._want_srt:
            self._write(stem + '.srt', segments_to_srt(segments))
        if self._want_txt:
            self._write(stem + '.txt',
                        '\n'.join(text for _, _, text in segments) + '\n')
        logger.info('Transcribed %s (%s, %d segments)', media, language,
                    len(segments))
        return [], info

    def _use_site_subtitles(self, info):
        """Turn subtitle files yt-dlp downloaded into the requested
        formats. False when there were none, so the caller transcribes."""
        wrote_any = False
        for lang, sub in sorted((info.get('requested_subtitles') or {}).items()):
            path = (sub or {}).get('filepath')
            if not path or not os.path.exists(path):
                continue
            if not path.lower().endswith('.srt'):
                os.remove(path)  # ffmpeg couldn't convert it; unusable
                continue
            if self._want_txt:
                with open(path, encoding='utf-8', errors='replace') as f:
                    self._write(path[:-4] + '.txt', srt_to_text(f.read()))
            if not self._want_srt:
                os.remove(path)
            wrote_any = True
        return wrote_any


def transcript_post_processors(settings, status=None):
    """Post-processors run_with_cookie_fallback should attach ([] when off).

    status, if given, is called with short progress messages for the UI.
    """
    if not transcript_enabled(settings):
        return []
    return [TranscriptPostProcessor(bool(settings.get('transcript_txt')),
                                    bool(settings.get('transcript_srt')),
                                    status)]


def run_download_with_transcripts(ydl_opts, settings, do_download, status=None):
    """Download with transcript handling attached. Transcripts are
    best-effort: if fetching the site's subtitles sinks the download (rate
    limits and the like), retry once without them - the video still
    arrives and its audio is transcribed instead."""
    post_processors = transcript_post_processors(settings, status)
    try:
        return run_with_cookie_fallback(
            ydl_opts, do_download, post_processors=post_processors)
    except Exception as e:
        if not transcript_enabled(settings) or 'subtitle' not in str(e).lower():
            raise
        logger.warning('Subtitle download failed (%s); retrying without '
                       'site subtitles', e)
        opts = {k: v for k, v in ydl_opts.items()
                if k not in ('writesubtitles', 'subtitleslangs')}
        pps = [pp for pp in opts.get('postprocessors', [])
               if pp.get('key') != 'FFmpegSubtitlesConvertor']
        opts.pop('postprocessors', None)
        if pps:
            opts['postprocessors'] = pps
        return run_with_cookie_fallback(
            opts, do_download, post_processors=post_processors)


def run_with_cookie_fallback(ydl_opts, action, post_processors=()):
    """Run action(ydl); if loading browser cookies fails, retry without them.

    Reading a browser's cookie DB fails routinely (the browser is running
    and locks the file, or uses cookie encryption yt-dlp can't decrypt -
    e.g. Chrome/Edge app-bound encryption fails with "Failed to decrypt
    with DPAPI", yt-dlp issue 10927). Public posts don't need the login
    anyway, so a broken cookie source must not take down every
    Instagram/Threads download.

    If the logged-out retry then fails too, that failure may well be
    *because* the login was missing (private/restricted posts), so the
    error is annotated with what happened to the cookies.
    """
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for pp in post_processors:
                ydl.add_post_processor(pp, when='after_move')
            return action(ydl)
    except Exception as e:
        err = str(e).lower()
        # Only genuine cookie-*loading* failures qualify. yt-dlp wraps them
        # in a DownloadError whose text names the cookie database / the
        # decryption step; extractor errors that merely *mention* cookies
        # ("...configure that browser for cookies") must not match, or a
        # perfectly good login gets blamed for an unrelated failure.
        if ('cookiesfrombrowser' not in ydl_opts
                or not any(s in err for s in (
                    'failed to load cookies', 'cookies database',
                    'cookie database', 'dpapi', 'decrypt', 'keyring'))):
            raise
        browser = ydl_opts['cookiesfrombrowser'][0]
        logger.warning("Browser cookies unavailable (%s); retrying without "
                       "cookies", e)
        opts = {k: v for k, v in ydl_opts.items() if k != 'cookiesfrombrowser'}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                for pp in post_processors:
                    ydl.add_post_processor(pp, when='after_move')
                return action(ydl)
        except Exception as retry_error:
            raise Exception(
                f"{retry_error}\n\nNote: your {browser} login was NOT used - "
                f"its cookies could not be read (usually because {browser} is "
                f"running and locking them). If this content needs your "
                f"login, close {browser} completely and try again."
            ) from retry_error


class FormatSelector(tk.Toplevel):
    def __init__(self, parent, formats, merge_audio=False):
        self.selected_format = None
        self.merge_audio = merge_audio
        try:
            super().__init__(parent)
            self.title("Select Format")

            if not formats:
                self.geometry("600x120")
                self.resizable(False, False)
                ttk.Label(self, text="No suitable formats found for this download type.").pack(pady=20)
                ttk.Button(self, text="Close", command=self.destroy).pack(pady=10)
                return

            # Bottom bar packed first so it keeps its space when resizing
            btn_frame = ttk.Frame(self)
            btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 8))
            ttk.Button(btn_frame, text="Download",
                       command=self.select_format).pack()

            main_frame = ttk.Frame(self)
            main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.tree = ttk.Treeview(main_frame, columns=("format_id", "ext", "resolution", "filesize", "tbr", "acodec"), show="headings")

            self.tree.heading("format_id", text="ID")
            self.tree.heading("ext", text="Format")
            self.tree.heading("resolution", text="Resolution")
            self.tree.heading("filesize", text="File Size")
            self.tree.heading("tbr", text="Bitrate")
            self.tree.heading("acodec", text="Audio")

            self.tree.column("format_id", width=40)
            self.tree.column("ext", width=50)
            self.tree.column("resolution", width=100)
            self.tree.column("filesize", width=80)
            self.tree.column("tbr", width=70)
            self.tree.column("acodec", width=80)

            scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.tree.yview)
            self.tree.configure(yscrollcommand=scrollbar.set)

            self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            for f in formats:
                try:
                    filesize = f.get('filesize', None)
                    if filesize and filesize > 0:
                        filesize = f"{filesize/1024/1024:.1f} MB"
                    else:
                        filesize = "N/A"

                    resolution = f.get('resolution', 'N/A')
                    if resolution == 'audio only':
                        resolution = f"Audio ({f.get('abr', 'N/A')}kbps)"
                    elif f.get('height'):
                        resolution = f"{f.get('height')}p"

                    acodec = f.get('acodec', 'N/A')
                    if self.merge_audio and acodec in (None, 'none'):
                        # Video-only stream; best audio is merged at download
                        acodec = 'auto (best)'

                    self.tree.insert("", tk.END, values=(
                        str(f.get('format_id', 'N/A')),
                        str(f.get('ext', 'N/A')),
                        str(resolution),
                        str(filesize),
                        f"{str(f.get('tbr', 'N/A'))} kbps" if f.get('tbr') else 'N/A',
                        str(acodec)
                    ))
                except Exception as e:
                    logger.error(f"Error processing format: {str(e)}")
                    continue

            self.tree.bind('<Double-1>', lambda e: self.select_format())

            # Resizable, but no taller than the end of the list
            rowheight = 20
            try:
                rh = ttk.Style(self).lookup("Treeview", "rowheight")
                rowheight = int(rh) if rh else 20
            except (ValueError, tk.TclError):
                pass
            chrome = 95  # heading row + button bar + padding
            full_height = len(formats) * rowheight + chrome
            self.resizable(True, True)
            self.minsize(460, min(200, full_height))
            self.maxsize(1200, max(200, full_height))
            self.geometry(f"600x{min(300, full_height)}")

            self.grab_set()
        except Exception as e:
            logger.error(f"Error in FormatSelector initialization: {str(e)}", exc_info=True)
            messagebox.showerror("Error", f"Failed to initialize format selector: {str(e)}")
            self.destroy()

    def select_format(self):
        selection = self.tree.selection()
        if not selection:
            self.bell()  # Download pressed with nothing selected
            return
        values = self.tree.item(selection[0])['values']
        self.selected_format = str(values[0])
        self.destroy()

class DownstreamApp:
    def __init__(self, root):
        try:
            logger.debug("Initializing Downstream GUI")
            self.root = root
            self.root.title(f"{APP_NAME} v{APP_VERSION}")

            # Initialize variables
            self.format_cache = {}
            self.setup_gui()

            # Size the window to exactly fit its content (no clipped fields,
            # no dead space) and keep it fixed
            self.root.update_idletasks()
            self.root.geometry("")
            self.root.resizable(False, False)
            logger.debug("GUI initialization completed")
        except Exception as e:
            logger.error(f"Failed to initialize GUI: {str(e)}", exc_info=True)
            messagebox.showerror("Error", f"Failed to initialize application: {str(e)}")
            raise

    def setup_gui(self):
        try:
            logger.debug("Setting up GUI components")
            # Settings are loaded first so widgets can pick up saved defaults
            self.base_path = get_base_path()
            self.settings = self.load_settings()
            self.set_window_icon()

            # Configure button style
            self.style = ttk.Style()
            self.style.configure("TButton", padding=5, width=10)
            self.style.configure("TLabel", padding=3)

            # Main frame setup - the frame's own padding is the window
            # margin; no extra outer padding on top of it
            self.main_frame = ttk.Frame(self.root, padding="10")
            self.main_frame.pack(fill=tk.BOTH, expand=True)

            # Create and pack all GUI elements
            self.create_url_frame()
            self.create_download_options()
            self.create_playlist_options()
            self.create_progress_frame()

            # Settings lives in the window's native title-bar menu (the icon
            # at the top left); deferred until the window exists on screen
            self.root.after(200, self.add_settings_to_system_menu)
            # A port bind failure surfaces within Flask's first moments;
            # checking a few seconds in is late enough to catch it
            self.root.after(3000, self._warn_if_port_conflict)
            logger.debug("GUI setup completed successfully")
        except Exception as e:
            logger.error(f"Error in setup_gui: {str(e)}", exc_info=True)
            raise

    def set_window_icon(self):
        # icon.ico sits next to the script in development; PyInstaller unpacks
        # bundled data files into sys._MEIPASS
        candidates = [os.path.join(self.base_path, "icon.ico")]
        if getattr(sys, 'frozen', False):
            candidates.insert(0, os.path.join(sys._MEIPASS, "icon.ico"))
        for icon_path in candidates:
            if os.path.exists(icon_path):
                try:
                    # default= applies to every window (Settings, History, ...)
                    self.root.iconbitmap(default=icon_path)
                except tk.TclError:
                    logger.warning(f"Could not apply window icon {icon_path}")
                return

    # App-defined WM_SYSCOMMAND ids must be < 0xF000
    SYSMENU_SETTINGS_ID = 0x1000
    SYSMENU_HISTORY_ID = 0x1010
    SYSMENU_OPENFOLDER_ID = 0x1020

    def add_settings_to_system_menu(self):
        """Append Settings... and Download History... to the native
        title-bar (system) menu.

        Windows-only: the system menu belongs to the OS window, so this uses
        the Win32 API and subclasses the window procedure to receive the
        clicks. On other platforms (or if the hook fails) a plain menubar is
        used instead so the dialogs stay reachable.
        """
        if os.name != 'nt':
            self._fallback_menubar()
            return
        # Never install twice: rebinding _wnd_proc_ref would garbage-collect
        # the callback Windows is still calling, crashing the process
        if getattr(self, '_wnd_proc_ref', None) is not None:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            LRESULT = ctypes.c_longlong
            GWLP_WNDPROC = -4
            WM_SYSCOMMAND = 0x0112
            MF_SEPARATOR, MF_STRING = 0x800, 0x0

            # The OS-level window is the parent of Tk's inner window
            hwnd = user32.GetParent(self.root.winfo_id())
            if not hwnd:
                return

            user32.GetSystemMenu.restype = ctypes.c_void_p
            user32.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
            user32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_size_t, wintypes.LPCWSTR]
            self._sysmenu_actions = {
                self.SYSMENU_SETTINGS_ID: ("Settings...", self.show_settings),
                self.SYSMENU_HISTORY_ID: ("Download History...", self.show_history),
                self.SYSMENU_OPENFOLDER_ID: ("Open Download Folder",
                                             self.open_download_folder),
            }
            sysmenu = user32.GetSystemMenu(hwnd, False)
            user32.AppendMenuW(sysmenu, MF_SEPARATOR, 0, None)
            for item_id, (label, _) in self._sysmenu_actions.items():
                user32.AppendMenuW(sysmenu, MF_STRING, item_id, label)

            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                               ctypes.c_uint, wintypes.WPARAM,
                                               wintypes.LPARAM]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                                 ctypes.c_void_p]

            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                                         wintypes.WPARAM, wintypes.LPARAM)

            # The callback must not call into Tcl/Tk: it runs re-entrantly
            # inside Tk's own message dispatch, which crashes the
            # interpreter. It only queues the id; the poller below reacts.
            self._sysmenu_clicked_ids = []

            def wnd_proc(h, msg, wparam, lparam):
                if msg == WM_SYSCOMMAND and int(wparam) in self._sysmenu_actions:
                    self._sysmenu_clicked_ids.append(int(wparam))
                    return 0
                return user32.CallWindowProcW(self._old_wnd_proc, h, msg, wparam, lparam)

            # Keep a reference on self: if the callback is garbage collected
            # while installed, the process crashes
            self._wnd_proc_ref = WNDPROC(wnd_proc)
            self._old_wnd_proc = user32.SetWindowLongPtrW(
                hwnd, GWLP_WNDPROC,
                ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p))

            def poll_sysmenu():
                while self._sysmenu_clicked_ids:
                    item_id = self._sysmenu_clicked_ids.pop(0)
                    self._sysmenu_actions[item_id][1]()
                self.root.after(150, poll_sysmenu)
            poll_sysmenu()
            logger.debug("Settings and History added to the system menu")
        except Exception:
            logger.warning("Could not modify the system menu; falling back "
                           "to a menubar", exc_info=True)
            self._fallback_menubar()

    def _fallback_menubar(self):
        menubar = tk.Menu(self.root, tearoff=0)
        app_menu = tk.Menu(menubar, tearoff=0)
        app_menu.add_command(label="Settings...", command=self.show_settings)
        app_menu.add_command(label="Download History...", command=self.show_history)
        app_menu.add_command(label="Open Download Folder",
                             command=self.open_download_folder)
        menubar.add_cascade(label="Menu", menu=app_menu)
        self.root.config(menu=menubar)

    def create_url_frame(self):
        # URL Frame with validation
        url_frame = ttk.Frame(self.main_frame)
        url_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(url_frame, text="Video URL:").pack(side=tk.LEFT)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        self.add_context_menu(self.url_entry)
        self.check_clipboard()

        # Enter in the URL field starts the format search
        self.url_entry.bind('<Return>', lambda e: self.prepare_download())

        # Auto-fetch formats when a complete video URL lands in the field
        # (paste or typing). The trace is installed after check_clipboard so
        # a URL auto-filled from the clipboard at startup doesn't pop a
        # dialog before the user has done anything.
        self._auto_fetch_job = None
        self._last_fetched_url = self.url_var.get().strip()
        self.url_var.trace_add('write', self._schedule_auto_fetch)

    # Matches only complete video/shorts/playlist/reel/post URLs so
    # auto-fetch doesn't fire on a half-typed address
    AUTO_FETCH_RE = re.compile(
        r'(youtube\.com/watch\?\S*v=[\w-]{11}'
        r'|youtu\.be/[\w-]{11}'
        r'|youtube\.com/shorts/[\w-]{11}'
        r'|youtube\.com/playlist\?\S*list=[\w-]+'
        # instagram.com/reel/CODE, /reels/, /p/, /tv/ - with or without a
        # leading /username/ path segment (share links include one)
        r'|instagram\.com/(?:[\w.]+/)?(?:reels?|p|tv)/[\w-]+'
        # threads.net|.com/@username/post/CODE
        r'|threads\.(?:net|com)/@?[\w.]+/post/[\w-]+'
        # threads.net|.com/share/CODE (resolved by the Threads plugin)
        r'|threads\.(?:net|com)/share/[\w-]+'
        # tiktok.com/@username/video/ID (the @username may be empty)
        r'|tiktok\.com/@[\w.-]*/video/\d+'
        # short links: vm.tiktok.com/CODE, vt.tiktok.com/CODE, tiktok.com/t/CODE
        r'|(?:vm|vt)\.tiktok\.com/[\w-]+'
        r'|tiktok\.com/t/[\w-]+)',
        re.IGNORECASE
    )

    def _schedule_auto_fetch(self, *_):
        if self._auto_fetch_job is not None:
            self.root.after_cancel(self._auto_fetch_job)
        self._auto_fetch_job = self.root.after(700, self._auto_fetch)

    def _auto_fetch(self):
        self._auto_fetch_job = None
        url = self.url_var.get().strip()
        if url and url != self._last_fetched_url and self.AUTO_FETCH_RE.search(url):
            self.prepare_download()

    def paste_url(self):
        """Paste the clipboard URL and search for formats immediately."""
        try:
            text = (pyperclip.paste() or "").strip()
        except Exception:
            text = ""
        if not text:
            messagebox.showerror("Error", "Clipboard is empty")
            return
        self.url_var.set(text)
        # Skip the debounce the paste just scheduled - search right now
        if self._auto_fetch_job is not None:
            self.root.after_cancel(self._auto_fetch_job)
            self._auto_fetch_job = None
        self.prepare_download()

    def create_download_options(self):
        # The Download button sits to the right of the option box, outside it
        options_row = ttk.Frame(self.main_frame)
        options_row.pack(fill=tk.X, pady=(0, 10))

        download_frame = ttk.LabelFrame(options_row, text="Download Options", padding="10")
        download_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        saved_type = self.settings.get("default_download_type", "video+audio")
        if saved_type not in ("video+audio", "video-only", "audio-only"):
            saved_type = "video+audio"
        self.download_type = tk.StringVar(value=saved_type)
        ttk.Radiobutton(download_frame, text="Video + Audio", value="video+audio",
                       variable=self.download_type).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(download_frame, text="Video Only", value="video-only",
                       variable=self.download_type).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(download_frame, text="Audio Only", value="audio-only",
                       variable=self.download_type).pack(side=tk.LEFT, padx=10)

        # Paste stacked on Download beside the box. The buttons are placed
        # with exact pixel geometry (see _align_action_buttons) so their
        # outer edges meet the box's drawn outline, which starts half a
        # label-line below the widget's top edge
        self.download_frame = download_frame
        self._btn_spacer = ttk.Frame(options_row, width=100)
        self._btn_spacer.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))

        self.paste_btn = tk.Button(self._btn_spacer, text="Paste", width=12,
                                   command=self.paste_url, relief=tk.RAISED)
        self.download_btn = tk.Button(self._btn_spacer, text="Download", width=12,
                                      command=self.prepare_download, relief=tk.RAISED)
        self._btn_spacer.configure(
            width=max(self.paste_btn.winfo_reqwidth(),
                      self.download_btn.winfo_reqwidth()))
        # Align once the box is actually on screen with real geometry;
        # idle-time callbacks can fire before Tk has computed any sizes
        self.download_frame.bind('<Map>', lambda e: self._align_action_buttons())

    def _align_action_buttons(self):
        """Pin Paste/Download to the drawn outline of the options box."""
        offset = tkfont.nametofont("TkDefaultFont").metrics("linespace") // 2
        box_height = self.download_frame.winfo_height()
        if box_height < 20:  # geometry not computed yet - try again shortly
            self.root.after(50, self._align_action_buttons)
            return
        usable = box_height - offset
        gap = 3
        top_h = (usable - gap) // 2
        bottom_h = usable - gap - top_h   # bottom edge lands exactly on the box
        width = self._btn_spacer.winfo_reqwidth()
        self.paste_btn.place(x=0, y=offset, width=width, height=top_h)
        self.download_btn.place(x=0, y=offset + top_h + gap, width=width, height=bottom_h)

    def create_playlist_options(self):
        # Add playlist options frame
        playlist_frame = ttk.LabelFrame(self.main_frame, text="Playlist Options", padding="10")
        playlist_frame.pack(fill=tk.X, pady=(0, 10))

        self.is_playlist = tk.BooleanVar(value=False)
        self.playlist_info = None

        # Playlist checkboxes
        self.download_all = tk.BooleanVar(value=True)
        self.reverse_playlist = tk.BooleanVar(value=False)

        ttk.Checkbutton(playlist_frame, text="Download All Videos", 
                       variable=self.download_all).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(playlist_frame, text="Reverse Order", 
                       variable=self.reverse_playlist).pack(side=tk.LEFT, padx=5)

        # Add playlist range entries
        range_frame = ttk.Frame(playlist_frame)
        range_frame.pack(side=tk.LEFT, padx=5)

        self.start_index = tk.StringVar(value="")
        self.end_index = tk.StringVar(value="")

        ttk.Label(range_frame, text="Start:").pack(side=tk.LEFT)
        ttk.Entry(range_frame, textvariable=self.start_index, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(range_frame, text="End:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(range_frame, textvariable=self.end_index, width=5).pack(side=tk.LEFT)

    def create_progress_frame(self):
        progress_frame = ttk.Frame(self.main_frame)
        progress_frame.pack(fill=tk.X, pady=(5, 0))  

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, 
                                          maximum=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(progress_frame, textvariable=self.status_var)
        self.status_label.pack(anchor=tk.W, pady=(2, 0))

    def _warn_if_port_conflict(self):
        """Tell the user when the extension API port couldn't be bound.

        Only overwrite an idle status bar - never an in-flight download's.
        """
        if api_port_conflict.is_set() and self.status_var.get() == "Ready":
            self.status_var.set(
                "Extension port busy - is another copy of Downstream running?")
            self.flash_status()

    def flash_status(self, flashes=3, interval=250):
        """Flash the status label red the given number of times, then
        restore the theme's default text color."""
        def step(i):
            if i >= flashes * 2:
                self.status_label.configure(foreground='')
                return
            self.status_label.configure(foreground='red' if i % 2 == 0 else 'black')
            self.root.after(interval, step, i + 1)
        step(0)

    def add_context_menu(self, entry):
        menu = tk.Menu(entry, tearoff=0)
        menu.add_command(label="Cut", command=lambda: entry.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: entry.event_generate("<<Paste>>"))

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        entry.bind("<Button-3>", show_menu)  
        entry.bind("<Control-Button-1>", show_menu)  

    def prepare_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter a video URL")
            return

        if not SUPPORTED_URL_RE.search(url):
            messagebox.showerror(
                "Error",
                "Unsupported URL.\n\nSupported sites: YouTube, Instagram, Threads, TikTok")
            return

        # Remember what we fetched so the auto-fetch trace doesn't fire a
        # second search for the same URL
        self._last_fetched_url = url

        # Clean up playlist URL if present (YouTube only)
        if 'youtube' in url and 'playlist' in url:
            try:
                # Extract the playlist ID
                if 'list=' in url:
                    playlist_id = url.split('list=')[1].split('&')[0]
                    url = f"https://youtube.com/playlist?list={playlist_id}"
            except Exception as e:
                logger.error(f"Error formatting playlist URL: {str(e)}")

        # Auto download: no format fetch, no picker - yt-dlp resolves the
        # configured quality preset itself at download time
        if self.settings.get("auto_download"):
            quality = self.settings.get("auto_download_quality", "best")
            self.status_var.set(f"Auto-downloading ({quality} quality)...")
            self.start_download(
                url, auto_quality=quality,
                is_playlist='youtube' in url and 'playlist' in url)
            return

        if url in self.format_cache:
            self.show_format_selector(self.format_cache[url], url)
        else:
            self.status_var.set("Fetching available formats...")
            threading.Thread(target=self.fetch_formats, args=(url,), daemon=True).start()

    def fetch_formats(self, url):
        try:
            info = run_with_cookie_fallback(
                site_ydl_opts(url, self.settings),
                lambda ydl: ydl.extract_info(url, download=False))

            # Check if URL is a playlist
            is_playlist = 'entries' in info
            self.root.after(0, self.is_playlist.set, is_playlist)
            if is_playlist:
                self.playlist_info = info
                # For playlists, use the first video's formats
                formats = info['entries'][0]['formats'] if info['entries'] else []

                # Update status with playlist info
                playlist_count = len(info['entries'])
                self.root.after(0, self.status_var.set, f"Playlist detected: {playlist_count} videos")
            else:
                self.playlist_info = None
                formats = info['formats']

            # Filter and sort formats based on download type
            download_type = self.download_type.get()
            all_formats = formats
            if download_type == "video+audio":
                # List every video format, not just files that already
                # contain audio - YouTube only serves those at low
                # resolution. Audio is merged in at download time.
                formats = [f for f in formats if
                    f.get('vcodec') != 'none' and
                    f.get('ext') in ['mp4', 'mkv', 'webm']
                ]
                if not formats:
                    # Other sites (Instagram/Threads) may use containers
                    # not in the list above - accept any video stream
                    formats = [f for f in all_formats if f.get('vcodec') != 'none']
                formats.sort(key=lambda x: (
                    int(x.get('height', 0) or 0),
                    float(x.get('tbr', 0) or 0)
                ), reverse=True)
            elif download_type == "video-only":
                formats = [f for f in formats if
                    f.get('vcodec') != 'none' and
                    f.get('acodec') == 'none' and
                    f.get('ext') in ['mp4', 'webm']
                ]
                formats.sort(key=lambda x: int(x.get('height', 0) or 0), reverse=True)
            else:  # audio-only
                formats = [f for f in formats if
                    f.get('acodec') != 'none' and
                    f.get('vcodec') == 'none' and
                    f.get('ext') in ['m4a', 'mp3', 'opus', 'webm']
                ]
                formats.sort(key=lambda x: float(x.get('abr', 0) or 0), reverse=True)
                if not formats:
                    # Instagram/Threads usually have no separate audio
                    # streams; offer combined files (acodec may be
                    # unknown/None there) - the MP3 extraction step
                    # strips the video at download time
                    formats = [f for f in all_formats if
                               f.get('acodec') != 'none']
                    formats.sort(key=lambda x: float(
                        x.get('abr', 0) or x.get('tbr', 0) or 0), reverse=True)

            # Cache the formats
            self.format_cache[url] = formats

            # Show format selector
            self.root.after(0, lambda: self.show_format_selector(formats, url))

        except Exception as e:
            error_msg = str(e)
            crosspost = CROSSPOST_ERROR_RE.search(error_msg)
            if crosspost:
                self.root.after(0, self.show_crosspost_warning,
                                crosspost.group('source'), crosspost.group('url'))
            else:
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to fetch formats: {error_msg}"))
                self.root.after(0, self.status_var.set, "Ready")

    def show_crosspost_warning(self, source, source_url):
        """The Threads post has no video of its own - it's cross-posted from
        another site. When the app supports that site, download from there
        instead (asking first only if the "confirm cross-posts" setting is
        on); otherwise say where the video actually lives."""
        self.status_var.set(f"Cross-post - video is on {source}")
        if SUPPORTED_URL_RE.search(source_url):
            if not self.settings.get("confirm_crosspost") or messagebox.askyesno(
                    "Cross-posted video",
                    "This Threads post is a cross-post - the video is "
                    f"actually hosted on {source}:\n\n{source_url}\n\n"
                    f"Download it from {source} now?"):
                self.url_var.set(source_url)
                self.prepare_download()
        else:
            messagebox.showwarning(
                "Cross-posted video",
                "This Threads post is a cross-post - the video is actually "
                f"hosted on {source}. Download it from the source "
                f"instead:\n\n{source_url}")

    def show_format_selector(self, formats, url):
        selector = FormatSelector(self.root, formats,
                                  merge_audio=self.download_type.get() == "video+audio")
        self.root.wait_window(selector)
        if selector.selected_format:
            self.start_download(url, selector.selected_format)
        else:
            self.status_var.set("Download cancelled")

    def start_download(self, url, format_id=None, auto_quality=None, is_playlist=None):
        """Download url.

        Either format_id (a concrete format chosen in the picker) or
        auto_quality ('best'/'medium'/'low', resolved by yt-dlp via
        QUALITY_FORMATS) must be given. is_playlist=None means "use the
        state discovered by fetch_formats"; auto downloads skip that fetch,
        so they pass an explicit URL-based value instead.
        """
        format_id = str(format_id) if format_id is not None else None
        download_type = self.download_type.get()
        if is_playlist is None:
            is_playlist = self.is_playlist.get() and self.playlist_info

        # Only prefix filenames with the playlist index for playlist downloads;
        # for single videos %(playlist_index)s expands to "NA"
        outtmpl = '%(playlist_index)s-%(title)s.%(ext)s' if is_playlist else '%(title)s.%(ext)s'
        outtmpl = transcript_outtmpl(outtmpl, self.settings)

        ydl_opts = {
            **site_ydl_opts(url, self.settings),
            **transcript_ydl_opts(self.settings),
            'outtmpl': outtmpl,
            'paths': build_download_paths(self.settings),
            'progress_hooks': [self.download_progress_hook],
            # Fetch DASH/HLS fragments in parallel - large downloads are
            # substantially faster
            'concurrent_fragment_downloads': 4
        }

        if not is_playlist:
            # A watch URL carrying a &list= param must not pull the playlist
            ydl_opts['noplaylist'] = True

        if is_playlist:
            # Skip broken entries and keep downloading the rest. Only for
            # playlists: on a single video this would swallow the real error
            # and leave nothing but a generic non-zero exit code
            ydl_opts['ignoreerrors'] = True
            # Add playlist-specific options
            if not self.download_all.get():
                try:
                    start = int(self.start_index.get()) if self.start_index.get() else None
                    end = int(self.end_index.get()) if self.end_index.get() else None

                    if start is not None and start < 1:
                        raise ValueError("Start index must be 1 or greater")
                    if end is not None and start is not None and end < start:
                        raise ValueError("End index must be greater than start index")

                    if start is not None:
                        ydl_opts['playliststart'] = start
                    if end is not None:
                        ydl_opts['playlistend'] = end
                except ValueError as e:
                    messagebox.showerror("Error", str(e))
                    return

            ydl_opts['noplaylist'] = False
            ydl_opts['playlist_reverse'] = self.reverse_playlist.get()

        if auto_quality is not None:
            type_presets = QUALITY_FORMATS[download_type]
            format_expr, format_sort = type_presets.get(
                auto_quality, type_presets['best'])
            ydl_opts['format'] = format_expr
            if format_sort:
                ydl_opts['format_sort'] = format_sort
            if download_type == "video+audio":
                ydl_opts['merge_output_format'] = self.settings['format']
            elif download_type == "audio-only":
                ydl_opts.setdefault('postprocessors', []).append({
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                })
        elif download_type == "video+audio":
            # Merge the chosen video with the best audio; fall back to the
            # bare format (progressive files already contain audio)
            ydl_opts.update({
                'format': f'{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/{format_id}/best',
                'merge_output_format': self.settings['format']
            })
        elif download_type == "video-only":
            ydl_opts['format'] = format_id
        else:
            ydl_opts['format'] = format_id
            ydl_opts.setdefault('postprocessors', []).append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            })

        playlist_suffix = " (Playlist)" if is_playlist else ""
        format_desc = f'auto-{auto_quality}' if auto_quality else format_id

        def download_thread():
            # Transcript problems end in a "Download complete - ..." status;
            # keep that on screen instead of the generic completion message
            transcript_notes = []

            def report(msg):
                if msg.startswith("Download complete - "):
                    transcript_notes.append(msg)
                self.root.after(0, self.status_var.set, msg)

            try:
                self.root.after(0, self.status_var.set, "Downloading..." + playlist_suffix)
                error_code = run_download_with_transcripts(
                    ydl_opts, self.settings, lambda ydl: ydl.download([url]),
                    status=report)
                if error_code != 0:
                    # Only reachable with ignoreerrors (playlists): some
                    # entries failed but the rest were downloaded
                    raise Exception("Some playlist entries could not be downloaded "
                                    "(see download history for details)")

                self.root.after(0, self.status_var.set,
                                transcript_notes[-1] if transcript_notes
                                else "Download completed!")
                self.root.after(0, self.flash_status)
                self.root.after(0, self.progress_var.set, 100)
                self.log_download(url, f"{download_type}:{format_desc}", "Success" + playlist_suffix)
            except Exception as e:
                # yt-dlp errors read "ERROR: <reason>"; show just the reason
                error_msg = re.sub(r'^\s*ERROR:\s*', '', str(e))
                self.log_download(url, f"{download_type}:{format_desc}", f"Failed: {error_msg}")
                crosspost = CROSSPOST_ERROR_RE.search(error_msg)
                if crosspost:
                    self.root.after(0, self.show_crosspost_warning,
                                    crosspost.group('source'), crosspost.group('url'))
                else:
                    self.root.after(0, self.status_var.set, "Download failed!")
                    self.root.after(0, lambda: messagebox.showerror("Error", f"Download failed: {error_msg}"))
            finally:
                self.root.after(0, self.progress_var.set, 0)

        threading.Thread(target=download_thread, daemon=True).start()

    def download_progress_hook(self, d):
        if d['status'] == 'downloading':
            try:
                total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    progress = (downloaded / total) * 100
                    self.root.after(0, self.progress_var.set, progress)
            except Exception:
                pass

    def load_settings(self):
        return load_settings(self.base_path)

    def save_settings(self, source_var, dest_var, type_var, format_var,
                      cookies_var, auto_var, quality_var, crosspost_var,
                      txt_var, srt_var, settings_window):
        source = source_var.get().strip()
        dest = dest_var.get().strip()
        if not os.path.isdir(dest):
            messagebox.showerror("Error", f"Destination folder does not exist:\n{dest}")
            return
        if source and not os.path.isdir(source):
            messagebox.showerror("Error", f"Source folder does not exist:\n{source}")
            return

        self.settings["temp_path"] = source
        self.settings["download_path"] = dest
        self.settings["default_download_type"] = type_var.get()
        self.settings["format"] = format_var.get()
        self.settings["cookies_browser"] = cookies_var.get().strip()
        self.settings["auto_download"] = bool(auto_var.get())
        self.settings["auto_download_quality"] = quality_var.get()
        self.settings["confirm_crosspost"] = bool(crosspost_var.get())
        self.settings["transcript_txt"] = bool(txt_var.get())
        self.settings["transcript_srt"] = bool(srt_var.get())
        self.save_settings_file()
        # Apply the new default to the main window immediately
        self.download_type.set(type_var.get())
        settings_window.destroy()
        self.status_var.set("Settings saved")

    def save_settings_file(self):
        settings_path = os.path.join(get_config_dir(), "settings.json")
        with open(settings_path, "w") as f:
            json.dump(self.settings, f)

    def check_clipboard(self):
        try:
            clipboard_content = pyperclip.paste()
            if SUPPORTED_URL_RE.search(clipboard_content or ""):
                self.url_var.set(clipboard_content)
        except Exception:
            pass

    def show_settings(self):
        settings_window = tk.Toplevel(self.root)
        settings_window.title("Settings")
        settings_window.resizable(False, False)

        settings_frame = ttk.Frame(settings_window, padding="10")
        settings_frame.pack(fill=tk.BOTH, expand=True)

        def folder_row(label, value):
            row = ttk.Frame(settings_frame)
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=label, width=22).pack(side=tk.LEFT)
            var = tk.StringVar(value=value)
            entry = ttk.Entry(row, textvariable=var)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            self.add_context_menu(entry)
            ttk.Button(row, text="Browse",
                       command=lambda: self.browse_path(var)).pack(side=tk.RIGHT)
            return var

        # Source folder holds in-progress downloads; finished files are moved
        # to the destination. Leave source empty to download directly.
        source_var = folder_row("Source (in-progress):", self.settings.get("temp_path", ""))
        dest_var = folder_row("Destination (finished):", self.settings["download_path"])

        type_frame = ttk.Frame(settings_frame)
        type_frame.pack(fill=tk.X, pady=3)
        ttk.Label(type_frame, text="Default download:", width=22).pack(side=tk.LEFT)
        type_var = tk.StringVar(value=self.download_type.get())
        for text, value in (("Video + Audio", "video+audio"),
                            ("Video Only", "video-only"),
                            ("Audio Only", "audio-only")):
            ttk.Radiobutton(type_frame, text=text, value=value,
                            variable=type_var).pack(side=tk.LEFT, padx=4)

        format_frame = ttk.Frame(settings_frame)
        format_frame.pack(fill=tk.X, pady=3)
        ttk.Label(format_frame, text="Format:", width=22).pack(side=tk.LEFT)
        format_var = tk.StringVar(value=self.settings["format"])
        format_entry = ttk.Entry(format_frame, textvariable=format_var, width=10)
        format_entry.pack(side=tk.LEFT, padx=5)

        self.add_context_menu(format_entry)

        # Instagram/Threads mostly require a login; picking a browser here
        # lets yt-dlp reuse that browser's session cookies
        cookies_frame = ttk.Frame(settings_frame)
        cookies_frame.pack(fill=tk.X, pady=3)
        ttk.Label(cookies_frame, text="Instagram/Threads login:", width=22).pack(side=tk.LEFT)
        cookies_var = tk.StringVar(value=self.settings.get("cookies_browser", ""))
        ttk.Combobox(cookies_frame, textvariable=cookies_var, width=10,
                     state="readonly",
                     values=("", "chrome", "edge", "firefox", "brave",
                             "opera", "vivaldi")).pack(side=tk.LEFT, padx=5)
        ttk.Label(cookies_frame,
                  text="browser to reuse cookies from").pack(side=tk.LEFT)

        # Auto download: skip the format picker and start downloading at a
        # preset quality as soon as a URL is entered/pasted
        auto_frame = ttk.Frame(settings_frame)
        auto_frame.pack(fill=tk.X, pady=3)
        ttk.Label(auto_frame, text="Auto download:", width=22).pack(side=tk.LEFT)
        auto_var = tk.BooleanVar(value=bool(self.settings.get("auto_download")))
        quality_var = tk.StringVar(
            value=self.settings.get("auto_download_quality", "best"))
        quality_box = ttk.Combobox(auto_frame, textvariable=quality_var, width=8,
                                   state="readonly", values=("best", "medium", "low"))

        def sync_quality_state(*_):
            quality_box.configure(state="readonly" if auto_var.get() else "disabled")

        ttk.Checkbutton(auto_frame, text="on, at quality:", variable=auto_var,
                        command=sync_quality_state).pack(side=tk.LEFT, padx=5)
        quality_box.pack(side=tk.LEFT, padx=5)
        sync_quality_state()

        # Threads cross-posts: the video is really on another site (e.g. an
        # Instagram reel). Off = download from the source site right away;
        # on = ask first.
        crosspost_frame = ttk.Frame(settings_frame)
        crosspost_frame.pack(fill=tk.X, pady=3)
        ttk.Label(crosspost_frame, text="Threads cross-posts:", width=22).pack(side=tk.LEFT)
        crosspost_var = tk.BooleanVar(value=bool(self.settings.get("confirm_crosspost")))
        ttk.Checkbutton(crosspost_frame,
                        text="ask before downloading from the source site",
                        variable=crosspost_var).pack(side=tk.LEFT, padx=5)

        # Transcripts: save the platform's captions with the video, as plain
        # text, closed captions, or both. Downloads then get their own
        # subfolder since several files arrive per video.
        trans_frame = ttk.Frame(settings_frame)
        trans_frame.pack(fill=tk.X, pady=3)
        ttk.Label(trans_frame, text="Transcripts:", width=22).pack(side=tk.LEFT)
        txt_var = tk.BooleanVar(value=bool(self.settings.get("transcript_txt")))
        srt_var = tk.BooleanVar(value=bool(self.settings.get("transcript_srt")))
        ttk.Checkbutton(trans_frame, text="plain text (.txt)",
                        variable=txt_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(trans_frame, text="closed captions (.srt)",
                        variable=srt_var).pack(side=tk.LEFT, padx=5)

        save_frame = ttk.Frame(settings_frame)
        save_frame.pack(fill=tk.X, pady=10)
        ttk.Button(save_frame, text="Save",
                   command=lambda: self.save_settings(source_var, dest_var, type_var,
                                                      format_var, cookies_var,
                                                      auto_var, quality_var,
                                                      crosspost_var,
                                                      txt_var, srt_var,
                                                      settings_window)).pack()

        # Size the window to its content rather than a fixed pixel geometry,
        # which clipped the right-hand labels and the Save button at display
        # scalings other than 100%. minsize keeps the folder entries roomy.
        settings_window.update_idletasks()
        settings_window.minsize(max(560, settings_window.winfo_reqwidth()),
                                settings_window.winfo_reqheight())

    def open_download_folder(self):
        """Show the destination folder in the file manager."""
        path = self.settings["download_path"]
        try:
            if os.name == 'nt':
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(['xdg-open', path])
        except OSError:
            messagebox.showerror("Error", f"Could not open folder:\n{path}")

    def browse_path(self, path_var):
        path = filedialog.askdirectory(
            initialdir=path_var.get() or self.settings["download_path"])
        if path:
            path_var.set(path)

    # Several download threads can finish at once; serialize their appends
    # so history lines can't interleave
    _history_lock = threading.Lock()

    def log_download(self, url, download_type, status):
        try:
            log_path = _migrate_legacy_file(self.base_path, "download_history.log")
            with self._history_lock, open(log_path, "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} | {download_type} | {url} | {status}\n")
        except Exception:
            pass

    def show_history(self):
        history_window = tk.Toplevel(self.root)
        history_window.title("Download History")
        history_window.geometry("600x400")
        history_window.resizable(False, False)  

        text_widget = tk.Text(history_window, wrap=tk.WORD, width=70, height=20)
        text_widget.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(history_window, orient=tk.VERTICAL, command=text_widget.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.configure(yscrollcommand=scrollbar.set)

        try:
            log_path = _migrate_legacy_file(self.base_path, "download_history.log")
            with open(log_path, "r") as f:
                history = f.read()
                text_widget.insert(tk.END, history)
        except OSError:
            text_widget.insert(tk.END, "No download history available.")

        text_widget.configure(state=tk.DISABLED)


# Create Flask app instance
flask_app = Flask(__name__)
CORS(flask_app)  # Enable CORS for Chrome extension

@flask_app.route('/')
def index():
    return jsonify({
        'status': 'running',
        'message': 'Downstream API is active'
    })

@flask_app.route('/api/download', methods=['POST'])
def api_download():
    try:
        data = request.json
        if not data or 'url' not in data or 'settings' not in data:
            return jsonify({
                'success': False,
                'error': 'Invalid request data. Required: url and settings'
            }), 400

        video_url = data['url']
        settings = data['settings']

        logger.info(f"Download request received for URL: {video_url}")
        logger.debug(f"Download settings: {settings}")

        download_type = settings.get('downloadType', 'video-audio')
        if download_type == 'video-audio':  # extension uses this spelling
            download_type = 'video+audio'
        # Extension quality names -> QUALITY_FORMATS keys
        quality = settings.get('quality', 'highest')
        quality = {'highest': 'best', 'lowest': 'low'}.get(quality, quality)

        type_presets = QUALITY_FORMATS.get(download_type)
        if type_presets is None:
            return jsonify({
                'success': False,
                'error': f'Unknown download type: {download_type}'
            }), 400
        selected_format, format_sort = type_presets.get(quality, type_presets['best'])
        logger.info(f"Selected format expression: {selected_format} sort: {format_sort}")

        # Honor the folders and merge format configured in the desktop
        # app's Settings
        app_settings = load_settings(get_base_path())
        ydl_opts = {
            **site_ydl_opts(video_url, app_settings),
            **transcript_ydl_opts(app_settings),
            'format': selected_format,
            'outtmpl': transcript_outtmpl('%(title)s.%(ext)s', app_settings),
            'paths': build_download_paths(app_settings),
            'merge_output_format': app_settings['format'],
            'concurrent_fragment_downloads': 4,
        }
        if format_sort:
            ydl_opts['format_sort'] = format_sort

        # Start download in background thread
        def download_thread():
            try:
                run_download_with_transcripts(
                    ydl_opts, app_settings,
                    lambda ydl: ydl.download([video_url]))
                logger.info("Download completed successfully")
            except Exception as e:
                logger.error(f"Download failed: {str(e)}")

        threading.Thread(target=download_thread, daemon=True).start()
        return jsonify({'success': True, 'message': 'Download started'})

    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Not 5000: that's Flask's default, and other dev servers squat on it (a
# collision was observed in the wild). Must match the extension's background.js
# and manifest.json host_permissions.
API_PORT = 47811


# Set when the API port can't be bound so the GUI can tell the user why the
# browser extension won't work this session
api_port_conflict = threading.Event()


def run_flask():
    # Bind to localhost only: the API is meant for the local Chrome extension,
    # exposing it on all interfaces would let anyone on the network trigger downloads
    try:
        flask_app.run(host='127.0.0.1', port=API_PORT)
    except OSError:
        # Port taken: GUI downloads still work, only the extension is affected
        api_port_conflict.set()
        logger.critical(
            f"Could not bind 127.0.0.1:{API_PORT} - is another copy of the "
            "app running? The Chrome extension will not work this session.",
            exc_info=True)

def main():
    logger.info("Starting Downstream application")
    if os.name == 'nt':
        # Give the process its own taskbar identity; otherwise Windows groups
        # the window under pythonw.exe and shows the Python/Tk icon instead
        # of ours
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Boneless3vil.Downstream")
        except Exception:
            logger.warning("Could not set AppUserModelID", exc_info=True)
    try:
        # Start Flask server in a separate thread
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("Flask server started")

        # Initialize Tk root with error handling
        try:
            root = tk.Tk()
            logger.debug("Created Tk root window")

            # Test Tk functionality
            test_label = ttk.Label(root, text="Initializing...")
            test_label.destroy()
            logger.debug("Successfully tested Tk widget creation")

        except tk.TclError as e:
            logger.error(f"Failed to initialize Tk: {str(e)}", exc_info=True)
            if "Can't find a usable init.tcl" in str(e):
                error_msg = (
                    "Error: Cannot initialize the graphical interface.\n\n"
                    "This might be caused by:\n"
                    "1. Missing Tcl/Tk libraries\n"
                    "2. Incorrect Tcl/Tk paths\n"
                    "3. Antivirus blocking the executable\n\n"
                    "Try:\n"
                    "1. Running as administrator\n"
                    "2. Temporarily disabling antivirus\n"
                    "3. Reinstalling the application"
                )
            else:
                error_msg = f"Failed to initialize graphical interface:\n{str(e)}"

            # Try to show error in GUI if possible, otherwise use console
            try:
                messagebox.showerror("Fatal Error", error_msg)
            except Exception:
                print("FATAL ERROR:", error_msg)
            sys.exit(1)

        app = DownstreamApp(root)
        logger.debug("Created DownstreamApp instance")
        logger.info("Starting main event loop")
        root.mainloop()

    except Exception as e:
        logger.error(f"Application error: {str(e)}", exc_info=True)
        if not isinstance(e, SystemExit):
            try:
                messagebox.showerror("Fatal Error",
                                   f"Application failed to start: {str(e)}\n\n"
                                   "Please check the logs for more details.")
            except Exception:
                print("FATAL ERROR:", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()