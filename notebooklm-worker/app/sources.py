"""Turn a job's source list into material NotebookLM can take.

Four kinds are supported, matching the NotebookLM source dialog:

* ``text``    - pasted text (the topic note is always one of these)
* ``url``     - a website
* ``youtube`` - a YouTube link
* ``file``    - a PDF, TXT, or Markdown upload stored through ``/uploads``

The ``auto`` kind picks one of the first three by looking at the value.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import dataclass

UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx", ".csv"}

YOUTUBE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/|youtu\.be/|youtube-nocookie\.com/)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def classify(value: str) -> str:
    """Return ``youtube``, ``url``, or ``text`` for one raw source value."""
    text = str(value or "").strip()
    if YOUTUBE_RE.match(text):
        return "youtube"
    if URL_RE.match(text):
        return "url"
    return "text"


def safe_suffix(filename: str) -> str:
    suffix = os.path.splitext(str(filename or ""))[1].lower()
    return suffix if suffix in UPLOAD_EXTENSIONS else ".txt"


def store_upload(uploads_dir: str, filename: str, data: bytes) -> str:
    """Store one uploaded material file and return its id."""
    os.makedirs(uploads_dir, exist_ok=True)
    upload_id = secrets.token_hex(6)
    target = os.path.join(uploads_dir, f"{upload_id}{safe_suffix(filename)}")
    with open(target, "wb") as handle:
        handle.write(data)
    return upload_id


def resolve_upload(uploads_dir: str, value: str) -> str:
    """Return the stored path of an upload id, or an empty string."""
    name = str(value or "").strip()
    if not name or os.path.sep in name or name.startswith("."):
        return ""
    if not os.path.isdir(uploads_dir):
        return ""
    for entry in os.listdir(uploads_dir):
        if entry.split(".", 1)[0] == name:
            path = os.path.join(uploads_dir, entry)
            if os.path.isfile(path):
                return path
    return ""


def prune_uploads(uploads_dir: str, ttl_seconds: int) -> int:
    """Delete stale uploads and return how many files were removed."""
    if ttl_seconds <= 0 or not os.path.isdir(uploads_dir):
        return 0
    deadline = time.time() - ttl_seconds
    removed = 0
    for entry in os.listdir(uploads_dir):
        path = os.path.join(uploads_dir, entry)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < deadline:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


@dataclass
class Material:
    kind: str
    value: str
    title: str = ""
    path: str = ""


def build_materials(job, uploads_dir: str) -> list[Material]:
    """Resolve the job sources into ordered material for the uploader."""
    materials: list[Material] = []
    for raw in job.sources or []:
        kind = str(raw.get("kind") or "auto").strip().lower()
        value = str(raw.get("value") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not value:
            continue
        if kind == "auto":
            kind = classify(value)
        if kind == "file":
            path = resolve_upload(uploads_dir, value) or (
                value if os.path.isfile(value) else ""
            )
            if not path:
                continue
            materials.append(
                Material(kind="file", value=value, title=title, path=path)
            )
            continue
        if kind not in {"text", "url", "youtube"}:
            kind = classify(value)
        materials.append(Material(kind=kind, value=value, title=title))
    return materials


def sources_note(materials: list[Material]) -> str:
    """Return the short source list appended to the generation prompt."""
    lines = []
    for material in materials:
        if material.kind == "text":
            lines.append(f"- {material.title or material.value[:80]}")
        elif material.kind == "file":
            lines.append(f"- {material.title or os.path.basename(material.path)}")
        else:
            lines.append(f"- {material.value}")
    return "\n".join(lines)
