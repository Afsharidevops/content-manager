"""Offline HTML article extraction and RSS/Atom parsing."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin

ATOM_NS = "http://www.w3.org/2005/Atom"
_SKIP_TAGS = {"script", "style", "noscript", "template", "iframe", "svg", "form"}


class _ArticleTextExtractor(HTMLParser):
    def __init__(self, container: str):
        super().__init__(convert_charrefs=True)
        self.container = container
        self.container_depth = 0
        self.skip_depth = 0
        self.title = ""
        self.description = ""
        self.image = ""
        self.published_at = ""
        self.author = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attributes = dict(attrs)
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
        elif tag == self.container and self.container_depth == 0:
            self.container_depth = 1
        if tag == "meta":
            prop = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "")
            if prop in {"og:title", "twitter:title", "title"} and not self.title:
                self.title = content
            elif prop in {"og:description", "twitter:description", "description"} and not self.description:
                self.description = content
            elif prop in {"og:image", "twitter:image"} and not self.image:
                self.image = content
            elif prop in {"article:published_time", "datepublished"} and not self.published_at:
                self.published_at = content
            elif prop == "author" and not self.author:
                self.author = content
        elif tag == "time" and not self.published_at:
            self.published_at = attributes.get("datetime", "")

    def handle_endtag(self, tag: str):
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag == self.container:
            self.container_depth = max(0, self.container_depth - 1)

    def handle_data(self, data: str):
        if self.container_depth > 0 and self.skip_depth == 0:
            self._parts.append(data)

    def clean_text(self, limit: int = 6000) -> str:
        text = re.sub(r"\s+", " ", " ".join(self._parts)).strip()
        return text[:limit]


def _pick_container(html_text: str) -> str:
    lowered = html_text.lower()
    for tag in ("article", "main", "body"):
        if re.search(rf"<{tag}[\s>]", lowered):
            return tag
    return "body"


def extract_article(html_text: str, base_url: str = "") -> dict:
    """Extract a plain-text article snapshot plus metadata from one HTML page."""
    container = _pick_container(html_text)
    parser = _ArticleTextExtractor(container)
    try:
        parser.feed(html_text)
    except Exception:
        pass
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    fallback_title = html.unescape(title_match.group(1)).strip() if title_match else ""
    image = urljoin(base_url, parser.image) if base_url else parser.image
    return {
        "title": parser.title or fallback_title,
        "description": parser.description,
        "image": image,
        "published_at": parser.published_at,
        "author": parser.author,
        "text": parser.clean_text(),
    }


def _text(element, path: str, default: str = "") -> str:
    node = element.find(path)
    if node is None or node.text is None:
        return default
    return " ".join(node.text.split())


def _absolute(base_url: str, url: str) -> str:
    return urljoin(base_url, (url or "").strip())


def parse_rss(source_name: str, source_url: str, content: bytes | str) -> list[dict]:
    """Parse RSS 2.0 or Atom XML into raw pipeline item dicts."""
    if isinstance(content, bytes):
        text = content.decode("utf-8", "replace")
    else:
        text = content
    root = ET.fromstring(text)
    items: list[dict] = []
    if root.tag.lower().endswith("rss"):
        for entry in root.findall("./channel/item"):
            link = _text(entry, "link")
            title = _text(entry, "title")
            if not title or not link:
                continue
            tags = [cat.text for cat in entry.findall("category") if cat.text]
            items.append(
                {
                    "source": source_name,
                    "source_url": source_url,
                    "title": title,
                    "url": _absolute(source_url, link),
                    "published_at": _text(entry, "pubDate") or _text(entry, "date"),
                    "summary": _text(entry, "description"),
                    "category": "",
                    "tags": tags,
                }
            )
    else:
        for entry in root.findall(f"{{{ATOM_NS}}}entry"):
            link = ""
            for node in entry.findall(f"{{{ATOM_NS}}}link"):
                if (node.get("rel") or "alternate") == "alternate":
                    link = node.get("href", "")
                    break
            title = _text(entry, f"{{{ATOM_NS}}}title")
            if not title or not link:
                continue
            published = (
                _text(entry, f"{{{ATOM_NS}}}published")
                or _text(entry, f"{{{ATOM_NS}}}updated")
            )
            tags = [cat.text for cat in entry.findall(f"{{{ATOM_NS}}}category") if cat.text]
            items.append(
                {
                    "source": source_name,
                    "source_url": source_url,
                    "title": title,
                    "url": _absolute(source_url, link),
                    "published_at": published,
                    "summary": _text(entry, f"{{{ATOM_NS}}}summary"),
                    "category": "",
                    "tags": tags,
                }
            )
    return items
