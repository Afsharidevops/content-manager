"""Bidirectional-text helpers shared by the Telegram and Instagram renderers."""

from __future__ import annotations

import unicodedata

# Invisible right-to-left mark: forces an RTL base direction on a line whose
# first visible character is Latin (product names, numbers, punctuation) and
# otherwise scrambles Persian word order.
RTL_MARK = "\u200f"

_RTL_BIDI_TYPES = {"R", "AL"}
_LTR_BIDI_TYPES = {"L"}


def first_strong_direction(text: str) -> str:
    """Return "R" or "L" for the first strong bidi character, or ""."""
    for char in str(text or ""):
        bidi = unicodedata.bidirectional(char)
        if bidi in _RTL_BIDI_TYPES:
            return "R"
        if bidi in _LTR_BIDI_TYPES:
            return "L"
    return ""


def contains_rtl(text: str) -> bool:
    """True when the text has at least one right-to-left character."""
    return any(
        unicodedata.bidirectional(char) in _RTL_BIDI_TYPES
        for char in str(text or "")
    )


def needs_rtl_mark(line: str) -> bool:
    """True when a line mixes Persian with a Latin start and would render LTR."""
    text = str(line or "")
    if not text.strip():
        return False
    return contains_rtl(text) and first_strong_direction(text) == "L"


def mark_lines(text: str) -> str:
    """Prefix every line that needs it with an RTL mark, keeping blank lines."""
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        if not raw.strip():
            lines.append("")
            continue
        lines.append(f"{RTL_MARK}{raw}" if needs_rtl_mark(raw) else raw)
    return "\n".join(lines)


def mark_caption_head(text: str) -> str:
    """Force an RTL base direction on the head of a caption such as a title.

    Apps that render the account name inline before the caption (Instagram)
    start that paragraph with a Latin user name, which flips the whole first
    paragraph to LTR and scrambles a Persian title without an explicit mark.
    """
    value = str(text or "")
    if not contains_rtl(value) or value.startswith(RTL_MARK):
        return value
    return f"{RTL_MARK}{value}"
