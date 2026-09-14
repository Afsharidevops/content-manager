"""Publication ledger and target resolution.

One draft can go to several destinations, and each destination succeeds or
fails on its own: LinkedIn may accept a post while a messenger channel is
down. Every attempt is stored as a publication row so the operator can see
which target published, which one failed, and why.

Targets come from the draft itself (``targets``) or from the defaults in
``editorial-policy.yaml`` (``publishing.targets``). A target is written as
``platform_account`` - ``bale``, ``eitaa``, ``linkedin_personal``,
``linkedin_company`` - and may also be given as a mapping with ``platform``
and ``account`` keys. Targets name the automatic channel adapters; Telegram
and Instagram keep their own approve-time publishing paths.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_target(value) -> str:
    """Return one target key out of a string or a mapping."""
    if isinstance(value, Mapping):
        platform = str(value.get("platform") or "").strip().lower()
        account = str(value.get("account") or "").strip().lower()
        if not platform:
            return ""
        return f"{platform}_{account}" if account else platform
    return str(value or "").strip().lower()


def resolve_targets(record: Mapping, defaults: Sequence = ()) -> list[str]:
    """Return the ordered, unique targets for one draft."""
    raw = record.get("targets") if isinstance(record.get("targets"), list) else []
    values = raw or list(defaults or [])
    targets: list[str] = []
    for value in values:
        target = normalize_target(value)
        if target and target not in targets:
            targets.append(target)
    return targets


def publication_defaults(policy: Mapping | None) -> list[str]:
    """Return the publishing targets configured in the editorial policy."""
    section = (policy or {}).get("publishing")
    if not isinstance(section, Mapping):
        return []
    values = section.get("targets")
    if isinstance(values, (str, Mapping)):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    return [target for target in (normalize_target(value) for value in values) if target]


@dataclass(frozen=True)
class Publication:
    """One publish attempt for one target."""

    content_id: str
    platform: str
    account: str
    status: str
    remote_id: str = ""
    error: str = ""
    published_at: str = ""
    id: str = ""

    @property
    def target(self) -> str:
        return f"{self.platform}_{self.account}" if self.account else self.platform

    def as_row(self) -> dict:
        return {
            "id": self.id or f"pub_{uuid.uuid4().hex[:12]}",
            "content_id": str(self.content_id or ""),
            "platform": str(self.platform or ""),
            "account": str(self.account or ""),
            "target": self.target,
            "status": str(self.status or ""),
            "remote_id": str(self.remote_id or ""),
            "published_at": self.published_at or _now(),
            "error": str(self.error or "")[:500],
        }


def build_publication(
    content_id: str,
    target: str,
    status: str,
    *,
    remote_id: str = "",
    error: str = "",
) -> Publication:
    """Build one publication row out of a target key."""
    platform, _, account = str(target or "").partition("_")
    return Publication(
        content_id=str(content_id or ""),
        platform=platform,
        account=account,
        status=status,
        remote_id=remote_id,
        error=error,
    )


def summarize_labels(rows: Iterable[Mapping], labels: Mapping[str, str] | None = None) -> str:
    """Return one human-readable line per publication row."""
    labels = labels or {}
    lines: list[str] = []
    for row in rows:
        target = str(row.get("target") or "")
        label = labels.get(target) or target or "target"
        status = str(row.get("status") or "")
        if status == STATUS_PUBLISHED:
            note = f"{label}: published"
            remote = str(row.get("remote_id") or "")
            if remote:
                note = f"{note} ({remote})"
        elif status == STATUS_SKIPPED:
            note = f"{label}: skipped"
        else:
            error = str(row.get("error") or "").strip()
            note = f"{label}: failed"
            if error:
                note = f"{note} - {error}"
        lines.append(note)
    return "\n".join(lines)
