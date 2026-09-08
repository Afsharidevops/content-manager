"""Google Flow video driver.

Drives flow.google.com in the session browser. Every interaction point is a
selector list that can be overridden through environment variables of the form
MEDIA_STUDIO_FLOW_<STEP>_SELECTOR (for example
MEDIA_STUDIO_FLOW_NEW_PROJECT_SELECTOR), so the flow can be calibrated without
a code change. When a step fails, a snapshot is written to the job artifact
directory for inspection.
"""

from __future__ import annotations

import os
import time

from media_studio.browser import configure_page, freeze_page, snapshot_page
from media_studio.drivers.base import Driver, DriverError, RunContext, body_contains, click_first, env_candidates, fill_first, first_visible

PROMPT_PLACEHOLDER = os.environ.get("MEDIA_STUDIO_FLOW_PROMPT_SUFFIX", "").strip()


class FlowVideoDriver(Driver):
    name = "flow-video"
    label = "Google Flow video generation (Flow account session)"
    group = "google"
    target_url = "https://flow.google.com/"

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        page = ctx.page
        configure_page(page, ctx.settings)
        ctx.log(f"flow-video: opening {self.target_url}")
        page.goto(self.target_url, wait_until="domcontentloaded", timeout=60000)
        self._check_geo_block(page, ctx)
        self._wait_ready(page, ctx)
        self._create_project(page, ctx)
        return self._export_video(page, ctx)

    def _check_geo_block(self, page, ctx) -> None:
        marker = body_contains(
            page,
            ("not available in your country", "unsupported country", "region", "in your area"),
            timeout_s=4.0,
        )
        if marker:
            raise DriverError(
                "Google Flow still reports an unsupported region.",
                step="geo",
                hint="Confirm MEDIA_STUDIO_BLOCK_GEO_REDIRECT=true and that the session browser can reach flow.google.com.",
            )

    def _wait_ready(self, page, ctx) -> None:
        if body_contains(page, ("sign in", "sign-in"), timeout_s=6.0):
            raise DriverError(
                "The session browser is not signed in to Google.",
                step="signin",
                hint="Open https://flow.google.com/ in the connected Chrome and sign in once, then retry.",
            )
        wait_s = float(os.environ.get("MEDIA_STUDIO_FLOW_READY_WAIT_SECONDS", "5"))
        time.sleep(wait_s)
        ctx.log(f"flow-video: waited {wait_s:.0f}s for the project dashboard.")

    def _create_project(self, page, ctx) -> None:
        candidates = env_candidates(
            "FLOW",
            "NEW_PROJECT",
            'button:has-text("New project")',
            '[role="button"]:has-text("New project")',
            'button:has-text("Create")',
        )
        button = first_visible(ctx, candidates, timeout_s=float(os.environ.get("MEDIA_STUDIO_FLOW_DASHBOARD_TIMEOUT", "45")), step="new-project")
        if getattr(ctx.settings, "freeze_on_ready", True):
            freeze_page(page, seconds=2.0)
        button.click()
        ctx.log("flow-video: clicked the project creation button.")

    def _export_video(self, page, ctx) -> list[tuple[str, str]]:
        prompt_box = env_candidates(
            "FLOW",
            "PROMPT_BOX",
            '[contenteditable="true"]',
            "textarea",
            'input[type="text"]',
            '[aria-label*="prompt" i]',
            '[placeholder*="prompt" i]',
        )
        fill_first(ctx, self._compose_prompt(ctx), prompt_box, step="prompt")

        submit = env_candidates("FLOW", "SUBMIT", 'button[aria-label*="Send" i]', '[role="button"][aria-label*="Send" i]')
        if submit:
            try:
                click_first(ctx, submit, timeout_s=6.0, step="submit-prompt")
            except DriverError:
                page.keyboard.press("Enter")
        else:
            page.keyboard.press("Enter")
        ctx.log("flow-video: prompt submitted; waiting for the storyboard.")

        self._wait_generation(page, ctx)

        export = env_candidates(
            "FLOW",
            "EXPORT",
            'button:has-text("Export")',
            '[role="button"]:has-text("Export")',
            'button:has-text("Download")',
            '[aria-label*="Export" i]',
        )
        export_button = first_visible(ctx, export, timeout_s=30.0, step="export")
        export_button.click()
        ctx.log("flow-video: export menu opened.")

        download = env_candidates(
            "FLOW",
            "DOWNLOAD",
            'button:has-text("Download")',
            '[role="menuitem"]:has-text("Download")',
            'a:has-text("Download")',
        )
        target = first_visible(ctx, download, timeout_s=20.0, step="download")
        with page.expect_download(timeout=float(os.environ.get("MEDIA_STUDIO_FLOW_EXPORT_TIMEOUT", "180")) * 1000) as download_info:
            target.click()
        download_item = download_info.value
        filename = os.path.join(ctx.work_dir, "flow_video.mp4")
        download_item.save_as(filename)
        ctx.log(f"flow-video: saved {download_item.suggested_filename}.")
        return [("flow_video.mp4", "video")]

    def _wait_generation(self, page, ctx) -> None:
        deadline = time.time() + max(30, float(os.environ.get("MEDIA_STUDIO_FLOW_GENERATION_TIMEOUT", "300")))
        ready_markers = env_candidates(
            "FLOW",
            "READY",
            'button:has-text("Export")',
            '[role="button"]:has-text("Export")',
            'video[src]',
        )
        while time.time() < deadline:
            try:
                for candidate in ready_markers:
                    if page.locator(candidate).first.is_visible(timeout=1000):
                        ctx.log("flow-video: storyboard looks ready; proceeding to export.")
                        return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)
        raise DriverError(
            "Timed out waiting for the Flow storyboard to finish.",
            step="generation",
            hint="Video generation can take minutes; raise MEDIA_STUDIO_FLOW_GENERATION_TIMEOUT or the driver may need selector calibration.",
        )

    @staticmethod
    def _compose_prompt(ctx: RunContext) -> str:
        prompt = ctx.prompt.strip()
        if PROMPT_PLACEHOLDER:
            prompt = f"{prompt} {PROMPT_PLACEHOLDER}"
        return prompt


def snapshot_failure(ctx: RunContext) -> None:
    if ctx.page is None or not ctx.work_dir:
        return
    try:
        files = snapshot_page(ctx.page, ctx.work_dir)
        ctx.log(f"flow-video: calibration snapshot written: {', '.join(files)}")
    except Exception:  # noqa: BLE001
        pass
