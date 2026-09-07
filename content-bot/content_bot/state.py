"""Small atomic JSON state store for drafts and daily publishing counters."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PUBLISHED_HISTORY_LIMIT = 300
CATEGORY_HISTORY_LIMIT = 20


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

    def is_known(self, content_hash: str) -> bool:
        return content_hash in set(self.load().get("published") or [])

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
