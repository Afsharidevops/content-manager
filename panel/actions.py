"""Whitelisted stack actions exposed by the operator panel.

The panel never accepts a free-form command: every action is a fixed spec that
resolves to ``docker compose`` or ``./manage.sh`` with hard-coded arguments.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from panel.stack import CommandError, CommandRunner

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
                "confirm": action.confirm,
            }
            for action in ACTIONS
        ]

    def command_for(self, action: Action) -> list[str]:
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
                *action.args,
            ]
        if action.kind == "manage":
            return [str(self.root / "manage.sh"), *action.args]
        raise ActionError(f"unsupported action kind: {action.kind}")

    def run(self, name: str) -> dict:
        action = ACTION_INDEX.get(str(name))
        if action is None:
            raise ActionError(f"unknown action: {name}")
        if not self.enabled:
            raise ActionError("actions are disabled (PANEL_ACTIONS_ENABLED=false)")
        command = self.command_for(action)
        started = time.monotonic()
        try:
            result = self.runner.run(command, timeout=action.timeout)
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
