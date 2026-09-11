"""Read-only views over the running stack for the operator panel.

Every helper shells out to the Docker CLI or ``manage.sh`` with an explicit
timeout and returns plain Python data; nothing here mutates the stack. Action
execution lives in ``panel.actions`` so read paths stay side-effect free.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 30.0


class CommandError(RuntimeError):
    """Raised when a read-only stack command cannot be executed."""


@dataclass
class CommandResult:
    command: list
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    """Run external commands with a timeout and captured output."""

    def __init__(self, root: Path, env: dict | None = None):
        self.root = Path(root)
        self.env = dict(os.environ)
        if env:
            self.env.update(env)

    def run(self, command, *, timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        try:
            completed = subprocess.run(
                [str(part) for part in command],
                cwd=str(self.root),
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as error:
            raise CommandError(f"command not found: {error.filename}") from error
        except subprocess.TimeoutExpired as error:
            output = (error.output or b"").decode("utf-8", "replace")
            raise CommandError(
                f"command timed out after {timeout:.0f}s: {' '.join(map(str, command))}"
                + (f"\n{output.strip()}" if output.strip() else "")
            ) from error
        return CommandResult(
            command=[str(part) for part in command],
            returncode=completed.returncode,
            output=completed.stdout.decode("utf-8", "replace"),
        )


def _parse_compose_rows(output: str) -> list[dict]:
    """Parse ``docker compose ps --format json`` output (lines or array)."""
    text = output.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


class StackView:
    """Status and log views for one compose project."""

    def __init__(self, root: Path, runner: CommandRunner | None = None):
        self.root = Path(root)
        self.runner = runner or CommandRunner(self.root)

    # -------------------------------------------------------------- compose

    def compose_args(self, *args: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-directory",
            str(self.root),
            "-f",
            str(self.root / "docker-compose.yml"),
            "--env-file",
            str(self.root / ".env"),
            *args,
        ]

    def services(self) -> list[dict]:
        result = self.runner.run(self.compose_args("ps", "--all", "--format", "json"))
        rows = _parse_compose_rows(result.output)
        services = []
        for row in rows:
            service = str(row.get("Service") or row.get("Name") or "")
            services.append(
                {
                    "service": service,
                    "name": str(row.get("Name") or service),
                    "state": str(row.get("State") or ""),
                    "status": str(row.get("Status") or ""),
                    "health": str(row.get("Health") or ""),
                    "image": str(row.get("Image") or ""),
                    "exit_code": row.get("ExitCode"),
                    "ports": [
                        str(publisher.get("PublishedPort"))
                        for publisher in (row.get("Publishers") or [])
                        if isinstance(publisher, dict) and publisher.get("PublishedPort")
                    ],
                    "running_for": str(row.get("RunningFor") or ""),
                }
            )
        services.sort(key=lambda item: item["service"])
        return services

    def profiles(self) -> list[str]:
        env_path = self.root / ".env"
        try:
            text = env_path.read_text(encoding="utf-8")
        except OSError:
            return []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("COMPOSE_PROFILES="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return [part.strip() for part in value.split(",") if part.strip()]
        return []

    def env_tags(self) -> dict:
        """Image tags pinned in ``.env`` for the stack-owned images."""
        wanted = (
            "CONTENT_BOT_IMAGE_TAG",
            "MEDIA_STUDIO_IMAGE_TAG",
            "SMART_ROUTER_IMAGE_TAG",
        )
        tags = {}
        try:
            lines = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        except OSError:
            return tags
        for line in lines:
            line = line.strip()
            for key in wanted:
                if line.startswith(f"{key}="):
                    tags[key] = line.split("=", 1)[1].strip().strip('"').strip("'")
        return tags

    def logs(self, service: str, *, lines: int = 200) -> list[str]:
        if not service or not service.replace("-", "").replace("_", "").isalnum():
            raise CommandError("invalid service name")
        result = self.runner.run(
            self.compose_args("logs", "--no-color", "--tail", str(int(lines)), service),
            timeout=30.0,
        )
        if result.returncode != 0 and not result.output.strip():
            raise CommandError(result.output.strip() or "docker compose logs failed")
        return result.output.rstrip("\n").splitlines()[-int(lines):]

    def disk(self) -> dict:
        usage = shutil.disk_usage(str(self.root))
        data_dir = self.root / "data"
        sizes = []
        if data_dir.is_dir():
            for entry in sorted(data_dir.iterdir()):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if entry.name in {"stack-secrets"}:
                    continue
                try:
                    result = self.runner.run(["du", "-s", "-m", str(entry)], timeout=5.0)
                except CommandError:
                    continue
                if not result.ok:
                    continue
                first = result.output.split("\t", 1)[0].strip()
                if first.isdigit():
                    sizes.append({"name": entry.name, "bytes": int(first) * 1024 * 1024})
        sizes.sort(key=lambda item: item["bytes"], reverse=True)
        return {
            "filesystem": {
                "total": _human_size(usage.total),
                "used": _human_size(usage.used),
                "free": _human_size(usage.free),
                "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
            },
            "data": [
                {"name": item["name"], "size": _human_size(item["bytes"])} for item in sizes[:12]
            ],
        }

    # Service -> environment key that controls the published host address.
    # Used to warn when an internal-only endpoint (MCP, router dashboards) is
    # reachable beyond loopback.
    BIND_KEYS = {
        "n8n": "N8N_BIND_IP",
        "nine-router": "NINEROUTER_BIND_IP",
        "omniroute": "OMNIROUTE_BIND_IP",
        "smart-router": "SMART_ROUTER_BIND_IP",
        "media-studio": "MEDIA_STUDIO_BIND_IP",
        "caddy": "CADDY_BIND_IP",
        "open-webui": "OPENWEBUI_BIND_IP",
        "rustfs": "RUSTFS_BIND_IP",
    }

    def _env_map(self) -> dict:
        values = {}
        try:
            lines = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        except OSError:
            return values
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values

    def exposure(self) -> dict:
        """Loopback vs network bind per service plus operator warnings."""
        env = self._env_map()
        rows = []
        warnings = []
        for service, key in self.BIND_KEYS.items():
            bind = env.get(key, "")
            if not bind:
                continue
            local = bind in {"127.0.0.1", "::1", "localhost"}
            rows.append({"service": service, "key": key, "bind": bind, "loopback": local})
            if not local and service == "n8n":
                warnings.append(
                    "n8n publishes its MCP endpoint and editor to "
                    f"{bind}. Set {key}=127.0.0.1 to keep MCP inside the "
                    "Docker network unless remote access is required."
                )
            elif not local and service in {"nine-router", "omniroute", "smart-router"}:
                warnings.append(
                    f"{service} is published to {bind} ({key}); confirm this is "
                    "intended for a trusted network only."
                )
            elif not local and service == "rustfs":
                warnings.append(
                    f"RustFS publishes the S3 API to {bind} ({key}). Keep it "
                    "behind a reverse proxy with strong credentials, and leave "
                    "RUSTFS_CONSOLE_BIND_IP on loopback unless the console must "
                    "be reachable too."
                )
        return {"rows": rows, "warnings": warnings}

    def snapshot(self) -> dict:
        """One payload with everything the overview page needs."""
        payload = {
            "profiles": self.profiles(),
            "image_tags": self.env_tags(),
            "disk": self.disk(),
            "exposure": self.exposure(),
            "services": [],
            "error": "",
        }
        try:
            payload["services"] = self.services()
        except CommandError as error:
            payload["error"] = str(error)
        return payload
