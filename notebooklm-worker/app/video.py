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


def _select_video_sources(page) -> None:
    """Click the source selector in Video Overview dialog and select all sources."""
    import time as _time
    try:
        # Find the source selector button (shows "0 source" or similar)
        src_btn = page.locator("[role='dialog'] button").filter(has_text=page.locator("text='\u0645\u0646\u0628\u0639'")).first
        if src_btn.count() > 0 and src_btn.is_visible():
            src_text = (src_btn.text_content(timeout=300) or "").strip()
            LOGGER.info("SOURCE SELECTOR: %s", src_text)
            src_btn.click()
            _time.sleep(0.5)
            # Look for menu/checkboxes to select sources
            # Click "Select all" or individual source checkboxes
            select_all = page.locator("text='\u0627\u0646\u062a\u062e\u0627\u0628 \u0647\u0645\u0647'").first
            if select_all.count() > 0:
                select_all.click()
                LOGGER.info("Selected all sources")
            else:
                # Try clicking individual source items
                items = page.locator("[role='menuitemcheckbox'], [role='option'], .mat-mdc-option")
                for i in range(min(items.count(), 10)):
                    try:
                        item = items.nth(i)
                        if item.is_visible():
                            item.click()
                    except Exception:
                        pass
                LOGGER.info("Clicked %d source items", min(items.count(), 10))
            _time.sleep(0.5)
            # Close the dropdown by clicking elsewhere
            page.keyboard.press("Escape")
            _time.sleep(0.3)
        else:
            LOGGER.info("No source selector found in dialog")
    except Exception as e:
        LOGGER.info("Source selection failed: %s", e)

