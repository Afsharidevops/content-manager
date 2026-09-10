"""Copy-ready upload packages for platforms without a publishing API.

Telegram and Instagram publish through their APIs. Platforms such as YouTube
and Aparat need a human upload step, so the bot hands the operator a package
that can be pasted into the platform editor: a title that fits the platform
limit, a description with the source link and hashtags, the direct upload
URL, and the stored media file re-sent for a quick download.

Profiles are policy-driven. The `platforms:` section of
`editorial-policy.yaml` can override the built-in profiles, add new ones, or
remove a profile by setting it to null; no code change is required.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class PlatformProfile:
    """One platform that receives a manual upload package."""

    key: str
    label: str
    upload_url: str = ""
    title_limit: int = 100
    description_limit: int = 5000
    note: str = ""
    hashtags: tuple[str, ...] = ()
    wants_video: bool = False


DEFAULT_PROFILES: dict[str, PlatformProfile] = {
    "youtube": PlatformProfile(
        key="youtube",
        label="YouTube",
        upload_url="https://studio.youtube.com/",
        title_limit=100,
        description_limit=5000,
        note="Vertical clips publish as Shorts; keep the title under 100 characters.",
        wants_video=True,
    ),
    "aparat": PlatformProfile(
        key="aparat",
        label="Aparat",
        upload_url="https://www.aparat.com/upload",
        title_limit=100,
        description_limit=4000,
        note="Upload the video file and paste the title and description.",
        wants_video=True,
    ),
}


def _text(value, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _positive_int(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _hashtags(value, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return fallback
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        return fallback
    tags = []
    for part in parts:
        tag = " ".join(str(part).split())
        if not tag:
            continue
        tag = tag.lstrip("#")
        tag = "_".join(tag.split())
        if tag:
            tags.append(f"#{tag}")
    return tuple(tags)


def load_profiles(policy: dict) -> dict[str, PlatformProfile]:
    """Return the platform profiles after applying the policy overrides."""
    profiles = dict(DEFAULT_PROFILES)
    section = policy.get("platforms")
    if not isinstance(section, dict):
        return profiles
    for raw_key, raw in section.items():
        key = str(raw_key or "").strip().lower()
        if not key:
            continue
        if raw is None:
            profiles.pop(key, None)
            continue
        if not isinstance(raw, dict):
            continue
        base = profiles.get(key) or PlatformProfile(key=key, label=key.title())
        profiles[key] = replace(
            base,
            label=_text(raw.get("label"), base.label),
            upload_url=_text(raw.get("upload_url"), base.upload_url),
            title_limit=_positive_int(raw.get("title_limit"), base.title_limit),
            description_limit=_positive_int(
                raw.get("description_limit"), base.description_limit
            ),
            note=_text(raw.get("note"), base.note),
            hashtags=_hashtags(raw.get("hashtags"), base.hashtags),
            wants_video=bool(raw.get("wants_video", base.wants_video)),
        )
    return profiles


def has_video(record: dict) -> bool:
    """True when the draft carries a video file."""
    media = record.get("media") or {}
    if str(media.get("kind") or "") == "video":
        return True
    return any(
        str(item.get("kind") or "") == "video" for item in (media.get("files") or [])
    )


def stored_media(record: dict) -> list[tuple[str, str, str]]:
    """Return (kind, path, name) for every stored media file of the draft."""
    media = record.get("media") or {}
    files = list(media.get("files") or [])
    if files:
        entries = [
            (
                str(item.get("kind") or "image"),
                str(item.get("local_path") or ""),
                str(item.get("name") or ""),
            )
            for item in files
        ]
    elif str(media.get("kind") or "") in {"image", "video"}:
        entries = [
            (
                str(media.get("kind") or ""),
                str(media.get("local_path") or ""),
                "",
            )
        ]
    else:
        entries = []
    result = []
    for kind, path, name in entries:
        if not path:
            continue
        result.append((kind, path, name or Path(path).name))
    return result


def compose_title(record: dict, profile: PlatformProfile) -> tuple[str, bool]:
    """Return the platform title and whether it had to be shortened."""
    title = " ".join(str(record.get("title") or "").split())
    if len(title) <= profile.title_limit:
        return title, False
    keep = max(profile.title_limit - 3, 1)
    shortened = title[:keep].rstrip() + "..."
    return shortened, True


def compose_description(record: dict, profile: PlatformProfile) -> tuple[str, bool]:
    """Return the platform description and whether it had to be shortened."""
    body = str(record.get("body") or "").strip()
    parts = [body] if body else []
    source_url = str(record.get("source_url") or "").strip()
    if source_url and source_url not in body:
        parts.append(f"Source: {source_url}")
    if profile.hashtags:
        parts.append(" ".join(profile.hashtags))
    description = "\n\n".join(parts)
    limit = profile.description_limit
    if len(description) <= limit:
        return description, False
    shortened = description[:limit]
    boundary = shortened.rfind("\n\n")
    if boundary >= limit // 2:
        shortened = shortened[:boundary]
    return shortened.rstrip(), True


def package_text(record: dict, profile: PlatformProfile) -> str:
    """Render the copy-ready package as Telegram HTML."""
    title, title_shortened = compose_title(record, profile)
    description, description_shortened = compose_description(record, profile)
    lines = [
        f"<b>{escape(profile.label)} upload package</b>",
        "",
        "<b>Title</b>",
        f"<code>{escape(title)}</code>",
        "",
        "<b>Description</b>",
        f"<code>{escape(description)}</code>",
    ]
    if profile.upload_url:
        host = urlparse(profile.upload_url).netloc or profile.upload_url
        lines += [
            "",
            "<b>Upload</b> "
            f'<a href="{escape(profile.upload_url, quote=True)}">{escape(host)}</a>',
        ]
    if profile.note:
        lines += ["", f"<i>{escape(profile.note)}</i>"]
    if title_shortened or description_shortened:
        lines += ["", "<i>Text was shortened to fit the platform limits.</i>"]
    if profile.wants_video and not has_video(record):
        lines += ["", "<i>No video is attached to this draft yet.</i>"]
    return "\n".join(lines)
