"""Gemini web app image driver.

Sends the prompt in the Gemini chat input, waits for new rendered images, and
pulls each image through the page context (works for blob: and authenticated
media URLs). Requires a signed-in Google session in the session browser.
"""

from __future__ import annotations

import base64
import os
import time

from media_studio.browser import configure_page
from media_studio.drivers.base import Driver, DriverError, RunContext, body_contains, env_candidates, first_visible


class GeminiImageDriver(Driver):
    name = "gemini-image"
    label = "Gemini image generation in the Gemini web app"
    group = "google"
    target_url = "https://gemini.google.com/app"

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        page = ctx.page
        configure_page(page, ctx.settings)
        page.goto(self.target_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        if body_contains(page, ("sign in", "sign-in"), timeout_s=5.0):
            raise DriverError(
                "The session browser is not signed in to Google.",
                step="signin",
                hint="Open https://gemini.google.com/app in the connected Chrome and sign in once, then retry.",
            )
        existing = self._existing_images(page)
        input_box = env_candidates(
            "GEMINI",
            "INPUT",
            '[contenteditable="true"]',
            "textarea",
            'rich-textarea',
            '[aria-label*="prompt" i]',
        )
        composer = first_visible(ctx, input_box, timeout_s=20.0, step="composer")
        composer.click()
        composer.fill(ctx.prompt.strip())
        page.keyboard.press("Enter")
        ctx.log("gemini-image: prompt submitted; waiting for generated images.")

        count = int(ctx.params.get("count", 1))
        images = self._wait_images(page, existing, count=count)
        if not images:
            raise DriverError(
                "No new image appeared in the Gemini conversation.",
                step="generation",
                hint="Gemini may have answered with text only; phrase the prompt as an image request or calibrate selectors.",
            )
        artifacts: list[tuple[str, str]] = []
        for index, src in enumerate(images[:count], start=1):
            content, mime = self._fetch_image(page, src)
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
            filename = f"gemini_image_{index}.{ext}"
            with open(os.path.join(ctx.work_dir, filename), "wb") as handle:
                handle.write(content)
            artifacts.append((filename, "image"))
            ctx.log(f"gemini-image: saved {filename} ({len(content)} bytes).")
        return artifacts

    @staticmethod
    def _existing_images(page) -> set[str]:
        try:
            return {src for src in page.locator("img").evaluate_all("els => els.map(e => e.currentSrc || e.src || '')") if src}
        except Exception:  # noqa: BLE001
            return set()

    def _wait_images(self, page, existing: set[str], count: int) -> list[str]:
        deadline = time.time() + float(os.environ.get("MEDIA_STUDIO_GEMINI_GENERATION_TIMEOUT", "180"))
        seen: list[str] = []
        while time.time() < deadline and len(seen) < count:
            try:
                info = page.locator("img").evaluate_all(
                    """els => els.map(e => {
                        const src = e.currentSrc || e.src || '';
                        const box = e.getBoundingClientRect();
                        return { src, w: box.width };
                    }).filter(x => x.src && x.w >= 160)"""
                )
            except Exception:  # noqa: BLE001
                info = []
            for item in info:
                if item["src"] not in existing and item["src"] not in seen:
                    seen.append(item["src"])
            if len(seen) < count:
                time.sleep(4)
        return seen

    @staticmethod
    def _fetch_image(page, src: str) -> tuple[bytes, str]:
        data_url = page.evaluate(
            """async (src) => {
                const blob = await (await fetch(src)).blob();
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = () => reject(new Error('read failed'));
                    reader.readAsDataURL(blob);
                });
            }""",
            src,
        )
        header, _, encoded = data_url.partition(",")
        mime = header.removeprefix("data:").split(";", 1)[0] if header.startswith("data:") else "image/png"
        return base64.b64decode(encoded), mime
