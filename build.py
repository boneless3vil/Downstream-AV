import PyInstaller.__main__
import os
import sys
import shutil
from pathlib import Path

def check_windows_environment():
    """Check if we're running on Windows and have necessary prerequisites"""
    if os.name != 'nt':
        print("\nError: This build script must be run on Windows.")
        print("Please copy the project to a Windows system and run the build there.")
        print("\nBuild requirements on Windows:")
        print("1. Python 3.11 or higher")
        print("2. Visual Studio Build Tools with C++ components")
        sys.exit(1)

def build_executable():
    """Build the Windows executable with proper configuration"""
    # Verify Windows environment first
    check_windows_environment()

    # Check Python version
    if sys.version_info < (3, 11):
        print("\nError: Python 3.11 or higher is required.")
        print("Please upgrade your Python installation.")
        sys.exit(1)

    print("\nPreparing to build Downstream for Windows...")

    # Ensure we're in the correct directory
    if not os.path.exists("downstream.py"):
        print("Error: downstream.py not found in current directory")
        print("Please run this script from the repository root")
        sys.exit(1)

    # Create Windows-specific runtime hook
    print("Creating Windows runtime hook...")
    with open('runtime-hook.py', 'w') as f:
        f.write('''
import os
import sys
import tkinter
import tkinter.ttk

def _find_tcl_tk():
    """Find and set TCL/TK paths for Windows executable"""
    import _tkinter
    tcl_dir = os.path.join(sys._MEIPASS, "tcl")
    tk_dir = os.path.join(sys._MEIPASS, "tk")

    if os.path.exists(tcl_dir) and os.path.exists(tk_dir):
        vers = [d for d in os.listdir(tcl_dir) if d.startswith('tcl')]
        if vers:
            tcl_vers = sorted(vers)[-1]  # Get latest version
            tk_vers = 'tk' + tcl_vers[3:]  # Corresponding tk version
            os.environ['TCL_LIBRARY'] = os.path.join(tcl_dir, tcl_vers)
            os.environ['TK_LIBRARY'] = os.path.join(tk_dir, tk_vers)
            return True
    return False

if os.name == 'nt':  # Windows-specific initialization
    try:
        if not _find_tcl_tk():
            print("Warning: Could not find bundled TCL/TK libraries")
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print(f"Warning: Tkinter initialization error: {e}")
''')

    # Clear existing build and dist directories
    print("Cleaning previous build files...")
    for dir_name in ['build', 'dist']:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)

    # Windows-specific PyInstaller options
    options = [
        'downstream.py',
        '--onefile',
        '--name=Downstream',
        '--windowed',  # Windows GUI mode
        # Settings are NOT bundled: the app reads/writes them in
        # %APPDATA%\Downstream (get_config_dir) so they survive updates
        # yt-dlp plugin package with the Threads extractor; unpacked into
        # sys._MEIPASS (which is on sys.path) so yt-dlp discovers it
        '--add-data=yt_dlp_plugins;yt_dlp_plugins',
        '--hidden-import=tkinter',
        '--hidden-import=tkinter.ttk',
        '--hidden-import=_tkinter',
        '--hidden-import=json',
        '--collect-all=yt_dlp',
        '--collect-all=pyperclip',
        # Local speech-to-text for transcripts: faster_whisper ships its
        # voice-activity model as a data file (assets/silero_vad_v6.onnx)
        # and ctranslate2 its inference DLLs, neither found by import
        # analysis alone. huggingface_hub (model download) imports lazily.
        '--collect-all=faster_whisper',
        '--collect-all=ctranslate2',
        '--collect-submodules=huggingface_hub',
        '--runtime-hook=runtime-hook.py',
        '--clean',
        '--disable-windowed-traceback',
        '--noupx',  # More reliable than UPX compression
    ]

    # Add icon if available: --icon sets the .exe file's icon, --add-data
    # ships the .ico so the app can also use it as the window icon at runtime
    if os.path.exists("icon.ico"):
        options.append('--icon=icon.ico')
        options.append('--add-data=icon.ico;.')

    try:
        print("\nStarting PyInstaller build process...")
        PyInstaller.__main__.run(options)

        # Verify the Windows executable was created
        exe_path = os.path.join('dist', 'Downstream.exe')

        if os.path.exists(exe_path):
            print(f"\nBuild completed successfully!")
            print(f"Windows executable created at: {exe_path}")
            print("\nImportant notes for Windows users:")
            print("1. Keep the executable in a path without spaces")
            print("2. Some antivirus software may flag the executable on first run")
            print("3. Make sure all Visual C++ redistributables are installed")
        else:
            raise FileNotFoundError("Windows executable not found after build")

    except Exception as e:
        print(f"\nError during Windows build: {str(e)}")
        print("\nWindows build troubleshooting steps:")
        print("1. Ensure all dependencies are installed (pip install pyinstaller yt-dlp pyperclip)")
        print("2. Install Visual C++ Redistributable for Visual Studio 2015-2022")
        print("3. Run from a directory path without spaces")
        print("4. Temporarily disable antivirus software")
        print("5. Clear the build and dist folders before rebuilding")
        sys.exit(1)

if __name__ == "__main__":
    build_executable()