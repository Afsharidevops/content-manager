"""Deterministic URL/title normalization for discovered content items.

Every item entering the pipeline is a plain dict.  Normalization is pure and
stateless: it never touches the network, the database, or any external state.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only exist to track a marketing or click campaign.
# Prefixes are matched case-insensitively (UTM_SOURCE == utm_source).
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "gbraid",
        "wbraid",
        "yclid",
        "_ga",
        "_gl",
        "_hsenc",
        "_hsmi",
        "twclid",
        "li_fat_id",
        "dclid",
        "s_cid",
    }
)

# Arabic letter forms that differ from their Persian equivalents are unified so
# that the same title spelled either way produces one canonical form.
_SCRIPT_ALIASES = str.maketrans(
    {
        "\u064a": "\u06cc",  # arabic yeh        -> persian yeh
        "\u0643": "\u06a9",  # arabic kaf        -> persian keheh
        "\u0623": "\u0627",  # alef hamza above  -> alef
        "\u0625": "\u0627",  # alef hamza below  -> alef
        "\u0622": "\u0627",  # alef madda        -> alef
        "\u0629": "\u0647",  # teh marbuta       -> heh
        "\u0624": "\u0648",  # waw hamza         -> waw
        "\u0626": "\u06cc",  # yeh hamza         -> persian yeh
    }
)

_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)


def canonicalize_url(url: str, *, drop_params=None, strip_fragment: bool = True) -> str:
    """Return a canonical form of ``url`` for exact-dedupe comparisons.

    - lowercases scheme and host;
    - strips a leading ``www.`` and default ports;
    - drops known tracking parameters and re-sorts the remaining ones;
    - removes the fragment and guarantees a non-empty path.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if strip_fragment:
        url = url.split("#", 1)[0]
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None:
        default_port = {"http": 80, "https": 443}.get(scheme)
        if port != default_port:
            host = f"{host}:{port}"

    path = parts.path or "/"

    drop = TRACKING_PARAMS if drop_params is None else set(drop_params)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in drop
        and not any(key.lower().startswith(prefix) for prefix in TRACKING_PARAM_PREFIXES)
    ]
    query.sort()
    encoded_query = urlencode(query)

    return urlunsplit((scheme, host, path, encoded_query, ""))


def normalize_title(title: str) -> str:
    """Return a canonical, comparable form of a title.

    Applies Unicode NFC, unifies Arabic letter forms to Persian, folds to
    lowercase, drops punctuation, and collapses whitespace.  Two titles that
    describe the same item normalize to the same string.
    """
    text = unicodedata.normalize("NFC", title or "")
    text = text.translate(_SCRIPT_ALIASES)
    text = _NON_WORD.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip().casefold()
    return text


def content_hash(canonical_url: str, title_norm: str = "") -> str:
    """Deterministic identity for one item across sources."""
    material = canonical_url or title_norm or ""
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_item(item: dict) -> dict:
    """Return a shallow copy of ``item`` enriched with canonical fields.

    Adds ``canonical_url``, ``title_norm`` and ``content_hash``.  The original
    dict is never mutated.
    """
    out = dict(item)
    url = str(item.get("url") or item.get("link") or "")
    out["canonical_url"] = canonicalize_url(url)
    out["title_norm"] = normalize_title(item.get("title") or "")
    out["content_hash"] = content_hash(out["canonical_url"], out["title_norm"])
    return out


def normalize_items(items) -> list:
    return [normalize_item(item) for item in items]
