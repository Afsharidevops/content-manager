"""HTTP client for the NotebookLM worker.

The worker turns a topic plus sources into a video with Google NotebookLM and
serves the result as an artifact; this client submits the job, follows its
stage, and downloads the finished video.
"""

from __future__ import annotations

import json

from content_bot.http import HttpError, request_bytes, request_json

#: Media driver name stored on a draft whose video comes from NotebookLM.
DRIVER = "notebooklm"

#: Stage labels the worker reports while a video is produced. They are the
#: public contract of GET /jobs/<id>, so the chat progress messages map onto
#: them one to one.
STAGE_LABELS = {
    "Preparing the sources": "⏳ Preparing the sources...",
    "Creating the notebook": "📚 Creating the notebook...",
    "Uploading the sources": "📤 Uploading the sources...",
    "Waiting for the sources": "📤 Uploading the sources...",
    "Generating the video overview": "🎬 Generating the video...",
    "Downloading the video": "⬇️ Downloading the output...",
}


class NotebookLMError(RuntimeError):
    pass


def stage_label(stage: str) -> str:
    """Return the chat line for one worker stage, or an empty string."""
    return STAGE_LABELS.get(str(stage or "").strip(), "")


def _error_detail(body: bytes | str, limit: int = 200) -> str:
    if isinstance(body, bytes):
        text = body.decode("utf-8", "replace")
    else:
        text = str(body or "")
    text = text.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return text[:limit]
    if isinstance(parsed, dict) and parsed.get("error"):
        return str(parsed["error"])[:limit]
    return text[:limit]


def _job_payload(payload: dict | None) -> dict:
    job = payload.get("job") if isinstance(payload, dict) else None
    return job if isinstance(job, dict) else {}


class NotebookLM:
    """Minimal client for NotebookLM worker jobs and artifacts.

    ``request_json_fn`` and ``request_bytes_fn`` are injectable for offline
    tests; they mirror the helpers of ``content_bot.http``.
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
            raise NotebookLMError("NotebookLM worker is not configured")
        return f"{self.root}/{path.lstrip('/')}"

    def submit(
        self,
        *,
        topic: str,
        profile: str,
        sources: list | None = None,
        content_id: str = "",
    ) -> str:
        """Queue one video job and return its id."""
        body = {
            "topic": str(topic or ""),
            "profile": str(profile or ""),
            "content_id": str(content_id or ""),
            "sources": list(sources or []),
        }
        try:
            payload = self.request_json(
                self._url("jobs"),
                payload=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except HttpError as error:
            raise NotebookLMError(
                f"job submit failed with HTTP {error.status}"
            ) from error
        except ConnectionError as error:
            raise NotebookLMError(f"job submit network error: {error}") from error
        job_id = str(_job_payload(payload).get("id") or "")
        if not job_id:
            raise NotebookLMError("job submit returned no job id")
        return job_id

    def job(self, job_id: str) -> dict:
        """Return the full job record, including its stage and log tail."""
        try:
            payload = self.request_json(
                self._url(f"jobs/{job_id}"),
                headers=self._headers(),
                timeout=self.timeout,
            )
        except HttpError as error:
            raise NotebookLMError(
                f"job lookup failed with HTTP {error.status}"
            ) from error
        except ConnectionError as error:
            raise NotebookLMError(f"job lookup network error: {error}") from error
        job = _job_payload(payload)
        if not job:
            raise NotebookLMError("job lookup returned no job record")
        return job

    def upload(self, content: bytes, *, filename: str = "material.txt") -> str:
        """Store one source file and return its upload id."""
        if not content:
            raise NotebookLMError("the source file is empty")
        headers = self._headers()
        headers["Content-Type"] = "application/octet-stream"
        headers["X-Filename"] = str(filename or "material.txt")
        try:
            status, body = self.request_bytes(
                self._url("uploads"),
                raw_body=content,
                headers=headers,
                timeout=120,
                max_bytes=64 * 1024 * 1024,
            )
        except ConnectionError as error:
            raise NotebookLMError(f"source upload network error: {error}") from error
        if status >= 400:
            detail = _error_detail(body)
            suffix = f": {detail}" if detail else ""
            raise NotebookLMError(f"source upload failed with HTTP {status}{suffix}")
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except ValueError as error:
            raise NotebookLMError("source upload returned invalid JSON") from error
        upload_id = str((payload or {}).get("id") or "")
        if not upload_id:
            raise NotebookLMError("source upload returned no upload id")
        return upload_id

    def download(self, job_id: str, name: str) -> bytes:
        """Download the finished video as bytes."""
        try:
            status, body = self.request_bytes(
                self._url(f"artifacts/{job_id}/{name}"),
                headers=self._headers(),
                timeout=300,
                max_bytes=512 * 1024 * 1024,
            )
        except ConnectionError as error:
            raise NotebookLMError(f"artifact download network error: {error}") from error
        if status >= 400:
            raise NotebookLMError(f"artifact download failed with HTTP {status}")
        if not body:
            raise NotebookLMError("artifact download returned an empty file")
        return body

    def session(self) -> dict:
        """Return the worker session description (no secrets)."""
        try:
            payload = self.request_json(
                self._url("session/info"),
                headers=self._headers(),
                timeout=self.timeout,
            )
        except HttpError as error:
            raise NotebookLMError(
                f"session lookup failed with HTTP {error.status}"
            ) from error
        except ConnectionError as error:
            raise NotebookLMError(f"session lookup network error: {error}") from error
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def pick_artifact(job: dict) -> tuple[str, str] | None:
        """Return ``(name, kind)`` for the finished video of one job."""
        if str(job.get("status") or "") != "ready":
            return None
        path = str(job.get("video_path") or "")
        name = path.rsplit("/", 1)[-1] if path else ""
        if not name:
            job_id = str(job.get("id") or "")
            name = f"{job_id}.mp4" if job_id else ""
        return (name, "video") if name else None
