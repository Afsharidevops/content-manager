"""API video driver: asynchronous video generation over HTTP.

Speaks the OpenAI-style video API that router gateways expose: one POST to
``<base>/videos/generations`` starts a billable upstream job, then GET
``<base>/videos/<id>`` is polled until the provider reports a finished video.
The request shape follows the xAI Grok Imagine contract that the 9router
family proxies, so the same driver serves ``xai/grok-imagine-video``,
``openrouter/google/veo-3.1``, and ``vertex/veo-3.1-fast-generate-preview``
when the gateway holds credentials for that provider.

Combos (multi-provider model groups such as ``ai`` or ``ai-strong``) are a
chat feature: a gateway rejects them for video generation, so this driver
asks for a concrete video model and explains the answer when it comes back.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from media_studio.drivers.base import Driver, DriverError, RunContext

DONE_STATES = {"done", "succeeded", "success", "completed", "complete", "ready"}
FAILED_STATES = {"failed", "failure", "error", "cancelled", "canceled", "rejected", "expired"}


def _ext_from_bytes(raw: bytes) -> str:
    if len(raw) > 12 and raw[4:8] == b"ftyp":
        return "mp4"
    if raw[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if raw[:4] == b"RIFF" and raw[8:12] == b"AVI ":
        return "avi"
    return "mp4"


def _numeric(value) -> int | None:
    """Return the seconds of a duration hint, or None when it is prose.

    The Content Bot sends "up_to_30_seconds" for its longer option; that is a
    prompt hint, not a provider value, so it is dropped here.
    """
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return None


def _failure_hint(status: int, detail: str) -> str:
    """Explain the common ways the video endpoint or model is misconfigured."""
    text = str(detail or "").lower()
    note = ""
    if "combos are not supported" in text:
        note = (
            "The gateway refuses provider combos for video. Set "
            "MEDIA_STUDIO_VIDEO_MODEL to the video model of a connected provider, "
            "for example xai/grok-imagine-video, openrouter/google/veo-3.1, or "
            "vertex/veo-3.1-fast-generate-preview."
        )
    elif "no credentials for provider" in text:
        provider = text.split("no credentials for provider", 1)[1].strip(" :\"'}").split()[:1]
        name = provider[0] if provider else "that"
        note = (
            f"The gateway holds no {name} credentials. Connect that provider in "
            "the router (an API key or an account sign-in) or point "
            "MEDIA_STUDIO_VIDEO_BASE_URL at an API that serves video generation."
        )
    elif status == 404:
        note = (
            "The endpoint has no /videos/generations route. Point "
            "MEDIA_STUDIO_VIDEO_BASE_URL at a gateway that serves the video API - "
            "the stack Smart Router serves chat completions only."
        )
    elif status in {401, 403}:
        note = (
            "The endpoint refused the credentials. Store the video API key in "
            "MEDIA_STUDIO_VIDEO_API_KEY."
        )
    if note:
        return f"{note} Provider response: {detail}"
    return f"Provider response: {detail}"


def _json_or_empty(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _provider_error(body: dict, fallback: str) -> str:
    """Return the readable message of a provider response."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail")
        if message:
            return str(message)
    if isinstance(error, str) and error:
        return error
    if isinstance(body, dict):
        for key in ("message", "detail", "error_description"):
            value = body.get(key)
            if value:
                return str(value)
    return fallback


