"""Video Overview: submit the prompt, wait for the render, download it."""

from __future__ import annotations

import logging
import os
import time

from app.notebook import (
    NotebookLMError,
    visible_nodes,
    click_first,
    find_first,
)

LOGGER = logging.getLogger("notebooklm.video")


def start_video_overview(page, prompt: str, settings, selectors: dict) -> None:
    """Open the Video Overview composer and submit the rendered prompt."""
    click_first(page, selectors["video_overview"], step="open video overview")
    page.wait_for_timeout(2000)
    customize = find_first(page, selectors["video_customize"], timeout=4)
    if customize is not None:
        try:
            customize.click()
            page.wait_for_timeout(1500)
        except Exception as error:  # noqa: BLE001 - the composer may already be open
            LOGGER.info("Video customize control could not be clicked: %s", error)
    node = find_first(page, selectors["video_prompt"], timeout=15)
    if node is not None:
        try:
            node.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(prompt, delay=3)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("Video prompt could not be typed: %s", error)
    else:
        LOGGER.warning("Video prompt field not found; generating with the defaults")
    click_first(page, selectors["video_generate"], step="start generation")


def video_ready(page, selectors: dict) -> bool:
    """True when a rendered video or its download affordance is visible."""
    for selector in selectors["video_ready"]:
        for node in visible_nodes(page, selector):
            try:
                if node.evaluate("(n) => n.tagName === 'VIDEO' ? !!n.currentSrc || !!n.src : true"):
                    return True
            except Exception:  # noqa: BLE001 - treat an unreadable node as not ready
                continue
    return False


def video_busy(page) -> bool:
    busy = 0
    for selector in ("[role='progressbar']", "mat-progress-bar"):
        busy += len(visible_nodes(page, selector))
    return busy > 0


def wait_for_video(page, settings, selectors: dict) -> None:
    """Poll until the Video Overview finishes rendering."""
    deadline = time.time() + max(60, settings.video_timeout_seconds)
    quiet = 0
    while time.time() < deadline:
        if video_ready(page, selectors):
            quiet = quiet + 1 if not video_busy(page) else 0
            if quiet >= 1:
                return
        else:
            quiet = 0
        page.wait_for_timeout(max(3, settings.poll_seconds) * 1000)
    raise NotebookLMError(
        "video generation",
        f"the overview was not ready after {settings.video_timeout_seconds}s",
    )


def download_video(page, target_path: str, settings, selectors: dict) -> str:
    """Download the finished overview to ``target_path`` and return the path."""
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    timeout = max(30, settings.download_timeout_seconds) * 1000

    menu = find_first(page, selectors["video_menu"], timeout=8)
    if menu is not None:
        try:
            menu.click()
            page.wait_for_timeout(800)
            with page.expect_download(timeout=timeout) as download_info:
                click_first(page, selectors["video_download"], step="download video")
            download = download_info.value
            download.save_as(target_path)
            return target_path
        except Exception as error:  # noqa: BLE001 - fall back to a direct button
            LOGGER.info("Video menu download failed (%s); trying a direct control", error)

    try:
        with page.expect_download(timeout=timeout) as download_info:
            click_first(page, selectors["video_download"], step="download video")
        download = download_info.value
        download.save_as(target_path)
        return target_path
    except Exception as error:  # noqa: BLE001
        raise NotebookLMError("download video", str(error)) from error
