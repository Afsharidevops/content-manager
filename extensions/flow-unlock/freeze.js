// The Flow dashboard reveals itself before the region check completes. When
// the project creation affordance appears, stop in-flight network for a short
// window so the background region check cannot replace the UI with the
// unsupported-country page. Companion rule set blocks that page entirely.
(function () {
  'use strict';

  let done = false;

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

  const observer = new MutationObserver(() => {
    if (done) return;
    if (findCreateButton()) {
      observer.disconnect();
      freezeOnce();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
