"""Topic search used when a link has little text or no link is sent.

The default provider is DuckDuckGo's HTML endpoint, which needs no API key
and works from any region. Every network call goes through an injectable
fetch function so unit tests stay offline.
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from content_bot.http import HttpError, request_bytes

SEARCH_URL = "https://html.duckduckgo.com/html/"
_RESULT_ANCHOR = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_ANCHOR = re.compile(
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIP = re.compile(r"<[^>]+>")


class SearchError(RuntimeError):
    pass


def _decode_target(href: str, base: str) -> str:
    """Resolve DuckDuckGo redirect links to the real destination URL."""
    href = html_mod.unescape(href or "").strip()
    parsed = urlsplit(href)
    if "duckduckgo.com" in (parsed.hostname or ""):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if not parsed.scheme and not parsed.netloc:
        return urljoin(base, href)
    return href


def _strip_tags(fragment: str) -> str:
    fragment = html_mod.unescape(_TAG_STRIP.sub(" ", fragment or ""))
    return " ".join(fragment.split())


def parse_results(body: str, base: str = SEARCH_URL) -> list[dict]:
    """Parse one DuckDuckGo HTML results page into search result records."""
    results: list[dict] = []
    anchors = _RESULT_ANCHOR.findall(body)
    snippets = _SNIPPET_ANCHOR.findall(body)
    for index, (href, title) in enumerate(anchors):
        title_text = _strip_tags(title)
        if not title_text:
            continue
        url = _decode_target(href, base)
        if not url.startswith(("http://", "https://")):
            continue
        snippet = _strip_tags(snippets[index]) if index < len(snippets) else ""
        results.append({"title": title_text, "url": url, "snippet": snippet})
        if len(results) >= 40:
            break
    return results


def _fetch_html(query: str, *, timeout: int) -> str:
    from urllib.parse import urlencode

    url = f"{SEARCH_URL}?{urlencode({'q': query})}"
    try:
        status, body = request_bytes(
            url,
            method="GET",
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
            max_bytes=1_500_000,
        )
    except ConnectionError as error:
        raise SearchError(f"search network error: {error}") from error
    if status >= 400:
        raise SearchError(f"search returned HTTP {status}")
    return body.decode("utf-8", "replace")


def search_topic(
    query: str,
    *,
    limit: int = 5,
    timeout: int = 25,
    fetch_html=None,
) -> list[dict]:
    """Search for one topic and return up to ``limit`` result records."""
    query = (query or "").strip()
    if len(query) < 3:
        raise SearchError("query is too short")
    fetcher = fetch_html or (lambda q: _fetch_html(q, timeout=timeout))
    try:
        body = fetcher(query)
    except SearchError:
        raise
    except Exception as error:  # noqa: BLE001
        raise SearchError(f"search failed: {error}") from error
    results = [item for item in parse_results(body) if item.get("snippet") or item.get("title")]
    return results[: max(1, int(limit))]
