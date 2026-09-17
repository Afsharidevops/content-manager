"""Video Overview: submit the prompt, wait for the render, download it."""

from __future__ import annotations

import logging
import os
import subprocess
import time

from app.notebook import (
    NotebookLMError,
    visible_nodes,
    click_first,
    find_first,
)

LOGGER = logging.getLogger("notebooklm.video")


def _dismiss_blocking_notifications(page) -> int:
    """Close notification dialogs that block interaction (NOT source dialogs)."""
    import time as _time
    dismissed = 0
    # Known notification texts (persian + english)
    known_notifications = [
        "اکنون انعطاف", "هم اکنون", "بیشتر استفاده کن", "قابلیت‌های جدید",
        "usage", "upgrade", "limit", "Gemini Notebook",
        "getting started", "what's new", "welcome", "tip",
    ]
    for _ in range(3):
        try:
            dialogs = page.locator("[role='dialog'], .cdk-overlay-pane, .notification-overlay")
            for i in range(min(dialogs.count(), 5)):
                try:
                    d = dialogs.nth(i)
                    if not d.is_visible():
                        continue
                    text = (d.text_content(timeout=300) or "").strip()
                    if not any(n in text for n in known_notifications):
                        continue
                    LOGGER.info("NOTIFICATION DIALOG: %s", text[:120])
                    # Try close button
                    close = d.locator("button[aria-label*='Close' i], button[aria-label*='بستن' i], [aria-label*='dismiss' i]")
                    if close.count() > 0 and close.first.is_visible():
                        close.first.click(timeout=1000)
                        dismissed += 1
                        _time.sleep(0.5)
                        continue
                    # Try Escape
                    page.keyboard.press("Escape")
                    _time.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass
    if dismissed:
        page.wait_for_timeout(800)
        LOGGER.info("Dismissed %d blocking notification(s)", dismissed)
    return dismissed


def start_video_overview(page, prompt: str, settings, selectors: dict) -> None:
    """Open the Video Overview composer and submit the rendered prompt."""
    # Dismiss any blocking notifications before starting
    _dismiss_blocking_notifications(page)
    click_first(page, selectors["video_overview"], step="open video overview")
    page.wait_for_timeout(2000)
    # Dismiss notifications that appeared after clicking video overview
    _dismiss_blocking_notifications(page)
    # Wait for the compose dialog to actually appear (with retry)
    _found_dialog = False
    for _ in range(3):
        if page.locator("[role='dialog']").count() > 0:
            _found_dialog = True
            break
        page.wait_for_timeout(1000)
        # Retry clicking video overview in case the first click missed
        click_first(page, selectors["video_overview"], step="retry video overview")
        page.wait_for_timeout(1000)
    if not _found_dialog:
        LOGGER.warning("Video compose dialog did NOT open after clicking Video Overview")
    # Now look for customize button
    customize = find_first(page, selectors["video_customize"], timeout=4)
    if customize is not None:
        try:
            customize.click()
            page.wait_for_timeout(1500)
        except Exception as error:  # noqa: BLE001 - the composer may already be open
            LOGGER.info("Video customize control could not be clicked: %s", error)
    # Dismiss notifications before prompt
    _dismiss_blocking_notifications(page)
    # Find the actual prompt textarea (not the search/URL input)
    node = find_first(page, selectors["video_prompt"], timeout=15)
    if node is not None:
        try:
            # Log what we found
            ph = node.get_attribute("placeholder") or ""
            aria = node.get_attribute("aria-label") or ""
            LOGGER.info("VIDEO PROMPT FIELD: placeholder=%r aria=%r", ph, aria)
            # Log dialog state before clicking generate
            try:
                dialogs = page.locator("[role='dialog']")
                dlg_count = dialogs.count()
                dlg_text = dialogs.first.text_content(timeout=300)[:200] if dlg_count > 0 else ''
                LOGGER.info("BEFORE GENERATE: dialogs=%d text=%r", dlg_count, dlg_text[:80])
            except Exception:
                pass
            # Scroll into view and click
            node.scroll_into_view_if_needed()
            node.click()
            page.wait_for_timeout(300)
            node.fill("")
            node.fill(prompt)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("Video prompt could not be typed: %s", error)
            # Fallback: keyboard type
            try:
                node.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.type(prompt, delay=3)
            except Exception as e2:
                LOGGER.warning("Video prompt keyboard fallback also failed: %s", e2)
    else:
        LOGGER.warning("Video prompt field not found; generating with the defaults")
    # Dismiss any last-moment notifications before clicking generate
    _dismiss_blocking_notifications(page)
    # Use click_first with dismiss=False so the compose dialog stays open
    # (click_first normally calls dismiss_overlays -> Escape which closes the dialog).
    click_first(page, selectors["video_generate"], step="start generation", dismiss=False)
    # Log state after generate click - confirm dialog still open and generation started
    try:
        after_gen = page.locator("[role='dialog']")
        gen_dlg = after_gen.count()
        gen_vis = after_gen.first.is_visible() if gen_dlg > 0 else False
        busy_bars = page.locator("[role='progressbar'], mat-progress-bar").count()
        LOGGER.info("AFTER GENERATE: dialogs=%d visible=%s busy_bars=%d", gen_dlg, gen_vis, busy_bars)
    except Exception:
        pass


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
    start = time.time()
    deadline = start + max(60, settings.video_timeout_seconds)
    quiet = 0
    last_log = 0.0
    while time.time() < deadline:
        if video_ready(page, selectors):
            quiet = quiet + 1 if not video_busy(page) else 0
            if quiet >= 1:
                return
        else:
            quiet = 0
        now = time.time()
        elapsed = now - start
        if now - last_log > 30.0:
            still_busy = video_busy(page)
            LOGGER.info("WAITING for video: %.0fs elapsed, busy=%s", elapsed, still_busy)
            last_log = now
        page.wait_for_timeout(max(3, settings.poll_seconds) * 1000)
    # Timeout - save screenshot before raising
    try:
        ss_path = os.path.join(settings.data_dir, "logs", f"video-timeout-{int(time.time())}.png")
        page.screenshot(path=ss_path)
        LOGGER.info("Video timeout screenshot saved: %s", ss_path)
    except Exception:
        pass
    raise NotebookLMError(
        "video generation",
        f"the overview was not ready after {settings.video_timeout_seconds}s",
    )


