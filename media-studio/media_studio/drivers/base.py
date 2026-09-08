"""Shared driver helpers. No Playwright import at module level."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RunContext:
    prompt: str
    params: dict = field(default_factory=dict)
    work_dir: str = ""
    settings: Any = None
    log: Callable[[str], None] = lambda _message: None
    page: Any = None


class DriverError(RuntimeError):
    def __init__(self, message: str, step: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.step = step
        self.hint = hint

    def describe(self) -> str:
        parts = []
        if self.step:
            parts.append(f"step={self.step}")
        parts.append(str(self))
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return " | ".join(parts)


class Driver:
    name = ""
    label = ""
    group = "api"
    target_url = ""

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        """Run one job. Writes files into ctx.work_dir and returns
        (filename, kind) pairs; kind is image, video, or file."""
        raise NotImplementedError


def env_candidates(prefix: str, step: str, *defaults: str) -> tuple[str, ...]:
    key = f"MEDIA_STUDIO_{prefix}_{step}_SELECTOR".upper().replace("-", "_")
    override = os.environ.get(key, "").strip()
    if override:
        return (override,)
    return defaults


def log(ctx: RunContext, message: str) -> None:
    ctx.log(message)


def first_visible(ctx: RunContext, candidates, timeout_s: float = 15.0, step: str = "find") -> Any:
    """Return the first visible locator matching any candidate selector."""
    page = ctx.page
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    for candidate in candidates:
        remaining = max(0.5, deadline - time.time())
        try:
            locator = page.locator(candidate)
            locator.wait_for(state="visible", timeout=remaining * 1000)
            return locator
        except Exception as exc:  # noqa: BLE001 - selector probing
            last_error = exc
    raise DriverError(
        f"Could not find a visible element with candidates {list(candidates)}.",
        step=step,
        hint="Run a session probe and share the snapshot so the selector list can be updated.",
    )


def click_first(ctx: RunContext, candidates, timeout_s: float = 15.0, step: str = "click") -> None:
    locator = first_visible(ctx, candidates, timeout_s=timeout_s, step=step)
    locator.click()
    log(ctx, f"{step}: clicked {locator}.")


def fill_first(ctx: RunContext, text: str, candidates, timeout_s: float = 15.0, step: str = "fill") -> None:
    locator = first_visible(ctx, candidates, timeout_s=timeout_s, step=step)
    locator.click()
    locator.fill(text)
    log(ctx, f"{step}: filled {locator}.")


def body_contains(page: Any, needles: tuple[str, ...], timeout_s: float = 10.0) -> str | None:
    """Return the first needle present in the visible body text."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            text = page.locator("body").inner_text(timeout=3000)
        except Exception:  # noqa: BLE001
            text = ""
        lowered = text.lower()
        for needle in needles:
            if needle.lower() in lowered:
                return needle
        time.sleep(1)
    return None
