"""Whitelisted stack actions exposed by the operator panel.

The panel never accepts a free-form command: every action is a fixed spec that
resolves to ``docker compose`` or ``./manage.sh`` with hard-coded arguments. The
one parameterised action (a partial backup) takes section names, and those are
validated against the list ``./manage.sh backup-sections`` prints before any
command is built.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from panel.stack import CommandError, CommandRunner, backup_sections

OUTPUT_LIMIT = 6000


class ActionError(RuntimeError):
    """Raised when an action is unknown, disabled, or fails to start."""


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    description: str
    kind: str
    args: tuple
    params: tuple = ()
    timeout: float = 120.0
    confirm: bool = False


ACTIONS: tuple[Action, ...] = (
    Action(
        name="stack-up",
        label="Apply changes",
        description=(
            "Run docker compose up -d so edited .env values and new images are "
            "applied to every enabled profile."
        ),
        kind="compose",
        args=("up", "-d"),
        timeout=600.0,
        confirm=True,
    ),
    Action(
        name="restart-content",
        label="Restart Content Bot",
        description="Restart the content-bot container.",
        kind="compose",
        args=("restart", "content-bot"),
    ),
    Action(
        name="restart-media",
        label="Restart Media Studio",
        description="Restart the media-studio container.",
        kind="compose",
        args=("restart", "media-studio"),
    ),
    Action(
        name="restart-router",
        label="Restart Smart Router",
        description="Restart the smart-router container.",
        kind="compose",
        args=("restart", "smart-router"),
    ),
    Action(
        name="pull-content",
        label="Pull Content Bot image",
        description="docker compose pull content-bot (published Docker Hub tag).",
        kind="compose",
        args=("pull", "content-bot"),
        timeout=300.0,
    ),
    Action(
        name="pull-media",
        label="Pull Media Studio image",
        description="docker compose pull media-studio (published Docker Hub tag).",
        kind="compose",
        args=("pull", "media-studio"),
        timeout=600.0,
    ),
    Action(
        name="content-status",
        label="Content status",
        description="manage.sh content-status summary without secrets.",
        kind="manage",
        args=("content-status",),
    ),
    Action(
        name="media-status",
        label="Media status",
        description="manage.sh media-status summary without secrets.",
        kind="manage",
        args=("media-status",),
    ),
    Action(
        name="router-status",
        label="Router status",
        description="manage.sh router-status summary.",
        kind="manage",
        args=("router-status",),
    ),
    Action(
        name="doctor",
        label="Run doctor",
        description="manage.sh doctor diagnostics and hardening checks.",
        kind="manage",
        args=("doctor",),
        timeout=300.0,
    ),
    Action(
        name="s3-status",
        label="Object storage status",
        description="manage.sh s3-status summary without secrets.",
        kind="manage",
        args=("s3-status",),
    ),
    Action(
        name="s3-verify",
        label="Verify object storage",
        description=(
            "manage.sh s3-verify: a signed request proves the endpoint, the "
            "credentials, and the bucket without any cloud CLI."
        ),
        kind="manage",
        args=("s3-verify",),
        timeout=300.0,
    ),
    Action(
        name="backup",
        label="Create stack backup",
        description=(
            "Runs manage.sh backup --label panel --no-pause in a one-off "
            "container from the panel image so every data directory is readable "
            "and the panel stays responsive. Run ./manage.sh backup on the host "
            "when you want a paused, consistent snapshot."
        ),
        kind="container",
        args=("backup", "--label", "panel", "--no-pause"),
        timeout=1200.0,
        confirm=True,
    ),
    Action(
        name="backup-section",
        label="Back up selected sections",
        description=(
            "Runs manage.sh backup --only SECTION[,...] --label panel-section "
            "--no-pause in the same one-off container: the archive holds only "
            "the chosen parts of the stack (see ./manage.sh backup-sections), so "
            "a restore of it merges instead of replacing the whole stack."
        ),
        kind="container",
        args=(
            "backup",
            "--only",
            "{sections}",
            "--label",
            "panel-section",
            "--no-pause",
        ),
        params=("sections",),
        timeout=1200.0,
        confirm=True,
    ),
)

ACTION_INDEX = {action.name: action for action in ACTIONS}


class ActionRunner:
    """Execute whitelisted actions and return trimmed output."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        runner: CommandRunner | None = None,
    ):
        self.root = Path(root)
        self.enabled = bool(enabled)
        self.runner = runner or CommandRunner(self.root)

    def listing(self) -> list[dict]:
        return [
            {
                "name": action.name,
                "label": action.label,
                "description": action.description,
                "params": list(action.params),
                "confirm": action.confirm,
            }
            for action in ACTIONS
        ]

    def command_for(self, action: Action, values: dict | None = None) -> list[str]:
        args = self._resolve_args(action, values)
        if action.kind == "compose":
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
        if action.kind == "manage":
            return [str(self.root / "manage.sh"), *args]
        if action.kind == "container":
            return self._container_command(action, args)
        raise ActionError(f"unsupported action kind: {action.kind}")

    def _resolve_args(self, action: Action, values: dict | None) -> list[str]:
        """Fill the parameter placeholders with validated operator input."""
        if not action.params:
            return list(action.args)
        values = values or {}
        resolved: dict[str, str] = {}
        for name in action.params:
            raw = str(values.get(name, "")).strip()
            if name == "sections":
                resolved[name] = self._validated_sections(raw)
            elif raw:
                resolved[name] = raw
            else:
                raise ActionError(f"{action.name} needs a {name} value")
        return [
            part.format(**resolved) if "{" in str(part) else str(part)
            for part in action.args
        ]

    def _validated_sections(self, raw: str) -> str:
        known = [row["name"] for row in backup_sections(self.runner, self.root)]
        if not known:
            raise ActionError("could not read the section list from manage.sh")
        chosen = []
        for item in raw.split(","):
            name = item.strip().lower()
            if not name:
                continue
            if name not in known:
                raise ActionError(
                    f"unknown backup section: {name} (expected one of {', '.join(known)})"
                )
            if name not in chosen:
                chosen.append(name)
        if not chosen:
            raise ActionError("select at least one backup section")
        return ",".join(chosen)

    def _container_command(self, action: Action, args: list[str]) -> list[str]:
        """Run manage.sh in a one-off container from the same panel image.

        The panel runs as the operator uid, which cannot read every data
        directory (``data/smart-router`` for example), so backup needs the same
        effective root a host run gets from sudo. The throwaway container
        mounts the stack, runs the very same manage.sh, and leaves the panel
        responsive while it works.

        stack-ops.sh deliberately writes archives as ``0600`` because they
        carry ``.env`` secrets, so a root run would leave files the operator
        and the panel cannot read afterwards. The wrapper hands the fresh
        ``hermes-stack-*`` files back to the owner of the backup directory,
        which keeps the archives as private as before while the Backups view
        can still list and read their metadata.
        """
        repository = self._stack_env("PANEL_IMAGE_REPOSITORY") or "afsharidevops/content-panel"
        tag = self._stack_env("PANEL_IMAGE_TAG") or "0.3.0"
        backups = self.root.parent / f"{self.root.name}-backups"
        command = [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            f"{self.root}:{self.root}",
            "-v",
            f"{backups}:{backups}",
        ]
        backup_dir = self._stack_env("CONTENT_MANAGER_BACKUP_DIR")
        if backup_dir:
            command += [
                "-v",
                f"{backup_dir}:{backup_dir}",
                "-e",
                f"CONTENT_MANAGER_BACKUP_DIR={backup_dir}",
            ]
        command += [
            f"{repository}:{tag}",
            "-c",
            self._container_wrapper(backups),
            "panel-action",
            *args,
        ]
        return command

    def _container_wrapper(self, backups: Path) -> str:
        """Fixed shell wrapper for a container action (no user input inside)."""
        return (
            f'"{self.root / "manage.sh"}" "$@"; status=$?; '
            f'if [ "$status" -eq 0 ]; then '
            f'owner=$(stat -c "%u:%g" "{backups}" 2>/dev/null) && '
            f'[ -n "$owner" ] && chown "$owner" "{backups}"/hermes-stack-* 2>/dev/null; '
            f"fi; exit $status"
        )

    def _stack_env(self, key: str) -> str:
        try:
            lines = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            found, value = stripped.split("=", 1)
            if found.strip() == key:
                return value.strip().strip('"').strip("'")
        return ""

    def _env_overrides(self, action: Action) -> dict:
        """Environment tweaks for actions that run from inside the stack.

        The panel container shares the stack network, so the S3 check must use
        the in-network endpoint (S3_ENDPOINT_URL) instead of the host loopback
        value the same command defaults to on the host.
        """
        if action.name != "s3-verify":
            return {}
        endpoint = self._stack_env("S3_ENDPOINT_URL")
        return {"S3_HOST_ENDPOINT_URL": endpoint} if endpoint else {}

    def run(self, name: str) -> dict:
        return self.run_with(name, {})

    def run_with(self, name: str, values: dict | None = None) -> dict:
        action = ACTION_INDEX.get(str(name))
        if action is None:
            raise ActionError(f"unknown action: {name}")
        if not self.enabled:
            raise ActionError("actions are disabled (PANEL_ACTIONS_ENABLED=false)")
        command = self.command_for(action, values)
        started = time.monotonic()
        try:
            result = self.runner.run(
                command, timeout=action.timeout, env=self._env_overrides(action)
            )
        except CommandError as error:
            return {
                "name": action.name,
                "label": action.label,
                "ok": False,
                "returncode": -1,
                "duration_seconds": round(time.monotonic() - started, 1),
                "output": str(error),
            }
        output = result.output.strip()
        if len(output) > OUTPUT_LIMIT:
            output = "...\n" + output[-OUTPUT_LIMIT:]
        return {
            "name": action.name,
            "label": action.label,
            "ok": result.ok,
            "returncode": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 1),
            "output": output,
        }
