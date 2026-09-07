"""Local commands run in a short-lived, non-root sandbox container.

They never run inside Hermes (which holds every stack secret) and never in the
host namespace. The only writable path is the execution workspace.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .engine import SANDBOX_LABEL, DockerEngine, EngineError, _demultiplex
from .schema import MAX_OUTPUT_CHARS

SANDBOX_UID = 10002
SANDBOX_GID = 10002
WORKSPACE_TARGET = "/workspace"


def build_sandbox_body(request: dict[str, Any], *, image: str, workspace_source: str,
                       egress_network: str) -> dict[str, Any]:
    host_config: dict[str, Any] = {
        "Binds": [f"{workspace_source}:{WORKSPACE_TARGET}:rw"],
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "Memory": 512 * 1024 * 1024,
        "NanoCpus": 1_000_000_000,
        "PidsLimit": 256,
        "AutoRemove": False,
        "RestartPolicy": {"Name": "no"},
        "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
        # A sandbox never joins agent-net and never sees the host gateway, so an
        # approved command cannot reach n8n, 9router, or Hermes itself.
        "NetworkMode": egress_network if request["network"] == "egress" else "none",
    }
    if request["net_raw"]:
        host_config["CapAdd"] = ["NET_RAW"]

    return {
        "Image": image,
        "User": f"{SANDBOX_UID}:{SANDBOX_GID}",
        "WorkingDir": request["workdir"],
        "Entrypoint": ["/bin/sh", "-c"],
        "Cmd": [request["command"]],
        "Env": ["HOME=/tmp", "TMPDIR=/tmp", f"PWD={request['workdir']}"],
        "AttachStdin": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Tty": False,
        "Labels": {SANDBOX_LABEL: "true"},
        "HostConfig": host_config,
    }


def run_sandbox(engine: DockerEngine, request: dict[str, Any], *, image: str,
                workspace_source: str, egress_network: str) -> dict[str, Any]:
    body = build_sandbox_body(
        request, image=image, workspace_source=workspace_source, egress_network=egress_network
    )
    started = time.monotonic()
    container = engine.create(body, None, timeout=60)
    timed_out = False
    try:
        engine.start(container, timeout=60)
        try:
            returncode = engine.wait(container, timeout=request["timeout"] + 10)
        except (EngineError, OSError, TimeoutError):
            engine.kill(container)
            timed_out = True
            returncode = 124
        output = engine.logs(container, tail=2_000, timeout=60)
    finally:
        try:
            engine.remove(container, force=True, volumes=False, timeout=60)
        except EngineError:
            pass

    truncated = len(output) > MAX_OUTPUT_CHARS
    return {
        "returncode": returncode,
        "output": output[-MAX_OUTPUT_CHARS:],
        "truncated": truncated,
        "timed_out": timed_out,
        "duration": round(time.monotonic() - started, 3),
    }