def _status_of(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    for key in ("status", "state"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    video = body.get("video")
    if isinstance(video, dict):
        value = video.get("status")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _video_source(body: dict) -> tuple[str, str]:
    """Return ("url" | "b64", value) for one finished video, or ("", "")."""
    if not isinstance(body, dict):
        return "", ""
    candidates: list = [body]
    for key in ("data", "videos", "output", "outputs", "result", "results"):
        value = body.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            candidates.append(value)
    video = body.get("video")
    if isinstance(video, dict):
        candidates.append(video)
    elif isinstance(video, str) and video:
        return "url", video
    for item in candidates:
        for key in ("b64_json", "video_base64"):
            encoded = item.get(key)
            if isinstance(encoded, str) and encoded:
                return "b64", encoded
        for key in ("url", "video_url", "download_url", "uri"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return "url", value
    return "", ""


def _job_id(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    for key in ("request_id", "requestId", "id", "job_id", "name"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("data", "video", "result"):
        value = body.get(key)
        if isinstance(value, dict):
            nested = _job_id(value)
            if nested:
                return nested
    return ""


class ApiVideoDriver(Driver):
    name = "api-video"
    label = "Video generation through an OpenAI-compatible video API"
    group = "api"

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        settings = ctx.settings
        params = dict(ctx.params or {})
        base_url = (getattr(settings, "video_base_url", "") or "").rstrip("/")
        api_key = getattr(settings, "video_api_key", "") or ""
        model = str(getattr(settings, "video_model", "") or "").strip()
        if not base_url:
            raise DriverError(
                "No video API endpoint is configured.",
                step="config",
                hint=(
                    "Set MEDIA_STUDIO_VIDEO_BASE_URL (or MEDIA_STUDIO_WRITER_BASE_URL) "
                    "before submitting api-video jobs."
                ),
            )
        if not model:
            raise DriverError(
                "No video model is configured.",
                step="config",
                hint=(
                    "Set MEDIA_STUDIO_VIDEO_MODEL to a video model of the endpoint, "
                    "for example xai/grok-imagine-video."
                ),
            )
        timeout = int(
            params.get("timeout_seconds")
            or getattr(settings, "video_timeout_seconds", 900)
            or 900
        )
        poll_seconds = max(
            1,
            int(params.get("poll_seconds") or getattr(settings, "video_poll_seconds", 10) or 10),
        )
        endpoint = f"{base_url}/videos/generations"
        provider = str(
            params.get("provider") or getattr(settings, "video_provider", "") or ""
        ).strip()
        if provider:
            endpoint = f"{endpoint}?provider={urllib.parse.quote(provider)}"
        payload: dict = {"model": model, "prompt": ctx.prompt}
        duration = _numeric(params.get("duration")) or _numeric(
            getattr(settings, "video_duration", "")
        )
        if duration:
            payload["duration"] = duration
        aspect_ratio = str(
            params.get("aspect_ratio") or getattr(settings, "video_aspect_ratio", "") or ""
        ).strip()
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        resolution = str(
            params.get("resolution") or getattr(settings, "video_resolution", "") or ""
        ).strip()
        if resolution:
            payload["resolution"] = resolution
        for extra in ("negative_prompt", "seed"):
            value = params.get(extra)
            if value not in (None, ""):
                payload[extra] = value

        body = self._post(endpoint, payload, api_key, timeout)
        status = _status_of(body)
        source_kind, source = _video_source(body)
        job_id = _job_id(body)
        ctx.log(f"api-video: submitted {model}{f' (job {job_id})' if job_id else ''}.")
        if source and (not job_id or status in DONE_STATES):
            return self._save(ctx, source_kind, source, api_key, timeout)
        if status in FAILED_STATES:
            raise DriverError(
                f"Video provider failed the job for model {model}.",
                step="request",
                hint=f"Provider response: {_provider_error(body, json.dumps(body)[:300])}",
            )
        if not job_id:
            raise DriverError(
                "Video API returned neither a video nor a job id.",
                step="response",
                hint=f"Provider response: {json.dumps(body)[:300]}",
            )
        status_url = str(params.get("status_url") or "").strip() or (
            f"{base_url}/videos/{urllib.parse.quote(job_id)}"
        )
        finished = self._poll(
            ctx, status_url, api_key, timeout, poll_seconds, job_id
        )
        source_kind, source = _video_source(finished)
        if not source:
            raise DriverError(
                "Video job finished without a video URL.",
                step="response",
                hint=f"Provider response: {json.dumps(finished)[:300]}",
            )
        return self._save(ctx, source_kind, source, api_key, timeout)

    def _post(self, endpoint: str, payload: dict, api_key: str, timeout: int) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-configured endpoint
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except OSError:
                pass
            raise DriverError(
                f"Video API returned HTTP {exc.code} for model {payload.get('model') or '<unset>'}.",
                step="request",
                hint=_failure_hint(exc.code, _provider_error(_json_or_empty(detail), detail)),
            ) from exc
        except urllib.error.URLError as exc:
            raise DriverError(
                f"Video API is unreachable at {endpoint}: {exc.reason}.",
                step="request",
                hint="Check network access and that the base URL includes /v1 when required.",
            ) from exc
        body = _json_or_empty(raw.decode("utf-8", "replace"))
        if not body:
            raise DriverError("Video API returned non-JSON output.", step="response")
        return body

    def _poll(
        self,
        ctx: RunContext,
        status_url: str,
        api_key: str,
        timeout: int,
        poll_seconds: int,
        job_id: str,
    ) -> dict:
        deadline = time.monotonic() + max(timeout, 1)
        attempt = 0
        last: dict = {}
        while True:
            attempt += 1
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            request = urllib.request.Request(status_url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-configured endpoint
                    last = _json_or_empty(response.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except OSError:
                    pass
                raise DriverError(
                    f"Video status lookup returned HTTP {exc.code} for job {job_id}.",
                    step="poll",
                    hint=_failure_hint(exc.code, _provider_error(_json_or_empty(detail), detail)),
                ) from exc
            except urllib.error.URLError as exc:
                raise DriverError(
                    f"Video status lookup failed for job {job_id}: {exc.reason}.",
                    step="poll",
                    hint="The job may still be running upstream; check the gateway job list.",
                ) from exc
            status = _status_of(last)
            if status in DONE_STATES:
                ctx.log(f"api-video: job {job_id} finished after {attempt} poll(s).")
                return last
            if status in FAILED_STATES:
                raise DriverError(
                    f"Video provider reported job {job_id} as {status}.",
                    step="poll",
                    hint=f"Provider response: {_provider_error(last, json.dumps(last)[:300])}",
                )
            if _video_source(last)[1]:
                return last
            if time.monotonic() >= deadline:
                raise DriverError(
                    f"Video job {job_id} did not finish within {timeout}s.",
                    step="timeout",
                    hint=(
                        "Video generation is slow; raise MEDIA_STUDIO_VIDEO_TIMEOUT_SECONDS. "
                        f"Last status: {status or 'unknown'}."
                    ),
                )
            ctx.log(f"api-video: job {job_id} {status or 'pending'}; waiting {poll_seconds}s.")
            time.sleep(poll_seconds)

    def _save(
        self,
        ctx: RunContext,
        source_kind: str,
        source: str,
        api_key: str,
        timeout: int,
    ) -> list[tuple[str, str]]:
        if source_kind == "b64":
            try:
                content = base64.b64decode(source)
            except ValueError as exc:
                raise DriverError("Video API returned invalid base64.", step="response") from exc
        else:
            content = self._download(source, api_key, timeout)
        if not content:
            raise DriverError("Video API returned an empty video.", step="response")
        name = f"video_1.{_ext_from_bytes(content)}"
        destination = os.path.join(ctx.work_dir, name)
        with open(destination, "wb") as handle:
            handle.write(content)
        ctx.log(f"api-video: saved {name} ({len(content)} bytes).")
        self._apply_brand(ctx, destination, name)
        return [(name, "video")]

    def _apply_brand(self, ctx: RunContext, path: str, name: str) -> None:
        """Bake the configured brand chip into the clip; never fail the job."""
        label = str(getattr(ctx.settings, "brand_label", "") or "").strip()
        if not label:
            return
        raw = str((ctx.params or {}).get("brand", "") or "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return
        from media_studio.video_edit import VideoEditError, edit_video

        branded = os.path.join(ctx.work_dir, f".branded-{name}")
        try:
            edit_video(
                path,
                branded,
                ffmpeg=str(getattr(ctx.settings, "ffmpeg_binary", "") or ""),
                max_side=int(getattr(ctx.settings, "video_edit_max_side", 1920) or 0),
                max_seconds=0,
                timeout=int(
                    getattr(ctx.settings, "video_edit_timeout_seconds", 900) or 900
                ),
                log=ctx.log,
                brand_label=label,
                brand_style=str(getattr(ctx.settings, "brand_style", "") or "aurora"),
                brand_position=str(
                    getattr(ctx.settings, "brand_position", "") or "bottom-right"
                ),
            )
        except VideoEditError as exc:
            ctx.log(f"brand mark skipped for {name}: {exc}")
            return
        try:
            os.replace(branded, path)
            ctx.log(f"brand chip applied to {name}")
        except OSError as exc:
            ctx.log(f"brand mark skipped for {name}: {exc}")

    @staticmethod
    def _download(url: str, api_key: str, timeout: int) -> bytes:
        headers = {}
        if api_key and (url.startswith("/") or "://" not in url):
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - provider-returned URL
                return response.read()
        except urllib.error.HTTPError as exc:
            raise DriverError(
                f"Video download returned HTTP {exc.code}.",
                step="download",
                hint="The provider link may have expired; submit the job again.",
            ) from exc
        except urllib.error.URLError as exc:
            raise DriverError(f"Video download failed: {exc.reason}.", step="download") from exc
