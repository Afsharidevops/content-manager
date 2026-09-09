"""HTTP client for the Media Studio job API."""

from __future__ import annotations

from content_bot.http import HttpError, request_bytes, request_json

MEDIA_KINDS = ("image", "video")


class MediaStudioError(RuntimeError):
    pass


def _job_payload(payload: dict | None) -> dict:
    job = payload.get("job") if isinstance(payload, dict) else None
    return job if isinstance(job, dict) else {}


class MediaStudio:
    """Minimal client for Media Studio jobs and artifacts.

    ``request_json_fn`` and ``request_bytes_fn`` are injectable for offline
    tests; they mirror ``content_bot.http`` helpers.
    """

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        request_json_fn=None,
        request_bytes_fn=None,
        timeout: int = 60,
    ):
        self.root = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.request_json = request_json_fn or request_json
        self.request_bytes = request_bytes_fn or request_bytes
        self.timeout = timeout

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _url(self, path: str) -> str:
        if not self.root:
            raise MediaStudioError("Media Studio is not configured")
        return f"{self.root}/{path.lstrip('/')}"

    def submit(self, driver: str, prompt: str, params: dict | None = None) -> str:
        """Submit one job and return its id."""
        try:
            payload = self.request_json(
                self._url("jobs"),
                payload={"driver": driver, "prompt": prompt, "params": params or {}},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except HttpError as error:
            raise MediaStudioError(f"job submit failed with HTTP {error.status}") from error
        except ConnectionError as error:
            raise MediaStudioError(f"job submit network error: {error}") from error
        job = _job_payload(payload)
        job_id = str(job.get("id") or "")
        if not job_id:
            raise MediaStudioError("job submit returned no job id")
        return job_id

    def job(self, job_id: str) -> dict:
        """Return the full job record for one job id."""
        try:
            payload = self.request_json(
                self._url(f"jobs/{job_id}"),
                headers=self._headers(),
                timeout=self.timeout,
            )
        except HttpError as error:
            raise MediaStudioError(f"job lookup failed with HTTP {error.status}") from error
        except ConnectionError as error:
            raise MediaStudioError(f"job lookup network error: {error}") from error
        job = _job_payload(payload)
        if not job:
            raise MediaStudioError("job lookup returned no job record")
        return job

    def download(self, job_id: str, name: str) -> bytes:
        """Download one finished artifact as bytes."""
        try:
            status, body = self.request_bytes(
                self._url(f"artifacts/{job_id}/{name}"),
                headers=self._headers(),
                timeout=120,
                max_bytes=60_000_000,
            )
        except ConnectionError as error:
            raise MediaStudioError(f"artifact download network error: {error}") from error
        if status >= 400:
            raise MediaStudioError(f"artifact download failed with HTTP {status}")
        if not body:
            raise MediaStudioError("artifact download returned an empty file")
        return body

    @staticmethod
    def pick_artifact(job: dict) -> tuple[str, str] | None:
        """Return ``(name, kind)`` for the first usable media artifact."""
        for artifact in job.get("artifacts") or []:
            kind = str(artifact.get("kind") or "")
            name = str(artifact.get("name") or "")
            if kind in MEDIA_KINDS and name:
                return name, kind
        return None
