"""Playwright session helpers for NotebookLM.

Two session modes exist:

* ``persistent`` (default): launch a container-local Chromium whose profile
  lives under ``NOTEBOOKLM_BROWSER_PROFILE``. The session is imported once
  from an export taken on the operator's local machine.
* ``cdp``: attach to a desktop Chromium that is already signed in to Google
  over the DevTools protocol. No credentials are stored anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager

LOGGER = logging.getLogger("notebooklm.browser")

SIGNED_OUT_RE = re.compile(
    r"accounts\.google\.com|/v3/signin|ServiceLogin|/signin/", re.IGNORECASE
)
NOTEBOOKLM_RE = re.compile(r"^https?://notebooklm\.google\.com/", re.IGNORECASE)


class SessionError(RuntimeError):
    """Raised when the browser session cannot be used."""


def is_signed_out_url(url: str) -> bool:
    return bool(SIGNED_OUT_RE.search(str(url or "")))


def is_notebooklm_url(url: str) -> bool:
    return bool(NOTEBOOKLM_RE.match(str(url or "")))


def _launch_args(settings) -> list[str]:
    return [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--start-maximized",
    ]


@contextmanager
def _cdp_page(settings):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        LOGGER.info("Connecting to Chromium over CDP at %s", settings.cdp_url)
        browser = playwright.chromium.connect_over_cdp(settings.cdp_url)
        contexts = browser.contexts
        if not contexts:
            raise SessionError(
                "The connected Chrome has no open window. Open a Chrome window, "
                "then retry."
            )
        context = contexts[0]
        page = context.new_page()
        try:
            yield page
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001 - closing a dead page must not fail the job
                pass


@contextmanager
def _persistent_page(settings):
    from playwright.sync_api import sync_playwright

    profile_dir = settings.browser_profile or os.path.join(
        settings.data_dir, "notebooklm-browser-profile"
    )
    os.makedirs(profile_dir, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=settings.headless,
            args=_launch_args(settings),
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
    if str(settings.session_mode).lower() == "cdp":
        with _cdp_page(settings) as page:
            yield page
    else:
        with _persistent_page(settings) as page:
            yield page


def configure_page(page, settings) -> None:
    page.set_default_timeout(max(5, settings.step_timeout_seconds) * 1000)


def open_notebooklm(page, settings) -> None:
    """Open the NotebookLM home page and fail when Google asks for a login."""
    page.goto(settings.home_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    url = str(page.url or "")
    if is_signed_out_url(url):
        raise SessionError(
            "The Google session is signed out. Run ./manage.sh notebooklm-login, "
            "sign in to NotebookLM in that browser, then retry."
        )


def signed_in(page) -> bool:
    """Best-effort check that the NotebookLM app shell is available."""
    url = str(page.url or "")
    if is_signed_out_url(url):
        return False
    if not is_notebooklm_url(url):
        return False
    for selector in ("text=Create new", "text=New notebook", "text=Notebooks"):
        try:
            if page.locator(selector).first.is_visible(timeout=1500):
                return True
        except Exception:  # noqa: BLE001 - a missing node is not an error here
            continue
    return False


def snapshot_page(page, out_dir: str, prefix: str = "snapshot") -> list[str]:
    """Capture a screenshot plus an element dump for selector calibration."""
    os.makedirs(out_dir, exist_ok=True)
    image_path = os.path.join(out_dir, f"{prefix}.png")
    json_path = os.path.join(out_dir, f"{prefix}.json")
    try:
        page.screenshot(path=image_path, full_page=False)
    except Exception:  # noqa: BLE001 - a broken page still deserves the dump
        image_path = ""
    data = {"url": page.url, "title": page.title()}
    elements = []
    try:
        locator = page.locator(
            "button, [role='button'], a, input, textarea, [contenteditable='true'],"
            " [role='menuitem'], [role='tab'], [role='dialog'], video"
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
                            text: (node.innerText || node.textContent || '').trim().slice(0, 80),
                            visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                            display: style.display,
                        };
                    }"""
                )
                elements.append(info)
            except Exception:  # noqa: BLE001 - one detached node is not fatal
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


