"""Runtime network fetch helpers (never exercised by unit tests)."""

from __future__ import annotations

from content_bot.http import HttpError, request_bytes

_HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_FEED_HEADERS = {
    "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5",
}


class FetchError(RuntimeError):
    pass


def fetch_page(url: str, *, timeout: int = 25) -> str:
    """Fetch one HTML page and decode it as text."""
    try:
        status, body = request_bytes(
            url,
            headers=_HTML_HEADERS,
            timeout=timeout,
            max_bytes=3_000_000,
        )
    except ConnectionError as error:
        raise FetchError(f"network error: {error}") from error
    if status >= 400:
        raise FetchError(f"HTTP {status}")
    return body.decode("utf-8", "replace")


def fetch_feed(url: str, *, timeout: int = 25) -> bytes:
    """Fetch one RSS/Atom feed body."""
    try:
        status, body = request_bytes(
            url,
            headers=_FEED_HEADERS,
            timeout=timeout,
            max_bytes=2_000_000,
        )
    except ConnectionError as error:
        raise FetchError(f"network error: {error}") from error
    if status >= 400:
        raise FetchError(f"HTTP {status}")
    return body
