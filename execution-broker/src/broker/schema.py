"""Structured request validation for the stack execution broker.

Every request is rejected unless it matches these schemas exactly. Unknown
fields, unbounded lists, and control characters fail closed: the approval text
the operator reads must describe the whole operation, so anything that cannot be
rendered must not be executable.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

MAX_COMMAND_CHARS = 4_000
MAX_LIST_ITEMS = 32
MAX_ENV_ITEMS = 32
MAX_TIMEOUT_SECONDS = 900
MIN_TIMEOUT_SECONDS = 1
DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 12_000

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{0,255}$")
_IMAGE_DIGEST_RE = re.compile(
    r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
_CONTAINER_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CAP_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

DOCKER_ACTIONS = ("pull", "run", "start", "stop", "restart", "remove", "logs", "inspect", "list")
NAMESPACE_MODES = ("default", "host")


class RequestError(ValueError):
    """A request that must never reach the Docker Engine or an SSH host."""


def _text(value: Any, field: str, *, limit: int = MAX_COMMAND_CHARS) -> str:
    if not isinstance(value, str) or not value:
        raise RequestError(f"{field} must be a non-empty string.")
    if len(value) > limit:
        raise RequestError(f"{field} exceeds {limit} characters.")
    if _CONTROL_RE.search(value):
        raise RequestError(f"{field} contains control characters.")
    return value


def _bool(value: Any, field: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise RequestError(f"{field} must be a boolean.")
    return value


def _int(value: Any, field: str, *, low: int, high: int, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise RequestError(f"{field} is required.")
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError(f"{field} must be an integer.")
    if not low <= value <= high:
        raise RequestError(f"{field} must be between {low} and {high}.")
    return value


def _reject_unknown(payload: dict[str, Any], allowed: tuple[str, ...], field: str) -> None:
    if not isinstance(payload, dict):
        raise RequestError(f"{field} must be an object.")
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise RequestError(f"{field} has unsupported fields: {', '.join(unknown)}.")


def _string_list(value: Any, field: str, pattern: re.Pattern[str] | None = None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RequestError(f"{field} must be a list.")
    if len(value) > MAX_LIST_ITEMS:
        raise RequestError(f"{field} exceeds {MAX_LIST_ITEMS} entries.")
    items = []
    for index, item in enumerate(value):
        text = _text(item, f"{field}[{index}]", limit=512)
        if pattern is not None and not pattern.fullmatch(text):
            raise RequestError(f"{field}[{index}] has an unsupported format.")
        items.append(text)
    return items


def validate_local(payload: dict[str, Any]) -> dict[str, Any]:
    """A command for a short-lived, non-root sandbox container."""
    _reject_unknown(payload, ("command", "workdir", "timeout", "network", "net_raw"), "local request")
    network = payload.get("network", "none")
    if network not in ("none", "egress"):
        raise RequestError("network must be none or egress.")
    workdir = payload.get("workdir", "/workspace")
    workdir = _text(workdir, "workdir", limit=256)
    normalized = posixpath.normpath(workdir)
    if (not _PATH_RE.fullmatch(workdir) or normalized != workdir
            or (workdir != "/workspace" and not workdir.startswith("/workspace/"))):
        raise RequestError("workdir must be a normalized absolute path inside /workspace.")
    return {
        "command": _text(payload.get("command"), "command"),
        "workdir": workdir,
        "timeout": _int(payload.get("timeout"), "timeout", low=MIN_TIMEOUT_SECONDS,
                        high=MAX_TIMEOUT_SECONDS, default=DEFAULT_TIMEOUT_SECONDS),
        "network": network,
        "net_raw": _bool(payload.get("net_raw"), "net_raw"),
    }


def validate_ssh(payload: dict[str, Any]) -> dict[str, Any]:
    """A command for one locally configured profile.

    Host, user, port, identity, known-host file, and SSH options are never
    model-controlled; only the profile name and the remote command are.
    """
    _reject_unknown(payload, ("profile", "command", "timeout"), "ssh request")
    profile = _text(payload.get("profile"), "profile", limit=64)
    if not _PROFILE_RE.fullmatch(profile):
        raise RequestError("profile must be a lowercase locally configured name.")
    return {
        "profile": profile,
        "command": _text(payload.get("command"), "command"),
        "timeout": _int(payload.get("timeout"), "timeout", low=MIN_TIMEOUT_SECONDS,
                        high=MAX_TIMEOUT_SECONDS, default=DEFAULT_TIMEOUT_SECONDS),
    }


def _mounts(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RequestError("mounts must be a list.")
    if len(value) > MAX_LIST_ITEMS:
        raise RequestError(f"mounts exceeds {MAX_LIST_ITEMS} entries.")
    result = []
    for index, item in enumerate(value):
        _reject_unknown(item, ("type", "source", "target", "read_only"), f"mounts[{index}]")
        kind = item.get("type")
        if kind not in ("bind", "volume", "tmpfs"):
            raise RequestError(f"mounts[{index}].type must be bind, volume, or tmpfs.")
        source = _text(item.get("source", ""), f"mounts[{index}].source", limit=256) \
            if kind != "tmpfs" else ""
        target = _text(item.get("target"), f"mounts[{index}].target", limit=256)
        if not _PATH_RE.fullmatch(target):
            raise RequestError(f"mounts[{index}].target must be an absolute path.")
        if kind == "bind" and not _PATH_RE.fullmatch(source):
            raise RequestError(f"mounts[{index}].source must be an absolute host path.")
        result.append({
            "type": kind,
            "source": source,
            "target": target,
            "read_only": _bool(item.get("read_only"), f"mounts[{index}].read_only"),
        })
    return result


def _ports(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RequestError("ports must be a list.")
    if len(value) > MAX_LIST_ITEMS:
        raise RequestError(f"ports exceeds {MAX_LIST_ITEMS} entries.")
    result = []
    for index, item in enumerate(value):
        _reject_unknown(item, ("host_ip", "host_port", "container_port", "protocol"), f"ports[{index}]")
        protocol = item.get("protocol", "tcp")
        if protocol not in ("tcp", "udp"):
            raise RequestError(f"ports[{index}].protocol must be tcp or udp.")
        host_ip = _text(item.get("host_ip", "127.0.0.1"), f"ports[{index}].host_ip", limit=64)
        result.append({
            "host_ip": host_ip,
            "host_port": _int(item.get("host_port"), f"ports[{index}].host_port", low=1, high=65535),
            "container_port": _int(item.get("container_port"), f"ports[{index}].container_port",
                                   low=1, high=65535),
            "protocol": protocol,
        })
    return result


def _environment(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RequestError("environment must be a list.")
    if len(value) > MAX_ENV_ITEMS:
        raise RequestError(f"environment exceeds {MAX_ENV_ITEMS} entries.")
    result = []
    for index, item in enumerate(value):
        _reject_unknown(item, ("name", "value", "secret_ref"), f"environment[{index}]")
        name = _text(item.get("name"), f"environment[{index}].name", limit=128)
        if not _ENV_NAME_RE.fullmatch(name):
            raise RequestError(f"environment[{index}].name is not a valid variable name.")
        has_value = "value" in item and item["value"] is not None
        has_ref = "secret_ref" in item and item["secret_ref"] is not None
        if has_value == has_ref:
            raise RequestError(
                f"environment[{index}] needs exactly one of value or secret_ref."
            )
        if has_ref:
            raise RequestError(
                f"environment[{index}].secret_ref is not supported until a broker-owned "
                "secret resolver is configured."
            )
        else:
            result.append({"name": name, "value": _text(item["value"], f"environment[{index}].value",
                                                        limit=1024)})
    return result


def _validate_docker_network(value: Any) -> str:
    network = _text(value, "network", limit=64)
    if network != "none" and not _CONTAINER_REF_RE.fullmatch(network):
        raise RequestError("network must be none or a safe Docker network name.")
    return network


def validate_docker(payload: dict[str, Any]) -> dict[str, Any]:
    """A structured Docker operation. Raw CLI text and HostConfig are rejected."""
    if not isinstance(payload, dict):
        raise RequestError("docker request must be an object.")
    action = payload.get("action")
    if action not in DOCKER_ACTIONS:
        raise RequestError(f"action must be one of: {', '.join(DOCKER_ACTIONS)}.")

    if action == "list":
        _reject_unknown(payload, ("action", "all"), "docker request")
        return {"action": "list", "all": _bool(payload.get("all"), "all")}

    if action == "pull":
        _reject_unknown(payload, ("action", "image"), "docker request")
        image = _text(payload.get("image"), "image", limit=512)
        if not _IMAGE_DIGEST_RE.fullmatch(image):
            raise RequestError(
                "image must be pinned as repository@sha256:<digest>. A mutable tag could "
                "change between approval and execution."
            )
        return {"action": "pull", "image": image}

    if action in ("start", "stop", "restart", "remove", "logs", "inspect"):
        allowed = ("action", "container", "timeout")
        if action == "remove":
            allowed += ("force", "remove_volumes")
        if action == "logs":
            allowed += ("tail",)
        _reject_unknown(payload, allowed, "docker request")
        container = _text(payload.get("container"), "container", limit=128)
        if not (_CONTAINER_ID_RE.fullmatch(container) or _CONTAINER_REF_RE.fullmatch(container)):
            raise RequestError("container must be a container name or ID.")
        request = {
            "action": action,
            "container": container,
            "timeout": _int(payload.get("timeout"), "timeout", low=MIN_TIMEOUT_SECONDS,
                            high=MAX_TIMEOUT_SECONDS, default=DEFAULT_TIMEOUT_SECONDS),
        }
        if action == "remove":
            request["force"] = _bool(payload.get("force"), "force")
            request["remove_volumes"] = _bool(payload.get("remove_volumes"), "remove_volumes")
        if action == "logs":
            request["tail"] = _int(payload.get("tail"), "tail", low=1, high=2_000, default=200)
        return request

    _reject_unknown(payload, (
        "action", "image", "name", "entrypoint", "command", "user", "workdir", "environment",
        "mounts", "ports", "network", "dns", "labels", "capabilities_add", "capabilities_drop",
        "devices", "security_opt", "sysctls", "privileged", "pid_mode", "ipc_mode", "uts_mode",
        "userns_mode", "read_only_rootfs", "detach", "auto_remove", "memory_mb", "cpus",
        "pids_limit", "timeout", "restart_policy",
    ), "docker request")

    image = _text(payload.get("image"), "image", limit=512)
    if not (_IMAGE_DIGEST_RE.fullmatch(image) or _IMAGE_ID_RE.fullmatch(image)):
        raise RequestError(
            "image must be a local image ID or repository@sha256:<digest>. Pull the exact "
            "digest as a separate approved operation first."
        )
    name = payload.get("name")
    if name is not None:
        name = _text(name, "name", limit=128)
        if not _CONTAINER_REF_RE.fullmatch(name):
            raise RequestError("name has an unsupported format.")
    restart_policy = payload.get("restart_policy", "no")
    if restart_policy not in ("no", "on-failure", "unless-stopped", "always"):
        raise RequestError("restart_policy must be no, on-failure, unless-stopped, or always.")
    for field in ("pid_mode", "ipc_mode", "uts_mode"):
        if payload.get(field, "default") not in NAMESPACE_MODES:
            raise RequestError(f"{field} must be default or host.")
    userns_mode = payload.get("userns_mode", "default")
    if userns_mode not in ("default", "host"):
        raise RequestError("userns_mode must be default or host.")

    return {
        "action": "run",
        "image": image,
        "name": name,
        "entrypoint": _string_list(payload.get("entrypoint"), "entrypoint"),
        "command": _string_list(payload.get("command"), "command"),
        "user": _text(payload.get("user"), "user", limit=64) if payload.get("user") else "",
        "workdir": _text(payload.get("workdir"), "workdir", limit=256) if payload.get("workdir") else "",
        "environment": _environment(payload.get("environment")),
        "mounts": _mounts(payload.get("mounts")),
        "ports": _ports(payload.get("ports")),
        "network": _validate_docker_network(payload.get("network", "none")),
        "dns": _string_list(payload.get("dns"), "dns"),
        "labels": _string_list(payload.get("labels"), "labels"),
        "capabilities_add": _string_list(payload.get("capabilities_add"), "capabilities_add", _CAP_RE),
        "capabilities_drop": _string_list(payload.get("capabilities_drop"), "capabilities_drop", _CAP_RE),
        "devices": _string_list(payload.get("devices"), "devices"),
        "security_opt": _string_list(payload.get("security_opt"), "security_opt"),
        "sysctls": _string_list(payload.get("sysctls"), "sysctls"),
        "privileged": _bool(payload.get("privileged"), "privileged"),
        "pid_mode": payload.get("pid_mode", "default"),
        "ipc_mode": payload.get("ipc_mode", "default"),
        "uts_mode": payload.get("uts_mode", "default"),
        "userns_mode": userns_mode,
        "read_only_rootfs": _bool(payload.get("read_only_rootfs"), "read_only_rootfs", True),
        "detach": _bool(payload.get("detach"), "detach"),
        "auto_remove": _bool(payload.get("auto_remove"), "auto_remove"),
        "memory_mb": _int(payload.get("memory_mb"), "memory_mb", low=16, high=32_768, default=512),
        "cpus": _int(payload.get("cpus"), "cpus", low=1, high=32, default=1),
        "pids_limit": _int(payload.get("pids_limit"), "pids_limit", low=16, high=8_192, default=256),
        "timeout": _int(payload.get("timeout"), "timeout", low=MIN_TIMEOUT_SECONDS,
                        high=MAX_TIMEOUT_SECONDS, default=DEFAULT_TIMEOUT_SECONDS),
        "restart_policy": restart_policy,
    }


VALIDATORS = {
    "local": validate_local,
    "ssh": validate_ssh,
    "docker": validate_docker,
}


def validate(feature: str, payload: Any) -> dict[str, Any]:
    validator = VALIDATORS.get(feature)
    if validator is None:
        raise RequestError(f"Unsupported execution feature: {feature}.")
    if not isinstance(payload, dict):
        raise RequestError("request must be an object.")
    return validator(payload)
