# Installing the Downstream Chrome Extension

## Installation Steps

1. Download and Extract Files:
   - Download all the extension files
   - Keep the folder structure intact:
     ```
     chrome_extension/
     ├── manifest.json
     ├── background.js
     ├── content.js
     ├── icons/
     │   ├── icon16.png
     │   ├── icon48.png
     │   └── icon128.png
     ```

2. Open Chrome Extensions Page:
   - Open Google Chrome
   - Type `chrome://extensions/` in the address bar
   - Press Enter

3. Enable Developer Mode:
   - Look for the "Developer mode" toggle in the top right corner
   - Turn it ON

4. Load the Extension:
   - Click the "Load unpacked" button that appears
   - Navigate to and select the `chrome_extension` folder
   - Click "Select Folder"

5. Verify Installation:
   - The extension icon should appear in your Chrome toolbar
   - If you don't see it, click the puzzle piece icon to find it
   - Pin the extension for easy access

## Usage

1. Single-Click Download:
   - Navigate to any YouTube video
   - Click the extension icon once
   - The video will download in best quality

2. Custom Settings (Right-Click Menu):
   - Right-click the extension icon
   - Select video quality (High/Medium/Low)
   - Choose download type:
     - Video + Audio
     - Video Only
     - Audio Only

## Requirements

- The desktop application must be installed and running. The extension's
  server is **built into the desktop app** — starting `downstream.py`
  (or `Downstream.bat`) also starts the API the extension talks to
  at `http://localhost:47811`. There is nothing separate to install or run.
- To install the desktop app, run `python install.py` in the repository
  root — the installer can also open this extension setup for you.

## Auto-starting the desktop app (optional)

The extension can start Downstream itself when you click it and the app
isn't running. This uses a browser "native messaging host" that has to be
registered once per Windows user:

1. Make sure `native_host\com.boneless3vil.downstream_launcher.json` lists
   your extension's ID in `allowed_origins` (find the ID on
   `chrome://extensions` / `edge://extensions`; for an unpacked extension it
   is derived from the folder path, so it changes if the folder moves).
2. Run `native_host\register_host.ps1` in PowerShell. It registers the host
   for both Chrome and Edge under HKCU — no admin rights needed.
3. Reload the extension.

Now a click with the app closed shows a blue "…" badge while Downstream
starts (the packaged exe takes a few seconds to unpack), then sends the
download. If the badge turns red, hover over it: the tooltip says whether the
host isn't registered or the app didn't start.

## Troubleshooting

If the extension doesn't work:
1. Check that the desktop app is running (visit http://localhost:47811 in
   your browser — it should reply that the API is active)
2. Verify the extension is enabled in Chrome
3. Try refreshing the YouTube page
4. Check Chrome's console for any error messages