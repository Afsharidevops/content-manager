"""Content adaptation layer: one draft, one voice per destination.

A draft is written once and then adapted for every target before it is
published. Tone profiles describe how each destination should read (a personal
LinkedIn profile is first person and opinionated, a company page is neutral
and educational, a messenger channel is short and news-shaped) and the prompt
that drives the rewrite lives in a template.

Templates ship with the bot and can be replaced per deployment: a file named
``<tone>.md`` inside ``prompt-templates/`` in the policy directory wins over
the built-in text. Adaptation never blocks a publish - when it is disabled,
when no writer is configured, or when the rewrite fails, the original draft
body is used unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from content_bot.accounts import ORGANIZATION

log = logging.getLogger("content_bot")

TEMPLATE_DIR = "prompt-templates"
DEFAULT_MAX_CHARS = 2800
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


@dataclass(frozen=True)
class ToneProfile:
    """How one destination should read."""

    key: str
    label: str = ""
    style: tuple[str, ...] = ()
    max_chars: int = DEFAULT_MAX_CHARS
    template: str = ""
    enabled: bool = True

    @property
    def template_key(self) -> str:
        return self.template or self.key

    @property
    def style_line(self) -> str:
        return ", ".join(self.style) if self.style else "clear and factual"


DEFAULT_TONES: dict[str, ToneProfile] = {
    "linkedin_personal": ToneProfile(
        key="linkedin_personal",
        label="LinkedIn (personal profile)",
        style=("first_person", "technical", "opinionated", "founder_voice"),
    ),
    "linkedin_company": ToneProfile(
        key="linkedin_company",
        label="LinkedIn (company page)",
        style=("educational", "neutral", "brand_voice"),
    ),
    "telegram": ToneProfile(
        key="telegram",
        label="Telegram channel",
        style=("concise", "news"),
        max_chars=3500,
    ),
}

DEFAULT_TEMPLATES: dict[str, str] = {
    "linkedin_personal": (
        "You rewrite one approved draft as a LinkedIn post for a personal profile.\n"
        "Voice: {style}.\n"
        "Write in the first person and keep the author's own opinion, stay under "
        "{max_chars} characters, and keep every fact of the draft.\n"
        "Never invent numbers, names, quotes, or results that are not in the draft.\n"
        "Return only the post text: no title line, no markdown headings, and no "
        "commentary about the rewrite."
    ),
    "linkedin_company": (
        "You rewrite one approved draft as a LinkedIn post for a company page.\n"
        "Voice: {style}.\n"
        "Write for the brand, keep it educational and neutral, stay under "
        "{max_chars} characters, and keep every fact of the draft.\n"
        "Never invent numbers, names, quotes, or results that are not in the draft.\n"
        "Return only the post text: no title line, no markdown headings, and no "
        "commentary about the rewrite."
    ),
    "telegram": (
        "You rewrite one approved draft as a Telegram channel post.\n"
        "Voice: {style}.\n"
        "Stay under {max_chars} characters, lead with the news, and keep every fact "
        "of the draft.\n"
        "Never invent numbers, names, quotes, or results that are not in the draft.\n"
        "Return only the post text: no title line and no commentary about the rewrite."
    ),
}


def tone_for_target(
    target: str,
    *,
    kind: str = "",
    tones: dict[str, ToneProfile] | None = None,
) -> ToneProfile:
    """Return the tone profile for one target key.

    A LinkedIn target adapts through the company tone when the account is an
    organization and through the personal tone otherwise; every other platform
    falls back to its platform-level tone.
    """
    tones = dict(tones or DEFAULT_TONES)
    key = str(target or "").strip().lower()
    if key in tones:
        return tones[key]
    platform = key.split("_", 1)[0]
    if platform == "linkedin":
        wanted = "linkedin_company" if str(kind).lower() == ORGANIZATION else "linkedin_personal"
        if wanted in tones:
            return tones[wanted]
    if platform in tones:
        return tones[platform]
    return ToneProfile(key=key or "default", style=("clear", "factual"))


def load_tones(policy: dict | None) -> dict[str, ToneProfile]:
    """Return the built-in tones with the policy overrides applied.

    The ``tones:`` section of ``editorial-policy.yaml`` may add a profile,
    override its style or limit, or remove one by setting it to null.
    """
    tones = dict(DEFAULT_TONES)
    section = (policy or {}).get("tones")
    if not isinstance(section, dict):
        return tones
    for raw_key, raw in section.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        if raw is None:
            tones.pop(key, None)
            continue
        if not isinstance(raw, dict):
            continue
        base = tones.get(key) or ToneProfile(key=key)
        style = raw.get("style")
        if isinstance(style, str):
            style = tuple(part.strip() for part in style.split(",") if part.strip())
        elif isinstance(style, (list, tuple)):
            style = tuple(str(part).strip() for part in style if str(part).strip())
        else:
            style = base.style
        try:
            max_chars = int(raw.get("max_chars", base.max_chars))
        except (TypeError, ValueError):
            max_chars = base.max_chars
        tones[key] = replace(
            base,
            label=str(raw.get("label") or base.label),
            style=style,
            max_chars=max_chars if max_chars > 0 else base.max_chars,
            template=str(raw.get("template") or base.template),
            enabled=bool(raw.get("enabled", base.enabled)),
        )
    return tones


def load_templates(prompt_dir: str | Path | None, tones: dict[str, ToneProfile] | None = None) -> dict[str, str]:
    """Return the prompt templates, deployment files overriding the defaults."""
    templates = dict(DEFAULT_TEMPLATES)
    keys = set(templates) | {tone.template_key for tone in (tones or {}).values()}
    if prompt_dir is None:
        return templates
    directory = Path(prompt_dir)
    if not directory.is_dir():
        return templates
    for key in keys:
        for suffix in (".md", ".txt"):
            path = directory / f"{key}{suffix}"
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError as error:
                log.warning("prompt template %s ignored: %s", path, error)
                continue
            if text:
                templates[key] = text
            break
    return templates


def _clean_output(text: str, limit: int) -> str:
    """Normalize one rewrite: drop fences, squeeze blank lines, cap length."""
    value = str(text or "").strip()
    value = _FENCE.sub("", value).strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    if limit > 0 and len(value) > limit:
        cut = value[:limit]
        boundary = cut.rfind("\n\n")
        if boundary >= limit // 2:
            cut = cut[:boundary]
        else:
            space = cut.rfind(" ")
            if space > 0:
                cut = cut[:space]
        value = cut.rstrip()
    return value


def fallback_body(record: dict) -> str:
    """Return the draft body that is published when no rewrite applies.

    Only the body is adapted: the title, the source link, and the media
    captions keep flowing through the existing publishing helpers, so a
    deployment without adaptation publishes exactly what it published before.
    """
    return str(record.get("body") or "").strip()


@dataclass
class ContentAdapter:
    """Rewrite one draft for one destination through the configured writer."""

    writer: object | None = None
    tones: dict[str, ToneProfile] = field(default_factory=lambda: dict(DEFAULT_TONES))
    templates: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TEMPLATES))
    enabled: bool = True
    max_tokens: int | None = None

    @classmethod
    def from_policy(
        cls,
        policy: dict | None,
        prompt_dir: str | Path | None,
        writer: object | None,
        *,
        enabled: bool = True,
    ) -> "ContentAdapter":
        tones = load_tones(policy)
        return cls(
            writer=writer,
            tones=tones,
            templates=load_templates(prompt_dir, tones),
            enabled=enabled,
        )

    def tone(self, target: str, *, kind: str = "") -> ToneProfile:
        return tone_for_target(target, kind=kind, tones=self.tones)

    def text_for(self, record: dict, target: str, *, kind: str = "") -> str:
        """Return the text to publish for one target (original text on failure)."""
        original = fallback_body(record)
        profile = self.tone(target, kind=kind)
        if not self.enabled or self.writer is None or not profile.enabled:
            return original
        template = self.templates.get(profile.template_key) or DEFAULT_TEMPLATES.get(
            profile.template_key
        )
        if not template:
            return original
        system = template.format(style=profile.style_line, max_chars=profile.max_chars)
        draft = {
            "title": str(record.get("title") or ""),
            "body": str(record.get("body") or ""),
            "source_url": str(record.get("source_url") or ""),
            "category": str(record.get("category") or ""),
        }
        try:
            raw = self.writer.chat(
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(draft, ensure_ascii=False, indent=2),
                    },
                ],
                max_tokens=self.max_tokens,
            )
        except Exception as error:  # writer failures must never block a publish
            log.warning("adaptation for %s failed: %s", target, error)
            return original
        text = _clean_output(raw, profile.max_chars)
        if not text:
            log.warning("adaptation for %s returned no text; using the draft", target)
            return original
        return text

    def variant(self, record: dict, target: str, *, kind: str = "") -> dict:
        """Return the variant row that is stored for one target."""
        original = fallback_body(record)
        text = self.text_for(record, target, kind=kind)
        profile = self.tone(target, kind=kind)
        platform, _, account = str(target or "").partition("_")
        adapted = text.strip() != original.strip()
        return {
            "platform": platform,
            "account": account,
            "tone": profile.key if adapted else "",
            "generated_text": text,
            "adapted": adapted,
        }
