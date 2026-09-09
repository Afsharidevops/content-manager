"""OpenAI-compatible chat writer that produces runtime Persian post copy."""

from __future__ import annotations

import json
import logging
import re
import unicodedata

from content_bot.http import HttpError, request_json

LOGGER = logging.getLogger("content_bot.writer")


_MOJIBAKE_TELLS = set("ØÙÚÛÜ§±¯¨ŠŒž…")
_RTL_BIDI = {"R", "AL"}
_LTR_BIDI = {"L"}


def _count_rtl(text: str) -> int:
    """Count characters with a right-to-left bidirectional class."""
    return sum(1 for char in str(text or "") if unicodedata.bidirectional(char) in _RTL_BIDI)


def _first_strong_direction(line: str) -> str:
    """Return "R" or "L" for the first strong bidi character, or ""."""
    for char in str(line or ""):
        bidi = unicodedata.bidirectional(char)
        if bidi in _RTL_BIDI:
            return "R"
        if bidi in _LTR_BIDI:
            return "L"
    return ""


SYSTEM_PROMPT = (
    "You write short educational Telegram posts in Persian (Farsi) for a "
    "technical channel. Reply with exactly one JSON object with two keys: "
    "'title' (at most 120 characters) and 'body' (600 to 1400 characters). "
    "Write like a real person, with a warm, slightly informal tone, as if the "
    "channel owner wrote the post by hand. Avoid formal, machine-like, or "
    "news-style phrasing and empty filler. Start the title with a Persian "
    "word, never with a Latin-script or brand word; place English names after "
    "the opening Persian word. Base the post only on the content of the "
    "provided source article; never describe the input record itself or "
    "mention fields such as title, url, or excerpt. Do not invent facts. "
    "Do not use Markdown, hashtags, labels, quotes, or backslash characters. "
    "Keep paragraphs short and separate them with single blank lines. Start "
    "every paragraph with a Persian word; never begin a paragraph with a "
    "Latin-script name, number, or symbol. Place English product and tool "
    "names inside the sentence right after the Persian opening phrase so "
    "the whole paragraph stays one natural right-to-left flow. End the "
    "body with one final line containing only the full source URL."
)


REVISE_PROMPT = (
    "You revise an existing Persian (Farsi) Telegram post based on feedback "
    "from the channel owner. Reply with exactly one JSON object with two keys: "
    "'title' (at most 120 characters) and 'body' (at most 1400 characters). "
    "Keep the warm, slightly informal tone of the current post and stay "
    "faithful to the same source article. Apply the feedback faithfully and "
    "make the revision clearly visible: when the owner asks for a shorter "
    "text, cut the body down noticeably; when they ask to reword a part, "
    "rewrite that part. The revised title and body must differ from the "
    "current version. Keep the title right-to-left friendly: start it with "
    "a Persian word and place English names after the opening Persian word. "
    "Never mention the feedback, the revision process, or the source record "
    "inside the post. Do not invent facts. Do not use Markdown, hashtags, "
    "labels, quotes, or backslash characters. Keep "
    "paragraphs short and separate them with single blank lines. Start "
    "every paragraph with a Persian word and never begin a paragraph with "
    "a Latin-script name, number, or symbol, so paragraphs keep one "
    "right-to-left flow. End the body with one final line containing only "
    "the full source URL."
)


class WriterError(RuntimeError):
    pass


def _chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base.endswith("/chat/completions"):
        base = f"{base}/chat/completions"
    return base


