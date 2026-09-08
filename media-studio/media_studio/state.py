"""Persistent job state: a JSON document with atomic writes."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_id() -> str:
    return secrets.token_hex(6)


@dataclass
class ArtifactInfo:
    name: str
    kind: str
    size: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "ArtifactInfo":
        return cls(name=str(raw.get("name", "")), kind=str(raw.get("kind", "file")), size=int(raw.get("size", 0)))


@dataclass
class Job:
    id: str
    driver: str
    prompt: str
    params: dict
    status: str = STATUS_QUEUED
    error: str = ""
    artifacts: list = field(default_factory=list)
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    log_path: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "driver": self.driver,
            "prompt": self.prompt,
            "params": self.params,
            "status": self.status,
            "error": self.error,
            "artifacts": [a.to_dict() if isinstance(a, ArtifactInfo) else a for a in self.artifacts],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Job":
        return cls(
            id=str(raw.get("id", "")),
            driver=str(raw.get("driver", "")),
            prompt=str(raw.get("prompt", "")),
            params=dict(raw.get("params") or {}),
            status=str(raw.get("status", STATUS_QUEUED)),
            error=str(raw.get("error", "")),
            artifacts=[ArtifactInfo.from_dict(a) for a in raw.get("artifacts", []) if isinstance(a, dict)],
            created_at=str(raw.get("created_at", "")),
            started_at=str(raw.get("started_at", "")),
            finished_at=str(raw.get("finished_at", "")),
            log_path=str(raw.get("log_path", "")),
        )


class StateStore:
    """Thread-safe JSON-backed job store. Writes are atomic (tmp + rename)."""

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

    def create_job(self, driver: str, prompt: str, params: dict | None = None) -> Job:
        job_id = _job_id()
        job = Job(
            id=job_id,
            driver=driver,
            prompt=prompt,
            params=params or {},
            log_path=os.path.join(os.path.dirname(self.path) or ".", "logs", f"{job_id}.log"),
        )
        with self._lock:
            self._jobs[job.id] = job.to_dict()
            self._save()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            raw = self._jobs.get(job_id)
            return Job.from_dict(raw) if raw else None

    def list_jobs(self, limit: int = 100) -> list[Job]:
        with self._lock:
            jobs = [Job.from_dict(raw) for raw in self._jobs.values()]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def claim_next_queued(self) -> Job | None:
        with self._lock:
            queued = [
                Job.from_dict(raw)
                for raw in self._jobs.values()
                if raw.get("status") == STATUS_QUEUED
            ]
            if not queued:
                return None
            queued.sort(key=lambda job: job.created_at)
            job = queued[0]
            job.status = STATUS_RUNNING
            job.started_at = _now()
            self._jobs[job.id] = job.to_dict()
            self._save()
            return job

    def update(self, job_id: str, **changes) -> Job | None:
        with self._lock:
            raw = self._jobs.get(job_id)
            if raw is None:
                return None
            for key, value in changes.items():
                if value is not None:
                    raw[key] = value
            job = Job.from_dict(raw)
            self._jobs[job_id] = job.to_dict()
            self._save()
            return job

    def finish(self, job_id: str, artifacts: list | None = None, error: str = "") -> Job | None:
        with self._lock:
            raw = self._jobs.get(job_id)
            if raw is None:
                return None
            status = STATUS_FAILED if error else STATUS_DONE
            raw["status"] = status
            raw["error"] = error[:1000]
            raw["finished_at"] = _now()
            if artifacts is not None:
                raw["artifacts"] = [a.to_dict() if isinstance(a, ArtifactInfo) else a for a in artifacts]
            job = Job.from_dict(raw)
            self._jobs[job_id] = job.to_dict()
            self._save()
            return job

    def cancel_queued(self, job_id: str) -> bool:
        with self._lock:
            raw = self._jobs.get(job_id)
            if raw is None or raw.get("status") != STATUS_QUEUED:
                return False
            raw["status"] = STATUS_CANCELED
            raw["finished_at"] = _now()
            self._save()
            return True

    def counts(self) -> dict:
        with self._lock:
            queued = sum(1 for raw in self._jobs.values() if raw.get("status") == STATUS_QUEUED)
            running = sum(1 for raw in self._jobs.values() if raw.get("status") == STATUS_RUNNING)
        return {"queued": queued, "running": running}
