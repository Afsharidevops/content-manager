"""Playwright session helpers plus the Google Flow geo policy.

Two session modes are supported:

* cdp (default): attach to a desktop Chromium started by the operator with
  --remote-debugging-port=9222. The operator is already signed in to Google in
  that Chrome, so no credentials are stored. Media Studio only opens and
  drives extra tabs.
* persistent: launch a dedicated Chromium under data/profile inside the
  container. Google login must be completed once through a debugger/VNC
  session; usable on a server without a desktop.

The geo policy mirrors two standalone techniques used by the Flow web app
when Google redirects unsupported regions to /unsupported-country:

* intercept and abort any request to the unsupported-country page, and
* optionally "freeze" the page (stop in-flight network) right after the
  project creation affordance appears, which prevents the background region
  check from replacing the editor UI.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager

LOGGER = logging.getLogger("media_studio.browser")

GEO_URL_RE = re.compile(r"^https?://flow\.google(?:-[a-z0-9-]+)?\.com/unsupported-country", re.IGNORECASE)


def is_geo_redirect_url(url: str) -> bool:
    """True when the URL is Flow's unsupported-country page."""
    return bool(GEO_URL_RE.match(url or ""))


def should_abort(url: str, block_geo_redirect: bool) -> bool:
    return bool(block_geo_redirect) and is_geo_redirect_url(url)


def install_geo_policy(page, block_geo_redirect: bool) -> None:
    if not block_geo_redirect:
        return

    def handler(route) -> None:
        if should_abort(route.request.url, True):
            LOGGER.info("Blocked unsupported-country navigation: %s", route.request.url)
            route.abort("blockedbyclient")
            return
        route.continue_()

    page.route(re.compile(r"^https?://flow\.google(?:-[a-z0-9-]+)?\.com/.*", re.IGNORECASE), handler)


def freeze_page(page, seconds: float = 2.5) -> None:
    """Stop in-flight network repeatedly; mirrors the standalone freeze trick."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            page.evaluate("window.stop(); true")
        except Exception:  # noqa: BLE001 - page may be navigating
            pass
        time.sleep(0.15)


def _extension_args(settings) -> list[str]:
    extension = os.environ.get("MEDIA_STUDIO_EXTENSION_PATH", "").strip()
    if not extension:
        extension = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extensions", "locallab-flow-unlock")
    if not os.path.isdir(extension):
        return []
    return [
        f"--disable-extensions-except={extension}",
        f"--load-extension={extension}",
    ]


@contextmanager
def _cdp_page(settings):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        LOGGER.info("Connecting to Chromium over CDP at %s", settings.cdp_url)
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError(
                "The connected Chrome has no open window. Open a Chrome window, then retry."
            )
        context = contexts[0]
        page = context.new_page()
        try:
            yield page
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def _persistent_page(settings):
    from playwright.sync_api import sync_playwright

    profile_dir = os.path.join(settings.data_dir, "profile")
    os.makedirs(profile_dir, exist_ok=True)
    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--start-maximized",
        *_extension_args(settings),
    ]
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=settings.headless,
            args=args,
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1440, "height": 900},
            locale=settings.locale,
            timezone_id=settings.timezone_id,
        )
        page = context.new_page()
        try:
            yield page
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass


@contextmanager
def open_page(settings):
    if settings.session_mode == "cdp":
        with _cdp_page(settings) as page:
            yield page
    else:
        with _persistent_page(settings) as page:
            yield page


def configure_page(page, settings) -> None:
    page.set_default_timeout(max(5, getattr(settings, "step_timeout_seconds", 30)) * 1000)
    install_geo_policy(page, getattr(settings, "block_geo_redirect", True))


def snapshot_page(page, out_dir: str, prefix: str = "snapshot") -> list[str]:
    """Capture a screenshot plus an interactive-element dump for calibration."""
    os.makedirs(out_dir, exist_ok=True)
    image_path = os.path.join(out_dir, f"{prefix}.png")
    json_path = os.path.join(out_dir, f"{prefix}.json")
    try:
        page.screenshot(path=image_path, full_page=False)
    except Exception:  # noqa: BLE001
        image_path = ""
    data = {"url": page.url, "title": page.title()}
    elements = []
    try:
        locator = page.locator(
            "button, [role='button'], a, input, textarea, [contenteditable='true'], select, [role='menuitem'], [role='tab'], video"
        )
        count = min(locator.count(), 400)
        for index in range(count):
            element = locator.nth(index)
            try:
                info = element.evaluate(
                    """(node) => {
                        const style = window.getComputedStyle(node);
                        return {
                            tag: node.tagName,
                            id: node.id || '',
                            cls: typeof node.className === 'string' ? node.className : '',
                            role: node.getAttribute('role') || '',
                            aria: node.getAttribute('aria-label') || '',
                            name: node.getAttribute('name') || '',
                            placeholder: node.getAttribute('placeholder') || '',
                            src: (node.currentSrc || node.src || node.getAttribute('src') || '').slice(0, 200),
                            text: (node.innerText || node.textContent || '').trim().slice(0, 80),
                            visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                            display: style.display,
                        };
                    }"""
                )
                elements.append(info)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    data["elements"] = elements
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    files = [os.path.basename(json_path)]
    if image_path:
        files.append(os.path.basename(image_path))
    return files
