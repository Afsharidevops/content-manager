"""OpenAI-compatible chat writer that produces runtime Persian post copy."""

from __future__ import annotations

import json
import re

from content_bot.http import HttpError, request_json

SYSTEM_PROMPT = (
    "You write short educational Telegram posts in Persian (Farsi) for a "
    "technical channel. Reply with exactly one JSON object with two keys: "
    "'title' (at most 120 characters) and 'body' (600 to 1400 characters). "
    "Write like a real person, with a warm, slightly informal tone, as if the "
    "channel owner wrote the post by hand. Avoid formal, machine-like, or "
    "news-style phrasing and empty filler. Base the post only on the content "
    "of the provided source article; never describe the input record itself "
    "or mention fields such as title, url, or excerpt. Do not invent facts. "
    "Do not use Markdown, hashtags, labels, quotes, or backslash characters. "
    "Keep paragraphs short and separate them with single blank lines. End the "
    "body with one final line containing only the full source URL."
)


REVISE_PROMPT = (
    "You revise an existing Persian (Farsi) Telegram post based on feedback "
    "from the channel owner. Reply with exactly one JSON object with two keys: "
    "'title' (at most 120 characters) and 'body' (600 to 1400 characters). "
    "Keep the warm, slightly informal tone of the current post and stay "
    "faithful to the same source article. Apply the feedback carefully; when "
    "it is vague, make a sensible improvement. Never mention the feedback, the "
    "revision process, or the source record inside the post. Do not invent "
    "facts. Do not use Markdown, hashtags, labels, quotes, or backslash "
    "characters. Keep paragraphs short and separate them with single blank "
    "lines. End the body with one final line containing only the full source "
    "URL."
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
            "stream": False,
            "temperature": 0.7,
            "max_tokens": 1600,
        }
        try:
            data = request_json(
                self.endpoint,
                payload=payload,
                headers=headers,
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

    @staticmethod
    def _clean_body(body: str) -> str:
        """Normalize writer body text for Telegram plain-text rendering."""
        body = (
            body.replace("\\r\\n", "\n")
            .replace("\\r", "\n")
            .replace("\\n", "\n")
        )
        for markdown_char in "_*`[]()#|~+-.!":
            body = body.replace("\\" + markdown_char, markdown_char)
        lines: list[str] = []
        for raw in body.splitlines():
            line = raw.rstrip("\\").strip()
            if not line:
                if lines and lines[-1]:
                    lines.append("")
                continue
            lines.append(line)
        return "\n".join(lines).strip()

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
        return self._finalize(
            content,
            fallback_title=str(item.get("title") or ""),
            fallback_body=content,
            source_url=str(source["url"] or ""),
        )

    def revise_post(
        self,
        *,
        title: str,
        body: str,
        feedback: str,
        source_url: str,
    ) -> dict:
        """Return a revised ``{"title", "body", "source_url"}`` applying feedback."""
        current = json.dumps(
            {"title": title, "body": body, "source_url": source_url},
            ensure_ascii=True,
            indent=2,
        )
        user_message = (
            f"Current post:\n{current}\n\n"
            f"Feedback from the channel owner:\n{feedback}"
        )
        content = self._chat(
            [
                {"role": "system", "content": REVISE_PROMPT},
                {"role": "user", "content": user_message},
            ]
        )
        return self._finalize(
            content,
            fallback_title=title,
            fallback_body=body,
            source_url=source_url,
        )

    @staticmethod
    def _finalize(
        content: str, *, fallback_title: str, fallback_body: str, source_url: str
    ) -> dict:
        """Parse one LLM reply into a clean ``{"title", "body", "source_url"}``."""
        parsed = Writer._parse_json_object(content)
        if parsed and str(parsed.get("title", "")).strip() and str(parsed.get("body", "")).strip():
            title = str(parsed["title"]).strip()[:120]
            body = Writer._clean_body(str(parsed["body"]))
        else:
            title = str(fallback_title).strip()[:120] or "Untitled"
            body = Writer._clean_body(fallback_body)
        source_url = str(source_url or "").strip()
        if source_url and source_url not in body:
            body = f"{body}\n\n{source_url}" if body else source_url
        return {
            "title": title,
            "body": body,
            "source_url": source_url,
        }
