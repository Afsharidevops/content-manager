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
from datetime import datetime, timezone
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

    def run(self, command, *, timeout: float = DEFAULT_TIMEOUT, env: dict | None = None) -> CommandResult:
        run_env = dict(self.env)
        if env:
            run_env.update({str(key): str(value) for key, value in env.items()})
        try:
            completed = subprocess.run(
                [str(part) for part in command],
                cwd=str(self.root),
                env=run_env,
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


def _parse_section_rows(output: str) -> list[dict]:
    """Parse the table printed by ``./manage.sh backup-sections``."""
    rows = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("./", "SECTION", "Examples:")) or ":" in stripped.split(" ")[0]:
            continue
        name, _, paths = stripped.partition(" ")
        # Section names are lower-case and may carry a digit (n8n, s3).
        if not (name.isalnum() and name.islower() and not name[0].isdigit()):
            continue
        if any(row["name"] == name for row in rows):
            continue
        rows.append({"name": name, "paths": paths.strip()})
    return rows


def backup_sections(runner: "CommandRunner", root: Path) -> list[dict]:
    """Section names and paths accepted by ``manage.sh backup --only``.

    ``scripts/stack-ops.sh`` owns the section table and prints it without
    touching the running stack, so the panel reads it instead of keeping a
    second copy that could drift.
    """
    try:
        result = runner.run([str(Path(root) / "manage.sh"), "backup-sections"], timeout=60.0)
    except (CommandError, OSError):
        return []
    return _parse_section_rows(result.output)


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

    # -------------------------------------------------------------- storage

    def _service_state(self, name: str) -> dict | None:
        """Compose state for one service, or None when it is not defined."""
        try:
            services = self.services()
        except (CommandError, ValueError):
            return None
        for service in services:
            if service.get("service") == name or service.get("name") == name:
                return {
                    "state": str(service.get("state") or ""),
                    "status": str(service.get("status") or ""),
                    "health": str(service.get("health") or ""),
                }
        return None

    def _rustfs_view(self, env: dict) -> dict:
        host = env.get("RUSTFS_BIND_IP") or "127.0.0.1"
        port = env.get("RUSTFS_PORT") or "9000"
        console_host = env.get("RUSTFS_CONSOLE_BIND_IP") or "127.0.0.1"
        console_port = env.get("RUSTFS_CONSOLE_PORT") or "9001"
        return {
            "api_url": f"http://{host}:{port}",
            "console_url": f"http://{console_host}:{console_port}/rustfs/console/",
            "bind": host,
            "console_bind": console_host,
            "service": self._service_state("rustfs"),
        }

    def storage(self) -> dict:
        """Shared S3 configuration, consumers, and the bundled RustFS state."""
        env = self._env_map()
        backend = (env.get("S3_STORAGE_BACKEND") or "off").strip().lower()
        if backend not in {"rustfs", "external"}:
            backend = "off"
        webui_mode = (env.get("OPENWEBUI_STORAGE_PROVIDER") or "local").strip().lower()
        warnings = []
        if webui_mode == "s3" and backend == "off":
            warnings.append(
                "Open WebUI is set to store files in the bucket, but the shared "
                "object storage is off. Run ./manage.sh s3-enable (or fix "
                "OPENWEBUI_STORAGE_PROVIDER) before uploading anything."
            )
        if backend == "external" and not env.get("S3_ENDPOINT_URL"):
            warnings.append(
                "The external backend is selected without S3_ENDPOINT_URL; "
                "services cannot reach the provider until it is set."
            )
        consumers = [
            {
                "service": "open-webui",
                "mode": "s3" if webui_mode == "s3" else "local",
                "note": (
                    "Uploads and generated files go to the bucket."
                    if webui_mode == "s3"
                    else "Local volumes; set OPENWEBUI_STORAGE_PROVIDER=s3 to use the bucket."
                ),
            },
            {
                "service": "content-bot",
                "mode": "local",
                "note": "Drafts, media, and Instagram state stay in data/content-bot.",
            },
            {
                "service": "media-studio",
                "mode": "local",
                "note": "Jobs and generated media stay in data/media-studio.",
            },
            {
                "service": "n8n",
                "mode": "local",
                "note": "External binary storage requires n8n Enterprise.",
            },
            {
                "service": "hermes-agent",
                "mode": "none",
                "note": "No object-storage integration.",
            },
        ]
        rustfs = self._rustfs_view(env) if backend == "rustfs" or env.get("RUSTFS_BIND_IP") else None
        return {
            "backend": backend,
            "endpoint": env.get("S3_ENDPOINT_URL", ""),
            "host_endpoint": env.get("S3_HOST_ENDPOINT_URL", ""),
            "bucket": env.get("S3_BUCKET", ""),
            "region": env.get("S3_REGION", ""),
            "key_prefix": env.get("S3_KEY_PREFIX", ""),
            "force_path_style": (env.get("S3_FORCE_PATH_STYLE", "") or "").lower() == "true",
            "public_base_url": env.get("S3_PUBLIC_BASE_URL", ""),
            "public_console_url": env.get("S3_PUBLIC_CONSOLE_URL", ""),
            "openwebui_storage_provider": webui_mode,
            "rustfs": rustfs,
            "consumers": consumers,
            "warnings": warnings,
            "guide": "docs/S3-STORAGE.md",
        }

    def backups(self) -> dict:
        """Backup archives in the stack backup directory.

        Listing happens here instead of shelling out to ``manage.sh
        backup-list``: the panel image is BusyBox-based and that command needs
        GNU ``find -printf``. Archives are never opened, only stat-ed, and the
        ``.meta.json`` sidecar supplies the section list.
        """
        directory = Path(
            self._env_map().get("CONTENT_MANAGER_BACKUP_DIR")
            or (self.root.parent / f"{self.root.name}-backups")
        )
        payload = {
            "ok": True,
            "error": "",
            "directory": str(directory),
            "exists": directory.is_dir(),
            "entries": [],
        }
        if not payload["exists"]:
            return payload
        try:
            candidates = list(directory.glob("hermes-stack-*.tar.gz")) + list(
                directory.glob("hermes-stack-*.tar.gz.age")
            )
        except OSError as error:
            payload.update(ok=False, error=str(error))
            return payload
        entries = []
        for path in candidates:
            try:
                info = path.stat()
            except OSError:
                continue
            entry = {
                "name": path.name,
                "path": str(path),
                "size": _human_size(info.st_size),
                "size_bytes": int(info.st_size),
                "modified": datetime.fromtimestamp(
                    info.st_mtime, timezone.utc
                ).isoformat(timespec="seconds"),
                "created_at": "",
                "full": True,
                "sections": [],
                "stack_version": "",
                "encrypted": path.name.endswith(".age"),
            }
            try:
                meta = json.loads(Path(f"{path}.meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if isinstance(meta, dict):
                entry["created_at"] = str(meta.get("created_at") or "")
                entry["full"] = bool(meta.get("full", True))
                entry["sections"] = [str(value) for value in (meta.get("sections") or [])]
                entry["stack_version"] = str(meta.get("stack_version") or "")
            entries.append(entry)
        entries.sort(key=lambda row: (row["modified"], row["name"]), reverse=True)
        payload["entries"] = entries
        # The Backups view offers a partial backup, so it needs the section
        # names the CLI accepts.
        payload["sections"] = backup_sections(self.runner, self.root)
        return payload

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
