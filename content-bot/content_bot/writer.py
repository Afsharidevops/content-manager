"""OpenAI-compatible chat writer that produces runtime Persian post copy."""

from __future__ import annotations

import json
import re

from content_bot.http import HttpError, request_json

SYSTEM_PROMPT = (
    "You write short educational posts for a technical Telegram channel. "
    "Reply with exactly one JSON object containing two keys: 'title' (at most "
    "120 characters) and 'body' (600 to 1400 characters). Write the title and "
    "the body in Persian (Farsi). Base the post only on the provided source, do "
    "not invent facts, do not use Markdown or hashtags, keep it a standalone "
    "post, and end the body with one short line attributing the source domain."
)


class WriterError(RuntimeError):
    pass


def _chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base.endswith("/chat/completions"):
        base = f"{base}/chat/completions"
    return base


class Writer:
    def __init__(self, base_url: str, api_key: str = "", model: str = "auto", timeout: int = 240):
        self.endpoint = _chat_endpoint(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _chat(self, messages: list[dict]) -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1600,
        }
        try:
            data = request_json(
                self.endpoint,
                payload=payload,
                timeout=self.timeout,
            )
        except HttpError as error:
            raise WriterError(f"writer HTTP {error.status}") from error
        except ConnectionError as error:
            raise WriterError(f"writer network error: {error}") from error
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise WriterError("writer returned no message content") from error
        return str(content)

    @staticmethod
    def _parse_json_object(content: str) -> dict | None:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(content[start : end + 1])
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def generate_post(self, item: dict) -> dict:
        """Return ``{"title", "body", "source_url"}`` for one approved item."""
        excerpt = str(item.get("text") or item.get("summary") or "")[:4000]
        source = {
            "title": item.get("title"),
            "url": item.get("url") or item.get("canonical_url"),
            "published_at": item.get("published_at"),
            "source": item.get("source"),
            "category": item.get("category"),
            "excerpt": excerpt,
        }
        user_message = json.dumps(source, ensure_ascii=True, indent=2)
        content = self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]
        )
        parsed = self._parse_json_object(content)
        if parsed and str(parsed.get("title", "")).strip() and str(parsed.get("body", "")).strip():
            title = str(parsed["title"]).strip()
            body = str(parsed["body"]).strip()
        else:
            title = str(item.get("title") or "").strip()[:120] or "Untitled"
            body = content.strip()
        return {
            "title": title,
            "body": body,
            "source_url": str(source["url"] or ""),
        }
