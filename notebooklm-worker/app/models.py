"""Job model, workflow states, and the JSON-backed job store."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

STATUS_CREATED = "created"
STATUS_UPLOADING = "uploading"
STATUS_PROCESSING = "processing"
STATUS_GENERATING = "generating"
STATUS_DOWNLOADING = "downloading"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"

TERMINAL_STATES = frozenset({STATUS_READY, STATUS_FAILED, STATUS_CANCELED})

#: The order the worker moves through while one video is produced. A failing
#: step may jump to ``failed`` from any state that is not terminal yet.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_CREATED: frozenset({STATUS_UPLOADING, STATUS_FAILED, STATUS_CANCELED}),
    STATUS_UPLOADING: frozenset({STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCELED}),
    STATUS_PROCESSING: frozenset({STATUS_GENERATING, STATUS_FAILED, STATUS_CANCELED}),
    STATUS_GENERATING: frozenset({STATUS_DOWNLOADING, STATUS_FAILED, STATUS_CANCELED}),
    STATUS_DOWNLOADING: frozenset({STATUS_READY, STATUS_FAILED, STATUS_CANCELED}),
    STATUS_READY: frozenset(),
    STATUS_FAILED: frozenset(),
    STATUS_CANCELED: frozenset(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_id() -> str:
    return secrets.token_hex(6)


class TransitionError(RuntimeError):
    """Raised when a job is asked to jump to an illegal state."""


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(str(current or ""), frozenset())


def normalize_sources(raw) -> list[dict]:
    """Return the source list as plain dictionaries for storage."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            cleaned.append({"kind": "auto", "value": item.strip()})
            continue
        if isinstance(item, dict):
            kind = str(item.get("kind") or "auto").strip().lower() or "auto"
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            entry = {"kind": kind, "value": value}
            title = str(item.get("title") or "").strip()
            if title:
                entry["title"] = title
            cleaned.append(entry)
    return cleaned


@dataclass
class NotebookLMJob:
    id: str = ""
    content_id: str = ""
    topic: str = ""
    sources: list = field(default_factory=list)
    profile: str = ""
    language: str = ""
    duration: str = ""
    voice_gender: str = ""
    style: str = ""
    tone: str = ""
    audience: str = ""
    status: str = STATUS_CREATED
    stage: str = ""
    video_path: str = ""
    video_paths: dict[str, str] = field(default_factory=dict)
    video_template: str = ""
    video_style: str = ""
    error: str = ""
    log_path: str = ""
    claimed_at: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _job_id()
        if not self.created_at:
            self.created_at = _now()
        self.sources = normalize_sources(self.sources)
        if isinstance(self.video_paths, list):
            self.video_paths = {}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "NotebookLMJob":
        known = {field_name for field_name in cls.__dataclass_fields__}
        payload = {key: value for key, value in (raw or {}).items() if key in known}
        return cls(**payload)


class JobStore:
    """Thread-safe JSON job store. Writes are atomic (tmp + rename)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
            self._jobs = {str(k): v for k, v in (raw.get("jobs") or {}).items()}
        except (ValueError, OSError):
            backup = f"{self.path}.corrupt-{int(time.time())}"
            try:
                os.replace(self.path, backup)
            except OSError:
                pass

    def _save(self) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp-{os.getpid()}-{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"jobs": self._jobs}, handle, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def create(self, job: NotebookLMJob) -> NotebookLMJob:
        if not job.log_path:
            job.log_path = os.path.join(
                os.path.dirname(self.path) or ".", "logs", f"{job.id}.log"
            )
        with self._lock:
            self._jobs[job.id] = job.to_dict()
            self._save()
        return job

    def get(self, job_id: str) -> NotebookLMJob | None:
        with self._lock:
            raw = self._jobs.get(str(job_id))
            return NotebookLMJob.from_dict(raw) if raw else None

    def list_jobs(self, limit: int = 100) -> list[NotebookLMJob]:
        with self._lock:
            jobs = [NotebookLMJob.from_dict(raw) for raw in self._jobs.values()]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def update(self, job_id: str, **changes) -> NotebookLMJob | None:
        with self._lock:
            raw = self._jobs.get(str(job_id))
            if raw is None:
                return None
            for key, value in changes.items():
                if value is not None:
                    raw[key] = value
            job = NotebookLMJob.from_dict(raw)
            self._jobs[job.id] = job.to_dict()
            self._save()
            return job

    def transition(
        self, job_id: str, status: str, *, stage: str = "", error: str = ""
    ) -> NotebookLMJob | None:
        """Move one job to the next state and reject illegal jumps."""
        with self._lock:
            raw = self._jobs.get(str(job_id))
            if raw is None:
                return None
            current = str(raw.get("status") or STATUS_CREATED)
            if status != current and not can_transition(current, status):
                raise TransitionError(f"{current} -> {status} is not allowed")
            raw["status"] = status
            if stage:
                raw["stage"] = stage
            if error:
                raw["error"] = error[:1000]
            if status == STATUS_FAILED or status == STATUS_CANCELED:
                raw["finished_at"] = _now()
            if status != STATUS_CREATED and not raw.get("started_at"):
                raw["started_at"] = _now()
            if status == STATUS_READY:
                raw["finished_at"] = _now()
            job = NotebookLMJob.from_dict(raw)
            self._jobs[job.id] = job.to_dict()
            self._save()
            return job

    def claim_next_created(self) -> NotebookLMJob | None:
        """Return the oldest queued job and mark it as being started."""
        with self._lock:
            waiting = [
                NotebookLMJob.from_dict(raw)
                for raw in self._jobs.values()
                if raw.get("status") == STATUS_CREATED and not raw.get("claimed_at")
            ]
            if not waiting:
                return None
            waiting.sort(key=lambda job: job.created_at)
            job = waiting[0]
            job.started_at = _now()
            job.claimed_at = _now()
            self._jobs[job.id] = job.to_dict()
            self._save()
            return job

    def cancel_created(self, job_id: str) -> bool:
        with self._lock:
            raw = self._jobs.get(str(job_id))
            if raw is None or raw.get("status") != STATUS_CREATED:
                return False
            raw["status"] = STATUS_CANCELED
            raw["finished_at"] = _now()
            self._save()
            return True

    def counts(self) -> dict:
        with self._lock:
            created = sum(
                1 for raw in self._jobs.values() if raw.get("status") == STATUS_CREATED
            )
            active = sum(
                1
                for raw in self._jobs.values()
                if raw.get("status")
                in {STATUS_UPLOADING, STATUS_PROCESSING, STATUS_GENERATING, STATUS_DOWNLOADING}
            )
        return {"queued": created, "running": active}
