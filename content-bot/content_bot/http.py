"""Small JSON/bytes HTTP transport built on the standard library."""

from __future__ import annotations

import json
import http.client
import urllib.error
import urllib.request
import uuid

USER_AGENT = "ContentBot/0.1"


class HttpError(RuntimeError):
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}")


def request_bytes(
    url: str,
    *,
    method: str | None = None,
    headers: dict | None = None,
    payload: dict | None = None,
    timeout: int = 30,
    max_bytes: int = 4_000_000,
) -> tuple[int, bytes]:
    """Perform one HTTP request and return ``(status, body)``."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    content_type = "application/json" if payload is not None else None
    return _request(url, data=data, headers=headers, content_type=content_type, method=method, timeout=timeout, max_bytes=max_bytes)


def request_multipart(
    url: str,
    *,
    fields: dict | None = None,
    file_field: str = "file",
    filename: str = "file",
    file_bytes: bytes = b"",
    headers: dict | None = None,
    timeout: int = 120,
    max_bytes: int = 60_000_000,
) -> tuple[int, bytes]:
    """Perform one multipart/form-data upload and return ``(status, body)``."""
    boundary = f"----ContentBot{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; '
            f'name="{file_field}"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return _request(
        url,
        data=b"".join(parts),
        headers=headers,
        content_type=f"multipart/form-data; boundary={boundary}",
        method=None,
        timeout=timeout,
        max_bytes=max_bytes,
    )


def _request(
    url: str,
    *,
    data: bytes | None,
    headers: dict | None,
    content_type: str | None,
    method: str | None,
    timeout: int,
    max_bytes: int,
) -> tuple[int, bytes]:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    request_headers.update(headers or {})
    if content_type:
        request_headers["Content-Type"] = content_type
    if method is None:
        method = "POST" if data is not None else "GET"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise HttpError(0, b"response body exceeds the size limit")
            return int(response.status), body
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(max_bytes + 1)
    except urllib.error.URLError as error:
        raise ConnectionError(str(error.reason)) from error
    except (http.client.IncompleteRead, TimeoutError) as error:
        raise ConnectionError(f"connection error: {error}") from error


def request_json(
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
) -> object:
    """Perform one HTTP request and parse the JSON response body."""
    status, body = request_bytes(url, headers=headers, payload=payload, timeout=timeout)
    if status >= 400:
        raise HttpError(status, body[:2048])
    return json.loads(body.decode("utf-8", "replace"))
