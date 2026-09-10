// The Flow dashboard reveals itself before the region check completes. When
// the project creation affordance appears, stop in-flight network for a short
// window so the background region check cannot replace the UI with the
// unsupported-country page. Companion rule set blocks that page entirely.
//
// Multi-account Google URLs carry a /u/<n>/ prefix (for example
// https://flow.google.com/u/1/unsupported-country). The rule set covers those
// variants; this script adds a second line of defence for the case where the
// web app swaps the route client-side without a network navigation.
(function () {
  'use strict';

  const BLOCK_MARKER = 'unsupported-country';
  const RETURN_KEY = 'locallabFlowUnlockBounces';
  const MAX_BOUNCES = 2;

  let froze = false;
  let bounceStarted = false;
  let freezeTimers = [];

  function onBlockRoute() {
    return window.location.pathname.toLowerCase().includes(BLOCK_MARKER);
  }

  // Reload the app for the account the tab is using. Dropping the /u/<n>/
  // prefix would silently fall back to the first signed-in account.
  function accountRoot() {
    const match = window.location.pathname.match(/^\/u\/(\d+)(?:\/|$)/);
    return match
      ? `https://flow.google.com/u/${match[1]}/`
      : 'https://flow.google.com/';
  }

  function readBounces() {
    try {
      return Number(window.sessionStorage.getItem(RETURN_KEY) || '0');
    } catch (error) {
      return 0;
    }
  }

  function writeBounces(value) {
    try {
      window.sessionStorage.setItem(RETURN_KEY, String(value));
    } catch (error) {
      /* storage can be disabled; the bounce limit is best effort then */
    }
  }

  // Stops scheduled stops so a recovery navigation is not cancelled by the
  // freeze that was aimed at the block page.
  function stopFreeze() {
    freezeTimers.forEach((timer) => window.clearTimeout(timer));
    freezeTimers = [];
    froze = true;
  }

  function freezeOnce() {
    if (froze) return;
    froze = true;
    for (let i = 0; i < 30; i++) {
      freezeTimers.push(window.setTimeout(() => window.stop(), i * 60));
    }
  }

  // Restart the app once or twice when the block route is already showing.
  function bounceOffBlockRoute() {
    if (!onBlockRoute() || bounceStarted) return onBlockRoute();
    const bounces = readBounces();
    if (bounces >= MAX_BOUNCES) return true;
    writeBounces(bounces + 1);
    bounceStarted = true;
    stopFreeze();
    window.location.replace(accountRoot());
    return true;
  }

  // Kept to the original matcher on purpose: broader selectors fired the
  // freeze during the app bootstrap, which can abort the data the dashboard
  // needs before the region check is even reached.
  function looksLikeCreateButton(element) {
    const text = (element.textContent || '').toLowerCase();
    const aria = (element.getAttribute('aria-label') || '').toLowerCase();
    return (
      text.includes('new project') ||
      aria.includes('new project') ||
      text.includes('create') ||
      aria.includes('create')
    );
  }

  function findCreateButton() {
    return Array.from(document.querySelectorAll('button, [role="button"]')).find(looksLikeCreateButton);
  }

  if (bounceOffBlockRoute()) return;

  const observer = new MutationObserver(() => {
    if (froze) return;
    if (findCreateButton()) {
      observer.disconnect();
      writeBounces(0);
      freezeOnce();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Client-side routing never issues a request, so watch the address bar too
  // and restart the app instead of letting the block view render.
  window.setInterval(() => {
    if (onBlockRoute()) bounceOffBlockRoute();
  }, 250);
})();