def start_video_overview(page, prompt: str, settings, selectors: dict) -> None:
    """Open the Video Overview composer and submit the rendered prompt."""
    # Dismiss any blocking notifications before starting
    # Start network monitoring (requests + responses)
    _video_requests = []
    _video_responses = []
    def _on_request(request):
        url = request.url
        if "batchexecute" in url or ("google.com" in url and url.startswith("https://notebook")):
            _video_requests.append({"url": url, "method": request.method})
    def _on_response(response):
        url = response.url
        if "batchexecute" in url:
            try:
                body = response.text()[:2000]
                _video_responses.append({"url": url, "status": response.status, "body": body})
            except Exception:
                pass
    try:
        page._video_requests = _video_requests
        page._video_responses = _video_responses
        page.on("request", _on_request)
        page.on("response", _on_response)
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
    # Try to select sources in the dialog
    _select_video_sources(page)
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
    # Log ALL buttons inside the dialog before clicking
    try:
        all_btns = page.evaluate("""() => {
            const dialog = document.querySelector('[role=\"dialog\"]');
            if (!dialog) return [];
            return Array.from(dialog.querySelectorAll('button')).map(b => ({
                text: (b.textContent || '').trim().substring(0, 50),
                aria: b.getAttribute('aria-label') || '',
                cls: (b.className || '').substring(0, 60),
                disabled: b.disabled || b.hasAttribute('disabled'),
                rect: (function(){const r=b.getBoundingClientRect(); return {t:r.top,l:r.left,w:r.width,h:r.height};})(),
            }));
        }""")
        LOGGER.info("DIALOG BUTTONS:")
        for i, b in enumerate(all_btns or []):
            LOGGER.info("  [%d] text=%s aria=%s disabled=%s rect=%dx%d", i, b.get('text','')[:40], b.get('aria','')[:30], b.get('disabled'), b.get('rect',{}).get('w',0), b.get('rect',{}).get('h',0))
        # Check if the only matching button is the "later" dismiss
        dismiss_btns = [b for b in (all_btns or []) if 'بعداً' in b.get('text','') or 'later' in b.get('text','').lower() or 'skip' in b.get('text','').lower()]
        if dismiss_btns and not [b for b in (all_btns or []) if 'بعداً' not in b.get('text','') and ('تولید' in b.get('text','') or 'ساخت' in b.get('text','') or 'ایجاد' in b.get('text','') or 'create' in b.get('text','').lower() or 'generate' in b.get('text','').lower())]:
            LOGGER.warning("Only dismiss button found! The real generate/submit button is missing.")
    except Exception as e:
        LOGGER.info("Button dump failed: %s", e)
    
    # Find the generate button and try multiple click strategies
    # NotebookLM uses Angular Material which may not respond to Playwright clicks
    # Prefer the primary submit button (usually has mat-mdc-unelevated-button class)
    gen_node = find_first(page, selectors["video_generate"], timeout=10)
    if gen_node is not None:
        # Verify we're NOT about to click the dismiss button
        actual_text = ""
        try:
            actual_text = (gen_node.text_content(timeout=200) or "").strip()
            if "بعداً" in actual_text:
                LOGGER.error("WRONG BUTTON (dismiss): '%s' - trying alternate selector", actual_text[:50])
                # Try to find the real submit button
                gen_node = find_first(page, [
                    s for s in selectors["video_generate"] 
                    if "بعداً" not in s
                ], timeout=5)
        except Exception:
            pass
        # Log button state before click
        try:
            btn_disabled = gen_node.is_disabled()
            btn_text = gen_node.text_content(timeout=300) or ""
            btn_aria = gen_node.get_attribute("aria-label") or ""
            btn_classes = gen_node.get_attribute("class") or ""
            LOGGER.info("GENERATE BTN BEFORE: disabled=%s text=%r aria=%r class=%s", btn_disabled, btn_text.strip()[:50], btn_aria, btn_classes[:40])
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
        page.wait_for_timeout(2000)
        # Strategy 5: keyboard Enter on prompt field if dialog still open
        try:
            if page.locator("[role='dialog']").count() > 0:
                prompt_input = page.locator(selectors["video_prompt"][0]).first
                if prompt_input.is_visible():
                    prompt_input.focus()
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(1000)
                    LOGGER.info("GENERATE: keyboard Enter on prompt field done")
        except Exception:
            pass
        page.wait_for_timeout(2000)
        # Try keyboard Enter on prompt field if dialog still open (as extra submit signal)
        try:
            if page.locator("[role='dialog']").count() > 0:
                # Find prompt field and press Enter to submit
                prompt_field = page.locator(selectors["video_prompt"][0]).first
                if prompt_field.is_visible():
                    prompt_field.focus()
                    page.wait_for_timeout(200)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(1000)
                    LOGGER.info("GENERATE: keyboard Enter submit done")
        except Exception:
            pass
        page.wait_for_timeout(2000)
        # Take screenshot and dump network requests right after click
        _dump_network_and_screenshot(page, settings, tag="after-generate")
        # Also capture console errors from the browser
        try:
            console_errors = []
            def _on_console(msg):
                if msg.type == 'error':
                    console_errors.append(msg.text[:300])
            page.on('console', _on_console)
            page.wait_for_timeout(500)
            if console_errors:
                for err in console_errors:
                    LOGGER.warning("CONSOLE ERROR: %s", err)
        except Exception:
            pass
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
        try:
            rpc_count = len(getattr(page, "_video_requests", []) or [])
            rpc_resp_count = len(getattr(page, "_video_responses", []) or [])
            LOGGER.info("RPC STATUS: requests=%d responses=%d", rpc_count, rpc_resp_count)
        except Exception:
            pass
    except Exception:
        pass


def video_ready(page, selectors: dict) -> bool:
    """True when a rendered video or its download affordance is visible.
    
    IMPORTANT: Does NOT match Studio create-artifact buttons ([class*='artifact']
    is always present in Studio and is a false positive). Only signals ready
    when there is actual generated content in the artifact library.
    """
    # First check for video element (most reliable)
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
                    LOGGER.info("VIDEO READY via %s: tag=%s", selector, tag)
                    return True
            except Exception:
                continue
    
    # Check for non-empty artifact library (generated artifact, not the create buttons)
    try:
        has_artifact = page.evaluate("""() => {
            // Find the artifact library container
            const containers = document.querySelectorAll('[class*="artifact-library"]');
            for (const c of containers) {
                // Skip empty state containers
                if (c.className.includes('empty')) continue;
                // Skip if it contains "will be saved here" empty-state text
                const text = c.textContent || '';
                if (text.includes('\\u062e\\u0648\\u0627\\u0647\\u062f \\u0634\\u062f') ||  // خواهد شد
                    text.includes('saved here')) continue;
                // Check it has actual children with content
                if (c.children.length > 0 && c.offsetParent !== null) {
                    // Look for any actionable element inside
                    const actionable = c.querySelector('button, video, [role="button"], a, img, [class*="card"]');
                    if (actionable) {
                        LOGGER.info("VIDEO ARTIFACT LIBRARY has content (non-empty)");
                        return true;
                    }
                }
            }
            return false;
        }""")
        if has_artifact:
            return True
    except Exception:
        pass
    
    return False







