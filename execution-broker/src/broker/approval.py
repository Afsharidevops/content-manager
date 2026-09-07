"""Canonical digests and the approval text the operator actually reads.

The summary must describe every effect of the operation, including the ones that
are "off": an operator who never sees `privileged: no` cannot tell the difference
between a safe default and an omitted field.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from typing import Any

MAX_SUMMARY_CHARS = 3_500

# Defense in depth only. An approved privileged container or an approved remote
# root command can still destroy the host; this floor stops the obvious cases
# that would also destroy the approval mechanism itself.
_DENY_PATTERNS = (
    (re.compile(r"(?i)\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\s+(-[a-z]+\s+)*/(?:\s|$)"),
     "recursive deletion of the filesystem root"),
    (re.compile(r"(?i)\bmkfs(\.[a-z0-9]+)?\b"), "filesystem creation on a block device"),
    (re.compile(r"(?i)\bdd\b[^\n]*\bof=/dev/(?:sd|nvme|vd|hd|mmcblk|xvd)"),
     "raw write to a block device"),
    (re.compile(r"(?i)>\s*/dev/(?:sd|nvme|vd|hd|mmcblk|xvd)"), "raw write to a block device"),
    (re.compile(r"(?i)\b(?:shutdown|reboot|halt|poweroff)\b"), "host shutdown or reboot"),
    (re.compile(r"(?i)\binit\s+0\b"), "host shutdown"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"(?i)\bchmod\s+(-[a-z]+\s+)*777\s+/(?:\s|$)"), "world-writable filesystem root"),
    (re.compile(r"(?i)/var/run/docker\.sock|/run/docker\.sock"),
     "delegation of the host Docker socket, which would bypass future approvals"),
)

PROTECTED_CONTAINERS = (
    "hermes-execution-docker-broker",
    "hermes-execution-ssh-broker",
    "hermes-execution-approver",
    "hermes-agent",
)
_PROTECTED_CONTAINER_IDS: frozenset[str] = frozenset()
PROTECTED_NETWORKS = (
    "hermes-execution-control",
    "execution-control-net",
)
_PROTECTED_NETWORK_IDS: frozenset[str] = frozenset()
_PROTECTED_HOST_PATHS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/run/secrets",
    "/state",
    "/profiles",
)


def _path_exposes(candidate: str, protected: str) -> bool:
    candidate = posixpath.normpath(candidate)
    protected = posixpath.normpath(protected)
    return (candidate == protected
            or protected.startswith(candidate.rstrip("/") + "/")
            or candidate.startswith(protected.rstrip("/") + "/"))


def set_protected_container_ids(ids: set[str] | frozenset[str]) -> None:
    global _PROTECTED_CONTAINER_IDS
    _PROTECTED_CONTAINER_IDS = _PROTECTED_CONTAINER_IDS.union(ids)


def set_protected_network_ids(ids: set[str] | frozenset[str]) -> None:
    global _PROTECTED_NETWORK_IDS
    _PROTECTED_NETWORK_IDS = _PROTECTED_NETWORK_IDS.union(ids)


def _protected_target(request: dict[str, Any]) -> bool:
    container = str(request.get("container", ""))
    return (request.get("resolved_name") in PROTECTED_CONTAINERS
            or any(protected.startswith(container) or container.startswith(protected)
                   for protected in _PROTECTED_CONTAINER_IDS if container))


class DeniedError(ValueError):
    """An operation the broker refuses regardless of approval."""


def check_floor(feature: str, request: dict[str, Any]) -> None:
    if feature in ("local", "ssh"):
        command = request.get("command", "")
        for pattern, reason in _DENY_PATTERNS:
            if pattern.search(command):
                raise DeniedError(f"Refused unconditionally: {reason}.")
        return

    if request.get("action") in ("start", "stop", "restart", "remove", "logs", "inspect"):
        target = request.get("container", "")
        if target in PROTECTED_CONTAINERS or _protected_target(request):
            raise DeniedError(
                "Refused unconditionally: the execution brokers and Hermes cannot be "
                "targeted or inspected, because that could disable or disclose the approval gate."
            )
        return

    if request.get("action") == "run":
        network = request.get("resolved_network_id") or request.get("network", "none")
        if request.get("network") in PROTECTED_NETWORKS or network in _PROTECTED_NETWORK_IDS:
            raise DeniedError(
                "Refused unconditionally: containers cannot join the execution control network."
            )
        for mount in request.get("mounts", []):
            if mount["type"] == "bind" and any(
                _path_exposes(mount["source"], protected)
                for protected in _PROTECTED_HOST_PATHS
            ):
                raise DeniedError(
                    "Refused unconditionally: this host bind exposes execution authority or "
                    "the Docker socket and would bypass future approvals."
                )


def canonical_digest(feature: str, request: dict[str, Any]) -> str:
    """A digest over the exact request. Any mutation invalidates the approval."""
    payload = json.dumps(
        {"feature": feature, "request": request},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _render_local(request: dict[str, Any]) -> tuple[list[str], list[str]]:
    lines = [
        "Local sandbox command",
        f"  image:     {request.get('resolved_image_id', '(unresolved)')}",
        f"  workspace: {request.get('workspace_generation', '(unsealed)')}",
        f"  command:   {request['command']}",
        f"  workdir:   {request['workdir']}",
        f"  timeout:   {request['timeout']}s",
        f"  network:   {request['network']}",
        f"  NET_RAW:   {_yes_no(request['net_raw'])}",
        "  identity:  non-root sandbox container, read-only root, all capabilities dropped",
        "  writable:  the execution workspace only; no Hermes secrets or host paths",
    ]
    warnings = []
    if request["network"] == "egress":
        warnings.append("This command has outbound network access.")
    if request["net_raw"]:
        warnings.append("NET_RAW is granted, allowing raw sockets (ping/traceroute).")
    return lines, warnings


def _render_ssh(request: dict[str, Any], profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    sealed = request.get("sealed_profile", profile)
    authority = sealed.get("authority", "unknown")
    auth = sealed.get("auth", "publickey")
    authentication = "password (broker-held)" if auth == "password" else "public key"
    lines = [
        "Remote SSH command",
        f"  profile:   {request['profile']}",
        f"  target:    {sealed.get('user', '?')}@{sealed.get('host', '?')}:{sealed.get('port', 22)}",
        f"  authority: {authority}",
        f"  host key:  {sealed.get('fingerprint', 'missing')}",
        f"  auth:      {authentication}",
    ]
    if auth == "publickey":
        lines.append(f"  identity:  {sealed.get('identity_fingerprint', 'missing')}")
    lines.extend([
        f"  command:   {request['command']}",
        f"  timeout:   {request['timeout']}s",
        "  options:   no agent/X11/port forwarding, no TTY, pinned host key",
    ])
    if auth == "password":
        lines.append("  password:  one broker-held prompt; public-key and keyboard-interactive auth disabled")
    warnings = []
    if authority in ("root", "sudo-nopasswd"):
        warnings.append(
            "This account is remote-root-equivalent. The command can change the remote "
            "host irreversibly, and approval does not make it reversible."
        )
    return lines, warnings


def _render_docker(request: dict[str, Any]) -> tuple[list[str], list[str]]:
    action = request["action"]
    warnings: list[str] = []

    if action == "list":
        return ["Docker: list containers"], warnings
    if action == "pull":
        return [
            "Docker: pull image",
            f"  image:     {request['image']}",
            "  pinned:    yes (immutable digest)",
        ], warnings
    if action in ("start", "stop", "restart", "remove", "logs", "inspect"):
        lines = [
            f"Docker: {action} container",
            f"  name:      {request.get('resolved_name', '(unknown)')}",
            f"  id:        {request['container']}",
            f"  timeout:   {request['timeout']}s",
        ]
        if action == "inspect":
            lines.append("  output:    redacted (environment values, sensitive labels, and mounts removed)")
        if action == "remove":
            lines.append(f"  force:     {_yes_no(request['force'])}")
            lines.append(f"  volumes:   {_yes_no(request['remove_volumes'])}")
            if request["remove_volumes"]:
                warnings.append("Named volumes will be deleted; their data is not recoverable.")
        if action == "logs":
            lines.append(f"  tail:      {request['tail']} lines")
        return lines, warnings

    env_names = [
        f"{item['name']}=<secret:{item['secret_ref']}>" if "secret_ref" in item
        else f"{item['name']}={item['value']}"
        for item in request["environment"]
    ]
    mounts = [
        f"{m['type']} {m['source'] or '(anonymous)'} -> {m['target']} "
        f"({'ro' if m['read_only'] else 'rw'})"
        for m in request["mounts"]
    ]
    ports = [
        f"{p['host_ip']}:{p['host_port']} -> {p['container_port']}/{p['protocol']}"
        for p in request["ports"]
    ]
    def show(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) if value else "none"
        return str(value) if value not in ("", None) else "none"

    lines = [
        "Docker: run container on the host daemon",
        f"  image:      {request['image']}",
        f"  name:       {request['name'] or '(generated)'}",
        f"  entrypoint: {show(request['entrypoint']) if request['entrypoint'] else '(image default)'}",
        f"  command:    {show(request['command']) if request['command'] else '(image default)'}",
        f"  user:       {request['user'] or '(image default)'}",
        f"  workdir:    {request['workdir'] or '(image default)'}",
        f"  env:        {show(env_names)}",
        f"  mounts:     {show(mounts)}",
        f"  ports:      {show(ports)}",
        f"  network:    {request['network']}",
        f"  dns:        {show(request['dns']) if request['dns'] else 'default'}",
        f"  privileged: {_yes_no(request['privileged'])}",
        f"  cap_add:    {show(request['capabilities_add'])}",
        f"  cap_drop:   {show(request['capabilities_drop'])}",
        f"  devices:    {show(request['devices'])}",
        f"  security:   {show(request['security_opt']) if request['security_opt'] else 'no-new-privileges:true (default)'}",
        f"  sysctls:    {show(request['sysctls'])}",
        f"  pid ns:     {request['pid_mode']}",
        f"  ipc ns:     {request['ipc_mode']}",
        f"  uts ns:     {request['uts_mode']}",
        f"  user ns:    {request['userns_mode']}",
        f"  read-only:  {_yes_no(request['read_only_rootfs'])}",
        f"  detach:     {_yes_no(request['detach'])}",
        f"  auto-rm:    {_yes_no(request['auto_remove'])}",
        f"  restart:    {request['restart_policy']}",
        f"  limits:     {request['memory_mb']}MB memory, {request['cpus']} CPU, "
        f"{request['pids_limit']} PIDs",
        f"  timeout:    {request['timeout']}s",
    ]

    if request["privileged"]:
        warnings.append("PRIVILEGED: this container is host-root-equivalent.")
    for field, label in (("pid_mode", "PID"), ("ipc_mode", "IPC"),
                         ("uts_mode", "UTS"), ("userns_mode", "user")):
        if request[field] == "host":
            warnings.append(f"Host {label} namespace: container isolation from the host is removed.")
    if request["devices"]:
        warnings.append("Host devices are exposed to the container.")
    if request["capabilities_add"]:
        warnings.append(f"Extra capabilities granted: {', '.join(request['capabilities_add'])}.")
    for mount in request["mounts"]:
        if mount["type"] == "bind":
            access = "read-only" if mount["read_only"] else "READ-WRITE"
            warnings.append(f"Host path {mount['source']} is bind-mounted {access}.")
    if request["ports"]:
        warnings.append("The container publishes host ports reachable from the network.")
    if request["restart_policy"] != "no":
        warnings.append("This container restarts automatically and outlives this approval.")

    return lines, warnings


def render_summary(feature: str, request: dict[str, Any],
                   profile: dict[str, Any] | None = None) -> str:
    if feature == "local":
        lines, warnings = _render_local(request)
    elif feature == "ssh":
        lines, warnings = _render_ssh(request, profile or {})
    elif feature == "docker":
        lines, warnings = _render_docker(request)
    else:
        raise DeniedError(f"Unsupported execution feature: {feature}.")

    if warnings:
        lines.append("")
        lines.append("Consequences:")
        lines.extend(f"  ! {warning}" for warning in warnings)

    summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY_CHARS:
        # A truncated approval would hide part of what is being authorised.
        raise DeniedError(
            "This operation cannot be rendered within the approval limit. Split it into "
            "smaller operations so every effect stays visible before approval."
        )
    return summary
