"""Minimal Docker Engine client over the unix socket.

Only the endpoints the broker needs are implemented, and every container body is
built here from an already-validated request — never from caller-supplied JSON.
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any
from urllib.parse import quote, urlencode

API_VERSION = "v1.44"
SANDBOX_LABEL = "stack.execution.sandbox"


class EngineError(RuntimeError):
    pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerEngine:
    def __init__(self, socket_path: str = "/var/run/docker.sock"):
        self._socket_path = socket_path

    def request(self, method: str, path: str, *, body: Any = None,
                params: dict[str, Any] | None = None, timeout: float = 60.0,
                raw: bool = False) -> Any:
        url = f"/{API_VERSION}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        payload = None
        headers = {"Host": "docker", "Accept": "application/json"}
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = _UnixConnection(self._socket_path, timeout)
        try:
            connection.request(method, url, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            if response.status >= 400:
                raise EngineError(f"Docker Engine returned HTTP {response.status}: "
                                  f"{data[:500].decode('utf-8', 'replace')}")
            if raw:
                return data
            if not data:
                return None
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return data.decode("utf-8", "replace")
        finally:
            connection.close()

    def ping(self) -> bool:
        connection = _UnixConnection(self._socket_path, 5.0)
        try:
            connection.request("GET", "/_ping", headers={"Host": "docker"})
            return connection.getresponse().status == 200
        except OSError:
            return False
        finally:
            connection.close()

    def pull(self, image: str, timeout: float) -> str:
        repository, _, digest = image.partition("@")
        data = self.request(
            "POST", "/images/create",
            params={"fromImage": repository, "tag": digest},
            timeout=timeout, raw=True,
        )
        return data.decode("utf-8", "replace")[-4_000:]

    def resolve_image(self, image: str) -> str:
        """Return the immutable local image ID, or fail if it is absent."""
        info = self.request("GET", f"/images/{quote(image, safe='')}/json", timeout=30)
        return info["Id"]

    def resolve_container(self, reference: str) -> dict[str, Any]:
        """Bind a name to an immutable ID at preparation time.

        A name can be detached and reattached to a different container between
        approval and execution; an ID cannot.
        """
        info = self.request("GET", f"/containers/{quote(reference, safe='')}/json", timeout=30)
        return {"id": info["Id"], "name": info["Name"].lstrip("/"), "image": info["Image"]}

    def resolve_network(self, reference: str) -> dict[str, str]:
        """Bind a Docker network name to its immutable ID."""
        info = self.request("GET", f"/networks/{quote(reference, safe='')}", timeout=30)
        return {"id": info["Id"], "name": info["Name"]}

    def create(self, body: dict[str, Any], name: str | None, timeout: float) -> str:
        params = {"name": name} if name else None
        result = self.request("POST", "/containers/create", body=body, params=params, timeout=timeout)
        return result["Id"]

    def start(self, container: str, timeout: float) -> None:
        self.request("POST", f"/containers/{quote(container, safe='')}/start", timeout=timeout)

    def stop(self, container: str, timeout: float) -> None:
        self.request("POST", f"/containers/{quote(container, safe='')}/stop",
                     params={"t": 10}, timeout=timeout)

    def restart(self, container: str, timeout: float) -> None:
        self.request("POST", f"/containers/{quote(container, safe='')}/restart",
                     params={"t": 10}, timeout=timeout)

    def remove(self, container: str, *, force: bool, volumes: bool, timeout: float) -> None:
        self.request("DELETE", f"/containers/{quote(container, safe='')}",
                     params={"force": str(force).lower(), "v": str(volumes).lower()},
                     timeout=timeout)

    def wait(self, container: str, timeout: float) -> int:
        result = self.request("POST", f"/containers/{quote(container, safe='')}/wait",
                              timeout=timeout)
        return int(result.get("StatusCode", -1))

    def kill(self, container: str) -> None:
        try:
            self.request("POST", f"/containers/{quote(container, safe='')}/kill", timeout=30)
        except EngineError:
            pass

    def logs(self, container: str, *, tail: int, timeout: float) -> str:
        data = self.request(
            "GET", f"/containers/{quote(container, safe='')}/logs",
            params={"stdout": "true", "stderr": "true", "tail": tail},
            timeout=timeout, raw=True,
        )
        return _demultiplex(data)

    def inspect(self, container: str, timeout: float) -> dict[str, Any]:
        return self.request("GET", f"/containers/{quote(container, safe='')}/json", timeout=timeout)

    def list_containers(self, *, all_containers: bool, timeout: float) -> list[dict[str, Any]]:
        items = self.request("GET", "/containers/json",
                             params={"all": str(all_containers).lower()}, timeout=timeout)
        return [
            {
                "id": item["Id"][:12],
                "names": [name.lstrip("/") for name in item.get("Names", [])],
                "image": item.get("Image"),
                "state": item.get("State"),
                "status": item.get("Status"),
            }
            for item in items or []
        ]


def _demultiplex(data: bytes) -> str:
    """Docker multiplexes non-TTY output into 8-byte-framed chunks."""
    output: list[str] = []
    offset = 0
    while offset + 8 <= len(data):
        if data[offset] not in (0, 1, 2):
            return data.decode("utf-8", "replace")
        length = int.from_bytes(data[offset + 4:offset + 8], "big")
        chunk = data[offset + 8:offset + 8 + length]
        output.append(chunk.decode("utf-8", "replace"))
        offset += 8 + length
    if not output:
        return data.decode("utf-8", "replace")
    return "".join(output)
