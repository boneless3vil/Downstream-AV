"""Interactive installer for Downstream.

Sets up everything needed to RUN the application:
  - a virtual environment with the Python dependencies
  - ffmpeg (required to merge video+audio and to extract MP3 audio)
  - Deno (JS runtime yt-dlp needs for full YouTube format extraction)
  - your download folder (saved to settings.json)
  - a launcher, and optionally a desktop shortcut
  - optionally, the Chrome extension

Build tooling (PyInstaller) is intentionally not installed here — that is
only needed to package an .exe; see build.py.
"""
import json
import os
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SCRIPT_DIR, ".venv")
IS_WINDOWS = os.name == "nt"
VENV_BIN = os.path.join(VENV_DIR, "Scripts" if IS_WINDOWS else "bin")
VENV_PYTHON = os.path.join(VENV_BIN, "python.exe" if IS_WINDOWS else "python")
RUNTIME_PACKAGES = ["yt-dlp", "pyperclip", "flask", "flask-cors"]
EXTENSION_DIR = os.path.join(SCRIPT_DIR, "chrome_extension")


def ask_yes_no(question, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def ask_text(question, default):
    try:
        answer = input(f"{question} [{default}]: ").strip()
    except EOFError:
        return default
    return answer or default


def winget_install(package_id):
    if not shutil.which("winget"):
        return False
    result = subprocess.run(
        ["winget", "install", "--id", package_id, "-e",
         "--accept-source-agreements", "--accept-package-agreements"]
    )
    return result.returncode == 0


def step_python_version():
    if sys.version_info < (3, 11):
        print("Error: Python 3.11 or higher is required "
              f"(you have {sys.version.split()[0]}).")
        sys.exit(1)
    print(f"[ok] Python {sys.version.split()[0]}")


def step_virtual_environment():
    if os.path.exists(VENV_PYTHON):
        print(f"[ok] Virtual environment already exists at {VENV_DIR}")
    else:
        print(f"Creating virtual environment at {VENV_DIR} ...")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])

    print(f"Installing packages: {', '.join(RUNTIME_PACKAGES)} ...")
    # Install with the venv's own interpreter so packages land in the venv,
    # not in the Python that happened to run this script
    subprocess.check_call(
        [VENV_PYTHON, "-m", "pip", "install", "--upgrade", "-q", *RUNTIME_PACKAGES]
    )
    print("[ok] Python packages installed")


def step_ffmpeg():
    if shutil.which("ffmpeg"):
        print("[ok] ffmpeg found")
        return
    print("\nffmpeg was not found. It is required to merge video and audio")
    print("tracks and to convert audio-only downloads to MP3.")
    if IS_WINDOWS and ask_yes_no("Install ffmpeg now with winget?"):
        if winget_install("Gyan.FFmpeg"):
            print("[ok] ffmpeg installed (open a new terminal before running the app)")
        else:
            print("[!!] winget install failed - install manually from https://ffmpeg.org")
    else:
        hint = ("winget install Gyan.FFmpeg" if IS_WINDOWS
                else "sudo apt install ffmpeg   (or: brew install ffmpeg)")
        print(f"[!!] Skipped. Install it later with: {hint}")


def step_js_runtime():
    if shutil.which("deno"):
        print("[ok] Deno found (yt-dlp JS runtime)")
        return
    print("\nDeno was not found. yt-dlp uses it to run YouTube's player")
    print("JavaScript; without it some video formats will be missing and")
    print("extraction may stop working as YouTube changes.")
    if IS_WINDOWS and ask_yes_no("Install Deno now with winget?"):
        if winget_install("DenoLand.Deno"):
            print("[ok] Deno installed (open a new terminal before running the app)")
        else:
            print("[!!] winget install failed - install manually from https://deno.com")
    else:
        hint = ("winget install DenoLand.Deno" if IS_WINDOWS
                else "curl -fsSL https://deno.land/install.sh | sh")
        print(f"[!!] Skipped. Install it later with: {hint}")


def step_settings():
    default_path = os.path.join(os.path.expanduser("~"), "Downloads")
    # Same per-user location the app uses (get_config_dir in downstream.py):
    # survives updates and rebuilds
    if IS_WINDOWS:
        config_base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        config_base = os.path.join(os.path.expanduser("~"), ".config")
    config_dir = os.path.join(config_base, "Downstream")
    os.makedirs(config_dir, exist_ok=True)
    settings_path = os.path.join(config_dir, "settings.json")
    current = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                current = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    download_path = ask_text("Where should downloads be saved?",
                             current.get("download_path", default_path))
    os.makedirs(download_path, exist_ok=True)

    # Merge over existing settings so keys managed in the app's Settings
    # dialog (source folder, default download type, ...) survive a re-install
    settings = {**current,
                "download_path": download_path,
                "format": current.get("format", "mp4")}
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"[ok] Settings saved to {settings_path}")


def step_launcher():
    if not IS_WINDOWS:
        print("\nTo run the application:")
        print(f"  {VENV_PYTHON} {os.path.join(SCRIPT_DIR, 'downstream.py')}")
        return

    launcher = os.path.join(SCRIPT_DIR, "Downstream.bat")
    with open(launcher, "w") as f:
        f.write('@echo off\r\n'
                'start "" "%~dp0.venv\\Scripts\\pythonw.exe" '
                '"%~dp0downstream.py"\r\n')
    print(f"[ok] Launcher created: {launcher}")

    if ask_yes_no("Create a desktop shortcut?"):
        shortcut_cmd = (
            "$ws = New-Object -ComObject WScript.Shell; "
            "$s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') "
            "+ '\\Downstream.lnk'); "
            f"$s.TargetPath = '{launcher}'; "
            f"$s.WorkingDirectory = '{SCRIPT_DIR}'; "
            "$s.Save()"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", shortcut_cmd])
        if result.returncode == 0:
            print("[ok] Desktop shortcut created")
        else:
            print("[!!] Could not create desktop shortcut")


def step_chrome_extension():
    print("\n--- Chrome extension (optional) ---")
    print("The extension adds one-click downloads on youtube.com. It talks to")
    print("a small server that is BUILT INTO the desktop app (port 47811 on")
    print("this machine only), so there is nothing extra to install or run -")
    print("just keep the desktop app open while you use the extension.")
    if not os.path.isdir(EXTENSION_DIR):
        print("[!!] chrome_extension folder not found next to this project - skipping")
        return
    if not ask_yes_no("Set up the Chrome extension now?", default=False):
        print("You can set it up any time: see chrome_extension/INSTALL.md")
        return

    print("\nChrome only allows unpacked extensions to be added manually:")
    print("  1. In the Chrome window that opens, turn ON 'Developer mode' (top right)")
    print("  2. Click 'Load unpacked'")
    print(f"  3. Select this folder: {EXTENSION_DIR}")
    print("  4. Pin the extension, open a YouTube video, and click its icon")
    if IS_WINDOWS:
        try:
            subprocess.run(["cmd", "/c", "start", "chrome", "chrome://extensions/"],
                           check=False)
            subprocess.run(["explorer", EXTENSION_DIR], check=False)
        except OSError:
            pass


def main():
    print("=== Downstream - interactive setup ===\n")
    step_python_version()
    step_virtual_environment()
    step_ffmpeg()
    step_js_runtime()
    step_settings()
    step_launcher()
    step_chrome_extension()

    print("\n=== Setup complete ===")
    if IS_WINDOWS:
        print('Run the app with "Downstream.bat" (or the desktop shortcut).')
    print("The Chrome extension works whenever the desktop app is running.")


if __name__ == "__main__":
    main()
