// Firefox event page for the Locallab Flow Unlock extension.
//
// Chrome aborts the block-route requests with declarativeNetRequest rules and
// recovers the tab from the resulting error page (see background.js). A
// Firefox event page can block and redirect requests directly, so this file
// carries the same policy without a rule file: a navigation to the
// unsupported-country route is sent back to the app root of the same account,
// other resources on that route are cancelled, and the recovery listeners
// stay as a fallback for tabs that still reach the block page or its error.
const api = typeof browser !== 'undefined' ? browser : chrome;

const APP_URL = 'https://flow.google.com/';
const MAX_ATTEMPTS = 3;
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
  return (
    host === 'flow.google.com' ||
    /^flow\.google-[a-z0-9-]+\.com$/.test(host) ||
    host === 'labs.google'
  );
}

function isBlockRoute(url) {
  return isFlowHost(url) && String(url).toLowerCase().includes('/unsupported-country');
}

// Keep the /u/<n>/ account prefix: restarting the app on the bare root
// silently falls back to the first signed-in Google account.
function accountRoot(url) {
  try {
    const match = new URL(url).pathname.match(/^\/u\/(\d+)(?:\/|$)/);
    if (match) return `https://flow.google.com/u/${match[1]}/`;
  } catch (error) {
    /* fall through to the plain root */
  }
  return APP_URL;
}

function budgetLeft(tabId) {
  const now = Date.now();
  const recent = (attempts.get(tabId) || []).filter((stamp) => now - stamp < WINDOW_MS);
  attempts.set(tabId, recent);
  return recent.length < MAX_ATTEMPTS;
}

function spend(tabId) {
  const recent = attempts.get(tabId) || [];
  recent.push(Date.now());
  attempts.set(tabId, recent);
}

// Firefox implements the chrome namespace with callbacks and returns a promise
// when no callback is given, so treat both shapes as best effort.
function updateTab(tabId, url) {
  if (tabId < 0) return;
  try {
    const result = api.tabs.update(tabId, { url });
    if (result && typeof result.then === 'function') result.then(null, () => {});
  } catch (error) {
    /* the tab may already be gone */
  }
}

function recover(tabId, url) {
  if (tabId < 0 || !isFlowHost(url)) return;
  if (!budgetLeft(tabId)) return;
  spend(tabId);
  updateTab(tabId, accountRoot(url));
}

api.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (!isBlockRoute(details.url)) return {};
    const navigation = details.type === 'main_frame' || details.type === 'sub_frame';
    if (navigation && budgetLeft(details.tabId)) {
      // A redirect keeps the tab on a real page; cancelling a top-level
      // navigation would leave Firefox on a blank or error view.
      spend(details.tabId);
      return { redirectUrl: accountRoot(details.url) };
    }
    return { cancel: true };
  },
  { urls: ['https://*.google.com/*', 'https://labs.google/*'] },
  ['blocking']
);

api.webNavigation.onErrorOccurred.addListener((details) => {
  if (details.frameId !== 0) return;
  const error = String(details.error || '');
  const blocked =
    error.includes('ERR_BLOCKED_BY_CLIENT') ||
    error.includes('NS_ERROR_ABORT') ||
    error.includes('NS_ERROR_FAILURE') ||
    error.includes('NS_BINDING_ABORTED');
  if (!blocked) return;
  recover(details.tabId, details.url);
});

api.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId !== 0) return;
  if (!isBlockRoute(details.url)) return;
  recover(details.tabId, details.url);
});

api.tabs.onRemoved.addListener((tabId) => {
  attempts.delete(tabId);
});