def _dump_network_and_screenshot(page, settings, tag: str = "") -> None:
    """Dump captured network requests/responses and save a screenshot."""
    import json as _json
    try:
        reqs = getattr(page, "_video_requests", []) or []
        if reqs:
            LOGGER.info("NETWORK REQUESTS [%s]: %s", tag, _json.dumps([{"url": r["url"][:200], "method": r["method"]} for r in reqs], ensure_ascii=False)[:1000])
    except Exception:
        pass
    try:
        resps = getattr(page, "_video_responses", []) or []
        for r in resps:
            LOGGER.info("NETWORK RESPONSE [%s]: url=%s status=%s body=%s", tag, r.get("url","")[:200], r.get("status"), r.get("body","")[:500])
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
    
    # Wait for artifact to fully render
    page.wait_for_timeout(3000)
    _dump_network_and_screenshot(page, settings, tag="download-start")
    
    # Step 1: Comprehensive DOM scan - log ALL visible elements on the page
    LOGGER.info("=== FULL PAGE DOM SCAN ===")
    try:
        dom = page.evaluate("""() => {
            const results = [];
            // Scan all visible elements in the main page area (not header/sidebar)
            const all = document.querySelectorAll('body *');
            for (const el of all) {
                if (!el.offsetParent) continue; // skip hidden
                const tag = el.tagName.toLowerCase();
                if (['script','style','meta','link','noscript'].includes(tag)) continue;
                const cls = (el.className || '').substring(0, 100);
                const txt = (el.textContent || '').trim().substring(0, 120);
                const aria = el.getAttribute('aria-label') || '';
                const role = el.getAttribute('role') || '';
                const hasVideo = !!el.querySelector('video') || tag === 'video';
                const hasBtn = el.querySelectorAll('button').length > 0 || tag === 'button';
                const rect = el.getBoundingClientRect();
                // Only log interesting elements
                if (hasVideo || hasBtn || aria || (txt && cls) || 
                    cls.includes('artifact') || cls.includes('card') || cls.includes('overview') ||
                    cls.includes('studio') || cls.includes('generated')) {
                    results.push({
                        tag: tag, cls: cls.substring(0, 80), txt: txt.substring(0, 100),
                        aria: aria.substring(0, 50), role: role,
                        w: Math.round(rect.width), h: Math.round(rect.height),
                        t: Math.round(rect.top), l: Math.round(rect.left),
                        hasVideo: hasVideo, hasBtn: hasBtn,
                    });
                }
            }
            return results;
        }""")
        LOGGER.info("Page has %d interesting visible elements:", len(dom))
        for i, d in enumerate(dom):
            LOGGER.info("  [%d] <%-6s> cls=%-30s aria=%s txt=%s hasV=%s hasB=%s rect=%dx%d at (%d,%d)",
                i, d.get('tag',''), d.get('cls',''), d.get('aria',''), d.get('txt','')[:50],
                d.get('hasVideo'), d.get('hasBtn'),
                d.get('w',0), d.get('h',0), d.get('l',0), d.get('t',0))
    except Exception as e:
        LOGGER.info("DOM scan failed: %s", e)
    
    # Step 2: Find ALL artifact-like elements and log them with full detail
    LOGGER.info("=== DEEP ARTIFACT SCAN ===")
    try:
        artifacts = page.evaluate("""() => {
            const results = [];
            const all = document.querySelectorAll('[class*="artifact"], [class*="overview"], [class*="card"], mat-card, [class*="studio"] > *, [class*="generated"], [class*="result"], [class*="output"]');
            for (const el of all) {
                if (!el.offsetParent) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 50 || rect.height < 20) continue; // too small
                const btns = el.querySelectorAll('button');
                const btnInfo = Array.from(btns).slice(0, 5).map(b => ({
                    text: (b.textContent || '').trim().substring(0, 40),
                    aria: b.getAttribute('aria-label') || '',
                    cls: (b.className || '').substring(0, 40),
                }));
                results.push({
                    tag: el.tagName,
                    cls: (el.className || '').substring(0, 150),
                    txt: (el.textContent || '').trim().substring(0, 300),
                    aria: el.getAttribute('aria-label') || '',
                    role: el.getAttribute('role') || '',
                    rect: {w: Math.round(rect.width), h: Math.round(rect.height), t: Math.round(rect.top), l: Math.round(rect.left)},
                    hasVideo: !!el.querySelector('video'),
                    hasSVG: !!el.querySelector('svg'),
                    hasPlayEl: !!(el.querySelector('[class*="play"]') || el.querySelector('[aria-label*="play" i]')),
                    btns: btnInfo,
                });
            }
            return results;
        }""")
        LOGGER.info("Found %d artifact-like containers:", len(artifacts))
        for i, a in enumerate(artifacts):
            LOGGER.info("  [%d] <%-6s> cls=%-50s rect=%dx%d play=%s video=%s",
                i, a.get('tag',''), a.get('cls','')[:50],
                a.get('rect',{}).get('w',0), a.get('rect',{}).get('h',0),
                a.get('hasPlayEl'), a.get('hasVideo'))
            # Log first 200 chars of text
            txt = a.get('txt','')
            if txt:
                LOGGER.info("       text: %s", txt[:150])
            # Log buttons
            for b in a.get('btns',[]):
                LOGGER.info("       btn: text=%s aria=%s cls=%s", b.get('text','')[:30], b.get('aria','')[:30], b.get('cls','')[:30])
    except Exception as e:
        LOGGER.info("Artifact scan failed: %s", e)
    
    # Step 3: Try to find video element anywhere on the page
    LOGGER.info("=== VIDEO ELEMENT SEARCH ===")
    try:
        video_src = page.evaluate("""() => {
            const v = document.querySelector('video');
            if (v) {
                const src = v.currentSrc || v.src || '';
                if (src) {
                    return {
                        src: src,
                        rect: (function(){const r=v.getBoundingClientRect(); return {w:r.width,h:r.height,t:r.top,l:r.left};})(),
                        visible: v.offsetParent !== null,
                        paused: v.paused,
                    };
                }
            }
            return null;
        }""")
        if video_src:
            LOGGER.info("VIDEO ELEMENT: src=%s rect=%dx%d visible=%s paused=%s",
                video_src.get('src','')[:150],
                video_src.get('rect',{}).get('w',0),
                video_src.get('rect',{}).get('h',0),
                video_src.get('visible'), video_src.get('paused'))
        else:
            LOGGER.info("No video element found on page")
    except Exception as e:
        LOGGER.info("Video search failed: %s", e)
    
    # Step 4: Try to find and click play button
    LOGGER.info("=== PLAY BUTTON SEARCH ===")
    play_selectors = [
        "button[aria-label*='play' i]",
        "[aria-label*='play' i]",
        "[class*='play-button']",
        "[class*='play_arrow']",
        "button:has(svg)",
        "[class*='video-overview'] button",
        "[class*='overview-card'] button",
    ]
    for sel in play_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible():
                LOGGER.info("Play button found: %s", sel)
                btn.click()
                page.wait_for_timeout(3000)
                # Check if video appeared after click
                video_after = page.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) return v.currentSrc || v.src || '';
                    return '';
                }""")
                if video_after:
                    LOGGER.info("VIDEO SRC AFTER PLAY: %s", video_after[:150])
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
                    }""", video_after)
                    if b64data and b64data.startswith("data:"):
                        _, data = b64data.split(",", 1)
                        raw = base64.b64decode(data)
                        with open(target_path, "wb") as f:
                            f.write(raw)
                        LOGGER.info("VIDEO DOWNLOADED after play: %d bytes", len(raw))
                        _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                        return target_path
                    else:
                        LOGGER.info("Fetch result: %s", str(b64data)[:100])
                else:
                    LOGGER.info("No video src after play click")
                break
        except Exception:
            continue
    else:
        LOGGER.info("No play button found with any selector")
    
    # Step 5: Take screenshot for visual debugging
    _dump_network_and_screenshot(page, settings, tag="download-end")
    
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
