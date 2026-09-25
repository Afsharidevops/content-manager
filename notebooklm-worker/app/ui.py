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
    "visual_style": ("Visual Style", "Style", "سبک بصری", "سبک دیداری", "سبک"),
    "generate_now": ("Generate now", "اکنون تولید کردن", "اکنون تولید"),
    "more": ("More", "More options", "بیشتر", "گزینههای بیشتر".replace("\x7f", "")),
    "download": ("Download", "دانلود", "بارگیری"),
    "sources": ("Sources", "منابع", "منبع"),
    "select_all": ("Select all", "انتخاب همه"),
}

STYLE_ALIASES: dict[str, tuple[str, ...]] = {
    "auto": ("auto", "default", "\u0627\u0646\u062a\u062e\u0627\u0628 \u062e\u0648\u062f\u06a9\u0627\u0631"),
    "classic": ("classic", "\u06a9\u0644\u0627\u0633\u06cc\u06a9", "\u06a9\u0644\u0627\u0633\u064a\u06a9"),
    "whiteboard": ("whiteboard", "white board", "\u062a\u062e\u062a\u0647 \u0633\u0641\u06cc\u062f", "\u062a\u062e\u062a\u0647\u200c\u0633\u0641\u06cc\u062f"),
    "kawaii": ("kawaii", "\u06a9\u0627\u0648\u0627\u06cc\u06cc"),
    "anime": ("anime", "\u0627\u0646\u06cc\u0645\u0647"),
    "watercolor": ("watercolor", "water color", "\u0622\u0628 \u0631\u0646\u06af", "\u0622\u0628\u200c\u0631\u0646\u06af"),
    "retro_print": ("retro print", "vintage print", "\u0686\u0627\u067e \u0633\u0628\u06a9 \u0642\u062f\u06cc\u0645"),
    "heritage": ("heritage", "\u0645\u06cc\u0631\u0627\u062b"),
    "paper_craft": ("paper craft", "papercraft", "\u06a9\u0627\u0631\u062f\u0633\u062a\u06cc \u06a9\u0627\u063a\u0630\u06cc"),
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
