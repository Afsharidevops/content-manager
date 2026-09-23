"""Smart Router assisted recovery for NotebookLM browser automation."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

LOGGER = logging.getLogger("notebooklm.recovery")


def page_state(page: Any) -> dict[str, Any]:
    """Return a compact, serializable description of the current browser page."""
    state: dict[str, Any] = {"url": "", "title": "", "text": "", "elements": []}
    try:
        state["url"] = str(page.url or "")
    except Exception:
        pass
    try:
        state["title"] = str(page.title() or "")[:300]
    except Exception:
        pass
    try:
        state["text"] = str(page.locator("body").inner_text(timeout=1500) or "")[:4000]
    except Exception:
        pass
    try:
        elements = page.evaluate(
            """() => Array.from(document.querySelectorAll('button, [role="button"], [role="dialog"], [aria-modal="true"], input, textarea, [contenteditable="true"], a'))
              .slice(0, 80)
              .map((el) => ({
                tag: el.tagName,
                role: el.getAttribute('role') || '',
                text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || '').trim().slice(0, 160),
                visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
              }))"""
        )
        if isinstance(elements, list):
            state["elements"] = elements
    except Exception:
        pass
    return state


class RecoveryClient:
    def __init__(self, settings: Any) -> None:
        self.enabled = bool(getattr(settings, "recovery_enabled", True))
        self.url = str(getattr(settings, "recovery_url", "") or "").rstrip("/")
        self.api_key = str(getattr(settings, "recovery_api_key", "") or "")
        self.timeout = max(1, int(getattr(settings, "recovery_timeout_seconds", 20) or 20))

    def decide(self, *, step: str, attempt: int, error: Exception, page: Any) -> dict[str, Any] | None:
        if not self.enabled or not self.url:
            return None
        payload = {
            "step": step,
            "attempt": attempt,
            "error": f"{type(error).__name__}: {error}",
            "url": getattr(page, "url", ""),
            "page_state": page_state(page),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("Recovery decision request failed: %s", exc)
            return None
        decision = parsed.get("decision") if isinstance(parsed, dict) else None
        return decision if isinstance(decision, dict) else None

    def apply(self, page: Any, decision: dict[str, Any]) -> str:
        action = str(decision.get("action") or "abort").strip().lower()
        if action == "wait":
            wait_seconds = max(1, min(30, int(decision.get("wait_seconds") or 3)))
            time.sleep(wait_seconds)
            return "retry"
        if action == "dismiss_overlay":
            _dismiss_overlay(page, str(decision.get("selector_hint") or ""))
            return "retry"
        if action in {"retry", "switch_flow", "abort"}:
            return action
        return "abort"


def _dismiss_overlay(page: Any, selector_hint: str = "") -> None:
    if selector_hint:
        try:
            locator = page.locator(selector_hint).first
            if locator.count() > 0:
                locator.click(timeout=1500)
                return
        except Exception:
            pass
    for selector in (
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
        'button[aria-label="Close"]',
        '[role="dialog"] button',
    ):
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=1500)
                return
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
