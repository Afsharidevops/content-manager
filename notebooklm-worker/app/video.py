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
    # Start network request monitoring
    _video_requests = []
    def _on_request(request):
        url = request.url
        if "notebook" in url or "video" in url or "overview" in url or "generate" in url:
            _video_requests.append({"url": url, "method": request.method, "headers": dict(request.headers)})
    try:
        page._video_requests = _video_requests
        page.on("request", _on_request)
    except Exception:
        pass
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
            page.wait_for_timeout(100)
            node.fill(prompt)
            page.wait_for_timeout(200)
            # Fire native input/change events so Angular/React sees the new value
            try:
                node.evaluate("(el) => { el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
            except Exception:
                pass
            # Log the actual value to confirm prompt was set
            try:
                val = node.input_value(timeout=500)
                LOGGER.info("PROMPT VALUE SET: chars=%d preview=%r", len(val), val[:80])
            except Exception:
                pass
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
    # Find the generate button and try multiple click strategies
    # NotebookLM uses Angular Material which may not respond to Playwright clicks
    gen_node = find_first(page, selectors["video_generate"], timeout=10)
    if gen_node is not None:
        # Log button state before click
        try:
            btn_disabled = gen_node.is_disabled()
            btn_text = gen_node.text_content(timeout=300) or ""
            btn_aria = gen_node.get_attribute("aria-label") or ""
            btn_classes = gen_node.get_attribute("class") or ""
            LOGGER.info("GENERATE BTN BEFORE: disabled=%s text=%r aria=%r", btn_disabled, btn_text.strip()[:40], btn_aria)
        except Exception:
            pass
        # Strategy 1: normal click
        try:
            gen_node.click(timeout=3000)
            LOGGER.info("GENERATE: click done")
        except Exception as exc:
            LOGGER.info("GENERATE: normal click failed: %s", exc)
            # Strategy 2: force click
            try:
                gen_node.click(force=True, timeout=3000, no_wait_after=True)
                LOGGER.info("GENERATE: force click done")
            except Exception as exc2:
                LOGGER.info("GENERATE: force click failed: %s", exc2)
                # Strategy 3: JS MouseEvent
                try:
                    page.evaluate("(el) => el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}))", gen_node)
                    LOGGER.info("GENERATE: JS MouseEvent done")
                except Exception as exc3:
                    LOGGER.info("GENERATE: JS MouseEvent failed: %s", exc3)
                    # Strategy 4: keyboard Enter
                    try:
                        gen_node.focus()
                        page.keyboard.press("Enter")
                        LOGGER.info("GENERATE: keyboard Enter done")
                    except Exception as exc4:
                        LOGGER.warning("GENERATE: all click strategies failed: %s", exc4)
        page.wait_for_timeout(1500)
        # Take screenshot and dump network requests right after click
        _dump_network_and_screenshot(page, settings, tag="after-generate")
    else:
        LOGGER.warning("GENERATE: button not found")
    # Log state after generate click
    try:
        after_gen = page.locator("[role='dialog']")
        gen_dlg = after_gen.count()
        gen_vis = after_gen.first.is_visible() if gen_dlg > 0 else False
        busy_bars = page.locator("[role='progressbar'], mat-progress-bar").count()
        # Log button state after click
        try:
            if gen_node:
                btn_disabled2 = gen_node.is_disabled()
                btn_text2 = gen_node.text_content(timeout=300) or ""
                LOGGER.info("GENERATE BTN AFTER: disabled=%s text=%r", btn_disabled2, btn_text2.strip()[:40])
        except Exception:
            pass
        LOGGER.info("AFTER GENERATE: dialogs=%d visible=%s busy_bars=%d", gen_dlg, gen_vis, busy_bars)
    except Exception:
        pass


def video_ready(page, selectors: dict) -> bool:
    """True when a rendered video or its download affordance is visible."""
    # First check for video_artifact containers
    for sel in selectors.get("video_artifact", []):
        for node in visible_nodes(page, sel):
            try:
                if node.is_visible():
                    LOGGER.info("VIDEO ARTIFACT found via: %s", sel)
                    return True
            except Exception:
                continue
    # Then check for video element or download buttons
    for selector in selectors["video_ready"]:
        for node in visible_nodes(page, selector):
            try:
                tag = node.evaluate("(n) => n.tagName")
                if tag == "VIDEO":
                    src = node.evaluate("(n) => n.currentSrc || n.src || ''")
                    if src:
                        LOGGER.info("VIDEO READY: src=%s", src[:120])
                        return True
                elif tag in ("BUTTON", "A"):
                    LOGGER.info("VIDEO ACTION found: tag=%s text=%s", tag, node.text_content(timeout=200)[:40].strip())
                    return True
                else:
                    return True
            except Exception:
                continue
    return False







def _dump_network_and_screenshot(page, settings, tag: str = "") -> None:
    """Dump captured network requests and save a screenshot for debugging."""
    import json as _json
    try:
        reqs = getattr(page, "_video_requests", []) or []
        if reqs:
            LOGGER.info("NETWORK REQUESTS [%s]: %s", tag, _json.dumps([{"url": r["url"][:200], "method": r["method"]} for r in reqs], ensure_ascii=False)[:1000])
    except Exception:
        pass
    try:
        ss_path = os.path.join(settings.data_dir, "logs", f"video-{tag}-{int(time.time())}.png")
        page.screenshot(path=ss_path)
        LOGGER.info("SCREENSHOT [%s]: %s", tag, ss_path)
    except Exception:
        pass

def wait_for_video(page, settings, selectors: dict) -> None:
    """Poll until the Video Overview finishes rendering."""
    start = time.time()
    deadline = start + max(60, settings.video_timeout_seconds)
    last_log = 0.0
    while time.time() < deadline:
        if video_ready(page, selectors):
            LOGGER.info("VIDEO GENERATION COMPLETE after %.0fs", time.time() - start)
            return
        elapsed = time.time() - start
        now = time.time()
        if now - last_log > 30.0:
            LOGGER.info("WAITING for video: %.0fs elapsed", elapsed)
            # Dump page state periodically for debugging
            try:
                buttons = page.locator("button, [role='button'], mat-card, video")
                visible = []
                for i in range(min(buttons.count(), 15)):
                    try:
                        b = buttons.nth(i)
                        if b.is_visible():
                            txt = (b.text_content(timeout=200) or "").strip()[:50]
                            aria = (b.get_attribute("aria-label") or "")[:30]
                            if txt or aria:
                                visible.append(f"  [{i}] text={txt!r} aria={aria!r}")
                    except Exception:
                        pass
                if visible:
                    LOGGER.info("PAGE STATE:\n%s", "\n".join(visible))
            except Exception:
                pass
            last_log = now
        page.wait_for_timeout(max(3, settings.poll_seconds) * 1000)
    _dump_network_and_screenshot(page, settings, tag="timeout")
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
    
    # Strategy 1: Find video element directly on the page (post-generation)
    LOGGER.info("Looking for video element to extract directly...")
    try:
        # Search for any video element with src
        video_info = page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                const src = v.currentSrc || v.src || '';
                if (src) {
                    return {
                        src: src,
                        rect: v.getBoundingClientRect(),
                        visible: v.offsetParent !== null,
                        paused: v.paused,
                    };
                }
            }
            return null;
        }""")
        if video_info and video_info.get('src'):
            LOGGER.info("VIDEO ELEMENT FOUND: src=%s visible=%s", video_info['src'][:150], video_info.get('visible'))
            # Click play to ensure the video is loading
            try:
                play_btn = page.locator("button[aria-label*='play' i], [aria-label*='Play' i] button, [class*='play'] button").first
                if play_btn.is_visible():
                    play_btn.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass
            # Download via fetch
            src = video_info['src']
            if src.startswith('blob:'):
                # For blob URLs, use JS fetch
                import base64
                b64data = page.evaluate("""async (url) => {
                    try {
                        const r = await fetch(url);
                        const blob = await r.blob();
                        return await new Promise((res, rej) => {
                            const reader = new FileReader();
                            reader.onload = () => res(reader.result);
                            reader.onerror = () => rej('FileReader error');
                            reader.readAsDataURL(blob);
                        });
                    } catch(e) { return 'ERROR:' + e.message; }
                }""", src)
                if b64data and b64data.startswith("data:"):
                    _, data = b64data.split(",", 1)
                    raw = base64.b64decode(data)
                    with open(target_path, "wb") as f:
                        f.write(raw)
                    LOGGER.info("VIDEO DOWNLOADED via blob fetch: %d bytes", len(raw))
                    _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                    return target_path
            else:
                # Direct URL - use fetch
                import base64
                b64data = page.evaluate("""async (url) => {
                    try {
                        const r = await fetch(url);
                        if (!r.ok) return 'HTTP:' + r.status;
                        const blob = await r.blob();
                        return await new Promise((res, rej) => {
                            const reader = new FileReader();
                            reader.onload = () => res(reader.result);
                            reader.onerror = () => rej('FileReader error');
                            reader.readAsDataURL(blob);
                        });
                    } catch(e) { return 'ERROR:' + e.message; }
                }""", src)
                if b64data and b64data.startswith("data:"):
                    _, data = b64data.split(",", 1)
                    raw = base64.b64decode(data)
                    with open(target_path, "wb") as f:
                        f.write(raw)
                    LOGGER.info("VIDEO DOWNLOADED via direct URL fetch: %d bytes", len(raw))
                    _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                    return target_path
                else:
                    LOGGER.warning("Fetch result: %s", str(b64data)[:100])
    except Exception as e:
        LOGGER.info("Direct video extraction failed: %s", e)
    
    # Strategy 2: Find the Video Overview card and click play, then extract
    LOGGER.info("Trying to find Video Overview card and play...")
    try:
        # Find card with play button (the overview artifact)
        card = page.evaluate("""() => {
            // Look for a card/container that has both a video/play icon and the text 'Video Overview' or '\\u0645\\u0631\\u0648\\u0631'
            const all = document.querySelectorAll('[class*="card"], [class*="artifact"], [class*="overview"], mat-card, [class*="studio"] > div, section');
            for (const el of all) {
                const text = el.textContent || '';
                if ((text.includes('Video Overview') || text.includes('\\u0645\\u0631\\u0648\\u0631')) && (el.querySelector('video') || el.querySelector('[class*="play"]') || el.querySelector('[aria-label*="play" i]'))) {
                    return { outerHTML: el.outerHTML.substring(0, 2000), tag: el.tagName, className: el.className.substring(0, 100) };
                }
            }
            return null;
        }""")
        if card:
            LOGGER.info("VIDEO OVERVIEW CARD: tag=%s class=%s", card.get('tag'), card.get('className','')[:80])
            LOGGER.info("CARD HTML: %s", card.get('outerHTML','')[:1000])
        else:
            LOGGER.info("No Video Overview card found with play button, trying any card with video...")
            card2 = page.evaluate("""() => {
                const cards = document.querySelectorAll('[class*="card"], mat-card, [class*="artifact"], section');
                for (const c of cards) {
                    const v = c.querySelector('video');
                    if (v) return { html: c.outerHTML.substring(0, 2000), tag: c.tagName, class: c.className.substring(0, 100) };
                }
                return null;
            }""")
            if card2:
                LOGGER.info("CARD WITH VIDEO: tag=%s class=%s", card2.get('tag'), card2.get('class','')[:80])
                LOGGER.info("CARD HTML: %s", card2.get('html','')[:1000])
    except Exception as e:
        LOGGER.info("Card detection failed: %s", e)
    
    # Strategy 3: Try playing and capturing network request
    LOGGER.info("Trying play + network capture approach...")
    try:
        # Find play button and click it
        play_selectors = [
            "button[aria-label*='play' i]",
            "[aria-label*='play' i]",
            "button:has-text('play_arrow')",
            "[class*='play'] button",
            "button[class*='play']",
            "button:has(svg)",
            "[class*='video-overview'] button",
        ]
        play_btn = None
        for sel in play_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible():
                    play_btn = btn
                    LOGGER.info("Play button found: %s", sel)
                    break
            except Exception:
                continue
        if play_btn:
            play_btn.click()
            page.wait_for_timeout(3000)
            # After playing, check for video element
            video_src = page.evaluate("""() => {
                const v = document.querySelector('video[src*="blob"], video[src]');
                if (v) return v.currentSrc || v.src || v.querySelector('source')?.src || '';
                return '';
            }""")
            if video_src:
                LOGGER.info("VIDEO SRC AFTER PLAY: %s", video_src[:200])
                import base64
                b64data = page.evaluate("""async (url) => {
                    try {
                        const r = await fetch(url);
                        const blob = await r.blob();
                        return await new Promise((res, rej) => {
                            const reader = new FileReader();
                            reader.onload = () => res(reader.result);
                            reader.onerror = () => rej('FileReader error');
                            reader.readAsDataURL(blob);
                        });
                    } catch(e) { return 'ERROR:' + e.message; }
                }""", video_src)
                if b64data and b64data.startswith("data:"):
                    _, data = b64data.split(",", 1)
                    raw = base64.b64decode(data)
                    with open(target_path, "wb") as f:
                        f.write(raw)
                    LOGGER.info("VIDEO DOWNLOADED via play+fetch: %d bytes", len(raw))
                    _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                    return target_path
            else:
                LOGGER.info("No video src found after play click")
    except Exception as e:
        LOGGER.info("Play approach failed: %s", e)

    raise NotebookLMError("download video", "could not download the generated video after all strategies")

def _try_trim(path: str, trim_last: int) -> None:
    """Trim last N seconds from video if ffmpeg/ffprobe are available."""
    if trim_last <= 0:
        return
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        total = float(probe.stdout.strip() or 0)
        if total > trim_last + 1:
            _trim_video(path, total - trim_last)
        else:
            LOGGER.info("Video too short (%.1fs) to trim %ds", total, trim_last)
    except Exception as exc:
        LOGGER.warning("Could not probe/trim video: %s", exc)
