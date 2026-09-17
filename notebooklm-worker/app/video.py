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
    
    # Wait a moment for the artifact to fully render
    page.wait_for_timeout(3000)
    
    # Step 1: Log ALL artifact-like elements to find the right one
    LOGGER.info("=== Scanning DOM for artifact candidates ===")
    try:
        candidates = page.evaluate("""() => {
            const all = document.querySelectorAll('[class*="artifact"], [class*="overview"], [class*="studio"] > div, mat-card, section[class*="card"]');
            return Array.from(all).map(el => ({
                tag: el.tagName,
                cls: (el.className || '').substring(0, 120),
                text: (el.textContent || '').trim().substring(0, 200),
                aria: el.getAttribute('aria-label') || '',
                role: el.getAttribute('role') || '',
                childBtns: el.querySelectorAll('button').length,
                hasVideo: !!el.querySelector('video'),
                hasPlay: !!(el.querySelector('[class*="play"]') || el.querySelector('[aria-label*="play" i]')),
                childLinks: el.querySelectorAll('a').length,
                rect: (function() { const r = el.getBoundingClientRect(); return {w: r.width, h: r.height, t: r.top, l: r.left}; })(),
                visible: el.offsetParent !== null,
            }));
        }""")
        LOGGER.info("Found %d artifact candidates:", len(candidates))
        for i, c in enumerate(candidates):
            if c.get('visible') and (c.get('hasVideo') or c.get('hasPlay') or '\\u0645\\u0631\\u0648\\u0631' in c.get('text','') or 'Video Overview' in c.get('text','') or c.get('childBtns',0) > 0):
                LOGGER.info("  [%d] tag=%-6s cls=%-40s visible=%s hasVideo=%s hasPlay=%s btns=%d text=%s",
                    i, c.get('tag',''), c.get('cls','')[:40], c.get('visible'), c.get('hasVideo'), c.get('hasPlay'), c.get('childBtns',0), c.get('text','')[:80])
    except Exception as e:
        LOGGER.info("Candidate scan failed: %s", e)
    
    # Step 2: Find the actual generated card
    artifact_card = None
    try:
        card_info = page.evaluate("""() => {
            // Strategy A: find element containing play button + overview text
            const playEls = document.querySelectorAll('[aria-label*="play" i], [class*="play"], button:has(svg)');
            for (const p of playEls) {
                const parent = p.closest('[class*="artifact"], [class*="card"], mat-card, [class*="overview"], section, [class*="studio"] > div');
                if (parent) {
                    const txt = parent.textContent || '';
                    if (txt.includes('\\u0645\\u0631\\u0648\\u0631') || txt.includes('Video Overview') || parent.querySelector('video')) {
                        return {
                            tag: parent.tagName,
                            cls: (parent.className || '').substring(0, 120),
                            outerHTML: parent.outerHTML.substring(0, 2500),
                        };
                    }
                }
            }
            // Strategy B: find any visible card with video
            const cards = document.querySelectorAll('mat-card, [class*="card"], section, [class*="artifact"]');
            for (const c of cards) {
                if (c.offsetParent !== null && (c.querySelector('video') || c.querySelector('[class*="play"]'))) {
                    return {
                        tag: c.tagName,
                        cls: (c.className || '').substring(0, 120),
                        outerHTML: c.outerHTML.substring(0, 2500),
                    };
                }
            }
            return null;
        }""")
        if card_info:
            LOGGER.info("=== GENERATED CARD FOUND ===")
            LOGGER.info("tag=%s cls=%s", card_info.get('tag',''), card_info.get('cls','')[:80])
            LOGGER.info("CARD HTML:\n%s", card_info.get('outerHTML',''))
            artifact_card = card_info
    except Exception as e:
        LOGGER.info("Card detection failed: %s", e)
    
    # Step 3: If we found a card, try to extract video from it
    if artifact_card:
        LOGGER.info("=== Attempting video extraction from card ===")
        try:
            # Look for video element inside the card or on the page
            video_data = page.evaluate("""() => {
                const v = document.querySelector('video[src*="blob"], video[src], video[currentSrc]');
                if (v) {
                    const src = v.currentSrc || v.src || '';
                    const rect = v.getBoundingClientRect();
                    return {
                        src: src,
                        visible: v.offsetParent !== null,
                        rect: {w: rect.width, h: rect.height},
                        paused: v.paused,
                    };
                }
                return null;
            }""")
            if video_data and video_data.get('src'):
                src = video_data['src']
                LOGGER.info("VIDEO FOUND: src=%s visible=%s", src[:150], video_data.get('visible'))
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
                    LOGGER.info("VIDEO DOWNLOADED: %d bytes from %s", len(raw), src[:80])
                    _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                    return target_path
        except Exception as vid_err:
            LOGGER.info("Video extraction from card failed: %s", vid_err)
        
        # Step 4: Try clicking play inside the card
        LOGGER.info("Trying play-click approach...")
        try:
            play_btn = page.locator("[aria-label*='play' i] button, button[aria-label*='play' i], [class*='play'] button, .play-button").first
            if play_btn.is_visible():
                play_btn.click()
                page.wait_for_timeout(3000)
                # Check for new video element
                video_after = page.evaluate("""() => {
                    const v = document.querySelector('video[src*="blob"], video[src], video[currentSrc]');
                    if (v) return v.currentSrc || v.src || '';
                    return '';
                }""")
                if video_after:
                    LOGGER.info("VIDEO SRC AFTER PLAY: %s", video_after[:150])
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
                    }""", video_after)
                    if b64data and b64data.startswith("data:"):
                        _, data = b64data.split(",", 1)
                        raw = base64.b64decode(data)
                        with open(target_path, "wb") as f:
                            f.write(raw)
                        LOGGER.info("VIDEO DOWNLOADED after play: %d bytes", len(raw))
                        _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                        return target_path
        except Exception as play_err:
            LOGGER.info("Play-click approach failed: %s", play_err)
    
    # Step 5: Last resort - extract any video on the page
    LOGGER.info("Last resort: finding any video on page...")
    try:
        any_video = page.evaluate("""() => {
            const v = document.querySelector('video');
            if (v) return v.currentSrc || v.src || v.querySelector('source')?.src || '';
            return '';
        }""")
        if any_video:
            LOGGER.info("ANY VIDEO SRC: %s", any_video[:150])
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
            }""", any_video)
            if b64data and b64data.startswith("data:"):
                _, data = b64data.split(",", 1)
                raw = base64.b64decode(data)
                with open(target_path, "wb") as f:
                    f.write(raw)
                LOGGER.info("VIDEO DOWNLOADED last resort: %d bytes", len(raw))
                _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                return target_path
    except Exception:
        pass
    
    raise NotebookLMError("download video", "could not find or download the generated video")

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
