chrome.runtime.onInstalled.addListener(() => {
  // Create main context menu items
  chrome.contextMenus.create({
    id: 'download-type',
    title: 'Download Type',
    contexts: ['action']
  });

  // Download type options
  const downloadTypes = [
    {id: 'video-audio', title: 'Video + Audio', checked: true},
    {id: 'video-only', title: 'Video Only'},
    {id: 'audio-only', title: 'Audio Only'}
  ];

  downloadTypes.forEach(type => {
    chrome.contextMenus.create({
      id: type.id,
      parentId: 'download-type',
      title: type.title,
      type: 'radio',
      checked: type.checked,
      contexts: ['action']
    });
  });

  // Create quality submenu
  chrome.contextMenus.create({
    id: 'quality',
    title: 'Quality',
    contexts: ['action']
  });

  // Quality options
  const qualities = [
    {id: 'highest', title: 'High Quality', checked: true},
    {id: 'medium', title: 'Medium Quality'},
    {id: 'lowest', title: 'Low Quality'}
  ];

  qualities.forEach(quality => {
    chrome.contextMenus.create({
      id: quality.id,
      parentId: 'quality',
      title: quality.title,
      type: 'radio',
      checked: quality.checked,
      contexts: ['action']
    });
  });
});

const defaultPreferences = {
  downloadType: 'video-audio',
  quality: 'highest'
};

// Preferences live in chrome.storage.sync (shared with the popup) — a plain
// variable would reset every time the MV3 service worker is unloaded
async function getPreferences() {
  const prefs = await chrome.storage.sync.get(defaultPreferences);
  if (prefs.downloadType === 'video+audio') {
    prefs.downloadType = 'video-audio';
  }
  return prefs;
}

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener((info) => {
  if (info.parentMenuItemId === 'download-type') {
    chrome.storage.sync.set({downloadType: info.menuItemId});
  } else if (info.parentMenuItemId === 'quality') {
    chrome.storage.sync.set({quality: info.menuItemId});
  }
});

// Badge feedback on the extension icon itself: visible even when the
// on-page button is missing (tab opened before install, YouTube DOM
// changes, or the content script failed to run)
function flashBadge(text, color, title) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
  chrome.action.setTitle({ title: title || 'Downstream' });
  setTimeout(() => {
    chrome.action.setBadgeText({ text: '' });
    chrome.action.setTitle({ title: 'Downstream' });
  }, 4000);
}

// Best-effort: update the on-page button too; ignore tabs with no listener
function notifyTab(tabId, action, message) {
  if (tabId == null) return;
  chrome.tabs.sendMessage(tabId, { action, message }).catch(() => {});
}

// Must match API_PORT in downstream.py and manifest host_permissions
const API_URL = 'http://localhost:47811/api/download';
const API_STATUS_URL = 'http://localhost:47811/';

// Native messaging host that starts the desktop app when it isn't running
// (registered in the OS by native_host/register_host.ps1)
const LAUNCHER_HOST = 'com.boneless3vil.downstream_launcher';

async function isAppRunning() {
  try {
    return (await fetch(API_STATUS_URL)).ok;
  } catch {
    return false;
  }
}

// Ask the native host to start Downstream, then wait for its API to come up.
// The launcher exits without replying, so sendNativeMessage always reports an
// error - that's expected and ignored; polling the API is the real signal.
// The wait is generous: the packaged exe unpacks itself before starting.
async function launchAppAndWait() {
  chrome.action.setBadgeBackgroundColor({ color: '#1565c0' });
  chrome.action.setBadgeText({ text: '…' });
  chrome.action.setTitle({ title: 'Starting Downstream...' });
  try {
    await chrome.runtime.sendNativeMessage(LAUNCHER_HOST, { action: 'launch' });
  } catch { /* expected: the launcher never replies */ }
  for (let i = 0; i < 45; i++) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    if (await isAppRunning()) return true;
  }
  return false;
}

async function postDownload(url) {
  const preferences = await getPreferences();
  const response = await fetch(API_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, settings: preferences })
  });
  let data;
  try {
    data = await response.json();
  } catch {
    // Something answered, but not with our API's JSON
    throw new Error('Another program is answering on the downloader\'s port');
  }
  if (!data.success) {
    throw new Error(data.error || 'Download failed');
  }
  return data;
}

// Pages the desktop app can download from (keep in sync with
// SUPPORTED_URL_RE / AUTO_FETCH_RE in downstream.py)
const VIDEO_PAGE_RE = new RegExp(
  'youtube\\.com/(watch|shorts/)' +
  '|youtu\\.be/' +
  '|instagram\\.com/([\\w.]+/)?(reels?|p|tv)/' +
  '|threads\\.(net|com)/@?[\\w.]+/post/', 'i');

async function startDownload(url, tabId) {
  if (!url || !VIDEO_PAGE_RE.test(url)) {
    flashBadge('!', '#f0ad4e',
      'Open a YouTube, Instagram, or Threads video page first');
    return { success: false, error: 'Not a supported video page' };
  }
  try {
    let data;
    try {
      data = await postDownload(url);
    } catch (error) {
      // "Failed to fetch" = nothing listening on the API port: start the
      // desktop app ourselves and retry once
      if (!String(error.message).includes('Failed to fetch')) {
        throw error;
      }
      if (!await launchAppAndWait()) {
        throw new Error('Could not start Downstream - start it manually');
      }
      data = await postDownload(url);
    }
    flashBadge('✓', '#2e7d32', 'Download started');
    notifyTab(tabId, 'downloadStarted', 'Download started successfully');
    return data;
  } catch (error) {
    flashBadge('✗', '#c62828', 'Download failed: ' + error.message);
    notifyTab(tabId, 'downloadError', error.message);
    return { success: false, error: error.message };
  }
}

// Exposed for automated E2E tests (drive the icon-click path directly)
globalThis.__startDownload = startDownload;

// Extension icon click: download directly - no content-script round trip,
// so it works even when the page button couldn't be injected
chrome.action.onClicked.addListener((tab) => {
  startDownload(tab.url, tab.id);
});

// On-page button clicks arrive from the content script
chrome.runtime.onMessage.addListener((request, sender) => {
  if (request.action === 'downloadVideo') {
    startDownload(request.url, sender.tab && sender.tab.id);
  }
});
