"""Google Flow video driver.

Drives flow.google.com in the session browser. Every interaction point is a
selector list that can be overridden through environment variables of the form
MEDIA_STUDIO_FLOW_<STEP>_SELECTOR (for example
MEDIA_STUDIO_FLOW_NEW_PROJECT_SELECTOR), so the flow can be calibrated without
a code change. When a step fails, a snapshot is written to the job artifact
directory for inspection.
"""

from __future__ import annotations

import base64
import os
import time

from media_studio.browser import configure_page, freeze_page
from media_studio.drivers.base import Driver, DriverError, RunContext, body_contains, click_first, env_candidates, fill_first, first_visible


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
        self._submit_prompt(page, ctx)
        self._wait_generation(page, ctx)
        return self._download(page, ctx)

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
            'button[aria-label*="New project" i]',
        )
        button = first_visible(
            ctx,
            candidates,
            timeout_s=float(os.environ.get("MEDIA_STUDIO_FLOW_DASHBOARD_TIMEOUT", "45")),
            step="new-project",
        )
        if getattr(ctx.settings, "freeze_on_ready", True):
            freeze_page(page, seconds=2.0)
        button.click()
        ctx.log("flow-video: clicked the project creation button.")

    def _submit_prompt(self, page, ctx) -> None:
        self._accept_consent(page, ctx)
        prompt_box = env_candidates(
            "FLOW",
            "PROMPT_BOX",
            '[contenteditable="true"]',
            "textarea",
            'input[type="text"]',
            '[aria-label*="prompt" i]',
            '[placeholder*="prompt" i]',
        )
        editor_timeout = float(os.environ.get("MEDIA_STUDIO_FLOW_EDITOR_TIMEOUT", "60"))
        fill_first(ctx, self._compose_prompt(ctx), prompt_box, timeout_s=editor_timeout, step="prompt")
        submit = env_candidates(
            "FLOW",
            "SUBMIT",
            'button[aria-label*="Send" i]',
            'button[aria-label="Start generation"]',
            '[role="button"][aria-label*="Send" i]',
        )
        try:
            click_first(ctx, submit, timeout_s=6.0, step="submit-prompt")
        except DriverError:
            page.keyboard.press("Enter")
        ctx.log("flow-video: prompt submitted; waiting for the storyboard.")

    def _accept_consent(self, page, ctx) -> None:
        for candidate in ('button:has-text("Understood")', 'button:has-text("OK, got it")', 'button:has-text("Accept all")'):
            try:
                locator = page.locator(candidate).first
                if locator.is_visible(timeout=1500):
                    locator.click()
                    ctx.log("flow-video: accepted the consent banner.")
                    return
            except Exception:  # noqa: BLE001
                continue

    def _wait_generation(self, page, ctx) -> None:
        deadline = time.time() + max(30.0, float(os.environ.get("MEDIA_STUDIO_FLOW_GENERATION_TIMEOUT", "300")))
        ready_markers = env_candidates(
            "FLOW",
            "READY",
            'button:has-text("Export")',
            'video[src]',
            'video source[src]',
            '[aria-label="Open video in editor"]',
        )
        while time.time() < deadline:
            self._maybe_approve_generation(page, ctx)
            for candidate in ready_markers:
                try:
                    if page.locator(candidate).first.is_visible(timeout=1000):
                        ctx.log("flow-video: generated video is available; downloading.")
                        return
                except Exception:  # noqa: BLE001
                    continue
            time.sleep(3)
        raise DriverError(
            "Timed out waiting for the Flow video to finish.",
            step="generation",
            hint="Generation can take minutes; raise MEDIA_STUDIO_FLOW_GENERATION_TIMEOUT.",
        )

    def _maybe_approve_generation(self, page, ctx) -> None:
        """Approve the Flow credit-usage prompt when auto-approval is enabled."""
        if os.environ.get("MEDIA_STUDIO_FLOW_AUTO_APPROVE", "").strip().lower() not in ("1", "true", "yes"):
            return
        for candidate in (
            'button:has-text("Always approve")',
            'button:has-text("Approve")',
            '[role="button"]:has-text("Approve")',
        ):
            try:
                button = page.locator(candidate).first
                if button.is_visible(timeout=700):
                    button.click()
                    ctx.log("flow-video: approved the Flow credit-usage prompt.")
                    time.sleep(1.5)
                    return
            except Exception:  # noqa: BLE001
                continue

    def _download(self, page, ctx) -> list[tuple[str, str]]:
        attempts = (
            ("editor", self._download_via_editor),
            ("card-menu", self._download_via_card_menu),
        )
        for label, attempt in attempts:
            try:
                return attempt(page, ctx)
            except DriverError as exc:
                ctx.log(f"flow-video: download path '{label}' unavailable ({exc.step}).")
        return self._download_via_media_element(page, ctx)

    def _download_via_editor(self, page, ctx) -> list[tuple[str, str]]:
        click_first(
            ctx,
            ('[aria-label="Open video in editor"]', '[role="button"][aria-label="Open video in editor"]'),
            timeout_s=10.0,
            step="open-editor",
        )
        ctx.log("flow-video: opened the generated video in the editor.")
        time.sleep(4)
        return self._download_via_ui(page, ctx)

    def _download_via_card_menu(self, page, ctx) -> list[tuple[str, str]]:
        """Open the three-dot menu next to a generated result, pick Download,
        then choose a quality entry and save the download."""
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        menu_buttons = page.locator('button[aria-label="More options"], [role="button"][aria-label="More options"]')
        count = min(menu_buttons.count(), 8)
        for index in range(count):
            button = menu_buttons.nth(index)
            if not button.is_visible(timeout=800):
                continue
            button.click()
            download_item = self._find_menu_item(page, ("Download", "Download video", "Save video"))
            if download_item is None:
                try:
                    page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    pass
                continue
            ctx.log("flow-video: Download entry found in the card menu.")
            download_item.click()
            if not self._quality_menu_visible(page):
                raise DriverError("Card Download did not open a quality menu.", step="card-menu")
            return self._pick_quality_and_download(page, ctx)
        raise DriverError(
            "No visible card menu offered a Download entry.",
            step="card-menu",
            hint="The generated result may not expose the overflow menu yet.",
        )

    def _find_menu_item(self, page, labels: tuple[str, ...]):
        for label in labels:
            candidates = (
                f'[role="menuitem"]:has-text("{label}")',
                f'button:has-text("{label}")',
                f'span:has-text("{label}")',
                f'[role="button"]:has-text("{label}")',
            )
            for candidate in candidates:
                try:
                    locator = page.locator(candidate).first
                    if locator.is_visible(timeout=1200):
                        return locator
                except Exception:  # noqa: BLE001
                    continue
        return None


    def _download_via_ui(self, page, ctx) -> list[tuple[str, str]]:
        # Flow exposes a Download control whose click opens a quality menu
        # (720p Original size / 1080p Upscaled / 4K Upscaled / 270p GIF).
        triggers = env_candidates(
            "FLOW",
            "DOWNLOAD_TRIGGER",
            '[aria-label="Download media"]',
            '[aria-label="Download"]',
            'button[aria-label*="Download" i]',
        )
        for candidate in triggers:
            try:
                button = page.locator(candidate).first
                if not button.is_visible(timeout=1200):
                    continue
                button.click()
                if self._quality_menu_visible(page):
                    ctx.log("flow-video: quality menu opened; downloading.")
                    return self._pick_quality_and_download(page, ctx)
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                continue
        raise DriverError(
            "No Download control opened a quality menu.",
            step="download-trigger",
            hint="Flow may not expose Download for this result yet.",
        )

    def _quality_menu_visible(self, page) -> bool:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if page.locator('[role="menuitem"]').first.is_visible(timeout=800):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        return False

    def _pick_quality_and_download(self, page, ctx) -> list[tuple[str, str]]:
        preference = os.environ.get("MEDIA_STUDIO_FLOW_QUALITY", "720p").strip()
        order = [preference, "1080p", "720p", "4K", "270p"]
        chosen = None
        items = page.locator('[role="menuitem"]')
        count = min(items.count(), 20)
        visible: list = []
        for index in range(count):
            item = items.nth(index)
            try:
                if item.is_visible(timeout=700):
                    visible.append(item)
            except Exception:  # noqa: BLE001
                continue
        for wanted in order:
            if not wanted:
                continue
            for item in visible:
                try:
                    if wanted.lower() in (item.inner_text() or "").lower():
                        chosen = item
                        break
                except Exception:  # noqa: BLE001
                    continue
            if chosen is not None:
                break
        if chosen is None and visible:
            chosen = visible[0]
        timeout_ms = float(os.environ.get("MEDIA_STUDIO_FLOW_EXPORT_TIMEOUT", "240")) * 1000
        try:
            with page.expect_download(timeout=timeout_ms) as download_info:
                if chosen is not None:
                    chosen.click()
                else:
                    page.keyboard.press("Enter")
            item = download_info.value
        except Exception as exc:  # noqa: BLE001
            raise DriverError(
                "No video download arrived after choosing a quality.",
                step="download-quality",
                hint="Try MEDIA_STUDIO_FLOW_QUALITY=1080p/720p or check whether Flow asked for an upgrade.",
            ) from exc
        filename = os.path.join(ctx.work_dir, "flow_video.mp4")
        item.save_as(filename)
        ctx.log(f"flow-video: saved {item.suggested_filename}.")
        return [("flow_video.mp4", "video")]

    def _download_via_media_element(self, page, ctx) -> list[tuple[str, str]]:
        video = first_visible(
            ctx,
            ("video[src]", "video source[src]", "video"),
            timeout_s=15.0,
            step="video-element",
        )
        src = video.evaluate(
            "(node) => node.currentSrc || node.src || (node.querySelector('source') && node.querySelector('source').src) || ''"
        )
        if not src:
            raise DriverError(
                "The generated video element has no readable media source.",
                step="video-element",
                hint="Flow may stream through MSE; a session probe of the result card is needed.",
            )
        ctx.log(f"flow-video: fetching rendered video bytes from {src[:90]}...")
        timeout_ms = float(os.environ.get("MEDIA_STUDIO_FLOW_EXPORT_TIMEOUT", "180")) * 1000
        payload = page.evaluate(
            """async (src) => {
                const blob = await (await fetch(src)).blob();
                return { mime: blob.type, b64: await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(String(reader.result).split(',')[1]);
                    reader.onerror = () => reject(new Error('read failed'));
                    reader.readAsDataURL(blob);
                }) };
            }""",
            src,
            timeout=min(240000, timeout_ms),
        )
        content = base64.b64decode(payload["b64"])
        mime = str(payload.get("mime", "")).lower()
        ext = "webm" if mime == "video/webm" else "mp4"
        filename = os.path.join(ctx.work_dir, f"flow_video.{ext}")
        with open(filename, "wb") as handle:
            handle.write(content)
        ctx.log(f"flow-video: saved rendered video ({len(content)} bytes, {mime or 'unknown'}).")
        return [(f"flow_video.{ext}", "video")]

    @staticmethod
    def _compose_prompt(ctx: RunContext) -> str:
        prompt = ctx.prompt.strip()
        suffix = os.environ.get("MEDIA_STUDIO_FLOW_PROMPT_SUFFIX", "").strip()
        if suffix:
            prompt = f"{prompt} {suffix}"
        return prompt
