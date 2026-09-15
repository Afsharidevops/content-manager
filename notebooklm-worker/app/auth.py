"""Auto sign-in with Google credentials using Playwright.

Implements the full Google OAuth flow that NotebookLM uses, with
optional TOTP support and CAPTCHA/2FA detection.
"""

from __future__ import annotations

import hmac
import base64
import logging
import re
import struct
import time

LOGGER = logging.getLogger("notebooklm.auth")

# --------------- TOTP helper (no pyotp needed) ---------------

def _totp(secret: str, interval: int = 30) -> str:
    """Generate a TOTP code from a base32-encoded secret (RFC 6238)."""
    key = base64.b32decode(secret.upper())
    msg = struct.pack(">Q", int(time.time()) // interval)
    digest = hmac.new(key, msg, "sha1").digest()
    offset = digest[-1] & 0xf
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated)[-6:].zfill(6)


# --------------- URL / page detectors ---------------

_ACCOUNTS_RE = re.compile(r"accounts\.google\.com", re.IGNORECASE)
_SIGNIN_RE = re.compile(r"signin|SignIn|ServiceLogin", re.IGNORECASE)
_CAPTCHA_RE = re.compile(r"captcha|recaptcha|unusual.traffic|challenge", re.IGNORECASE)
_TOTP_RE = re.compile(r"2sv|two-factor|verification.code|authenticator", re.IGNORECASE)

def _needs_signin(page) -> bool:
    url = str(page.url or "")
    return bool(_ACCOUNTS_RE.search(url) or _SIGNIN_RE.search(url))

def _has_captcha(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=2000)
        return bool(_CAPTCHA_RE.search(text))
    except Exception:
        return False

def _has_totp(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=2000)
        return bool(_TOTP_RE.search(text))
    except Exception:
        return False

def _has_totp_input(page) -> bool:
    selectors = [
        "input[type='tel']",
        "input[autocomplete='one-time-code']",
        "input[aria-label*='code' i]",
        "input[aria-label*='verification' i]",
        "input[aria-label*='authenticator' i]",
        "input[name*= 'totp' i]",
        "input[name*= '2fa' i]",
    ]
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False

# --------------- Sign-in flow ---------------

def login_with_credentials(
    page,
    email: str,
    password: str,
    totp_secret: str = "",
    interactive: bool = False,
) -> int:
    """Automate Google sign-in with the given credentials.

    Returns 0 on success, 1 on manual intervention required, 2 on hard failure.
    """
    # --- Step 0: wait a moment for redirects ---
    time.sleep(3)

    # Already signed in?
    url = str(page.url or "")
    if "notebooklm.google.com" in url:
        LOGGER.info("Appears already signed in to NotebookLM")
        return 0

    if not _needs_signin(page):
        LOGGER.info("No sign-in page detected (url=%s), assuming signed in", url)
        return 0

    # --- Step 1: email ---
    LOGGER.info("Entering email…")
    email_sel = page.locator(
        "input[type='email'], input[name='identifier'], "
        "input[autocomplete='username'], input[aria-label*='email' i]"
    ).first
    if email_sel.is_visible(timeout=8000):
        email_sel.click()
        time.sleep(0.3)
        page.keyboard.type(email, delay=80)
        page.keyboard.press("Enter")
        LOGGER.info("Submitted email, waiting for password field…")
    else:
        LOGGER.warning("Email field not found, trying to continue…")

    time.sleep(3)

    # --- Step 2: password ---
    pwd_sel = page.locator(
        "input[type='password'], input[name='Passwd'], "
        "input[autocomplete='current-password']"
    ).first
    if pwd_sel.is_visible(timeout=10000):
        pwd_sel.click()
        time.sleep(0.3)
        page.keyboard.type(password, delay=60)
        page.keyboard.press("Enter")
        LOGGER.info("Submitted password")
    else:
        # Maybe already past the password (saved session?)
        LOGGER.info("No password field; may already be past that step")

    time.sleep(4)

    # --- Step 3: 2FA / TOTP ---
    if _has_totp_input(page):
        LOGGER.info("2FA code input detected")
        totp_code = ""
        if totp_secret:
            totp_code = _totp(totp_secret)
            LOGGER.info("Generated TOTP code from secret")
        elif interactive:
            totp_code = input("Enter 2FA code from authenticator app: ").strip()
        else:
            LOGGER.warning("2FA required but no TOTP secret configured")
            return 1  # manual intervention needed

        if totp_code:
            code_sel = page.locator(
                "input[type='tel'], input[autocomplete='one-time-code'], "
                "input[aria-label*='code' i]"
            ).first
            if code_sel.is_visible(timeout=3000):
                code_sel.click()
                time.sleep(0.2)
                page.keyboard.type(totp_code, delay=60)
                page.keyboard.press("Enter")
                LOGGER.info("Submitted 2FA code")
                time.sleep(4)

    # --- Step 4: check result ---
    if _has_captcha(page):
        LOGGER.warning("CAPTCHA challenge detected – manual intervention needed")
        print("Google requires CAPTCHA verification. Open the browser and complete it.", flush=True)
        return 1

    final_url = str(page.url or "")
    if "notebooklm.google.com" in final_url and "/" in final_url.split("//", 1)[-1]:
        LOGGER.info("Sign-in successful (url=%s)", final_url)
        return 0

    if "myaccount" in final_url or "signin" in final_url:
        LOGGER.warning("Sign-in may need more steps (url=%s)", final_url)
        return 1

    LOGGER.info("Sign-in flow completed (url=%s)", final_url)
    return 0


def verify_session(page) -> bool:
    """Check whether the persistent context has a valid NotebookLM session."""
    from app.browser import signed_in
    try:
        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        return signed_in(page)
    except Exception as exc:
        LOGGER.warning("Session verification failed: %s", exc)
        return False