def session_info(settings) -> dict:
    """Cheap description of the configured session; opens no browser."""
    return {
        "mode": settings.session_mode,
        "profile": settings.browser_profile,
        "cdp_url": settings.cdp_url if str(settings.session_mode).lower() == "cdp" else "",
        "headless": bool(settings.headless),
        "home_url": settings.home_url,
    }


_IMPORT_STATE_KEY = "_notebooklm_session_imported"


def import_session(profile_dir: str, session_path: str) -> int:
    """Import cookies and localStorage from a session JSON file into a
    Playwright persistent profile directory.

    The session file must be the JSON produced by ``notebooklm-worker/scripts/export_session.py``
    (a ``storage_state()`` snapshot).  This function creates a temporary persistent
    context with the target profile, applies the stored state, and closes the
    context so the profile is written to disk.  After that the worker's normal
    ``launch_persistent_context`` picks up the session automatically.

    Returns 0 on success, 1 on failure.
    """
    from playwright.sync_api import sync_playwright

    if not os.path.isfile(session_path):
        LOGGER.error("Session file not found: %s", session_path)
        return 1

    with open(session_path, "r", encoding="utf-8") as fh:
        state = json.load(fh)

    cookies = state.get("cookies", [])
    origins = state.get("origins", [])

    os.makedirs(profile_dir, exist_ok=True)
    LOGGER.info(
        "Importing %d cookies from %d origins into %s",
        len(cookies), len(origins), profile_dir,
    )

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            # Apply cookies (Playwright's add_cookies accepts HttpOnly/Secure
            # cookies without enforcing __Secure- / __Host- prefix rules).
            if cookies:
                context.add_cookies(cookies)

            # Apply localStorage for each origin.
            for entry in origins:
                origin = entry.get("origin", "")
                items = entry.get("localStorage", [])
                if not origin or not items:
                    continue
                try:
                    page = context.new_page()
                    page.goto(origin, wait_until="domcontentloaded")
                    page.wait_for_timeout(1000)
                    for item in items:
                        try:
                            page.evaluate(
                                "localStorage.setItem(arg[0], arg[1])",
                                item["name"], item["value"],
                            )
                        except Exception as exc:  # noqa: BLE001
                            LOGGER.warning(
                                "localStorage set %r on %s: %s",
                                item.get("name", ""), origin, exc,
                            )
                    page.close()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Could not apply localStorage for %s: %s", origin, exc)

            # Mark the profile as imported so the runner can verify.
            try:
                page = context.new_page()
                page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                page.evaluate(
                    "localStorage.setItem(arg[0], '1')",
                    _IMPORT_STATE_KEY,
                )
                page.close()
            except Exception as exc:
                LOGGER.warning("Could not write import marker: %s", exc)

            context.close()  # flushes cookies + localStorage to disk
    except Exception as exc:
        LOGGER.error("Session import failed: %s", exc)
        return 1

    LOGGER.info("Session imported; the worker will reuse it on next start")
    return 0


def login(settings, wait_seconds: int = 600) -> int:
    """Open NotebookLM for a manual sign-in and save the session.

    In cdp mode the browser already belongs to the operator, so the helper
    only opens the page and reports the state. In persistent mode the page
    runs in this container; the operator signs in through the visible window.
    """
    wait_seconds = max(0, int(wait_seconds))
    with open_page(settings) as page:
        configure_page(page, settings)
        page.goto(settings.home_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        deadline = time.time() + wait_seconds
        last = ""
        while True:
            url = str(page.url or "")
            state = "signed in" if signed_in(page) else "waiting for sign-in"
            if state != last:
                print(f"NotebookLM: {state} ({url})", flush=True)
                last = state
            if state == "signed in" or time.time() >= deadline:
                break
            time.sleep(5)
        if last != "signed in":
            print(
                "Sign-in was not detected. Open the browser, sign in to "
                "https://notebooklm.google.com/, then rerun this command.",
                flush=True,
            )
            return 1
        if str(settings.session_mode).lower() == "persistent":
            print(
                "The profile is stored under "
                f"{settings.browser_profile}; the worker reuses it.",
                flush=True,
            )
        return 0
