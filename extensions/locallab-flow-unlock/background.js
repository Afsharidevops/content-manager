// Recovers a Flow tab that Chrome stranded on a blocked navigation.
//
// The rule set aborts the unsupported-country page, but a navigation initiated
// by the web app (or a manual refresh while the address bar still shows the
// block route) ends on Chrome's error page, where no content script can run.
// This worker sends the tab back to the app root a few times per minute so the
// freeze script gets another chance to win the race against the region check.
const APP_URL = 'https://flow.google.com/';
const MAX_RETRIES = 2;
const WINDOW_MS = 60000;

const attempts = new Map();

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch (error) {
    return '';
  }
}

function isFlowHost(url) {
  const host = hostOf(url);
  return host === 'flow.google.com' || host.startsWith('flow.google-') || host === 'labs.google';
}

function isBlockRoute(url) {
  return isFlowHost(url) && String(url).toLowerCase().includes('unsupported-country');
}

function retry(tabId, url) {
  if (tabId < 0 || !isFlowHost(url)) return;
  const now = Date.now();
  const recent = (attempts.get(tabId) || []).filter((stamp) => now - stamp < WINDOW_MS);
  if (recent.length >= MAX_RETRIES) {
    attempts.set(tabId, recent);
    return;
  }
  recent.push(now);
  attempts.set(tabId, recent);
  chrome.tabs.update(tabId, { url: APP_URL }).catch(() => {});
}

chrome.webNavigation.onErrorOccurred.addListener((details) => {
  if (details.frameId !== 0) return;
  if (!String(details.error || '').includes('ERR_BLOCKED_BY_CLIENT')) return;
  retry(details.tabId, details.url);
});

chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId !== 0) return;
  if (!isBlockRoute(details.url)) return;
  retry(details.tabId, details.url);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  attempts.delete(tabId);
});