def _trim_video(path: str, keep: float) -> str | None:
    """Trim a video to ``keep`` seconds from the start using ffmpeg.
    
    Returns the path to the trimmed file, or None on failure.
    The original file is replaced with the trimmed version.
    """
    import subprocess as _sp
    import tempfile as _tf
    try:
        fd, tmp = _tf.mkstemp(suffix=os.path.splitext(path)[1] or ".mp4")
        os.close(fd)
        result = _sp.run(
            ["ffmpeg", "-y", "-i", path, "-t", str(keep),
             "-c", "copy", "-avoid_negative_ts", "make_zero", tmp],
            capture_output=True, timeout=120, text=True,
        )
        if result.returncode != 0:
            LOGGER.warning("trim failed (ffmpeg exit %d): %s", result.returncode, result.stderr[:200])
            os.unlink(tmp)
            return None
        os.replace(tmp, path)
        LOGGER.info("Trimmed %s to %.1fs", path, keep)
        return path
    except FileNotFoundError:
        LOGGER.warning("ffmpeg not available, skip trim")
        return None
    except Exception as exc:
        LOGGER.warning("trim error: %s", exc)
        return None


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
            # Trim last N seconds if configured
            if getattr(settings, "trim_last_seconds", 0) > 0:
                try:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", target_path],
                        capture_output=True, text=True, timeout=30,
                    )
                    total = float(probe.stdout.strip() or 0)
                    if total > settings.trim_last_seconds + 1:
                        _trim_video(target_path, total - settings.trim_last_seconds)
                    else:
                        LOGGER.info("Video too short (%.1fs) to trim %ds", total, settings.trim_last_seconds)
                except Exception as exc:
                    LOGGER.warning("Could not probe/trim video: %s", exc)
            return target_path
        except Exception as error:  # noqa: BLE001 - fall back to a direct button
            LOGGER.info("Video menu download failed (%s); trying a direct control", error)

    try:
        with page.expect_download(timeout=timeout) as download_info:
            click_first(page, selectors["video_download"], step="download video")
        download = download_info.value
        download.save_as(target_path)
        # Trim last N seconds if configured
        if getattr(settings, "trim_last_seconds", 0) > 0:
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", target_path],
                    capture_output=True, text=True, timeout=30,
                )
                total = float(probe.stdout.strip() or 0)
                if total > settings.trim_last_seconds + 1:
                    _trim_video(target_path, total - settings.trim_last_seconds)
                else:
                    LOGGER.info("Video too short (%.1fs) to trim %ds", total, settings.trim_last_seconds)
            except Exception as exc:
                LOGGER.warning("Could not probe/trim video: %s", exc)
        return target_path
    except Exception as error:  # noqa: BLE001
        raise NotebookLMError("download video", str(error)) from error
