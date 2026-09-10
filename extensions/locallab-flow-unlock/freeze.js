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

  let done = false;
  let bounced = false;

  function onBlockRoute() {
    return window.location.pathname.toLowerCase().includes(BLOCK_MARKER);
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

  // Restart the app once or twice when the block route is already showing.
  function bounceOffBlockRoute() {
    if (!onBlockRoute()) return false;
    const bounces = readBounces();
    if (bounces >= MAX_BOUNCES) return true;
    writeBounces(bounces + 1);
    bounced = true;
    window.location.replace('https://flow.google.com/');
    return true;
  }

  function freezeOnce() {
    if (done) return;
    done = true;
    for (let i = 0; i < 30; i++) {
      setTimeout(() => window.stop(), i * 60);
    }
  }

  function looksLikeCreateButton(element) {
    const text = (element.textContent || '').toLowerCase();
    const aria = (element.getAttribute('aria-label') || '').toLowerCase();
    const title = (element.getAttribute('title') || '').toLowerCase();
    return (
      text.includes('new project') ||
      text.includes('create') ||
      aria.includes('new project') ||
      aria.includes('create') ||
      title.includes('new project') ||
      title.includes('create')
    );
  }

  function findCreateButton() {
    const nodes = document.querySelectorAll('button, [role="button"], a[href*="project"]');
    return Array.from(nodes).find(looksLikeCreateButton);
  }

  if (bounceOffBlockRoute()) return;

  const observer = new MutationObserver(() => {
    if (done) return;
    if (findCreateButton()) {
      observer.disconnect();
      writeBounces(0);
      freezeOnce();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Client-side routing never issues a request, so watch the address bar too:
  // freeze immediately and restart the app instead of rendering the block view.
  window.setInterval(() => {
    if (!onBlockRoute()) return;
    freezeOnce();
    if (!bounced) bounceOffBlockRoute();
  }, 250);
})();
