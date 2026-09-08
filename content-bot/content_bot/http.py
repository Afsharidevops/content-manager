"""Small JSON/bytes HTTP transport built on the standard library."""

from __future__ import annotations

import json
import http.client
import urllib.error
import urllib.request

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
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    request_headers.update(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
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