class Writer:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "auto",
        *,
        timeout: int = 240,
        max_tokens: int = 1600,
        reasoning_effort: str = "",
    ):
        self.endpoint = _chat_endpoint(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def _chat(self, messages: list[dict]) -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.7,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
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
        if content is None:
            raise WriterError("writer returned no content")
        content = str(content)
        if not content.strip():
            raise WriterError("writer returned empty content")
        return content

    @staticmethod
    def _parse_json_object(content: str) -> dict | None:
        """Extract one JSON object, tolerating markdown fences and trailing text."""
        content = str(content or "")
        candidates = [content]
        fence = re.search(r"```[a-zA-Z]*\s*(.*?)```", content, re.DOTALL)
        if fence:
            candidates.insert(0, fence.group(1))
        for candidate in candidates:
            parsed = Writer._balanced_json_object(candidate)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _balanced_json_object(text: str) -> dict | None:
        """Parse the first brace-balanced JSON object inside ``text``."""
        start = text.find("{")
        while start != -1:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(text[start : index + 1])
                        except ValueError:
                            break
                        return parsed if isinstance(parsed, dict) else None
            start = text.find("{", start + 1)
        return None

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

    @staticmethod
    def _repair_mojibake(value: str) -> str:
        """Reverse UTF-8 Persian text that arrived decoded as one legacy charset."""
        text = str(value or "")
        if not text or _count_rtl(text):
            return text
        for codec in ("cp1252", "latin-1"):
            try:
                raw = text.encode(codec)
            except UnicodeEncodeError:
                continue
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if decoded and _count_rtl(decoded):
                return decoded
        return text

    @staticmethod
    def _still_mojibake(value: str) -> bool:
        """True when text still looks like UTF-8 read as Latin-1 (unrecoverable)."""
        text = str(value or "")
        if not text or _count_rtl(text):
            return False
        return sum(1 for char in text if char in _MOJIBAKE_TELLS) >= 3

    def generate_post(self, item: dict, *, guidance: str = "") -> dict:
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
        system_content = SYSTEM_PROMPT
        guidance = " ".join(str(guidance or "").split())
        if guidance:
            system_content = (
                f"{SYSTEM_PROMPT}\n\nChannel owner notes to honor when writing:\n{guidance}"
            )
        content = self._chat(
            [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message},
            ]
        )
        post = self._parse_post(content)
        if post is None:
            repaired = Writer._repair_mojibake(content)
            if Writer._still_mojibake(repaired):
                raise WriterError("writer returned garbled text; try the request again")
            title = str(item.get("title") or "").strip()[:120] or "Untitled"
            post = {"title": title, "body": Writer._clean_body(repaired)}
        return self._with_source_url(post, str(source["url"] or ""))

    def revise_post(
        self,
        *,
        title: str,
        body: str,
        feedback: str,
        source_url: str,
    ) -> dict:
        """Return a revised ``{"title", "body", "source_url"}`` applying feedback.

        Raises ``WriterError`` when the model returns no usable JSON or when
        the revised post is identical to the current one, so callers never
        silently re-publish unchanged copy.
        """
        current = json.dumps(
            {"title": title, "body": body, "source_url": source_url},
            ensure_ascii=True,
            indent=2,
        )
        user_message = (
            f"Current post:\n{current}\n\n"
            f"Feedback from the channel owner:\n{feedback}"
        )
        messages = [
            {"role": "system", "content": REVISE_PROMPT},
            {"role": "user", "content": user_message},
        ]
        previous_title = str(title).strip()[:120]
        previous_body = Writer._strip_source_url(self._clean_body(str(body)), source_url)
        last_error: WriterError | None = None
        unchanged = False
        for _attempt in range(2):
            try:
                content = self._chat(messages)
            except WriterError as error:
                last_error = error
                continue
            post = self._parse_post(content)
            if post is None:
                last_error = WriterError("revision returned no usable JSON output")
                continue
            revised_body = Writer._strip_source_url(post["body"], source_url)
            if post["title"] == previous_title and revised_body == previous_body:
                unchanged = True
                continue
            return self._with_source_url(post, source_url)
        if unchanged:
            raise WriterError(
                "revision produced no changes; make the feedback more specific"
            )
        if last_error is not None:
            raise last_error
        raise WriterError("revision returned no usable JSON output")

    @staticmethod
    def _parse_post(content: str) -> dict | None:
        """Parse one LLM reply into ``{"title", "body"}`` or ``None``."""
        parsed = Writer._parse_json_object(content)
        if parsed is None:
            parsed = Writer._regex_fields(content)
            if parsed is None:
                LOGGER.warning("writer reply was not parseable; raw head: %s", str(content)[:400])
                return None
        title = Writer._repair_mojibake(str(parsed.get("title", ""))).strip()
        body_text = Writer._repair_mojibake(str(parsed.get("body", ""))).strip()
        if not title or not body_text:
            return None
        if Writer._still_mojibake(title) or Writer._still_mojibake(body_text):
            LOGGER.warning("writer reply contained garbled text; raw head: %s", str(content)[:400])
            return None
        return {"title": title[:120], "body": Writer._clean_body(body_text)}

    @staticmethod
    def _regex_fields(content: str) -> dict | None:
        """Best-effort title/body extraction when the JSON object is broken."""
        def grab(key: str) -> str:
            match = re.search(
                rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"',
                str(content or ""),
                re.DOTALL,
            )
            if not match:
                return ""
            value = match.group(1)
            try:
                value = json.loads(f'"{value}"')
            except ValueError:
                value = value.encode("utf-8").decode("unicode_escape", errors="ignore")
            return str(value).strip()

        title = grab("title")
        body = grab("body")
        if not title or not body:
            return None
        return {"title": title[:120], "body": Writer._clean_body(body)}

    @staticmethod
    def _strip_source_url(body: str, source_url: str) -> str:
        body = body.strip()
        if source_url:
            body = body.replace(source_url, "").strip()
        while "\n\n\n" in body:
            body = body.replace("\n\n\n", "\n\n")
        return body.strip()

    @staticmethod
    def _with_source_url(post: dict, source_url: str) -> dict:
        source_url = str(source_url or "").strip()
        body = Writer._dedupe_source_url(post["body"], source_url)
        if source_url and source_url not in body:
            body = f"{body}\n\n{source_url}" if body else source_url
        return {
            "title": post["title"],
            "body": body,
            "source_url": source_url,
        }

    @staticmethod
    def _dedupe_source_url(body: str, source_url: str) -> str:
        """Keep only the final URL-only line so the link is never repeated."""
        url = str(source_url or "").strip()
        lines = str(body or "").splitlines()
        last_url_index = -1
        for index, line in enumerate(lines):
            if line.strip() == url:
                last_url_index = index
        if last_url_index >= 0:
            kept = [
                line
                for index, line in enumerate(lines)
                if line.strip() != url or index == last_url_index
            ]
            return "\n".join(kept).strip()
        if url:
            text = str(body or "").strip()
            return f"{text}\n\n{url}" if text else url
        return str(body or "").strip()
