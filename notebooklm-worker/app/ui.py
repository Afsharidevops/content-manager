"""Shared NotebookLM UI labels and normalization helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata


UI_LABELS: dict[str, tuple[str, ...]] = {
    "fast_research": ("Fast Research", "تحقیق سریع"),
    "insert": ("Insert", "درج کردن", "درج"),
    "video_overview": ("Video Overview", "مرور ویدیویی"),
    "customize": ("Customize", "سفارشیسازی".replace("\x7f", "")),
    "language": ("Language", "زبان"),
    "persian": ("Persian", "فارسی"),
    "format": ("Format", "فرمت", "قالب"),
    "explainer": ("Explainer", "توضیحدهنده".replace("\x7f", "")),
    "visual_style": ("Visual Style", "Style", "سبک بصری", "سبک"),
    "generate_now": ("Generate now", "اکنون تولید کردن", "اکنون تولید"),
    "more": ("More", "More options", "بیشتر", "گزینههای بیشتر".replace("\x7f", "")),
    "download": ("Download", "دانلود", "بارگیری"),
    "sources": ("Sources", "منابع", "منبع"),
    "select_all": ("Select all", "انتخاب همه"),
}

STYLE_ALIASES: dict[str, tuple[str, ...]] = {
    "anime": ("anime", "انیمه"),
}


def labels(key: str) -> tuple[str, ...]:
    """Return the Persian and English labels for one UI concept."""
    return UI_LABELS.get(key, ())


def normalize_label(value: str) -> str:
    """Normalize text for case-insensitive UI matching and filenames."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u200c", " ").replace("\u200f", " ")
    text = text.replace("ي", "ی").replace("ك", "ک")
    return " ".join(text.casefold().split())


def matches_label(value: str, candidates: tuple[str, ...] | list[str]) -> bool:
    """Return whether a visible/accessibility label matches a UI label."""
    actual = normalize_label(value)
    return bool(actual) and any(actual == normalize_label(candidate) for candidate in candidates)


def style_aliases(style_name: str) -> tuple[str, ...]:
    """Return known aliases while keeping unknown styles fully dynamic."""
    normalized = normalize_label(style_name)
    return STYLE_ALIASES.get(normalized, (normalized,))


def style_matches(requested: str, *visible_values: str) -> bool:
    """Match a requested configured style to a discovered UI card."""
    requested_values = {normalize_label(value) for value in style_aliases(requested)}
    actual_values = {normalize_label(value) for value in visible_values if value}
    return bool(requested_values & actual_values)


def safe_style_slug(label: str) -> str:
    """Return a collision-resistant filename component for a discovered style."""
    normalized = normalize_label(label)
    ascii_label = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_label).strip("-")
    if slug:
        return slug[:64]
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"style-{digest}"
