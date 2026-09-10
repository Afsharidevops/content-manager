"""Validated editors for the stack configuration files.

The panel never parses and rewrites YAML/JSON for the operator; it reviews the
raw text, validates a copy, keeps a timestamped backup, then writes the file
atomically. Secrets in ``.env`` are never returned to the browser.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

MAX_EDIT_BYTES = 512 * 1024
SECRET_KEY_RE = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|_KEY$|API_KEY|HASH)", re.IGNORECASE)
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class EditError(ValueError):
    """Raised when a submitted file fails validation or cannot be written."""


@dataclass(frozen=True)
class ConfigFile:
    name: str
    title: str
    path: Path
    kind: str
    description: str


def default_config_files(root: Path) -> list[ConfigFile]:
    config_dir = Path(root) / "data" / "content-manager" / "config"
    return [
        ConfigFile(
            name="editorial-policy",
            title="Editorial policy",
            path=config_dir / "editorial-policy.yaml",
            kind="yaml",
            description=(
                "Pipeline limits, exclusions, scoring, on-demand behavior, the "
                "platform packages, and the scheduled routines list."
            ),
        ),
        ConfigFile(
            name="sources",
            title="Discovery sources",
            path=config_dir / "sources.yaml",
            kind="yaml",
            description="RSS/Atom feeds the research step reads on scheduled runs.",
        ),
        ConfigFile(
            name="categories",
            title="Categories",
            path=config_dir / "categories.yaml",
            kind="yaml",
            description="Category names and keyword rules used while normalizing items.",
        ),
        ConfigFile(
            name="tools",
            title="Tool registry",
            path=config_dir / "tools.json",
            kind="json",
            description="Shared MCP/OpenAPI/HTTP tool catalog for bot, router, and n8n.",
        ),
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConfigStore:
    """Read, validate, back up, and write the editable configuration files."""

    def __init__(self, root: Path, backup_dir: Path | None = None, keep_backups: int = 5):
        self.root = Path(root)
        self.files = {item.name: item for item in default_config_files(self.root)}
        self.backup_dir = Path(backup_dir) if backup_dir else self.root / "data" / "panel" / "backups"
        self.keep_backups = keep_backups

    # ------------------------------------------------------------------ list

    def listing(self) -> list[dict]:
        rows = []
        for item in self.files.values():
            exists = item.path.is_file()
            rows.append(
                {
                    "name": item.name,
                    "title": item.title,
                    "kind": item.kind,
                    "description": item.description,
                    "path": str(item.path),
                    "exists": exists,
                    "bytes": item.path.stat().st_size if exists else 0,
                    "modified_at": (
                        datetime.fromtimestamp(item.path.stat().st_mtime, timezone.utc)
                        .isoformat(timespec="seconds")
                        if exists
                        else ""
                    ),
                }
            )
        return rows

    def read(self, name: str) -> dict:
        item = self._file(name)
        if not item.path.is_file():
            raise EditError(f"{item.path} does not exist yet; start the stack once to seed it")
        text = item.path.read_text(encoding="utf-8")
        return {
            "name": item.name,
            "title": item.title,
            "kind": item.kind,
            "path": str(item.path),
            "text": text,
            "modified_at": datetime.fromtimestamp(
                item.path.stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds"),
        }

    # ----------------------------------------------------------------- write

    def write(self, name: str, text: str) -> dict:
        item = self._file(name)
        if not isinstance(text, str):
            raise EditError("body must be a string")
        if len(text.encode("utf-8")) > MAX_EDIT_BYTES:
            raise EditError(f"file is larger than {MAX_EDIT_BYTES // 1024} KiB")
        validate_config(item, text)
        backup = self._backup(item) if item.path.is_file() else ""
        item.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            dir=item.path.parent,
            prefix=f".{item.path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(handle.name, item.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
        return {
            "name": item.name,
            "bytes": item.path.stat().st_size,
            "backup": backup,
            "modified_at": _now(),
        }

    def backups(self, name: str) -> list[dict]:
        item = self._file(name)
        directory = self._backup_dir(item)
        if not directory.is_dir():
            return []
        rows = []
        for path in sorted(directory.glob(f"{item.name}-*")):
            rows.append(
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "modified_at": datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat(timespec="seconds"),
                }
            )
        return list(reversed(rows))

    def restore(self, name: str, backup_name: str) -> dict:
        item = self._file(name)
        directory = self._backup_dir(item)
        backup = directory / Path(backup_name).name
        if not backup.is_file():
            raise EditError("backup not found")
        text = backup.read_text(encoding="utf-8")
        validate_config(item, text)
        return self.write(item.name, text)

    # --------------------------------------------------------------- helpers

    def _file(self, name: str) -> ConfigFile:
        item = self.files.get(str(name))
        if item is None:
            raise EditError(f"unknown configuration file: {name}")
        return item

    def _backup_dir(self, item: ConfigFile) -> Path:
        return self.backup_dir

    def _backup(self, item: ConfigFile) -> str:
        directory = self._backup_dir(item)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = directory / f"{item.name}-{stamp}"
        shutil.copy2(item.path, target)
        existing = sorted(directory.glob(f"{item.name}-*"))
        for stale in existing[: max(0, len(existing) - self.keep_backups)]:
            try:
                stale.unlink()
            except OSError:
                pass
        return target.name


def validate_config(item: ConfigFile, text: str) -> None:
    """Validate one submission; raises EditError with an operator-facing reason."""
    if item.kind == "json":
        _validate_json(item.name, text)
        return
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise EditError(f"YAML error: {error}") from error
    if not isinstance(parsed, dict):
        raise EditError("the file must be a YAML mapping at the top level")
    if item.name == "editorial-policy":
        _validate_policy(parsed)
    if item.name == "sources":
        _validate_sources(parsed)
    if item.name == "categories":
        _validate_categories(parsed)


def _validate_policy(parsed: dict) -> None:
    pipeline = parsed.get("pipeline")
    if pipeline is not None and not isinstance(pipeline, dict):
        raise EditError("pipeline must be a mapping")
    if isinstance(pipeline, dict):
        for key in ("timezone", "daily_proposal_time"):
            if key in pipeline and not isinstance(pipeline[key], str):
                raise EditError(f"pipeline.{key} must be a string")
    routines = parsed.get("routines", [])
    if routines is None:
        return
    if not isinstance(routines, list):
        raise EditError("routines must be a list")
    seen = set()
    for index, routine in enumerate(routines):
        if not isinstance(routine, dict):
            raise EditError(f"routines[{index}] must be a mapping")
        routine_id = str(routine.get("id") or "").strip()
        if not routine_id:
            raise EditError(f"routines[{index}] needs a non-empty id")
        if routine_id in seen:
            raise EditError(f"duplicate routine id: {routine_id}")
        seen.add(routine_id)
        cadence = str(routine.get("cadence") or "daily").lower()
        if cadence not in {"daily", "weekly"}:
            raise EditError(f"routines[{index}].cadence must be daily or weekly")
        if "count" in routine:
            try:
                count = int(routine["count"])
            except (TypeError, ValueError) as error:
                raise EditError(f"routines[{index}].count must be a number") from error
            if count < 1:
                raise EditError(f"routines[{index}].count must be at least 1")


def _validate_sources(parsed: dict) -> None:
    sources = parsed.get("sources")
    if sources is None:
        return
    if not isinstance(sources, list):
        raise EditError("sources must be a list")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise EditError(f"sources[{index}] must be a mapping")
        url = str(source.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise EditError(f"sources[{index}].url must start with http:// or https://")


def _validate_categories(parsed: dict) -> None:
    categories = parsed.get("categories")
    if categories is None:
        return
    if not isinstance(categories, (list, dict)):
        raise EditError("categories must be a list or a mapping")


def _validate_json(name: str, text: str) -> None:
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise EditError(f"JSON error: {error}") from error
    if name != "tools":
        return
    if not isinstance(parsed, dict):
        raise EditError("the registry must be a JSON object")
    if int(parsed.get("schema_version") or 0) != 1:
        raise EditError("schema_version must be 1")
    tools = parsed.get("tools")
    if not isinstance(tools, list) or not tools:
        raise EditError("tools must be a non-empty list")
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise EditError(f"tools[{index}] must be an object")
        tool_id = str(tool.get("id") or "").strip()
        if not tool_id:
            raise EditError(f"tools[{index}] needs a non-empty id")
        kind = str(tool.get("kind") or "")
        if kind not in {"mcp", "openapi", "http"}:
            raise EditError(f"tools[{index}].kind must be mcp, openapi, or http")
        if kind == "mcp" and not tool.get("url"):
            raise EditError(f"tools[{index}] (mcp) needs a url")
        if kind in {"openapi", "http"} and not (tool.get("base_url") or tool.get("spec_url")):
            raise EditError(f"tools[{index}] ({kind}) needs base_url or spec_url")
        auth = tool.get("auth")
        if auth is not None and not isinstance(auth, dict):
            raise EditError(f"tools[{index}].auth must be an object")


# --------------------------------------------------------------------- .env


class EnvStore:
    """Line-preserving editor for the stack ``.env`` file."""

    def __init__(self, root: Path):
        self.path = Path(root) / ".env"
        self.example_path = Path(root) / ".env.example"

    def entries(self) -> list[dict]:
        text = self._read()
        example = self._example_comments()
        rows = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if not ENV_KEY_RE.match(key):
                continue
            value = value.strip()
            secret = bool(SECRET_KEY_RE.search(key))
            rows.append(
                {
                    "key": key,
                    "value": None if secret else _unquote(value),
                    "secret": secret,
                    "set": bool(_unquote(value)),
                    "comment": example.get(key, ""),
                }
            )
        rows.sort(key=lambda item: item["key"])
        return rows

    def set(self, key: str, value) -> dict:
        key = str(key or "").strip()
        if not ENV_KEY_RE.match(key):
            raise EditError("environment keys must look like CONTENT_BOT_TOKEN")
        value = "" if value is None else str(value)
        if "\n" in value or "\r" in value:
            raise EditError("environment values must stay on one line")
        text = self._read()
        lines = text.splitlines()
        target = None
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.split("=", 1)[0].strip() == key:
                target = index
                break
        rendered = f"{key}={_quote(value)}"
        if target is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(rendered)
        else:
            lines[target] = rendered
        body = "\n".join(lines).rstrip("\n") + "\n"
        self._write(body)
        return {"key": key, "set": bool(value)}

    # --------------------------------------------------------------- helpers

    def _read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _write(self, body: str) -> None:
        if len(body.encode("utf-8")) > MAX_EDIT_BYTES:
            raise EditError("the environment file is too large for the panel editor")
        backup = self.path.with_name(self.path.name + ".panel-backup")
        if self.path.is_file():
            shutil.copy2(self.path, backup)
            os.chmod(backup, 0o600)
        handle = tempfile.NamedTemporaryFile(
            "w",
            dir=self.path.parent,
            prefix=".env.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.chmod(handle.name, 0o600)
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    def _example_comments(self) -> dict:
        comments = {}
        try:
            lines = self.example_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return comments
        pending = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                pending.append(stripped.lstrip("# ").strip())
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if pending:
                    comments[key] = " ".join(pending)
            pending = []
        return comments


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    if value == "":
        return ""
    if re.search(r"[\s#\"'$`]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value
