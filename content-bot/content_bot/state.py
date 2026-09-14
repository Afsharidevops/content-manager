"""Small atomic JSON state store for drafts and daily publishing counters."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PUBLISHED_HISTORY_LIMIT = 300
PACKAGE_ARCHIVE_LIMIT = 25
CATEGORY_HISTORY_LIMIT = 20
MEMORY_LESSONS_LIMIT = 30
PUBLICATIONS_LIMIT = 400
CONTENT_VARIANTS_LIMIT = 400


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict | None = None

    @staticmethod
    def _defaults() -> dict:
        return {
            "drafts": {},
            "published": [],
            "day": "",
            "published_today": 0,
            "daily_last_run": "",
            "last_categories": [],
        }

    def load(self) -> dict:
        if self._data is not None:
            return self._data
        data = self._defaults()
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                data.update(loaded)
        self._data = data
        return data

    def save(self) -> None:
        data = self.load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            dir=self.path.parent,
            prefix=".state.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            handle.close()
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    def reset_day(self, day: str) -> dict:
        data = self.load()
        if data.get("day") != day:
            data["day"] = day
            data["published_today"] = 0
        return data

    def add_draft(self, draft_id: str, payload: dict) -> None:
        data = self.load()
        drafts = data.setdefault("drafts", {})
        drafts[draft_id] = payload
        self.save()

    def update_draft(self, draft_id: str, payload: dict) -> None:
        data = self.load()
        drafts = data.setdefault("drafts", {})
        if draft_id not in drafts:
            raise KeyError(draft_id)
        drafts[draft_id].update(payload)
        self.save()

    def draft_for_message(self, chat_id, message_id: int) -> dict | None:
        """Return the pending draft attached to one chat message, if any."""
        for draft in self.load().get("drafts", {}).values():
            if (
                isinstance(draft, dict)
                and draft.get("chat_id") == chat_id
                and int(draft.get("message_id") or -1) == int(message_id)
            ):
                return dict(draft)
        return None

    def get_draft(self, draft_id: str) -> dict | None:
        draft = self.load().get("drafts", {}).get(draft_id)
        return dict(draft) if isinstance(draft, dict) else None

    def drop_draft(self, draft_id: str) -> None:
        data = self.load()
        drafts = data.setdefault("drafts", {})
        if draft_id in drafts:
            del drafts[draft_id]
            self.save()

    def archive_package(self, record: dict) -> None:
        """Keep a slim copy of one published draft for later upload packages."""
        draft_id = str(record.get("id") or "")
        if not draft_id:
            return
        data = self.load()
        archive = data.setdefault("package_archive", {})
        archive.pop(draft_id, None)
        archive[draft_id] = {
            "id": draft_id,
            "chat_id": record.get("chat_id"),
            "title": record.get("title") or "",
            "body": record.get("body") or "",
            "source_url": record.get("source_url") or "",
            "category": record.get("category") or "",
            "kind": record.get("kind") or "",
            "media": record.get("media") or {},
            "published_at": record.get("published_at") or "",
        }
        while len(archive) > PACKAGE_ARCHIVE_LIMIT:
            oldest = next(iter(archive))
            archive.pop(oldest, None)
        self.save()

    def get_archived_package(self, draft_id: str) -> dict | None:
        """Return the archived copy kept for a published draft, if any."""
        item = (self.load().get("package_archive") or {}).get(str(draft_id or ""))
        return dict(item) if isinstance(item, dict) else None

    def is_known(self, content_hash: str) -> bool:
        return content_hash in set(self.load().get("published") or [])

    def forget_published(self, content_hash: str) -> bool:
        """Remove one published link hash so the same link can be posted again."""
        data = self.load()
        published = data.get("published") or []
        if content_hash not in published:
            return False
        data["published"] = [item for item in published if item != content_hash]
        self.save()
        return True

    def remember_published(
        self,
        content_hash: str,
        category: str = "",
        day: str = "",
        *,
        count_toward_limit: bool = True,
    ) -> None:
        data = self.reset_day(day)
        published = data.setdefault("published", [])
        if content_hash not in published:
            published.append(content_hash)
            data["published"] = published[-PUBLISHED_HISTORY_LIMIT:]
        if count_toward_limit:
            data["published_today"] = int(data.get("published_today", 0)) + 1
        if category:
            history = data.setdefault("last_categories", [])
            history.append(category)
            data["last_categories"] = history[-CATEGORY_HISTORY_LIMIT:]
        self.save()

    def add_lesson(self, lesson: str) -> None:
        """Store one owner feedback lesson for future copy guidance."""
        lesson = " ".join(str(lesson or "").split())
        if not lesson:
            return
        data = self.load()
        lessons = data.setdefault("memory", {}).setdefault("lessons", [])
        if lesson not in lessons:
            lessons.append(lesson)
            data["memory"]["lessons"] = lessons[-MEMORY_LESSONS_LIMIT:]
            self.save()

    def lessons(self, limit: int = 6) -> list[str]:
        """Return the most recent owner feedback lessons."""
        data = self.load()
        lessons = (data.get("memory") or {}).get("lessons") or []
        return [str(lesson) for lesson in lessons[-max(1, int(limit)):]]

    def add_publication(self, row: dict) -> dict:
        """Append one publication row and return the stored copy."""
        record = dict(row or {})
        if not record.get("id"):
            record["id"] = f"pub_{os.urandom(6).hex()}"
        data = self.load()
        rows = data.setdefault("publications", [])
        rows.append(record)
        data["publications"] = rows[-PUBLICATIONS_LIMIT:]
        self.save()
        return record

    def publications_for(self, content_id: str) -> list[dict]:
        """Return every publication row stored for one draft."""
        wanted = str(content_id or "")
        rows = self.load().get("publications") or []
        return [
            dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("content_id") or "") == wanted
        ]

    def add_variant(self, row: dict) -> dict:
        """Store one adapted text variant for a draft and target."""
        record = dict(row or {})
        if not record.get("id"):
            record["id"] = f"var_{os.urandom(6).hex()}"
        data = self.load()
        rows = data.setdefault("content_variants", [])
        rows.append(record)
        data["content_variants"] = rows[-CONTENT_VARIANTS_LIMIT:]
        self.save()
        return record

    def variants_for(self, content_id: str) -> list[dict]:
        """Return every stored variant of one draft."""
        wanted = str(content_id or "")
        rows = self.load().get("content_variants") or []
        return [
            dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("content_id") or "") == wanted
        ]

    def variant_for(self, content_id: str, target: str) -> dict | None:
        """Return the newest stored variant of one draft and target."""
        wanted = str(target or "")
        for row in reversed(self.variants_for(content_id)):
            if str(row.get("target") or "") == wanted:
                return row
        return None
